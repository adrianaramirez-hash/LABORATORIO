import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Etiquetas de secciones (fallback si Mapa_Preguntas no trae section_name)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinación",
    "SER": "Servicios (Administrativos/Generales)",
    "ADM": "Acceso a soporte administrativo",
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Plataforma SEAC",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",
    "MAT": "Materiales en la plataforma",
    "UDL": "Comunicación con la Universidad",
    "COM": "Comunicación con compañeros",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

SHEET_PROCESADO = "PROCESADO"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"

# ============================================================
# Helpers (ORIGINALES – SIN CAMBIOS)
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _wrap_text(s: str, width: int = 18, max_lines: int = 3) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    if not s:
        return ""
    lines = textwrap.wrap(s, width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    kept[-1] = (kept[-1][:-1] + "…") if len(kept[-1]) >= 1 else "…"
    return "\n".join(kept)


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _ensure_prepa_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Servicio" not in out.columns:
        out["Servicio"] = "Preparatoria"
    if "Carrera_Catalogo" not in out.columns:
        out["Carrera_Catalogo"] = "Preparatoria"
    return out


def _best_carrera_col(df: pd.DataFrame) -> str | None:
    candidates = [
        "Carrera_Catalogo",
        "Servicio",
        "Selecciona el programa académico que estudias",
        "Servicio de procedencia",
        "Programa",
        "Carrera",
    ]
    for c in candidates:
        if c in df.columns:
            vals = df[c].dropna().astype(str).str.strip()
            if vals.nunique() >= 2:
                return c
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _auto_classify_numcols(df: pd.DataFrame, num_cols: list[str]) -> tuple[list[str], list[str]]:
    if not num_cols:
        return [], []
    dnum = df[num_cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max(axis=0, skipna=True)
    likert_cols = [c for c in num_cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1.0]
    yesno_cols = [c for c in num_cols if c not in likert_cols]
    return likert_cols, yesno_cols


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def ws_to_df(ws_title: str) -> pd.DataFrame:
        ws = sh.worksheet(ws_title)
        values = ws.get_all_values()
        headers = values[0]
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df = ws_to_df(SHEET_PROCESADO)
    mapa = ws_to_df(SHEET_MAPA)
    catalogo = ws_to_df(SHEET_CATALOGO) if SHEET_CATALOGO in [w.title for w in sh.worksheets()] else pd.DataFrame()
    return df, mapa, catalogo

# ============================================================
# RENDER PRINCIPAL
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    modalidad = st.selectbox(
        "Modalidad",
        ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
        index=0,
    )

    url = st.secrets.get(
        "EC_VIRTUAL_URL" if modalidad == "Virtual / Mixto"
        else "EC_ESCOLAR_URL" if modalidad == "Escolarizado / Ejecutivas"
        else "EC_PREPA_URL"
    )

    df, mapa, _ = _load_from_gsheets_by_url(url)

    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)

    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)
    mapa["section_name"] = mapa.get("section_name", mapa["section_code"])
    mapa["section_name"] = mapa["section_name"].replace(SECTION_LABELS)

    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

    f = df.copy()

    st.caption(f"Hoja usada: **PROCESADO** | Registros: **{len(f)}**")

    # ---------------------------
    # Tabs
    # ---------------------------
    tabs = ["Resumen", "Por sección"]
    if vista == "Dirección General":
        tabs.append("Comparativo entre carreras")
    tabs.append("Comentarios")

    tab_objs = st.tabs(tabs)
    tab1, tab2 = tab_objs[0], tab_objs[1]
    tab_comp = tab_objs[2] if vista == "Dirección General" else None
    tab3 = tab_objs[-1]

    # ---------------------------
    # Resumen
    # ---------------------------
    with tab1:
        st.metric("Respuestas", len(f))

    # ---------------------------
    # Por sección
    # ---------------------------
    with tab2:
        for sec, g in mapa.groupby("section_name"):
            cols = [c for c in g["header_num"] if c in likert_cols]
            if not cols:
                continue
            mean_val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            st.write(f"**{sec}**: {mean_val:.2f}")

    # ---------------------------
    # Comparativo entre carreras
    # ---------------------------
    if tab_comp:
        with tab_comp:
            carrera_col = _best_carrera_col(f)
            for sec, g in mapa.groupby("section_name"):
                cols = [c for c in g["header_num"] if c in likert_cols]
                rows = []
                for car, dfc in f.groupby(carrera_col):
                    val = pd.to_numeric(dfc[cols].stack(), errors="coerce").mean()
                    if pd.notna(val):
                        rows.append({"Carrera": car, "Promedio": round(val, 2)})
                if rows:
                    st.subheader(sec)
                    st.dataframe(pd.DataFrame(rows).sort_values("Promedio", ascending=False))

    # ---------------------------
    # Comentarios
    # ---------------------------
    with tab3:
        open_cols = [c for c in f.columns if not c.endswith("_num")]
        if open_cols:
            sel = st.selectbox("Campo", open_cols)
            st.dataframe(f[[sel]].dropna())
