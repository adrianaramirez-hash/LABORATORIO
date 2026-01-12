# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread

SHEET_FORM = "AULAS_VIRTUALES_FORM"
SHEET_CATALOGO = "CAT_SERVICIOS_ESTRUCTURA"

# Columnas numéricas (ya creadas en tu Sheet)
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

# Columnas texto (por si quieres mostrar ejemplos después)
TEXT_COLS = {
    "beneficios": "En caso de considerarlo útil, ¿Qué beneficios principales identifica?",
    "limitaciones": "En caso de considerarlo poco útil o nada útil, ¿Qué limitaciones o dificultades ha encontrado?",
    "mejoras": "¿Qué mejoras sugiere para optimizar el uso de las Aulas Virtuales en la planeación docente?",
    "cual_alt": "¿Cuál?",
}


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

    missing = []
    if not ws_form:
        missing.append(SHEET_FORM)
    if not ws_cat:
        missing.append(SHEET_CATALOGO)

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
        headers = [h.strip() for h in values[0]]
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df_form = ws_to_df(ws_form)
    df_cat = ws_to_df(ws_cat)
    return df_form, df_cat


def _as_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _pct_eq(series: pd.Series, value: float) -> float | None:
    s = _as_num(series).dropna()
    if s.empty:
        return None
    return float((s == value).mean() * 100)


