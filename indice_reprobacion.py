import pandas as pd
import streamlit as st
import altair as alt
import gspread
import re

import bajas_retencion  # resumen de bajas
from collections.abc import Mapping

# =========================================
# Config
# =========================================
SHEET_NAME_DEFAULT = "REPROBACION"

# =========================================
# Normalización (MISMO CRITERIO QUE BAJAS)
# =========================================
def normalizar_texto(valor) -> str:
    if pd.isna(valor):
        return ""
    s = str(valor).lower()
    s = s.replace("\u00A0", " ")
    s = (
        s.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# =========================================
# Helpers
# =========================================
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().upper() for c in out.columns]
    return out


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return None


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


def _ciclo_to_int(x) -> int | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s or s == "(Todos)":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _user_can_see_bajas() -> bool:
    if bool(st.session_state.get("user_allow_all", False)):
        return True
    mods = st.session_state.get("user_modulos", set())
    return "bajas_retencion" in set(mods)


# =========================================
# Carga de Reprobación
# =========================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_reprobacion_from_gsheets(url: str, sheet_name: str | None = None) -> pd.DataFrame:
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    ws = sh.worksheet(sheet_name) if sheet_name else sh.sheet1
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()

    df = pd.DataFrame(values[1:], columns=[h.strip() for h in values[0]]).replace("", pd.NA)
    df = _norm_cols(df)

    renames = {
        _pick_col(df, ["CALIF FINAL", "CALIF_FINAL", "CALIFICACION FINAL", "CALIFICACIÓN FINAL"]): "CALIF_FINAL",
        _pick_col(df, ["MATERIA", "ASIGNATURA"]): "MATERIA",
        _pick_col(df, ["AREA", "CARRERA", "SERVICIO"]): "AREA",
        _pick_col(df, ["MATRICULA", "MATRÍCULA"]): "MATRICULA",
        _pick_col(df, ["CICLO", "CICLO_ESCOLAR"]): "CICLO",
    }

    for k, v in renames.items():
        if k and k != v:
            df = df.rename(columns={k: v})

    return df


# =========================================
# Render principal
# =========================================
def render_indice_reprobacion(vista: str | None = None, carrera: str | None = None):
    st.subheader("Índice de reprobación")

    if not vista:
        vista = "Dirección General"

    url = st.secrets.get("IR_URL", "").strip()
    sheet_name = st.secrets.get("IR_SHEET_NAME", SHEET_NAME_DEFAULT).strip() or None

    if not url:
        st.error("Falta configurar IR_URL en Secrets.")
        return

    df = _load_reprobacion_from_gsheets(url, sheet_name)
    if df.empty:
        st.warning("La hoja de reprobación está vacía.")
        return

    # Normalizaciones base
    for c in ["AREA", "MATERIA", "MATRICULA", "CICLO"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    if "CALIF_FINAL" in df.columns:
        df["CALIF_FINAL"] = _to_num(df["CALIF_FINAL"])

    # === Catálogo desde Bajas (fuente única de verdad) ===
    bajas_df = bajas_retencion.get_bajas_base_df()
    cat = (
        bajas_df[["AREA_ID", "AREA_OFICIAL"]]
        .dropna()
        .drop_duplicates()
    )

    df["AREA_norm"] = df["AREA"].apply(normalizar_texto)
    cat["AREA_norm"] = cat["AREA_OFICIAL"].apply(normalizar_texto)

    df = df.merge(
        cat[["AREA_ID", "AREA_norm"]],
        on="AREA_norm",
        how="left",
    )

    # =========================
    # Filtros
    # =========================
    f = df.copy()

    if vista == "Director de carrera" and carrera:
        carrera_norm = normalizar_texto(carrera)
        f = f[f["AREA_norm"] == carrera_norm]

        st.text_input("Carrera", value=carrera, disabled=True)

        ciclos = ["(Todos)"] + sorted(f["CICLO"].dropna().unique().tolist())
        ciclo_sel = st.selectbox("Ciclo", ciclos)

        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"] == ciclo_sel]

        area_ctx = carrera

    else:
        c1, c2 = st.columns(2)
        with c1:
            area_sel = st.selectbox(
                "Carrera",
                ["(Todas)"] + sorted(f["AREA"].dropna().unique().tolist())
            )
        with c2:
            ciclo_sel = st.selectbox(
                "Ciclo",
                ["(Todos)"] + sorted(f["CICLO"].dropna().unique().tolist())
            )

        if area_sel != "(Todas)":
            f = f[f["AREA"] == area_sel]
        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"] == ciclo_sel]

        area_ctx = None if area_sel == "(Todas)" else area_sel

    st.caption(f"Registros filtrados: **{len(f)}**")
    if f.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # =========================
    # Resumen de bajas
    # =========================
    if _user_can_see_bajas():
        with st.expander("📌 Resumen de bajas (contexto)", expanded=True):
            ciclo_int = _ciclo_to_int(ciclo_sel)
            res = bajas_retencion.resumen_bajas_por_filtros(
                ciclo=ciclo_int,
                area=area_ctx,
            )
            st.metric("Bajas", f"{res.get('n', 0):,}")
            if not res["top_motivos"].empty:
                st.dataframe(res["top_motivos"], use_container_width=True)

    st.divider()

    # =========================
    # KPIs
    # =========================
    c1, c2, c3 = st.columns(3)
    c1.metric("Registros", len(f))
    c2.metric("Alumnos únicos", f["MATRICULA"].nunique() if "MATRICULA" in f.columns else "—")
    c3.metric(
        "Promedio calificación",
        f"{f['CALIF_FINAL'].mean():.2f}" if "CALIF_FINAL" in f.columns else "—"
    )

    # =========================
    # Comparativo por carrera
    # =========================
    g = f.groupby("AREA")

    resumen = pd.DataFrame({
        "AREA": g.size().index,
        "REPROBADOS": g.size().values,
        "ALUMNOS_UNICOS": g["MATRICULA"].nunique().values if "MATRICULA" in f.columns else g.size().values,
        "PROM_CALIF": g["CALIF_FINAL"].mean().values if "CALIF_FINAL" in f.columns else pd.NA,
    }).sort_values("ALUMNOS_UNICOS", ascending=False)

    st.markdown("## Comparativo por carrera")
    st.dataframe(resumen, use_container_width=True)

    chart = (
        alt.Chart(resumen.reset_index(drop=True))
        .mark_bar()
        .encode(
            y=alt.Y("AREA:N", sort="-x"),
            x=alt.X("ALUMNOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
            tooltip=["AREA", "REPROBADOS", "ALUMNOS_UNICOS", alt.Tooltip("PROM_CALIF", format=".2f")],
        )
        .properties(height=max(300, 24 * len(resumen)))
    )
    st.altair_chart(chart, use_container_width=True)
