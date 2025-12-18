import pandas as pd
import streamlit as st
import altair as alt
import gspread
import json
import textwrap
from google.oauth2.service_account import Credentials


# =========================
# CONFIG
# =========================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

MODALIDADES = [
    "Virtual / Mixto",
    "Escolarizado / Ejecutivas",
    "Preparatoria",
]

URL_KEYS = {
    "Virtual / Mixto": "EC_VIRTUAL_URL",
    "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
    "Preparatoria": "EC_PREPA_URL",
}

SHEET_PROCESADO = "PROCESADO"
SHEET_RESPUESTAS_FALLBACK = "Respuestas"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"

# Etiquetas de secciones (si tus códigos coinciden con los prefijos _num)
SECTION_LABELS = {
    "DIR": "Director/Coordinador",
    "SER": "Servicios",
    "ACD": "Servicios académicos",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación",
    "APR": "Aprendizaje",
    "MAT": "Materiales en la plataforma",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Soporte académico / SEAC",
    "ADM": "Acceso a soporte administrativo",
    "COM": "Comunicación con compañeros",
    "PLAT": "Plataforma SEAC",
    "UDL": "Comunicación con la universidad",
}

# Sí/No (si las tienes) en 0/1
YESNO_COLS = {
    "ADM_ContactoExiste_num",
    "REC_Volveria_num",
    "REC_Recomendaria_num",
    "UDL_ViasComunicacion_num",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7


# =========================
# HELPERS
# =========================
def _normalize(s: object) -> str:
    return str(s).strip().lower() if pd.notna(s) else ""


def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTROS"


def _is_yesno_col(col: str) -> bool:
    return col in YESNO_COLS


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
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[value_col])
    if df.empty:
        return None

    n = len(df)

    cat_axis_vertical = alt.Axis(
        title=None,
        labels=not hide_category_labels,
        ticks=not hide_category_labels,
        labelAngle=0,
        labelLimit=0,
    )
    cat_axis_horizontal = alt.Axis(
        title=None,
        labels=not hide_category_labels,
        ticks=not hide_category_labels,
        labelLimit=0,
    )

    if n <= max_vertical:
        df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3))
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X(
                    "_cat_wrapped:N",
                    sort=alt.SortField(field=value_col, order="descending"),
                    axis=cat_axis_vertical,
                ),
                y=alt.Y(
                    f"{value_col}:Q",
                    scale=alt.Scale(domain=value_domain),
                    axis=alt.Axis(title=value_title),
                ),
                tooltip=tooltip_cols,
            )
            .properties(height=max(320, base_height))
        )

    df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_horizontal, max_lines=3))
    dynamic_height = max(base_height, n * height_per_row)

    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(
                "_cat_wrapped:N",
                sort=alt.SortField(field=value_col, order="descending"),
                axis=cat_axis_horizontal,
            ),
            x=alt.X(
                f"{value_col}:Q",
                scale=alt.Scale(domain=value_domain),
                axis=alt.Axis(title=value_title),
            ),
            tooltip=tooltip_cols,
        )
        .properties(height=dynamic_height)
    )


def _get_gspread_client() -> gspread.Client:
    secret_val = st.secrets.get("gcp_service_account_json", None)
    if secret_val is None:
        raise KeyError('Falta el secret "gcp_service_account_json".')

    if isinstance(secret_val, str):
        creds_dict = json.loads(secret_val)
    else:
        creds_dict = dict(secret_val)

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_url_for_modalidad(modalidad: str) -> str:
    key = URL_KEYS.get(modalidad)
    if not key:
        raise ValueError(f"Modalidad no reconocida: {modalidad}")
    url = st.secrets.get(key, "").strip()
    if not url:
        raise KeyError(f"Falta configurar {key} en Secrets.")
    return url


def _make_unique_headers(raw_headers):
    """
    Evita crash por encabezados duplicados (gspread.get_all_records falla).
    """
    seen_base = {}
    used_final = set()
    out = []
    prev_nonempty = ""

    for h in raw_headers:
        base = (h or "").strip()
        if base == "":
            base = "SIN_TITULO"

        seen_base[base] = seen_base.get(base, 0) + 1
        is_dup = seen_base[base] > 1

        if base.lower().startswith("¿por qué") and (is_dup or base in used_final):
            candidate = f"{base} — {prev_nonempty}" if prev_nonempty else base
        elif is_dup or base in used_final:
            candidate = f"{base} ({seen_base[base]})"
        else:
            candidate = base

        while candidate in used_final:
            candidate = f"{candidate}*"

        out.append(candidate)
        used_final.add(candidate)

        if base != "SIN_TITULO":
            prev_nonempty = base

    return out


