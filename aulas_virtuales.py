# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread
import re

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

TEXT_COLS = {
    "beneficios": "En caso de considerarlo útil, ¿Qué beneficios principales identifica?",
    "limitaciones": "En caso de considerarlo poco útil o nada útil, ¿Qué limitaciones o dificultades ha encontrado?",
    "mejoras": "¿Qué mejoras sugiere para optimizar el uso de las Aulas Virtuales en la planeación docente?",
}

CATS_BENEFICIOS = {
    "Organización y planeación": ["organiza", "orden", "planea", "planeación", "planear", "estructura", "control"],
    "Seguimiento y evidencia": ["seguimiento", "evidencia", "registro", "bitácora", "bitacora", "historial", "control"],
    "Acceso a materiales": ["material", "recursos", "documentos", "archivos", "disponible", "consultar"],
    "Comunicación": ["comunicación", "comunicar", "avisos", "mensajes", "retro", "retroalimentación"],
    "Apoyo al aprendizaje": ["aprendizaje", "aprenden", "refuerzo", "repaso", "autónomo", "autonomo", "mejora"],
    "Ahorro de tiempo": ["tiempo", "agiliza", "rápido", "rapido", "automat", "eficiente"],
}

CATS_LIMITACIONES = {
    "Falta de tiempo/carga de trabajo": ["tiempo", "carga", "satur", "mucho trabajo", "no me da", "no alcanza"],
    "Problemas técnicos/plataforma": ["seac", "plataforma", "lento", "falla", "error", "cae", "no sirve", "problema técnico", "tecnico"],
    "Falta de capacitación": ["capacitación", "capacitacion", "taller", "curso", "no sé", "no se", "desconozco"],
    "Resistencia/hábito": ["no acostumbro", "costumbre", "resistencia", "prefer", "no me gusta", "no uso"],
    "Acceso/conectividad": ["internet", "conexión", "conexion", "equipo", "computadora", "celular", "red"],
    "Duplicidad de trabajo": ["doble", "duplic", "repet", "otra vez", "redund", "mismo"],
}

CATS_MEJORAS = {
    "Capacitación y acompañamiento": ["capacitación", "capacitacion", "taller", "curso", "acompañamiento", "asesoría", "asesoria"],
    "Simplificación/plantilla": ["plantilla", "formato", "simpl", "más fácil", "mas facil", "guiar", "estructura"],
    "Mejoras de plataforma": ["seac", "plataforma", "lento", "mejorar", "error", "optim", "usabilidad", "interfaz"],
    "Automatización": ["automat", "autollen", "auto", "integrar", "sincron", "importar"],
    "Seguimiento/monitoreo": ["seguimiento", "supervisión", "supervision", "revisión", "revision", "control"],
    "Comunicación y recordatorios": ["recordatorio", "avisos", "notificación", "notificacion", "alerta", "calendario"],
}


def _get_av_url() -> str:
    url = st.secrets.get("AV_URL", "").strip()
    if not url:
        raise KeyError("Falta configurar AV_URL en Secrets.")
    return url


def _norm_sheet_title(x: str) -> str:
    return str(x).strip().lower().replace(" ", "").replace("_", "")


