import streamlit as st
import pandas as pd
import gspread
import json
import re
import altair as alt
from collections.abc import Mapping
from google.oauth2.service_account import Credentials

# ============================================================
# Config (desde Secrets)
# ============================================================
# Secrets esperados (opcionales si app.py ya inyecta df_cat_carreras):
#   BAJAS_URL
#   BAJAS_SHEET_NAME
#   CATALOGO_CARRERAS_URL
#   CATALOGO_CARRERAS_SHEET

BAJAS_SHEET_URL = (st.secrets.get("BAJAS_URL", "") or "").strip()
BAJAS_TAB_NAME = (st.secrets.get("BAJAS_SHEET_NAME", "BAJAS") or "BAJAS").strip()

CATALOGO_URL = (st.secrets.get("CATALOGO_CARRERAS_URL", "") or "").strip()
CATALOGO_SHEET = (st.secrets.get("CATALOGO_CARRERAS_SHEET", "CAT_CARRERAS") or "CAT_CARRERAS").strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ============================================================
# Helpers base
# ============================================================
def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url or "")
    if not m:
        raise ValueError("No pude extraer el ID del Google Sheet desde la URL.")
    return m.group(1)


def _load_creds_dict() -> dict:
    raw = st.secrets["gcp_service_account_json"]
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


@st.cache_resource(show_spinner=False)
def _get_gspread_client():
    creds_dict = _load_creds_dict()
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _load_ws_df(url: str, sheet_name: str) -> pd.DataFrame:
    if not url:
        raise ValueError("URL de Google Sheet no configurada.")
    gc = _get_gspread_client()
    sh = gc.open_by_key(_extract_sheet_id(url))
    ws = sh.worksheet(sheet_name)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = [str(h).strip() for h in values[0]]
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


# ============================================================
# Normalización de texto
# ============================================================
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


# ============================================================
# Catálogo maestro de carreras (primero session_state, luego Secrets)
# ============================================================
@st.cache_data(ttl=600, show_spinner=False)
def _build_catalog_maps_from_df(df: pd.DataFrame):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    required = {"carrera_id", "nombre_oficial", "variantes"}
    if not required.issubset(set(df.columns)):
        raise ValueError("El catálogo debe tener columnas: carrera_id, nombre_oficial, variantes")

    var_to_id = {}
    id_to_nombre = {}

    for _, row in df.iterrows():
        cid = str(row["carrera_id"]).strip()
        nombre = str(row["nombre_oficial"]).strip()
        id_to_nombre[cid] = nombre

        variantes = str(row["variantes"]).split("|")
        for v in variantes:
            key = normalizar_texto(v)
            if key:
                var_to_id[key] = cid

    return var_to_id, id_to_nombre


@st.cache_data(ttl=600, show_spinner=False)
def _load_catalogo_carreras_from_secrets():
    if not CATALOGO_URL:
        raise ValueError("Falta configurar CATALOGO_CARRERAS_URL en Secrets.")
    df = _load_ws_df(CATALOGO_URL, CATALOGO_SHEET)
    return _build_catalog_maps_from_df(df)


def _get_catalog_maps():
    # 1) Preferir lo inyectado por app.py
    df_cat = st.session_state.get("df_cat_carreras")
    if isinstance(df_cat, pd.DataFrame) and not df_cat.empty:
        return _build_catalog_maps_from_df(df_cat)

    # 2) fallback a Secrets
    return _load_catalogo_carreras_from_secrets()


