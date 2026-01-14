# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Etiquetas de secciones
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

# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


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
    kept[-1] = kept[-1][:-1] + "…" if len(kept[-1]) > 1 else "…"
    return "\n".join(kept)


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _auto_classify_numcols(df: pd.DataFrame, num_cols: list[str]):
    dnum = df[num_cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max(axis=0, skipna=True)
    likert_cols = [c for c in num_cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1]
    yesno_cols = [c for c in num_cols if c not in likert_cols]
    return likert_cols, yesno_cols


# ============================================================
# Google Sheets
# ============================================================
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

    return ws_to_df(SHEET_PROCESADO), ws_to_df(SHEET_MAPA)


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    vista = vista or "Dirección General"

    modalidad = st.selectbox(
        "Modalidad",
        ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
        index=0,
    )

    URL_KEYS = {
        "Virtual / Mixto": "EC_VIRTUAL_URL",
        "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
        "Preparatoria": "EC_PREPA_URL",
    }
    url = st.secrets[URL_KEYS[modalidad]]

    with st.spinner("Cargando datos…"):
        df, mapa = _load_from_gsheets_by_url(url)

    # ======================
    # Preparación
    # ======================
    num_cols = [c for c in df.columns if c.endswith("_num")]
    likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

    mapa = mapa.copy()
    mapa["header_num"] = mapa["header_num"].astype(str).str.strip()
    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna("Otros")
    mapa_ok = mapa[mapa["header_num"].isin(df.columns)]

    # ======================
    # Tabs
    # ======================
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Resumen", "Por sección", "Comentarios", "Comparativo entre carreras"]
    )

    # ======================
    # TAB 1 – RESUMEN (SIN CAMBIOS)
    # ======================
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", len(df))

        if likert_cols:
            overall = pd.to_numeric(df[likert_cols].stack(), errors="coerce").mean()
            c2.metric("Promedio global (Likert)", f"{overall:.2f}")

        if yesno_cols:
            pct_yes = pd.to_numeric(df[yesno_cols].stack(), errors="coerce").mean() * 100
            c3.metric("% Sí (Sí/No)", f"{pct_yes:.1f}%")

    # ======================
    # TAB 2 – POR SECCIÓN (SIN CAMBIOS)
    # ======================
    with tab2:
        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"] if c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(df[cols].stack(), errors="coerce").mean()
            if pd.notna(val):
                rows.append({"Sección": sec_name, "Promedio": val})

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        st.dataframe(sec_df, use_container_width=True)

    # ======================
    # TAB 3 – COMENTARIOS (SIN CAMBIOS)
    # ======================
    with tab3:
        open_cols = [
            c for c in df.columns
            if not c.endswith("_num")
            and any(k in c.lower() for k in ["coment", "suger", "por qué", "porque"])
        ]

        if not open_cols:
            st.info("No se detectaron comentarios.")
        else:
            sel = st.selectbox("Campo de comentarios", open_cols)
            st.dataframe(df[[sel]].dropna(), use_container_width=True)

    # ============================================================
    # TAB 4 – COMPARATIVO ENTRE CARRERAS (TABLA ÚNICAMENTE)
    # ============================================================
    with tab4:
        if vista != "Dirección General":
            st.info("Este comparativo solo está disponible para Dirección General.")
            return

        st.markdown("### Comparativo entre carreras")

        secciones = (
            mapa_ok[mapa_ok["header_num"].isin(likert_cols)]
            .groupby("section_name")
            .size()
            .index.tolist()
        )

        if not secciones:
            st.warning("No hay secciones Likert disponibles.")
            return

        sec_sel = st.selectbox("Selecciona la sección a comparar", secciones)

        sec_cols = mapa_ok[
            (mapa_ok["section_name"] == sec_sel)
            & (mapa_ok["header_num"].isin(likert_cols))
        ]["header_num"].tolist()

        rows = []
        carrera_col = next(
            (c for c in ["Carrera_Catalogo", "Servicio", "Selecciona el programa académico que estudias"] if c in df.columns),
            None,
        )

        if not carrera_col:
            st.warning("No se encontró una columna válida de carrera/servicio.")
            return

        for car in df[carrera_col].dropna().astype(str).unique():
            f = df[df[carrera_col].astype(str) == car]
            val = pd.to_numeric(f[sec_cols].stack(), errors="coerce").mean()
            if pd.notna(val):
                rows.append({
                    "Carrera/Servicio": car,
                    "Promedio sección": round(val, 2),
                    "Preguntas": len(sec_cols),
                })

        if not rows:
            st.info("No hay datos suficientes para esta sección.")
            return

        comp_df = pd.DataFrame(rows).sort_values("Promedio sección", ascending=False)
        st.dataframe(comp_df, use_container_width=True)
