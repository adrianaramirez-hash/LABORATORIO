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
    if s is None or pd.isna(s):
        return ""
    lines = textwrap.wrap(str(s), width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    lines = lines[:max_lines]
    lines[-1] += "…"
    return "\n".join(lines)


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _auto_classify_numcols(df: pd.DataFrame, num_cols: list[str]):
    dnum = df[num_cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max()
    likert = [c for c in num_cols if maxs.get(c, 0) > 1]
    yesno = [c for c in num_cols if c not in likert]
    return likert, yesno


def _best_carrera_col(df: pd.DataFrame):
    for c in [
        "Carrera_Catalogo",
        "Servicio",
        "Selecciona el programa académico que estudias",
        "Carrera",
        "Programa",
    ]:
        if c in df.columns:
            return c
    return None


def _bar_chart_auto(
    df_in,
    category_col,
    value_col,
    value_domain,
    value_title,
    tooltip_cols,
    max_vertical,
    wrap_width_vertical=18,
    wrap_width_horizontal=30,
    base_height=320,
):
    if df_in.empty:
        return None

    df = df_in.copy()
    n = len(df)

    if n <= max_vertical:
        df["_cat"] = df[category_col].apply(lambda x: _wrap_text(x, wrap_width_vertical))
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("_cat:N", sort="-y", title=None),
                y=alt.Y(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), title=value_title),
                tooltip=tooltip_cols,
            )
            .properties(height=base_height)
        )

    df["_cat"] = df[category_col].apply(lambda x: _wrap_text(x, wrap_width_horizontal))
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y("_cat:N", sort="-x", title=None),
            x=alt.X(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), title=value_title),
            tooltip=tooltip_cols,
        )
        .properties(height=max(base_height, n * 28))
    )


# ============================================================
# Google Sheets
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def ws_to_df(name):
        ws = sh.worksheet(name)
        data = ws.get_all_values()
        return pd.DataFrame(data[1:], columns=data[0]).replace("", pd.NA)

    return ws_to_df(SHEET_PROCESADO), ws_to_df(SHEET_MAPA)


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista=None, carrera=None):
    st.subheader("Encuesta de calidad")
    vista = vista or "Dirección General"

    modalidad = st.selectbox(
        "Modalidad",
        ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
    )

    URL_KEYS = {
        "Virtual / Mixto": "EC_VIRTUAL_URL",
        "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
        "Preparatoria": "EC_PREPA_URL",
    }

    url = st.secrets[URL_KEYS[modalidad]]

    with st.spinner("Cargando datos…"):
        df, mapa = _load_from_gsheets_by_url(url)

    num_cols = [c for c in df.columns if c.endswith("_num")]
    likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna("Otros")
    mapa_ok = mapa[mapa["header_num"].isin(df.columns)]

    carrera_col = _best_carrera_col(df)

    # ============================================================
    # TABS
    # ============================================================
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Resumen", "Por sección", "Comentarios", "Comparativo entre carreras"]
    )

    # ============================================================
    # TAB 1 – RESUMEN (ORIGINAL)
    # ============================================================
    with tab1:
        st.markdown("### Resumen general")
        if likert_cols:
            prom = pd.to_numeric(df[likert_cols].stack(), errors="coerce").mean()
            st.metric("Promedio global (Likert)", f"{prom:.2f}")
        if yesno_cols:
            pct = pd.to_numeric(df[yesno_cols].stack(), errors="coerce").mean() * 100
            st.metric("% Sí (Sí/No)", f"{pct:.1f}%")

    # ============================================================
    # TAB 2 – POR SECCIÓN (ORIGINAL)
    # ============================================================
    with tab2:
        st.markdown("### Promedio por sección")
        rows = []
        for (sc, sn), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"] if c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(df[cols].stack(), errors="coerce").mean()
            if pd.notna(val):
                rows.append({"Sección": sn, "Promedio": val})

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        st.dataframe(sec_df, use_container_width=True)

    # ============================================================
    # TAB 3 – COMENTARIOS (ORIGINAL)
    # ============================================================
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
    # TAB 4 – COMPARATIVO ENTRE CARRERAS (NUEVO)
    # ============================================================
    with tab4:
        if vista != "Dirección General":
            st.info("Vista disponible solo para Dirección General.")
            return

        st.markdown("### Comparativo entre carreras")

        secciones = (
            mapa_ok[mapa_ok["header_num"].isin(likert_cols)]
            .groupby("section_name")
            .size()
            .index.tolist()
        )

        sec_sel = st.selectbox("Selecciona la sección", secciones)

        sec_cols = mapa_ok[
            (mapa_ok["section_name"] == sec_sel)
            & (mapa_ok["header_num"].isin(likert_cols))
        ]["header_num"].tolist()

        rows = []
        for car in df[carrera_col].dropna().unique():
            f = df[df[carrera_col] == car]
            val = pd.to_numeric(f[sec_cols].stack(), errors="coerce").mean()
            if pd.notna(val):
                rows.append({
                    "Carrera": car,
                    "Promedio": val,
                    "Preguntas": len(sec_cols),
                })

        comp_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        st.dataframe(comp_df, use_container_width=True)

        chart = _bar_chart_auto(
            comp_df,
            "Carrera",
            "Promedio",
            [1, 5],
            "Promedio",
            ["Carrera", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
            MAX_VERTICAL_SECTIONS,
        )

        if chart:
            st.altair_chart(chart, use_container_width=True)
