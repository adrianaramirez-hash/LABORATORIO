# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread

SHEET_FORM = "AULAS_VIRTUALES_FORM"
SHEET_CATALOGO = "CAT_SERVICIOS_ESTRUCTURA"

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

# ----------------------------
# Helpers generales
# ----------------------------
def _get_av_url() -> str:
    url = st.secrets.get("AV_URL", "").strip()
    if not url:
        raise KeyError("Falta configurar AV_URL en Secrets.")
    return url


def _norm(x: str) -> str:
    return str(x).strip().lower().replace(" ", "").replace("_", "")


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _to_datetime_safe(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {_norm(t): t for t in titles}

    def resolve(sheet_name: str) -> str | None:
        return titles_norm.get(_norm(sheet_name))

    ws_form = resolve(SHEET_FORM)
    ws_cat = resolve(SHEET_CATALOGO)

    if not ws_form or not ws_cat:
        raise ValueError(
            "No encontré pestañas requeridas. "
            f"Buscadas: {SHEET_FORM}, {SHEET_CATALOGO} | "
            f"Disponibles: {', '.join(titles)}"
        )

    def ws_to_df(ws_title: str) -> pd.DataFrame:
        ws = sh.worksheet(ws_title)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = [h.strip() for h in values[0]]
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    return ws_to_df(ws_form), ws_to_df(ws_cat)


def _as_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _avg(series: pd.Series) -> float | None:
    s = _as_num(series).dropna()
    return float(s.mean()) if not s.empty else None


def _pct_eq(series: pd.Series, value: float) -> float | None:
    s = _as_num(series).dropna()
    return float((s == value).mean() * 100) if not s.empty else None


def _dist_counts(series: pd.Series) -> pd.DataFrame:
    s = _as_num(series).dropna()
    if s.empty:
        return pd.DataFrame(columns=["Nivel", "Conteo"])
    vc = s.value_counts().sort_index()
    out = vc.reset_index()
    out.columns = ["Nivel", "Conteo"]
    out["Nivel"] = out["Nivel"].astype(int).astype(str)
    return out


def _bar(df: pd.DataFrame, title: str):
    if df.empty:
        st.info("Sin datos para graficar.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Nivel:N", title=None),
            y=alt.Y("Conteo:Q", title=None),
            tooltip=["Nivel", "Conteo"],
        )
        .properties(height=280, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


# ----------------------------
# Render principal
# ----------------------------
def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    try:
        url = _get_av_url()
        with st.spinner("Cargando Aulas Virtuales…"):
            df, cat = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar los datos de Aulas Virtuales.")
        st.exception(e)
        return

    if df.empty or cat.empty:
        st.warning("No hay datos suficientes para mostrar este apartado.")
        return

    if "Indica el servicio" not in df.columns or "servicio" not in cat.columns:
        st.error("Estructura incorrecta en Google Sheets.")
        return

    # Normalización
    df = df.copy()
    cat = cat.copy()
    df["servicio_std"] = df["Indica el servicio"].astype(str).str.strip()
    cat["servicio_std"] = cat["servicio"].astype(str).str.strip()

    for col in ["escuela", "nivel", "tipo_unidad"]:
        if col not in cat.columns:
            cat[col] = pd.NA

    df = df.merge(
        cat[["servicio_std", "escuela", "nivel", "tipo_unidad"]],
        on="servicio_std",
        how="left",
    )

    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---- Filtro interno
    servicio_base = (carrera or "").strip()
    opciones = ["(Todos)"] + sorted(df["servicio_std"].dropna().unique().tolist())

    default_idx = opciones.index(servicio_base) if servicio_base in opciones else 0

    servicio_sel = st.selectbox(
        "Servicio a analizar (Aulas Virtuales)",
        options=opciones,
        index=default_idx,
    )

    f = df if servicio_sel == "(Todos)" else df[df["servicio_std"] == servicio_sel]

    if f.empty:
        st.warning("No hay registros con el filtro seleccionado.")
        return

    st.caption(f"Respuestas analizadas: **{len(f)}**")

    # Validar columnas numéricas
    faltantes = [v for v in NUM_COLS.values() if v not in f.columns]
    if faltantes:
        st.error("Faltan columnas numéricas:\n- " + "\n- ".join(faltantes))
        return

    fx = f.copy()
    for col in NUM_COLS.values():
        fx[col] = _as_num(fx[col])

    # ---- Tabs
    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

    with tab1:
        c1, c2, c3 = st.columns(3)
        alumnos_avg = _avg(fx[NUM_COLS["alumnos"]])
        docente_avg = _avg(fx[NUM_COLS["docente"]])

        c1.metric("Alumnos (prom 0–2)", f"{alumnos_avg:.2f}" if alumnos_avg is not None else "—")
        c2.metric("Docente (prom 0–2)", f"{docente_avg:.2f}" if docente_avg is not None else "—")

        st.divider()
        _bar(_dist_counts(fx[NUM_COLS["alumnos"]]), "Uso del Aula Virtual (Alumnos)")
        _bar(_dist_counts(fx[NUM_COLS["docente"]]), "Uso del Aula Virtual (Docente)")