def _ws_to_df(ws: gspread.Worksheet) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _make_unique_headers(values[0])
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


@st.cache_data(ttl=300, show_spinner=False)
def _load_from_gsheets_by_url(sheet_url: str):
    gc = _get_gspread_client()
    sh = gc.open_by_url(sheet_url)

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {str(t).strip().lower(): t for t in titles}

    def pick(name: str) -> str | None:
        n = name.strip().lower()
        return titles_norm.get(n)

    # PROCESADO con fallback a Respuestas
    t_proc = pick(SHEET_PROCESADO)
    t_resp = pick(SHEET_RESPUESTAS_FALLBACK)
    t_map = pick(SHEET_MAPA)
    t_cat = pick(SHEET_CATALOGO)

    if not t_map:
        raise ValueError(f"No encontré la pestaña {SHEET_MAPA}. Pestañas: {titles}")
    if not t_cat:
        raise ValueError(f"No encontré la pestaña {SHEET_CATALOGO}. Pestañas: {titles}")
    if not t_proc and not t_resp:
        raise ValueError(f"No encontré {SHEET_PROCESADO} ni {SHEET_RESPUESTAS_FALLBACK}. Pestañas: {titles}")

    ws_main = sh.worksheet(t_proc) if t_proc else sh.worksheet(t_resp)
    ws_map = sh.worksheet(t_map)
    ws_cat = sh.worksheet(t_cat)

    df_main = _ws_to_df(ws_main)
    mapa = _ws_to_df(ws_map)
    catalogo = _ws_to_df(ws_cat)

    return df_main, mapa, catalogo, (t_proc if t_proc else t_resp)


def _merge_catalogo(df: pd.DataFrame, catalogo: pd.DataFrame) -> pd.DataFrame:
    """
    Solo aplica si el sheet trae la columna:
    "Selecciona el programa académico que estudias"
    y el catálogo trae (programa, servicio) y opcional carrera.
    """
    if df.empty or catalogo.empty:
        return df

    cat = catalogo.copy()
    cat.columns = [c.strip().lower() for c in cat.columns]

    if "programa" not in cat.columns or "servicio" not in cat.columns:
        return df

    key = "Selecciona el programa académico que estudias"
    if key not in df.columns:
        return df

    cat["programa"] = cat["programa"].astype(str).str.strip()
    cat["servicio"] = cat["servicio"].astype(str).str.strip()

    out = df.copy()
    out[key] = out[key].astype(str).str.strip()

    cols = ["programa", "servicio"]
    if "carrera" in cat.columns:
        cat["carrera"] = cat["carrera"].astype(str).str.strip()
        cols.append("carrera")

    out = out.merge(cat[cols], how="left", left_on=key, right_on="programa")
    out.drop(columns=["programa"], inplace=True, errors="ignore")

    out.rename(columns={"servicio": "Servicio"}, inplace=True)
    if "carrera" in out.columns:
        out.rename(columns={"carrera": "Carrera_Catalogo"}, inplace=True)

    out["Servicio"] = out["Servicio"].fillna("SIN_CLASIFICAR")
    return out


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    """
    Regla actual:
    - Dirección General: selecciona manual.
    - Director de carrera:
        Si empieza con "Lic. Ejecutiva:" => Escolarizado/Ejecutivas
        Si no => Escolarizado/Ejecutivas (por ahora; luego metemos lista de virtuales)
    """
    if vista == "Dirección General":
        return ""
    if carrera and _normalize(carrera).startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"


def _pick_year_source(df: pd.DataFrame) -> tuple[str, str]:
    """
    Regresa ("Anio", "int") si existe Anio; si no, ("Marca temporal", "date").
    """
    if "Anio" in df.columns:
        return ("Anio", "int")
    if "Marca temporal" in df.columns:
        return ("Marca temporal", "date")
    return ("", "")


def _pick_servicio_col(df: pd.DataFrame) -> str:
    for c in ["Servicio", "Carrera_Catalogo", "Servicio de procedencia", "Selecciona"]:
        if c in df.columns:
            return c
    return ""


