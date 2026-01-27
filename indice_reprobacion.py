import pandas as pd
import streamlit as st
import altair as alt
import gspread
import re

import bajas_retencion  # resumen de bajas

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
    try:
        return "bajas_retencion" in set(mods)
    except Exception:
        return False


def _ciclo_sort_key(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.strip(), errors="coerce")


def _make_hist_line(df_hist: pd.DataFrame, titulo: str):
    """
    df_hist columnas esperadas:
      - CICLO (str)
      - REPROBADOS_UNICOS (int)
    """
    base = alt.Chart(df_hist)

    line = base.mark_line(point=True).encode(
        x=alt.X(
            "CICLO:N",
            title="Ciclo",
            sort=None,
            axis=alt.Axis(labelAngle=0, labelOverlap="greedy"),
        ),
        y=alt.Y("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
        tooltip=[
            alt.Tooltip("CICLO:N", title="Ciclo"),
            alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos"),
        ],
    ).properties(height=360, title=titulo)

    # Etiqueta numérica en cada punto (mejor lectura)
    labels = base.mark_text(dy=-10).encode(
        x=alt.X("CICLO:N", sort=None),
        y=alt.Y("REPROBADOS_UNICOS:Q"),
        text=alt.Text("REPROBADOS_UNICOS:Q"),
    )

    return (line + labels)


# =========================================
# Carga de Reprobación
# =========================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_reprobacion_from_gsheets(url: str, sheet_name: str | None = None) -> pd.DataFrame:
    sa = st.secrets["gcp_service_account_json"]
    sa_dict = dict(sa) if isinstance(sa, dict) else dict(sa)

    gc = gspread.service_account_from_dict(sa_dict)
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

    url = (st.secrets.get("IR_URL", "") or "").strip()
    sheet_name = (st.secrets.get("IR_SHEET_NAME", SHEET_NAME_DEFAULT) or "").strip() or None

    if not url:
        st.error("Falta configurar IR_URL en Secrets.")
        return

    try:
        df = _load_reprobacion_from_gsheets(url, sheet_name)
    except Exception as e:
        st.error("No se pudo cargar el Google Sheet de reprobación.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja de reprobación está vacía.")
        return

    # Validar mínimas
    for req in ["AREA", "CICLO"]:
        if req not in df.columns:
            st.error(f"Falta columna requerida: {req}")
            st.caption(f"Columnas detectadas: {', '.join(df.columns)}")
            return

    # Normalizaciones base
    for c in ["AREA", "MATERIA", "MATRICULA", "CICLO"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()

    if "CALIF_FINAL" in df.columns:
        df["CALIF_FINAL"] = _to_num(df["CALIF_FINAL"])

    df["AREA_norm"] = df["AREA"].apply(normalizar_texto)

    # =========================
    # Filtros
    # =========================
    f = df.copy()
    area_ctx = None
    ciclo_sel = "(Todos)"

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
            try:
                res = bajas_retencion.resumen_bajas_por_filtros(
                    ciclo=ciclo_int,
                    area=area_ctx,
                )
                st.metric("Bajas", f"{res.get('n', 0):,}")
                top = res.get("top_motivos")
                if top is not None and not top.empty:
                    st.dataframe(top, use_container_width=True)
                else:
                    st.caption("Sin detalle de motivos para este filtro.")
            except Exception as e:
                st.error("No pude calcular el resumen de bajas.")
                st.exception(e)

    st.divider()

    # =========================
    # KPIs
    # =========================
    k1, k2, k3 = st.columns(3)
    k1.metric("Registros", len(f))
    k2.metric("Alumnos únicos", f["MATRICULA"].nunique() if "MATRICULA" in f.columns else "—")
    k3.metric(
        "Promedio calificación",
        f"{f['CALIF_FINAL'].mean():.2f}" if "CALIF_FINAL" in f.columns else "—"
    )

    st.divider()

    # =========================
    # ✅ GRÁFICA EN PICOS POR CICLO (LO QUE NECESITAS EN DC)
    # =========================
    st.markdown("## Histórico de reprobados por ciclo (picos)")

    if "CICLO" not in f.columns or f["CICLO"].dropna().empty:
        st.info("No se detectó una columna CICLO usable para construir el histórico.")
    else:
        base = f.copy()

        if "MATRICULA" in base.columns:
            hist = base.groupby("CICLO")["MATRICULA"].nunique().reset_index(name="REPROBADOS_UNICOS")
        else:
            hist = base.groupby("CICLO").size().reset_index(name="REPROBADOS_UNICOS")

        hist["CICLO_NUM"] = _ciclo_sort_key(hist["CICLO"])
        hist = hist.sort_values(["CICLO_NUM", "CICLO"]).drop(columns=["CICLO_NUM"])
        hist["REPROBADOS_UNICOS"] = pd.to_numeric(hist["REPROBADOS_UNICOS"], errors="coerce").fillna(0).astype(int)

        titulo = "Institucional (todas las carreras)" if (vista != "Director de carrera" and area_ctx is None) else f"{area_ctx or 'Carrera'}"

        chart = _make_hist_line(hist, titulo=f"Reprobados (únicos) por ciclo — {titulo}")
        st.altair_chart(chart, use_container_width=True)
        st.dataframe(hist, use_container_width=True)

    st.divider()

    # =========================
    # Comparativo por carrera (tabla + barras SOLO si hay varias áreas)
    # =========================
    st.markdown("## Comparativo por carrera")

    g = f.groupby("AREA", dropna=False)

    resumen = pd.DataFrame({
        "AREA": g.size().index.astype(str),
        "REPROBADOS": g.size().values,
        "ALUMNOS_UNICOS": g["MATRICULA"].nunique().values if "MATRICULA" in f.columns else g.size().values,
        "PROM_CALIF": g["CALIF_FINAL"].mean().values if "CALIF_FINAL" in f.columns else pd.NA,
    }).sort_values("ALUMNOS_UNICOS", ascending=False).reset_index(drop=True)

    st.dataframe(resumen, use_container_width=True)

    # ✅ Evita la barra “gigante” cuando solo hay 1 carrera (tu screenshot)
    if len(resumen) >= 2:
        chart_bar = (
            alt.Chart(resumen)
            .mark_bar()
            .encode(
                y=alt.Y("AREA:N", sort="-x", title=None),
                x=alt.X("ALUMNOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
                tooltip=[
                    alt.Tooltip("AREA:N", title="Área"),
                    alt.Tooltip("REPROBADOS:Q", title="Registros"),
                    alt.Tooltip("ALUMNOS_UNICOS:Q", title="Únicos"),
                    alt.Tooltip("PROM_CALIF:Q", title="Prom. calif", format=".2f"),
                ],
            )
            .properties(height=max(280, min(900, 24 * len(resumen))))
        )
        st.altair_chart(chart_bar, use_container_width=True)
    else:
        st.caption("En este filtro solo hay **1 carrera**, por eso el comparativo en barras no aporta; el histórico por ciclo es el foco.")