# ============================================================
# Base BAJAS normalizada y enriquecida
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_bajas_base_df() -> pd.DataFrame:
    if not BAJAS_SHEET_URL:
        raise ValueError("Falta configurar BAJAS_URL en Secrets.")

    df = _load_ws_df(BAJAS_SHEET_URL, BAJAS_TAB_NAME)
    if df.empty:
        return df

    df.columns = [c.strip().upper() for c in df.columns]

    # AREA
    if "AREA" in df.columns:
        df["AREA_norm"] = df["AREA"].apply(normalizar_texto)
    else:
        df["AREA_norm"] = ""

    # CICLO
    if "CICLO" in df.columns:
        df["CICLO"] = pd.to_numeric(df["CICLO"], errors="coerce")

    # FECHA
    if "FECHA_BAJA" in df.columns:
        df["FECHA_BAJA_DT"] = pd.to_datetime(df["FECHA_BAJA"], errors="coerce", dayfirst=True)
        df["MES"] = df["FECHA_BAJA_DT"].dt.to_period("M").astype(str)
    else:
        df["FECHA_BAJA_DT"] = pd.NaT
        df["MES"] = ""

    # MOTIVO (fix pd.NA)
    if "MOTIVO_BAJA" in df.columns:
        def split_motivo(x):
            if pd.isna(x):
                return "", ""
            s = str(x).strip()
            if not s:
                return "", ""
            p = s.split("-", 1)
            return p[0].strip().upper(), p[1].strip() if len(p) > 1 else ""

        df["MOTIVO_RAW"], df["MOTIVO_DETALLE"] = zip(*df["MOTIVO_BAJA"].apply(split_motivo))
    else:
        df["MOTIVO_RAW"] = ""
        df["MOTIVO_DETALLE"] = ""

    def std_cat(x):
        x = str(x or "")
        if "ECON" in x:
            return "ECONÓMICO"
        if "CAMBIO" in x:
            return "CAMBIO DE CARRERA"
        if "SALUD" in x or "ENFERM" in x:
            return "SALUD"
        if "ADMIN" in x or "COBRAN" in x:
            return "BAJA ADMINISTRATIVA"
        if "ADMISI" in x or "ENTREV" in x:
            return "ADMISIÓN / INGRESO"
        if "PERSONAL" in x:
            return "MOTIVOS PERSONALES"
        if "OTRO" in x:
            return "OTROS"
        return "SIN ESPECIFICAR"

    df["MOTIVO_CATEGORIA_STD"] = df["MOTIVO_RAW"].apply(std_cat)

    # === Enriquecimiento con catálogo maestro ===
    var_to_id, id_to_nombre = _get_catalog_maps()
    df["AREA_ID"] = df["AREA_norm"].map(var_to_id)
    df["AREA_OFICIAL"] = df["AREA_ID"].map(id_to_nombre)

    return df


# ============================================================
# API para otros módulos (Índice de Reprobación)
# ============================================================
def resumen_bajas_por_filtros(ciclo: int | None = None, area: str | None = None) -> dict:
    df = get_bajas_base_df()
    if df.empty:
        return {"ok": True, "n": 0, "top_motivos": pd.DataFrame()}

    x = df.copy()

    if ciclo is not None and "CICLO" in x.columns:
        x = x[x["CICLO"] == ciclo]

    if area:
        area_norm = normalizar_texto(area)
        x = x[x["AREA_norm"] == area_norm]

    top = (
        x["MOTIVO_CATEGORIA_STD"]
        .value_counts()
        .head(8)
        .reset_index()
        .rename(columns={"index": "Motivo", "MOTIVO_CATEGORIA_STD": "Bajas"})
    )

    return {"ok": True, "n": len(x), "top_motivos": top}


