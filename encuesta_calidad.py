import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import unicodedata

# ============================================================
# Config
# ============================================================
MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

MODALIDADES = ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"]

URL_KEYS = {
    "Virtual / Mixto": "EC_VIRTUAL_URL",
    "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
    "Preparatoria": "EC_PREPA_URL",
}

REQ_SHEETS = {
    "PROCESADO": "PROCESADO",
    "Mapa_Preguntas": "Mapa_Preguntas",
    # Catalogo_Servicio puede existir o no; NO lo hacemos obligatorio
    "Catalogo_Servicio": "Catalogo_Servicio",
}

LIKERT_SCALE_CODES = {"LIKERT_1_5", "ACUERDO_1_5", "NOSE_LIKERT_1_5"}
YESNO_SCALE_CODE = "YESNO_01"
TEXT_SCALE_CODE = "TEXTO"


# ============================================================
# Helpers
# ============================================================
def _strip_accents(s: str) -> str:
    s = str(s)
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def _norm(s: object) -> str:
    if pd.isna(s):
        return ""
    return _strip_accents(str(s)).strip().lower()

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

def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

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
    if df_in is None or df_in.empty:
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
        df["_cat_wrapped"] = df[category_col].apply(
            lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3)
        )
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

    df["_cat_wrapped"] = df[category_col].apply(
        lambda x: _wrap_text(x, width=wrap_width_horizontal, max_lines=3)
    )
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

def _get_url_for_modalidad(modalidad: str) -> str:
    key = URL_KEYS.get(modalidad)
    if not key:
        raise KeyError(f"Modalidad no reconocida: {modalidad}")
    url = st.secrets.get(key, "").strip()
    if not url:
        raise KeyError(f"Falta configurar {key} en Secrets.")
    return url

def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    """
    Regla práctica:
    - Si carrera == Preparatoria -> Prepa
    - Si empieza con 'Licenciatura Ejecutiva:' o 'Lic. Ejecutiva:' -> Escolarizado/Ejecutivas
    - Default -> Escolarizado/Ejecutivas
    """
    if vista == "Dirección General":
        return ""
    c = (carrera or "").strip()
    c_norm = _norm(c)
    if c_norm == "preparatoria":
        return "Preparatoria"
    if c_norm.startswith("licenciatura ejecutiva:") or c_norm.startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"

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

def _choose_carrera_col(df: pd.DataFrame) -> str | None:
    """
    Columna para 'ver por carrera' en Dirección General.
    Preferimos Carrera_Catalogo; si no existe, Servicio; si no, ninguna.
    """
    for c in ["Carrera_Catalogo", "Carrera", "Programa", "Servicio"]:
        if c in df.columns:
            return c
    return None


