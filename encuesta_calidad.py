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
SHEET_CATALOGO = "Catalogo_Servicio"


# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


def _wrap_text(s: str, width: int = 18, max_lines: int = 3) -> str:
    if not s or pd.isna(s):
        return ""
    lines = textwrap.wrap(str(s), width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    lines = lines[:max_lines]
    lines[-1] += "…"
    return "\n".join(lines)


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _bar_chart_auto(
    df_in: pd.DataFrame,
    category_col: str,
    value_col: str,
    value_domain: list,
    value_title: str,
    tooltip_cols: list,
    max_vertical: int,
    wrap_width_vertical: int = 18,
    wrap_width_horizontal: int = 30,
    height_per_row: int = 28,
    base_height: int = 260,
    hide_category_labels: bool = True,
):
    if df_in.empty:
        return None

    df = df_in.copy()
    n = len(df)

    if n <= max_vertical:
        df["_cat"] = df[category_col].apply(
            lambda x: _wrap_text(x, wrap_width_vertical)
        )
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("_cat:N", sort="-y", axis=alt.Axis(labels=not hide_category_labels)),
                y=alt.Y(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), title=value_title),
                tooltip=tooltip_cols,
            )
            .properties(height=max(300, base_height))
        )

    df["_cat"] = df[category_col].apply(
        lambda x: _wrap_text(x, wrap_width_horizontal)
    )
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y("_cat:N", sort="-x", axis=alt.Axis(labels=not hide_category_labels)),
            x=alt.X(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), title=value_title),
            tooltip=tooltip_cols,
        )
        .properties(height=max(base_height, n * height_per_row))
    )


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

    df = ws_to_df(SHEET_PROCESADO)
    mapa = ws_to_df(SHEET_MAPA)
    return df, mapa


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
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
    # Tabs
    # ============================================================
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Resumen", "Por sección", "Comentarios", "Comparativo entre carreras"]
    )

    # ============================================================
    # TAB 4 – COMPARATIVO
    # ============================================================
    with tab4:
        if vista != "Dirección General":
            st.info("Este comparativo solo está disponible para Dirección General.")
            return

        st.markdown("### Comparativo entre carreras")

        sections = (
            mapa_ok[mapa_ok["header_num"].isin(likert_cols)]
            .groupby("section_name")
            .size()
            .sort_index()
            .index.tolist()
        )

        if not sections:
            st.warning("No hay secciones Likert disponibles.")
            return

        sec_sel = st.selectbox("Selecciona la sección a comparar", sections)

        sec_map = mapa_ok[
            (mapa_ok["section_name"] == sec_sel)
            & (mapa_ok["header_num"].isin(likert_cols))
        ]

        carreras = (
            df[carrera_col]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )

        rows = []
        for car in carreras:
            f = df[df[carrera_col].astype(str).str.strip() == car]
            cols = sec_map["header_num"].tolist()
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append(
                {
                    "Carrera/Servicio": car,
                    "Promedio": float(val),
                    "Preguntas": len(cols),
                }
            )

        if not rows:
            st.info("No hay datos suficientes para esta sección.")
            return

        comp_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        st.dataframe(comp_df, use_container_width=True)

        chart = _bar_chart_auto(
            df_in=comp_df,
            category_col="Carrera/Servicio",
            value_col="Promedio",
            value_domain=[1, 5],
            value_title="Promedio",
            tooltip_cols=[
                "Carrera/Servicio",
                alt.Tooltip("Promedio:Q", format=".2f"),
                "Preguntas",
            ],
            max_vertical=MAX_VERTICAL_SECTIONS,
            wrap_width_vertical=22,
            wrap_width_horizontal=36,
            base_height=340,
        )

        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