def _clean_service_name(x: str) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_text(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_key(s: str) -> str:
    s = _norm_text(s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_multi(x: str) -> list[str]:
    """
    Permite que 'carrera' o AV_VALOR venga con múltiples valores:
      - separadores: coma, pipe, punto y coma
    """
    if not x:
        return []
    parts = re.split(r"[,\|;]", str(x))
    return [p.strip() for p in parts if p.strip()]


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _to_datetime_safe(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    sa = st.secrets["gcp_service_account_json"]
    sa_dict = dict(sa) if isinstance(sa, dict) else sa
    gc = gspread.service_account_from_dict(sa_dict)
    sh = gc.open_by_url(url)

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {_norm_sheet_title(t): t for t in titles}

    def resolve(sheet_name: str) -> str | None:
        return titles_norm.get(_norm_sheet_title(sheet_name))

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

    return ws_to_df(ws_form), ws_to_df(ws_cat)


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
    try:
        out["Nivel"] = out["Nivel"].astype(int).astype(str)
    except Exception:
        out["Nivel"] = out["Nivel"].astype(str)
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


def _classify_text(s: str, cats: dict) -> str:
    t = _norm_text(s)
    if not t:
        return ""
    for cat, kws in cats.items():
        for kw in kws:
            if kw in t:
                return cat
    return "Otros / sin clasificar"


def _top_categories(text_series: pd.Series, cats: dict, top_n: int = 6) -> pd.DataFrame:
    s = text_series.dropna().astype(str)
    s = s[s.str.strip() != ""]
    if s.empty:
        return pd.DataFrame(columns=["Categoría", "Conteo"])

    classified = s.apply(lambda x: _classify_text(x, cats))
    classified = classified[classified != ""]
    if classified.empty:
        return pd.DataFrame(columns=["Categoría", "Conteo"])

    vc = classified.value_counts().head(top_n).reset_index()
    vc.columns = ["Categoría", "Conteo"]
    return vc


def _plot_cat_counts(df: pd.DataFrame, title: str):
    if df is None or df.empty:
        st.info("Sin comentarios para clasificar en este filtro.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y("Categoría:N", sort="-x", title=None),
            x=alt.X("Conteo:Q", title=None),
            tooltip=["Categoría", "Conteo"],
        )
        .properties(height=max(260, 32 * len(df)), title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _metodologia_expander():
    with st.expander("Metodología de cálculo (escalas y porcentajes)", expanded=False):
        st.markdown(
            """
**Fuente de cálculo:** columnas numéricas generadas en la hoja `AULAS_VIRTUALES_FORM`.

**Nota:** Los porcentajes se calculan únicamente con respuestas válidas (se excluyen celdas vacías o no numéricas).
            """
        )


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

    # ---------------------------
    # Validaciones mínimas
    # ---------------------------
    if "Indica el servicio" not in df.columns:
        st.error("En AULAS_VIRTUALES_FORM falta la columna exacta: 'Indica el servicio'")
        st.dataframe(df.head(20), use_container_width=True)
        return
    if "servicio" not in cat.columns:
        st.error("En CAT_SERVICIOS_ESTRUCTURA falta la columna exacta: 'servicio'")
        st.dataframe(cat.head(20), use_container_width=True)
        return

    # ---------------------------
    # Normalización + merge catálogo
    # ---------------------------
    df = df.copy()
    cat = cat.copy()

    df["servicio_std"] = df["Indica el servicio"].apply(_clean_service_name)
    cat["servicio_std"] = cat["servicio"].apply(_clean_service_name)

    for col in ["escuela", "nivel", "tipo_unidad"]:
        if col not in cat.columns:
            cat[col] = pd.NA

    df = df.merge(
        cat[["servicio_std", "escuela", "nivel", "tipo_unidad"]],
        on="servicio_std",
        how="left",
    )

    df["servicio_key"] = df["servicio_std"].apply(_norm_key)
    df["escuela_key"] = df["escuela"].apply(_norm_key)
    df["nivel_key"] = df["nivel"].apply(_norm_key)

    # ---------------------------
    # Periodo (solo contexto)
    # ---------------------------
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Resolver asignación AV (DC)
    # ---------------------------
    av_tipo = (st.session_state.get("av_tipo") or "").strip().upper()
    av_valor = (st.session_state.get("av_valor") or "").strip()

    # Prioridad: AV_VALOR (si existe) > carrera param (app.py)
    raw_asignacion = av_valor or (carrera or "")
    asignaciones = _split_multi(raw_asignacion)
    asignaciones_keys = [_norm_key(x) for x in asignaciones]

    # ---------------------------
    # Filtros
    # ---------------------------
    if vista != "Dirección General":
        if not asignaciones_keys:
            st.error("Vista DC: falta AV_VALOR (o carrera) para filtrar Aulas Virtuales.")
            st.stop()

        # Si AV_TIPO viene explícito, filtra por esa dimensión; si no, intenta escuela -> servicio.
        if av_tipo == "ESCUELA":
            f = df[df["escuela_key"].isin(asignaciones_keys)].copy()
            unidad_txt = f"Escuela: {', '.join(asignaciones) if asignaciones else ''}"
        elif av_tipo == "SERVICIO":
            f = df[df["servicio_key"].isin(asignaciones_keys)].copy()
            unidad_txt = f"Servicio: {', '.join(asignaciones) if asignaciones else ''}"
        else:
            # fallback inteligente: primero escuela, si no hay nada, servicio
            f = df[df["escuela_key"].isin(asignaciones_keys)].copy()
            if not f.empty:
                unidad_txt = f"Escuela: {', '.join(asignaciones)}"
            else:
                f = df[df["servicio_key"].isin(asignaciones_keys)].copy()
                unidad_txt = f"Servicio: {', '.join(asignaciones)}"

        if f.empty:
            st.error("No hay registros para tu asignación (escuela/servicio) con el filtro actual.")
            st.caption(f"AV_TIPO: {av_tipo or '(vacío)'} | AV_VALOR/carrera: '{raw_asignacion}'")
            return

    else:
        # DG: filtros internos por Nivel/Escuela/Servicio
        with st.container(border=True):
            st.markdown("**Filtro del apartado (Aulas Virtuales)**")

            niveles = sorted([x for x in df["nivel"].dropna().astype(str).str.strip().unique().tolist() if x])
            escuelas = sorted([x for x in df["escuela"].dropna().astype(str).str.strip().unique().tolist() if x])

            cA, cB, cC = st.columns([1.1, 1.4, 2.2])
            with cA:
                nivel_sel = st.selectbox("Nivel", ["(Todos)"] + niveles, index=0)
            with cB:
                escuela_sel = st.selectbox("Escuela", ["(Todas)"] + escuelas, index=0)

            # Servicios dependientes (para que DG sí “filtre por carrera”)
            tmp = df.copy()
            if nivel_sel != "(Todos)":
                tmp = tmp[tmp["nivel"].astype(str).str.strip() == str(nivel_sel).strip()]
            if escuela_sel != "(Todas)":
                tmp = tmp[tmp["escuela"].astype(str).str.strip() == str(escuela_sel).strip()]

            servicios = sorted([x for x in tmp["servicio_std"].dropna().astype(str).str.strip().unique().tolist() if x])
            with cC:
                servicio_sel = st.selectbox("Servicio", ["(Todos)"] + servicios, index=0)

        f = df.copy()
        unidad_parts = []

        if nivel_sel != "(Todos)":
            f = f[f["nivel"].astype(str).str.strip() == str(nivel_sel).strip()]
            unidad_parts.append(f"Nivel: {nivel_sel}")

        if escuela_sel != "(Todas)":
            f = f[f["escuela"].astype(str).str.strip() == str(escuela_sel).strip()]
            unidad_parts.append(f"Escuela: {escuela_sel}")

        if servicio_sel != "(Todos)":
            f = f[f["servicio_std"].astype(str).str.strip() == str(servicio_sel).strip()]
            unidad_parts.append(f"Servicio: {servicio_sel}")

        unidad_txt = " | ".join(unidad_parts) if unidad_parts else "Todos los servicios"

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
        st.caption(
            f"{unidad_txt} | Periodo del levantamiento: **{fmin:%d %b %Y} – {fmax:%d %b %Y}** | "
            f"Año: **{year_txt}** | Respuestas: **{n}**"
        )
    else:
        st.caption(f"{unidad_txt} | Respuestas: **{n}** (sin fecha válida en 'Marca temporal')")

    _metodologia_expander()

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

    fx = f.copy()
    for col in NUM_COLS.values():
        fx[col] = _as_num(fx[col])

    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

    # ============================================================
    # TAB 1
    # ============================================================
    with tab1:
        c1, c2, c3, c4, c5, c6 = st.columns(6)

        alumnos_avg = _avg(fx[NUM_COLS["alumnos"]])
        docente_avg = _avg(fx[NUM_COLS["docente"]])
        alumnos_siempre = _pct_eq(fx[NUM_COLS["alumnos"]], 2)
        docente_siempre = _pct_eq(fx[NUM_COLS["docente"]], 2)

        def_avg = _avg(fx[NUM_COLS["definicion"]])
        def_completa = _pct_eq(fx[NUM_COLS["definicion"]], 2)
        def_no = _pct_eq(fx[NUM_COLS["definicion"]], 0)

        bloques_avg = _avg(fx[NUM_COLS["bloques"]])
        bloques_todas = _pct_eq(fx[NUM_COLS["bloques"]], 2)

        freq_avg = _avg(fx[NUM_COLS["frecuencia"]])
        util_avg = _avg(fx[NUM_COLS["utilidad"]])

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

        alt_si = _pct_eq(fx[NUM_COLS["formato_alt"]], 1)
        c12.metric("% Quiere formato alternativo", f"{alt_si:.1f}%" if alt_si is not None else "—")

        st.divider()

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

        if fx[NUM_COLS["secciones_count"]].notna().any():
            st.divider()
            _bar(_dist_counts(fx[NUM_COLS["secciones_count"]]), "Secciones completadas (conteo)")

    # ============================================================
    # TAB 2
    # ============================================================
    with tab2:
        st.markdown("### Sección I. Uso del Aula Virtual")

        brecha = None
        if docente_avg is not None and alumnos_avg is not None:
            brecha = docente_avg - alumnos_avg

        d1, d2, d3 = st.columns(3)
        d1.metric("Prom. alumnos (0–2)", f"{alumnos_avg:.2f}" if alumnos_avg is not None else "—")
        d2.metric("Prom. docente (0–2)", f"{docente_avg:.2f}" if docente_avg is not None else "—")
        d3.metric("Brecha (docente - alumnos)", f"{brecha:.2f}" if brecha is not None else "—")

        st.divider()
        st.markdown("### Sección II. Llenado de la planeación")

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("% Definición completa", f"{def_completa:.1f}%" if def_completa is not None else "—")
        inc = _pct_eq(fx[NUM_COLS["definicion"]], 1)
        e2.metric("% Definición incompleta", f"{inc:.1f}%" if inc is not None else "—")
        e3.metric("% Definición NO", f"{def_no:.1f}%" if def_no is not None else "—")
        sc_avg = _avg(fx[NUM_COLS["secciones_count"]])
        e4.metric("Prom. secciones completadas", f"{sc_avg:.2f}" if sc_avg is not None else "—")

        st.divider()
        st.markdown("### Sección II. Sesiones/Bloques y actualización")

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("% Bloques todas semanas", f"{bloques_todas:.1f}%" if bloques_todas is not None else "—")
        par = _pct_eq(fx[NUM_COLS["bloques"]], 1)
        f2.metric("% Bloques parcial", f"{par:.1f}%" if par is not None else "—")
        no_b = _pct_eq(fx[NUM_COLS["bloques"]], 0)
        f3.metric("% Bloques NO", f"{no_b:.1f}%" if no_b is not None else "—")
        f4.metric("Frecuencia prom (0–3)", f"{freq_avg:.2f}" if freq_avg is not None else "—")

        st.divider()
        st.markdown("### Sección III. Utilidad y sugerencias")

        g1c, g2c, g3c, g4c = st.columns(4)
        g1c.metric("Utilidad prom (0–3)", f"{util_avg:.2f}" if util_avg is not None else "—")
        muy = _pct_eq(fx[NUM_COLS["utilidad"]], 3)
        g2c.metric("% Muy útil", f"{muy:.1f}%" if muy is not None else "—")
        poco = _pct_eq(fx[NUM_COLS["utilidad"]], 1)
        nada = _pct_eq(fx[NUM_COLS["utilidad"]], 0)
        pn = None
        if poco is not None or nada is not None:
            pn = (poco or 0) + (nada or 0)
        g3c.metric("% Poco/Nada útil", f"{pn:.1f}%" if pn is not None else "—")
        g4c.metric("% Quiere formato alternativo", f"{alt_si:.1f}%" if alt_si is not None else "—")

        st.divider()
        st.markdown("### Hallazgos cualitativos (clasificación por categorías)")

        missing_text_cols = [v for v in TEXT_COLS.values() if v not in f.columns]
        if missing_text_cols:
            st.warning(
                "No se encontraron algunas columnas de texto para clasificar. "
                "Revisa encabezados en tu hoja:\n- " + "\n- ".join(missing_text_cols)
            )
            return

        cB, cL, cM = st.columns(3)

        with cB:
            st.markdown("**Beneficios (Top categorías)**")
            top_b = _top_categories(f[TEXT_COLS["beneficios"]], CATS_BENEFICIOS, top_n=6)
            _plot_cat_counts(top_b, "Beneficios")

        with cL:
            st.markdown("**Limitaciones (Top categorías)**")
            top_l = _top_categories(f[TEXT_COLS["limitaciones"]], CATS_LIMITACIONES, top_n=6)
            _plot_cat_counts(top_l, "Limitaciones")

        with cM:
            st.markdown("**Mejoras (Top categorías)**")
            top_m = _top_categories(f[TEXT_COLS["mejoras"]], CATS_MEJORAS, top_n=6)
            _plot_cat_counts(top_m, "Mejoras")

        st.divider()
        st.markdown("### Sección IV. Formato alternativo")

        h1, h2 = st.columns(2)
        h1.metric("% Sí", f"{alt_si:.1f}%" if alt_si is not None else "—")
        h2.metric("% No", f"{(100 - alt_si):.1f}%" if alt_si is not None else "—")