# ============================================================
# Carga Google Sheets
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm_title(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm_title(t): t for t in titles}

    # Resolver obligatorias
    def resolve(name: str) -> str | None:
        tnorm = norm_title(name)
        return titles_norm.get(tnorm)

    ws_pro = resolve(REQ_SHEETS["PROCESADO"])
    ws_map = resolve(REQ_SHEETS["Mapa_Preguntas"])
    ws_cat = resolve(REQ_SHEETS["Catalogo_Servicio"])  # opcional

    missing = []
    if not ws_pro:
        missing.append("PROCESADO")
    if not ws_map:
        missing.append("Mapa_Preguntas")
    if missing:
        raise ValueError(
            "No encontré estas pestañas: "
            + ", ".join(missing)
            + " | Pestañas disponibles: "
            + ", ".join(titles)
        )

    def ws_to_df(ws_title: str) -> pd.DataFrame:
        ws = sh.worksheet(ws_title)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = values[0]
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df = ws_to_df(ws_pro)
    mapa = ws_to_df(ws_map)
    catalogo = ws_to_df(ws_cat) if ws_cat else pd.DataFrame()

    return df, mapa, catalogo


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # ---------------------------
    # Selección de modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", MODALIDADES, index=0)
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    url = _get_url_for_modalidad(modalidad)

    # ---------------------------
    # Carga
    # ---------------------------
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            df, mapa, _catalogo = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar las hojas requeridas (PROCESADO / Mapa_Preguntas).")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja PROCESADO está vacía.")
        return

    # Prepa: normalizar columnas para filtros
    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Validación mínima del mapa
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()

    # Asegurar section_name “completo”
    if "section_name" not in mapa.columns:
        # si no existe, lo llenamos por prefijo, pero la recomendación es que sí exista
        mapa["section_name"] = ""
    mapa["section_name"] = mapa["section_name"].fillna("").astype(str).str.strip()

    # Si quedó vacío, como fallback usamos el prefijo del header_num (SEAC, ACD, etc.)
    mapa["section_code"] = mapa["header_num"].astype(str).apply(lambda x: x.split("_", 1)[0] if "_" in str(x) else "OTR")
    mapa.loc[mapa["section_name"] == "", "section_name"] = mapa["section_code"]

    # Quedarnos con preguntas que sí existen en PROCESADO
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Columnas numéricas existentes
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    if not num_cols:
        st.warning("No encontré columnas *_num en PROCESADO. Verifica que tu script haya generado los numéricos.")
        st.dataframe(df.head(30), use_container_width=True)
        return

    # Tipos por mapa (no por hardcode)
    mapa_ok["scale_code"] = mapa_ok["scale_code"].astype(str).str.strip()
    likert_cols = mapa_ok.loc[mapa_ok["scale_code"].isin(LIKERT_SCALE_CODES), "header_num"].tolist()
    yesno_cols = mapa_ok.loc[mapa_ok["scale_code"] == YESNO_SCALE_CODE, "header_num"].tolist()

    # ---------------------------
    # Filtros (sin duplicados)
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    if vista == "Dirección General":
        carrera_col = _choose_carrera_col(df)
        carrera_opts = ["(Todas)"]
        if carrera_col:
            carrera_opts += sorted(df[carrera_col].dropna().astype(str).unique().tolist())

        c1, c2, c3 = st.columns([1.2, 1.0, 2.6])
        with c1:
            st.markdown(f"**Modalidad:** {modalidad}")
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        with c3:
            if carrera_col:
                carrera_sel = st.selectbox("Carrera/Servicio", carrera_opts, index=0)
                st.caption(f"Filtro usa columna: **{carrera_col}**")
            else:
                carrera_sel = "(Todas)"
                st.info("No encontré columna de Carrera/Servicio en PROCESADO para habilitar ese filtro.")
    else:
        c1, c2 = st.columns([2.4, 1.2])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True)
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        carrera_sel = carrera  # fijo

    st.divider()

    # ---------------------------
    # Aplicar filtros
    # ---------------------------
    f = df.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if vista == "Dirección General":
        carrera_col = _choose_carrera_col(f)
        if carrera_col and carrera_sel != "(Todas)":
            f = f[f[carrera_col].astype(str) == str(carrera_sel)]
    else:
        # Director: intentar filtrar por Carrera_Catalogo o Servicio (robusto)
        if not carrera_sel:
            st.info("Selecciona una carrera arriba para ver resultados.")
            return

        candidates = [c for c in ["Carrera_Catalogo", "Servicio", "Carrera", "Programa"] if c in f.columns]
        if candidates:
            target = _norm(carrera_sel)
            mask = False
            for c in candidates:
                mask = mask | (f[c].astype(str).map(_norm) == target)
            f = f[mask]
        else:
            st.warning("No hay columnas de carrera/servicio para filtrar en esta modalidad.")
            return

    st.caption(f"Hoja usada: **PROCESADO** | Registros filtrados: **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
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
        c1.metric("Respuestas", f"{len(f)}")

        # Likert
        if likert_cols:
            overall = pd.to_numeric(f[likert_cols].stack(), errors="coerce").mean()
            c2.metric("Promedio global (Likert)", f"{overall:.2f}" if pd.notna(overall) else "—")
        else:
            c2.metric("Promedio global (Likert)", "—")

        # Sí/No (%Sí)
        if yesno_cols:
            pct_yes = pd.to_numeric(f[yesno_cols].stack(), errors="coerce").mean() * 100
            c3.metric("% Sí (Sí/No)", f"{pct_yes:.1f}%" if pd.notna(pct_yes) else "—")
        else:
            c3.metric("% Sí (Sí/No)", "—")

        st.divider()
        st.markdown("### Promedio por sección (Likert)")

        rows = []
        for sec_name, g in mapa_ok.groupby("section_name"):
            cols = [c for c in g.loc[g["scale_code"].isin(LIKERT_SCALE_CODES), "header_num"].tolist() if c in f.columns]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols)})

        if not rows:
            st.info("No hay datos suficientes para calcular promedios por sección con los filtros actuales.")
            return

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        st.dataframe(sec_df, use_container_width=True)

        sec_chart = _bar_chart_auto(
            df_in=sec_df,
            category_col="Sección",
            value_col="Promedio",
            value_domain=[1, 5],
            value_title="Promedio",
            tooltip_cols=["Sección", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
            max_vertical=MAX_VERTICAL_SECTIONS,
            wrap_width_vertical=22,   # más ancho para nombres completos
            wrap_width_horizontal=34, # más ancho para nombres completos
            base_height=320,
            hide_category_labels=True,
        )
        if sec_chart is not None:
            st.altair_chart(sec_chart, use_container_width=True)

    # ---------------------------
    # Por sección
    # ---------------------------
    with tab2:
        st.markdown("### Desglose por sección (preguntas)")

        # Lista secciones con promedio
        rows = []
        for sec_name, g in mapa_ok.groupby("section_name"):
            cols = [c for c in g.loc[g["scale_code"].isin(LIKERT_SCALE_CODES), "header_num"].tolist() if c in f.columns]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({"Sección": sec_name, "Promedio": float(val)})

        if not rows:
            st.info("No hay datos suficientes para mostrar secciones con los filtros actuales.")
            return

        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        for _, r in sec_df2.iterrows():
            sec_name = r["Sección"]
            sec_avg = r["Promedio"]

            with st.expander(f"{sec_name} — Promedio: {sec_avg:.2f}", expanded=False):
                mm = mapa_ok[mapa_ok["section_name"] == sec_name].copy()

                qrows = []
                for _, m in mm.iterrows():
                    col = m["header_num"]
                    if col not in f.columns:
                        continue
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue

                    sc = str(m["scale_code"]).strip()
                    if sc == YESNO_SCALE_CODE:
                        qrows.append({"Pregunta": m["header_exacto"], "% Sí": float(mean_val) * 100, "Tipo": "Sí/No"})
                    elif sc in LIKERT_SCALE_CODES:
                        qrows.append({"Pregunta": m["header_exacto"], "Promedio": float(mean_val), "Tipo": "Likert"})
                    else:
                        # TEXTO u otros: no van aquí (van a Comentarios)
                        continue

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

                # Likert
                qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                if not qdf_l.empty:
                    qdf_l = qdf_l.sort_values("Promedio", ascending=False)
                    st.markdown("**Preguntas Likert (1–5)**")

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
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
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
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
                        hide_category_labels=True,
                    )
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # ---------------------------
    # Comentarios (texto)
    # ---------------------------
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        # Preferimos TEXTO desde el Mapa_Preguntas
        mapa_txt = mapa_ok.loc[mapa_ok["scale_code"].astype(str).str.strip() == TEXT_SCALE_CODE, "header_num"].tolist()

        # Fallback heurístico por si no están marcados como TEXTO
        heuristic_txt = [
            c for c in f.columns
            if (not str(c).endswith("_num"))
            and any(k in str(c).lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "describ"])
        ]

        open_cols = []
        for c in mapa_txt + heuristic_txt:
            if c in f.columns and c not in open_cols:
                open_cols.append(c)

        if not open_cols:
            st.info("No detecté columnas de comentarios (TEXTO) con el mapa ni por heurística.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
