# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread
import re

from catalogos import mapear_carrera_id

SHEET_FORM = "AULAS_VIRTUALES_FORM"
SHEET_CATALOGO = "CAT_SERVICIOS_ESTRUCTURA"

# ============================================================
# Columnas numéricas
# ============================================================
NUM_COLS = {
    "alumnos": "alumnos_uso_num",
    "docente": "docente_uso_num",
    "definicion": "definicion_curso_num",
    "secciones_count": "def_secciones_count",
    "bloques": "bloques_agregados_num",
    "frecuencia": "frecuencia_actualizacion_num",
    "utilidad": "utilidad_num",
    "formato_alt": "formato_alternativo_num",
}

TEXT_COLS = {
    "beneficios": "En caso de considerarlo útil, ¿Qué beneficios principales identifica?",
    "limitaciones": "En caso de considerarlo poco útil o nada útil, ¿Qué limitaciones o dificultades ha encontrado?",
    "mejoras": "¿Qué mejoras sugiere para optimizar el uso de las Aulas Virtuales en la planeación docente?",
}

# ============================================================
# Unidades compactadas
# ============================================================
UNIDADES_ALIASES = {
    "EDN": "EDN",
    "ECDG": "ECDG",
    "EDG": "ECDG",
    "EJEC": "EJEC",
    "LICENCIATURASEJECUTIVAS": "EJEC",
    "LICENCIATURAEJECUTIVA": "EJEC",
}

UNIDAD_LABEL = {
    "EDN": "EDN",
    "ECDG": "ECDG",
    "EJEC": "Licenciaturas Ejecutivas",
}

# ============================================================
# Helpers catálogo
# ============================================================
def _get_catalogo_carreras_df():
    df = st.session_state.get("df_cat_carreras")
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _safe_mapear_carrera_id(texto, df_cat):
    try:
        return mapear_carrera_id(texto, df_cat)
    except Exception:
        return None


def _slug(s: str) -> str:
    s = str(s or "").upper()
    s = re.sub(r"\s+", "", s)
    return s


def _detect_unidad_id(texto: str):
    k = _slug(texto)
    return UNIDADES_ALIASES.get(k)


# ============================================================
# Helpers generales
# ============================================================
def _get_av_url():
    return st.secrets["AV_URL"]


def _clean_service_name(x):
    return str(x or "").strip()


@st.cache_data(ttl=300, show_spinner=False)
def _load_from_gsheets_by_url(url):
    sa = st.secrets["gcp_service_account_json"]
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    ws_form = sh.worksheet(SHEET_FORM)
    ws_cat = sh.worksheet(SHEET_CATALOGO)

    def ws_to_df(ws):
        values = ws.get_all_values()
        return pd.DataFrame(values[1:], columns=values[0]).replace("", pd.NA)

    return ws_to_df(ws_form), ws_to_df(ws_cat)


def _as_num(s):
    return pd.to_numeric(s, errors="coerce")


def _avg(s):
    s = _as_num(s).dropna()
    return float(s.mean()) if not s.empty else None


def _pct_eq(s, v):
    s = _as_num(s).dropna()
    return float((s == v).mean() * 100) if not s.empty else None


# ============================================================
# RENDER PRINCIPAL
# ============================================================
def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    # =========================
    # DIAGNÓSTICO
    # =========================
    st.info("Diagnóstico activo")
    st.write("Vista:", vista)
    st.write("Carrera recibida:", carrera)

    df_cat_carreras = _get_catalogo_carreras_df()
    st.write("CAT_CARRERAS filas:", len(df_cat_carreras))

    # =========================
    # CARGA DATOS
    # =========================
    url = _get_av_url()
    df, cat = _load_from_gsheets_by_url(url)

    if df.empty:
        st.warning("AULAS_VIRTUALES_FORM vacío")
        return

    # =========================
    # MAPEO CARRERA / UNIDAD
    # =========================
    df["UNIDAD_ID"] = df["Indica el servicio"].apply(_detect_unidad_id)

    if not df_cat_carreras.empty:
        df["CARRERA_ID"] = df["Indica el servicio"].apply(
            lambda x: _safe_mapear_carrera_id(x, df_cat_carreras)
        )
    else:
        df["CARRERA_ID"] = None

    df["FILTRO_ID"] = df["UNIDAD_ID"].fillna(df["CARRERA_ID"])
    df["FILTRO_LABEL"] = df.apply(
        lambda r: UNIDAD_LABEL.get(r["UNIDAD_ID"])
        if pd.notna(r["UNIDAD_ID"])
        else r["Indica el servicio"],
        axis=1,
    )

    # =========================
    # FILTROS
    # =========================
 if vista == "Dirección General":
    # Construye catálogo único por ID (evita que labels duplicados “no filtren”)
    cat_filtro = (
        df[["FILTRO_ID", "FILTRO_LABEL"]]
        .dropna(subset=["FILTRO_LABEL"])
        .copy()
    )
    cat_filtro["FILTRO_ID"] = cat_filtro["FILTRO_ID"].astype(str)
    cat_filtro["FILTRO_LABEL"] = cat_filtro["FILTRO_LABEL"].astype(str).str.strip()

    # Si hay labels repetidos, se quedan varias filas con distintos IDs.
    # Para que el usuario vea algo estable, mostramos label + id en el selector.
    cat_filtro = cat_filtro.drop_duplicates()

    cat_filtro["OPCION"] = cat_filtro["FILTRO_LABEL"] + "  —  [" + cat_filtro["FILTRO_ID"] + "]"

    opciones = ["(Todos)"] + sorted(cat_filtro["OPCION"].unique().tolist())
    sel = st.selectbox("Servicio / Unidad", opciones)

    if sel == "(Todos)":
        f = df.copy()
    else:
        # Extrae el ID entre corchetes: [XXXX]
        m = re.search(r"\[([^\]]+)\]\s*$", sel)
        sel_id = m.group(1).strip() if m else ""
        f = df[df["FILTRO_ID"].astype(str).str.strip() == sel_id].copy()

    # Diagnóstico mínimo (para confirmar que SÍ está filtrando)
    st.caption(f"Filtro aplicado (DG): {sel} | Registros filtrados: {len(f)}")
else:
    ...

        else:
            f = df[df["FILTRO_LABEL"] == sel].copy()

    else:
        carrera = str(carrera).strip()
        unidad = _detect_unidad_id(carrera)

        if unidad:
            f = df[df["UNIDAD_ID"] == unidad].copy()
        else:
            if not df_cat_carreras.empty:
                cid = _safe_mapear_carrera_id(carrera, df_cat_carreras)
                f = df[df["CARRERA_ID"] == cid].copy()
            else:
                f = df[df["Indica el servicio"] == carrera].copy()

    if f.empty:
        st.warning("No hay datos para este filtro")
        return

    st.success(f"Registros filtrados: {len(f)}")

    # =========================
    # MÉTRICAS BASE
    # =========================
    alumnos_avg = _avg(f[NUM_COLS["alumnos"]])
    docente_avg = _avg(f[NUM_COLS["docente"]])

    c1, c2 = st.columns(2)
    c1.metric("Uso alumnos", f"{alumnos_avg:.2f}" if alumnos_avg else "—")
    c2.metric("Uso docente", f"{docente_avg:.2f}" if docente_avg else "—")