def _avg(series: pd.Series) -> float | None:
    s = _as_num(series).dropna()
    if s.empty:
        return None
    return float(s.mean())


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
    if df is None or df.empty:
        st.info("Sin datos para graficar.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X("Nivel:N", title=None, sort=None),
            y=alt.Y("Conteo:Q", title=None),
            tooltip=["Nivel", "Conteo"],
        )
        .properties(height=280, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    # ---------------------------
    # Carga
    # ---------------------------
    try:
        url = _get_av_url()
        with st.spinner("Cargando Aulas Virtuales (Google Sheets)…"):
            df, cat = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar los datos de Aulas Virtuales.")
        st.exception(e)
        st.stop()

    if df.empty:
        st.warning("La hoja AULAS_VIRTUALES_FORM está vacía.")
        return
    if cat.empty:
        st.warning("La hoja CAT_SERVICIOS_ESTRUCTURA está vacía.")
        return

    # Validaciones mínimas
    if "Indica el servicio" not in df.columns:
        st.error("En AULAS_VIRTUALES_FORM falta la columna exacta: 'Indica el servicio'")
        st.dataframe(df.head(20), use_container_width=True)
        return
    if "servicio" not in cat.columns:
        st.error("En CAT_SERVICIOS_ESTRUCTURA falta la columna exacta: 'servicio'")
        st.dataframe(cat.head(20), use_container_width=True)
        return

    # ---------------------------
    # Normalización + join de catálogo
    # ---------------------------
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

    # ---------------------------
    # Periodo (anual): usar fecha solo como contexto
    # ---------------------------
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Selector interno: servicio / escuela
    # ---------------------------
    with st.container(border=True):
        st.markdown("**Filtro del apartado (Aulas Virtuales)**")

        servicio_base = (carrera or "").strip()

        escuela_base = None
        if servicio_base:
            fila_base = cat[cat["servicio_std"] == servicio_base]
            if not fila_base.empty:
                escuela_base = fila_base.iloc[0].get("escuela")

        servicios_disponibles = (
            cat["servicio_std"].dropna().astype(str).str.strip().unique().tolist()
        )
        servicios_disponibles = sorted(set([s for s in servicios_disponibles if s]))

        if escuela_base and str(escuela_base).strip().lower() not in ["nan", "none", ""]:
            servicios_escuela = (
                cat[cat["escuela"] == escuela_base]["servicio_std"]
                .dropna().astype(str).str.strip().unique().tolist()
            )
            servicios_escuela = sorted(set([s for s in servicios_escuela if s]))
            opciones = [f"Todos los servicios de {escuela_base}"] + servicios_escuela
        else:
            opciones = servicios_disponibles

        default_idx = 0
        if servicio_base and servicio_base in opciones:
            default_idx = opciones.index(servicio_base)

        servicio_sel = st.selectbox(
            "Servicio a analizar (Aulas Virtuales)",
            options=opciones,
            index=default_idx
        )

    # ---------------------------
    # Filtrado
    # ---------------------------
    if servicio_sel.startswith("Todos los servicios de "):
        f = df[df["escuela"] == escuela_base].copy()
    else:
        f = df[df["servicio_std"] == servicio_sel].copy()

    if f.empty:
        st.warning("No hay registros con el filtro seleccionado.")
        return

    # ---------------------------
    # Encabezado ejecutivo (contexto)
    # ---------------------------
    n = len(f)
    if fecha_col and f[fecha_col].notna().any():
        fmin = f[fecha_col].min()
        fmax = f[fecha_col].max()
        years = sorted(f[fecha_col].dt.year.dropna().unique().astype(int).tolist())
        year_txt = str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]}"
        st.caption(f"Periodo del levantamiento: **{fmin:%d %b %Y} – {fmax:%d %b %Y}** | Año: **{year_txt}** | Respuestas: **{n}**")
    else:
        st.caption(f"Respuestas: **{n}** (sin fecha válida en 'Marca temporal')")

    # ---------------------------
    # Validar columnas numéricas
    # ---------------------------
    missing_num = [v for v in NUM_COLS.values() if v not in f.columns]
    if missing_num:
        st.error(
            "Faltan columnas numéricas en AULAS_VIRTUALES_FORM. "
            "Revisa que existan estas columnas:\n- " + "\n- ".join(missing_num)
        )
        return

    # Convertir a num para cálculo
    fx = f.copy()
    for col in NUM_COLS.values():
        fx[col] = _as_num(fx[col])

    # ---------------------------
    # Tabs (sin pestaña 3)
    # ---------------------------
    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

    # ============================================================
    # TAB 1: Resumen ejecutivo
    # ============================================================
    with tab1:
        # KPIs (tarjetas)
        c1, c2, c3, c4, c5, c6 = st.columns(6)

        # Adopción (Siempre/A veces/Nunca -> 2/1/0)
        alumnos_avg = _avg(fx[NUM_COLS["alumnos"]])
        docente_avg = _avg(fx[NUM_COLS["docente"]])
        alumnos_siempre = _pct_eq(fx[NUM_COLS["alumnos"]], 2)
        docente_siempre = _pct_eq(fx[NUM_COLS["docente"]], 2)

        # Planeación
        def_avg = _avg(fx[NUM_COLS["definicion"]])
        def_completa = _pct_eq(fx[NUM_COLS["definicion"]], 2)
        def_no = _pct_eq(fx[NUM_COLS["definicion"]], 0)

        bloques_avg = _avg(fx[NUM_COLS["bloques"]])
        bloques_todas = _pct_eq(fx[NUM_COLS["bloques"]], 2)

        freq_avg = _avg(fx[NUM_COLS["frecuencia"]])   # 0–3
        util_avg = _avg(fx[NUM_COLS["utilidad"]])     # 0–3

        c1.metric("Alumnos (prom 0–2)", f"{alumnos_avg:.2f}" if alumnos_avg is not None else "—")
        c2.metric("Docente (prom 0–2)", f"{docente_avg:.2f}" if docente_avg is not None else "—")
        c3.metric("% Siempre (alumnos)", f"{alumnos_siempre:.1f}%" if alumnos_siempre is not None else "—")
        c4.metric("% Siempre (docente)", f"{docente_siempre:.1f}%" if docente_siempre is not None else "—")
        c5.metric("% Definición completa", f"{def_completa:.1f}%" if def_completa is not None else "—")
        c6.metric("% Definición NO realizada", f"{def_no:.1f}%" if def_no is not None else "—")

        st.divider()

        c7, c8, c9, c10, c11, c12 = st.columns(6)
        c7.metric("Planeación (prom 0–2)", f"{def_avg:.2f}" if def_avg is not None else "—")
        c8.metric("Bloques (prom 0–2)", f"{bloques_avg:.2f}" if bloques_avg is not None else "—")
        c9.metric("% Bloques: todas semanas", f"{bloques_todas:.1f}%" if bloques_todas is not None else "—")
        c10.metric("Frecuencia (prom 0–3)", f"{freq_avg:.2f}" if freq_avg is not None else "—")
        c11.metric("Utilidad (prom 0–3)", f"{util_avg:.2f}" if util_avg is not None else "—")

        # Formato alternativo
        alt_si = _pct_eq(fx[NUM_COLS["formato_alt"]], 1)
        c12.metric("% Quiere formato alternativo", f"{alt_si:.1f}%" if alt_si is not None else "—")

        st.divider()

        # Gráficos compactos (distribuciones)
        g1, g2 = st.columns(2)
        with g1:
            _bar(_dist_counts(fx[NUM_COLS["alumnos"]]), "Uso del Aula Virtual (Alumnos) 0–2")
        with g2:
            _bar(_dist_counts(fx[NUM_COLS["docente"]]), "Uso del Aula Virtual (Docente) 0–2")

        g3, g4 = st.columns(2)
        with g3:
            _bar(_dist_counts(fx[NUM_COLS["definicion"]]), "Definición del curso 0–2")
        with g4:
            _bar(_dist_counts(fx[NUM_COLS["bloques"]]), "Sesiones/Bloques 0–2")

        g5, g6 = st.columns(2)
        with g5:
            _bar(_dist_counts(fx[NUM_COLS["frecuencia"]]), "Frecuencia de actualización 0–3")
        with g6:
            _bar(_dist_counts(fx[NUM_COLS["utilidad"]]), "Utilidad percibida 0–3")

        # Secciones completadas (solo si hay datos)
        if fx[NUM_COLS["secciones_count"]].notna().any():
            st.divider()
            _bar(_dist_counts(fx[NUM_COLS["secciones_count"]]), "Secciones completadas (conteo)")

    # ============================================================
    # TAB 2: Diagnóstico por secciones
    # ============================================================
    with tab2:
        st.markdown("### Sección I. Uso del Aula Virtual")

        brecha = None
        if docente_avg is not None and alumnos_avg is not None:
            brecha = docente_avg - alumnos_avg

        c1, c2, c3 = st.columns(3)
        c1.metric("Prom. alumnos (0–2)", f"{alumnos_avg:.2f}" if alumnos_avg is not None else "—")
        c2.metric("Prom. docente (0–2)", f"{docente_avg:.2f}" if docente_avg is not None else "—")
        c3.metric("Brecha (docente - alumnos)", f"{brecha:.2f}" if brecha is not None else "—")

        st.divider()
        st.markdown("### Sección II. Llenado de la planeación")

        cc1, cc2, cc3, cc4 = st.columns(4)
        cc1.metric("% Definición completa", f"{def_completa:.1f}%" if def_completa is not None else "—")
        cc2.metric("% Definición incompleta", f"{_pct_eq(fx[NUM_COLS['definicion']], 1):.1f}%" if _pct_eq(fx[NUM_COLS['definicion']], 1) is not None else "—")
        cc3.metric("% Definición NO", f"{def_no:.1f}%" if def_no is not None else "—")
        sc_avg = _avg(fx[NUM_COLS["secciones_count"]])
        cc4.metric("Prom. secciones completadas", f"{sc_avg:.2f}" if sc_avg is not None else "—")

        st.divider()
        st.markdown("### Sección II. Sesiones/Bloques y actualización")

        dd1, dd2, dd3, dd4 = st.columns(4)
        dd1.metric("% Bloques todas semanas", f"{bloques_todas:.1f}%" if bloques_todas is not None else "—")
        dd2.metric("% Bloques parcial", f"{_pct_eq(fx[NUM_COLS['bloques']], 1):.1f}%" if _pct_eq(fx[NUM_COLS['bloques']], 1) is not None else "—")
        dd3.metric("% Bloques NO", f"{_pct_eq(fx[NUM_COLS['bloques']], 0):.1f}%" if _pct_eq(fx[NUM_COLS['bloques']], 0) is not None else "—")
        dd4.metric("Frecuencia prom (0–3)", f"{freq_avg:.2f}" if freq_avg is not None else "—")

        st.divider()
        st.markdown("### Sección III. Utilidad y sugerencias")

        ee1, ee2, ee3, ee4 = st.columns(4)
        ee1.metric("Utilidad prom (0–3)", f"{util_avg:.2f}" if util_avg is not None else "—")
        ee2.metric("% Muy útil", f"{_pct_eq(fx[NUM_COLS['utilidad']], 3):.1f}%" if _pct_eq(fx[NUM_COLS['utilidad']], 3) is not None else "—")
        ee3.metric("% Poco/Nada útil", f"{(_pct_eq(fx[NUM_COLS['utilidad']], 1) or 0) + (_pct_eq(fx[NUM_COLS['utilidad']], 0) or 0):.1f}%" if ( _pct_eq(fx[NUM_COLS['utilidad']], 1) is not None or _pct_eq(fx[NUM_COLS['utilidad']], 0) is not None ) else "—")
        ee4.metric("% Quiere formato alternativo", f"{alt_si:.1f}%" if alt_si is not None else "—")

        # Nota: aquí todavía NO hacemos minería de texto (lo dejamos para un paso posterior)
        st.info("Siguiente mejora: clasificar Beneficios/Limitaciones/Mejoras por categorías para graficar hallazgos sin tabla cruda.")

        st.divider()
        st.markdown("### Sección IV. Formato alternativo")

        ff1, ff2 = st.columns(2)
        ff1.metric("% Sí", f"{alt_si:.1f}%" if alt_si is not None else "—")
        ff2.metric("% No", f"{(100 - alt_si):.1f}%" if alt_si is not None else "—")