# =========================
# MAIN MODULE
# =========================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # 1) Modalidad
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", MODALIDADES, index=1)
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    # 2) Cargar sheet seleccionado
    try:
        url = _get_url_for_modalidad(modalidad)
        df, mapa, catalogo, hoja_usada = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar los datos desde Google Sheets para esta modalidad.")
        st.exception(e)
        return

    if df.empty:
        st.warning(f"La hoja {hoja_usada} está vacía.")
        return

    # 3) Normalizar / enriquecer (si aplica)
    df = _merge_catalogo(df, catalogo)

    # Fecha
    if "Marca temporal" in df.columns:
        df["Marca temporal"] = _to_datetime_safe(df["Marca temporal"])

    # 4) Validación Mapa
    required_cols = {"header_exacto", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer al menos: header_exacto, header_num.")
        st.caption(f"Columnas detectadas en Mapa_Preguntas: {list(mapa.columns)}")
        return

    mapa = mapa.copy()
    mapa["section_code"] = mapa["header_num"].astype(str).apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Num cols
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    likert_cols = [c for c in num_cols if not _is_yesno_col(c)]
    yesno_cols = [c for c in num_cols if _is_yesno_col(c)]

    # 5) Barra de filtros
    year_col, year_kind = _pick_year_source(df)
    servicio_col = _pick_servicio_col(df)

    # Opciones servicio
    servicios = ["(Todos)"]
    if servicio_col:
        servicios += sorted(df[servicio_col].dropna().astype(str).unique().tolist())

    # Opciones año
    years = ["(Todos)"]
    if year_col and year_kind == "int":
        yrs = pd.to_numeric(df[year_col], errors="coerce").dropna().unique().astype(int).tolist()
        years += sorted(yrs, reverse=True)
    elif year_col and year_kind == "date" and df[year_col].notna().any():
        yrs = df[year_col].dt.year.dropna().unique().astype(int).tolist()
        years += sorted(yrs, reverse=True)

    carreras = ["(Todas)"]
    if "Carrera_Catalogo" in df.columns:
        carreras += sorted(df["Carrera_Catalogo"].dropna().astype(str).unique().tolist())

    if vista == "Dirección General":
        f1, f2, f3 = st.columns([1.2, 1.0, 2.3])
        with f1:
            servicio_sel = st.selectbox("Servicio/Carrera", servicios, index=0)
        with f2:
            year_sel = st.selectbox("Año", years, index=0)
        with f3:
            carrera_sel = st.selectbox("Carrera (Catálogo)", carreras, index=0)
    else:
        f1, f2 = st.columns([1.2, 1.0])
        with f1:
            servicio_sel = st.selectbox("Servicio/Carrera", servicios, index=0)
        with f2:
            year_sel = st.selectbox("Año", years, index=0)
        carrera_sel = carrera

    st.divider()

    # 6) Aplicar filtros
    f = df.copy()

    if servicio_col and servicio_sel != "(Todos)":
        f = f[f[servicio_col].astype(str) == str(servicio_sel)]

    if year_col and year_sel != "(Todos)":
        y = int(year_sel)
        if year_kind == "int":
            f = f[pd.to_numeric(f[year_col], errors="coerce").fillna(-1).astype(int) == y]
        else:
            f = f[f[year_col].dt.year == y]

    if vista == "Director de carrera":
        if not carrera_sel:
            st.info("Selecciona una carrera en la parte superior para ver resultados.")
            return
        if "Carrera_Catalogo" in f.columns:
            f = f[f["Carrera_Catalogo"].astype(str) == str(carrera_sel)]
    else:
        if carrera_sel != "(Todas)" and "Carrera_Catalogo" in f.columns:
            f = f[f["Carrera_Catalogo"].astype(str) == str(carrera_sel)]

    st.caption(f"Hoja usada: **{hoja_usada}** | Registros filtrados: **{len(f)}**")

    if f.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # Si no hay numéricos, avisar claramente (sobre todo Prepa)
    if not num_cols:
        st.warning("No encontré columnas *_num. Para ver promedios/gráficas se requiere PROCESADO con columnas numéricas.")
        st.dataframe(f.head(40), use_container_width=True)
        return

    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

    # =========================
    # TAB 1: RESUMEN
    # =========================
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", f"{len(f)}")

        if likert_cols:
            overall = pd.to_numeric(f[likert_cols].stack(), errors="coerce").mean()
            c2.metric("Promedio global (Likert)", f"{overall:.2f}" if pd.notna(overall) else "—")
        else:
            c2.metric("Promedio global (Likert)", "—")

        if yesno_cols:
            pct_yes = pd.to_numeric(f[yesno_cols].stack(), errors="coerce").mean() * 100
            c3.metric("% Sí (Sí/No)", f"{pct_yes:.1f}%" if pd.notna(pct_yes) else "—")
        else:
            c3.metric("% Sí (Sí/No)", "—")

        st.divider()
        st.markdown("### Promedio por sección (Likert)")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        if sec_df.empty:
            st.info("No hay datos suficientes para calcular promedios por sección con los filtros actuales.")
            return

        st.dataframe(sec_df.drop(columns=["sec_code"]), use_container_width=True)

        sec_chart = _bar_chart_auto(
            df_in=sec_df,
            category_col="Sección",
            value_col="Promedio",
            value_domain=[1, 5],
            value_title="Promedio",
            tooltip_cols=["Sección", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
            max_vertical=MAX_VERTICAL_SECTIONS,
            wrap_width_vertical=16,
            wrap_width_horizontal=28,
            base_height=300,
            hide_category_labels=True,
        )
        if sec_chart is not None:
            st.altair_chart(sec_chart, use_container_width=True)

    # =========================
    # TAB 2: POR SECCIÓN
    # =========================
    with tab2:
        st.markdown("### Desglose por sección (comparativo de preguntas)")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})
        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        for _, r in sec_df2.iterrows():
            sec_code = r["sec_code"]
            sec_name = r["Sección"]
            sec_avg = r["Promedio"]

            with st.expander(f"{sec_name} — Promedio: {sec_avg:.2f}", expanded=False):
                mm = mapa_ok[mapa_ok["section_code"] == sec_code].copy()

                qrows = []
                for _, m in mm.iterrows():
                    col = m["header_num"]
                    if col not in f.columns:
                        continue
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue

                    qrows.append(
                        {
                            "Pregunta": m["header_exacto"],
                            "Promedio": mean_val if not _is_yesno_col(col) else None,
                            "% Sí": (mean_val * 100) if _is_yesno_col(col) else None,
                            "Tipo": "Sí/No" if _is_yesno_col(col) else "Likert",
                        }
                    )

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

                # Likert
                qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                if not qdf_l.empty:
                    qdf_l = qdf_l.sort_values("Promedio", ascending=False)
                    st.markdown("**Preguntas Likert**")

                    show_l = qdf_l[["Pregunta", "Promedio"]].reset_index(drop=True)
                    st.dataframe(show_l, use_container_width=True)

                    chart_l = _bar_chart_auto(
                        df_in=show_l,
                        category_col="Pregunta",
                        value_col="Promedio",
                        value_domain=[1, 5],
                        value_title="Promedio",
                        tooltip_cols=[
                            alt.Tooltip("Promedio:Q", format=".2f"),
                            alt.Tooltip("Pregunta:N", title="Pregunta"),
                        ],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=18,
                        wrap_width_horizontal=34,
                        base_height=320,
                        hide_category_labels=True,
                    )
                    if chart_l is not None:
                        st.altair_chart(chart_l, use_container_width=True)

                # Sí/No
                qdf_y = qdf[qdf["Tipo"] == "Sí/No"].copy()
                if not qdf_y.empty:
                    qdf_y = qdf_y.sort_values("% Sí", ascending=False)
                    st.markdown("**Preguntas Sí/No**")

                    show_y = qdf_y[["Pregunta", "% Sí"]].reset_index(drop=True)
                    st.dataframe(show_y, use_container_width=True)

                    chart_y = _bar_chart_auto(
                        df_in=show_y,
                        category_col="Pregunta",
                        value_col="% Sí",
                        value_domain=[0, 100],
                        value_title="% Sí",
                        tooltip_cols=[
                            alt.Tooltip("% Sí:Q", format=".1f"),
                            alt.Tooltip("Pregunta:N", title="Pregunta"),
                        ],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=18,
                        wrap_width_horizontal=34,
                        base_height=320,
                        hide_category_labels=True,
                    )
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # =========================
    # TAB 3: COMENTARIOS
    # =========================
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        open_cols = [
            c
            for c in f.columns
            if (not str(c).endswith("_num"))
            and any(k in str(c).lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "descríbelo", "describelo"])
        ]

        if not open_cols:
            st.info("No detecté columnas de comentarios con la heurística actual.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
