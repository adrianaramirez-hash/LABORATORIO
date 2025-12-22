import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Etiquetas de secciones
# ============================================================
SECTION_LABELS = {
    "DIR": "Director / Coordinación",
    "SER": "Servicios administrativos",
    "ADM": "Atención administrativa",
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del aprendizaje",
    "SEAC": "Plataforma SEAC",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",  # Prepa
    "MAT": "Materiales",
    "UDL": "Comunicación institucional",
    "COM": "Comunicación",
    "INS": "Instalaciones y equipo",
    "AMB": "Ambiente escolar",
    "REC": "Satisfacción y recomendación",
    "OTR": "Otros",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

SCALE_YESNO = "YESNO_01"
SCALE_LIKERT = {"LIKERT_1_5", "ACUERDO_1_5", "NOSE_LIKERT_1_5"}

SHEET_PROCESADO = "PROCESADO"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"

# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"

def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

def _wrap_text(s: str, width: int = 22, max_lines: int = 3) -> str:
    if pd.isna(s):
        return ""
    s = str(s).strip()
    lines = textwrap.wrap(s, width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[:max_lines - 1] + [lines[max_lines - 1] + "…"])

def _mean_numeric(series):
    return pd.to_numeric(series, errors="coerce").mean()

def _pick_fecha_col(df):
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "Timestamp"]:
        if c in df.columns:
            return c
    return None

def _ensure_prepa_columns(df):
    if "Servicio" not in df.columns:
        df["Servicio"] = "Preparatoria"
    if "Carrera_Catalogo" not in df.columns:
        df["Carrera_Catalogo"] = "Preparatoria"
    return df

def _get_url_for_modalidad(mod):
    keys = {
        "Virtual / Mixto": "EC_VIRTUAL_URL",
        "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
        "Preparatoria": "EC_PREPA_URL",
    }
    return st.secrets[keys[mod]]

# ============================================================
# Carga Google Sheets
# ============================================================
@st.cache_data(ttl=300)
def _load_from_gsheets(url):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def get_df(name):
        ws = sh.worksheet(name)
        data = ws.get_all_values()
        return pd.DataFrame(data[1:], columns=data[0]).replace("", pd.NA)

    return get_df(SHEET_PROCESADO), get_df(SHEET_MAPA)

# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista="Dirección General", carrera=None):

    st.subheader("Encuesta de calidad")

    # ---------------------------
    # Modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox(
            "Modalidad",
            ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"]
        )
    else:
        modalidad = "Preparatoria" if carrera == "Preparatoria" else "Escolarizado / Ejecutivas"
        st.caption(f"Modalidad asignada: **{modalidad}**")

    # ---------------------------
    # Carga
    # ---------------------------
    df, mapa = _load_from_gsheets(_get_url_for_modalidad(modalidad))
    if df.empty:
        st.warning("PROCESADO vacío")
        return

    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)

    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Mapa
    # ---------------------------
    mapa = mapa.copy()
    mapa["header_num"] = mapa["header_num"].astype(str)
    mapa["scale_code"] = mapa["scale_code"].astype(str)
    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)

    mapa["section_name"] = mapa.get("section_name", mapa["section_code"])
    mapa["section_name"] = mapa["section_name"].map(
        lambda x: SECTION_LABELS.get(x, x)
    )

    mapa = mapa[mapa["header_num"].isin(df.columns)]

    # ---------------------------
    # Columnas numéricas
    # ---------------------------
    num_cols = [c for c in df.columns if c.endswith("_num")]

    # ========= PARCHE CLAVE =========
    yesno_from_map = set(
        mapa.loc[mapa["scale_code"] == SCALE_YESNO, "header_num"]
    )

    yesno_by_data = set()
    for c in num_cols:
        vals = pd.to_numeric(df[c], errors="coerce").dropna().unique()
        if len(vals) and set(vals).issubset({0, 1}):
            yesno_by_data.add(c)

    yesno_cols = sorted(list(yesno_from_map | yesno_by_data))
    likert_cols = [c for c in num_cols if c not in yesno_cols]
    # =================================

    # ---------------------------
    # Filtros
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col:
        years += sorted(df[fecha_col].dt.year.dropna().unique(), reverse=True)

    year_sel = st.selectbox("Año", years)

    if vista == "Dirección General":
        carrera_col = next(
            (c for c in ["Carrera_Catalogo", "Servicio"] if c in df.columns),
            None
        )
        carrera_sel = "(Todas)"
        if carrera_col:
            carrera_sel = st.selectbox(
                "Carrera / Servicio",
                ["(Todas)"] + sorted(df[carrera_col].dropna().unique())
            )
    else:
        carrera_sel = carrera

    f = df.copy()
    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == year_sel]

    if vista == "Dirección General" and carrera_sel != "(Todas)" and carrera_col:
        f = f[f[carrera_col] == carrera_sel]

    if f.empty:
        st.warning("Sin datos con estos filtros")
        return

    # ---------------------------
    # Tabs
    # ---------------------------
    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

    # ---------------------------
    # Resumen
    # ---------------------------
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", len(f))

        if likert_cols:
            c2.metric(
                "Promedio global",
                f"{pd.to_numeric(f[likert_cols].stack()).mean():.2f}"
            )
        else:
            c2.metric("Promedio global", "—")

        if yesno_cols:
            c3.metric(
                "% Sí",
                f"{pd.to_numeric(f[yesno_cols].stack()).mean() * 100:.1f}%"
            )
        else:
            c3.metric("% Sí", "—")

    # ---------------------------
    # Por sección
    # ---------------------------
    with tab2:
        for sec, g in mapa.groupby("section_name"):
            cols = [c for c in g["header_num"] if c in likert_cols]
            if not cols:
                continue
            avg = pd.to_numeric(f[cols].stack()).mean()
            st.markdown(f"**{sec} — {avg:.2f}**")

    # ---------------------------
    # Comentarios
    # ---------------------------
    with tab3:
        open_cols = [
            c for c in f.columns
            if not c.endswith("_num")
            and any(k in c.lower() for k in ["coment", "suger", "¿por qué", "descr"])
        ]
        if not open_cols:
            st.info("No hay comentarios")
            return

        col = st.selectbox("Campo", open_cols)
        st.dataframe(f[[col]].dropna(), use_container_width=True)