# ============================================================
# Render principal
# ============================================================
def render_bajas_retencion(vista: str, carrera: str | None):
    st.subheader("Bajas / Retención")

    try:
        df = get_bajas_base_df()
    except Exception as e:
        st.error("No se pudieron cargar las bajas.")
        st.exception(e)
        return

    if df.empty:
        st.warning("No hay datos de bajas.")
        return

    st.markdown("### Filtros")

    carrera_norm = normalizar_texto(carrera) if vista == "Director de carrera" and carrera else None

    col1, col2 = st.columns(2)

    if carrera_norm:
        with col1:
            st.markdown(f"**Área:** {carrera}")
        f = df[df["AREA_norm"] == carrera_norm]
    else:
        areas = sorted(df["AREA_OFICIAL"].dropna().unique().tolist())
        area_sel = col1.selectbox("Área", ["(Todas)"] + areas)
        f = df if area_sel == "(Todas)" else df[df["AREA_OFICIAL"] == area_sel]

    ciclos = sorted(df["CICLO"].dropna().unique().tolist())
    ciclo_sel = col2.selectbox("Ciclo", ["(Todos)"] + ciclos)
    if ciclo_sel != "(Todos)":
        f = f[f["CICLO"] == ciclo_sel]

    st.divider()

    st.metric("Bajas", len(f))

    # ============================================================
    # ✅ Gráfica 1: Histórico total (línea)
    # ============================================================
    st.markdown("### Histórico total de bajas")

    use_mes = ("FECHA_BAJA_DT" in f.columns) and f["FECHA_BAJA_DT"].notna().any()

    if use_mes:
        ts_total = (
            f.dropna(subset=["FECHA_BAJA_DT"])
            .groupby("MES")
            .size()
            .reset_index(name="bajas")
            .sort_values("MES")
        )
        chart_total = (
            alt.Chart(ts_total)
            .mark_line(point=True)
            .encode(
                x=alt.X("MES:N", title="Mes"),
                y=alt.Y("bajas:Q", title="Bajas"),
                tooltip=[alt.Tooltip("MES:N", title="Mes"), alt.Tooltip("bajas:Q", title="Bajas")],
            )
            .properties(height=320)
        )
        st.altair_chart(chart_total, use_container_width=True)

    elif "CICLO" in f.columns and f["CICLO"].notna().any():
        ts_total = (
            f.dropna(subset=["CICLO"])
            .groupby("CICLO")
            .size()
            .reset_index(name="bajas")
        )
        ts_total["CICLO_NUM"] = pd.to_numeric(ts_total["CICLO"], errors="coerce")
        ts_total = ts_total.sort_values(["CICLO_NUM", "CICLO"]).drop(columns=["CICLO_NUM"])

        chart_total = (
            alt.Chart(ts_total)
            .mark_line(point=True)
            .encode(
                x=alt.X("CICLO:O", title="Ciclo"),
                y=alt.Y("bajas:Q", title="Bajas"),
                tooltip=[alt.Tooltip("CICLO:O", title="Ciclo"), alt.Tooltip("bajas:Q", title="Bajas")],
            )
            .properties(height=320)
        )
        st.altair_chart(chart_total, use_container_width=True)
    else:
        st.info("No hay FECHA_BAJA o CICLO usable para construir el histórico total.")

    st.divider()

    # ============================================================
    # ✅ Gráfica 2: Histórico por motivo (stack)
    # ============================================================
    st.markdown("### Histórico de bajas por motivo (apilado)")

    if "MOTIVO_CATEGORIA_STD" not in f.columns:
        st.info("No se detectó la columna MOTIVO_CATEGORIA_STD para construir el histórico por motivo.")
    else:
        xcol = "MES" if use_mes else ("CICLO" if ("CICLO" in f.columns and f["CICLO"].notna().any()) else None)

        if not xcol:
            st.info("No hay eje temporal usable (MES o CICLO) para el histórico por motivo.")
        else:
            fx = f.copy()
            fx["MOTIVO_CATEGORIA_STD"] = fx["MOTIVO_CATEGORIA_STD"].fillna("SIN ESPECIFICAR").astype(str)

            ts_stack = (
                fx.groupby([xcol, "MOTIVO_CATEGORIA_STD"])
                .size()
                .reset_index(name="bajas")
            )

            # orden temporal
            if xcol == "CICLO":
                ts_stack["CICLO_NUM"] = pd.to_numeric(ts_stack["CICLO"], errors="coerce")
                ts_stack = ts_stack.sort_values(["CICLO_NUM", "CICLO", "MOTIVO_CATEGORIA_STD"]).drop(columns=["CICLO_NUM"])
                x_enc = alt.X("CICLO:O", title="Ciclo")
            else:
                ts_stack = ts_stack.sort_values([xcol, "MOTIVO_CATEGORIA_STD"])
                x_enc = alt.X("MES:N", title="Mes")

            chart_stack = (
                alt.Chart(ts_stack)
                .mark_bar()
                .encode(
                    x=x_enc,
                    y=alt.Y("bajas:Q", title="Bajas"),
                    color=alt.Color("MOTIVO_CATEGORIA_STD:N", title="Motivo"),
                    tooltip=[
                        alt.Tooltip(f"{xcol}:N", title=("Mes" if xcol == "MES" else "Ciclo")),
                        alt.Tooltip("MOTIVO_CATEGORIA_STD:N", title="Motivo"),
                        alt.Tooltip("bajas:Q", title="Bajas"),
                    ],
                )
                .properties(height=360)
            )
            st.altair_chart(chart_stack, use_container_width=True)

    st.divider()

    # ============================================================
    # Tabla de conteo de motivos
    # ============================================================
    st.markdown("### Motivos (conteo)")

    vc = (
        f["MOTIVO_CATEGORIA_STD"]
        .fillna("SIN ESPECIFICAR")
        .astype(str)
        .value_counts()
        .reset_index()
    )
    vc.columns = ["Motivo", "Bajas"]
    st.dataframe(vc, use_container_width=True)
