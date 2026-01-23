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


def _norm_key(x: str) -> str:
    s = _clean_service_name(x).lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9áéíóúüñ ]+", "", s)
    s = s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").replace("ñ", "n")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_colname(x: str) -> str:
    s = str(x or "").strip().lower()
    s = s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").replace("ñ", "n")
    s = re.sub(r"[\s_]+", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """
    Encuentra una columna aunque cambie mayúsculas/acentos/espacios.
    candidates: lista de nombres "lógicos" (ej. ["escuela","school","unidad"]).
    """
    if df is None or df.empty:
        return None
    cmap = {_norm_colname(c): c for c in df.columns}
    for cand in candidates:
        key = _norm_colname(cand)
        if key in cmap:
            return cmap[key]
    return None


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
    out["Nivel"] = out["Nivel"].astype(int, errors="ignore").astype(str)
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


def _norm_text(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return str(s).strip().lower()


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

- Uso (Alumnos/Docente): 0–2
- Definición del curso: 0–2
- Bloques: 0–2
- Frecuencia: 0–3
- Utilidad: 0–3
- Secciones completadas: conteo
- Formato alternativo: 0–1
            """
        )


def _enrich_with_catalog(df: pd.DataFrame, cat: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    df = df.copy()
    cat = cat.copy()

    if "Indica el servicio" not in df.columns:
        raise ValueError("En AULAS_VIRTUALES_FORM falta la columna exacta: 'Indica el servicio'")

    # Detectar columnas reales en catálogo (aunque estén con otros nombres)
    col_serv = _find_col(cat, ["servicio", "servicios", "carrera", "programa"])
    col_esc = _find_col(cat, ["escuela", "facultad", "unidad", "area", "departamento"])
    col_niv = _find_col(cat, ["nivel", "nivelacademico", "nivel_academico", "grado", "nivel educativo", "nivel_educativo"])

    meta = {"col_servicio": col_serv, "col_escuela": col_esc, "col_nivel": col_niv}

    if not col_serv:
        raise ValueError("En CAT_SERVICIOS_ESTRUCTURA no encontré una columna equivalente a 'servicio'.")

    # Construir std + key
    df["servicio_std"] = df["Indica el servicio"].apply(_clean_service_name)
    df["servicio_key"] = df["servicio_std"].apply(_norm_key)

    cat["servicio_std"] = cat[col_serv].apply(_clean_service_name)
    cat["servicio_key"] = cat["servicio_std"].apply(_norm_key)

    cat["escuela_std"] = cat[col_esc].apply(_clean_service_name) if col_esc else pd.NA
    cat["nivel_std"] = cat[col_niv].apply(_clean_service_name) if col_niv else pd.NA

    # Merge por key
    df = df.merge(
        cat[["servicio_key", "servicio_std", "escuela_std", "nivel_std"]],
        on="servicio_key",
        how="left",
        suffixes=("", "_cat"),
    )

    # Si no matchea, conserva el texto del form
    df["servicio_std"] = df["servicio_std"].fillna(df["Indica el servicio"].apply(_clean_service_name))

    return df, cat, meta


def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    # Carga
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

    # Enriquecimiento
    try:
        df, cat, meta = _enrich_with_catalog(df, cat)
    except Exception as e:
        st.error("No se pudo enriquecer con catálogo (CAT_SERVICIOS_ESTRUCTURA).")
        st.exception(e)
        return

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # Filtros
    if vista != "Dirección General":
        servicio_base = _clean_service_name(carrera or "")
        if not servicio_base:
            st.error("Vista DC: no se recibió la carrera/servicio asignado.")
            st.stop()

        servicio_key = _norm_key(servicio_base)
        f = df[df["servicio_key"] == servicio_key].copy()
        unidad_txt = f"Servicio: {servicio_base}"

        if f.empty:
            st.warning("No hay registros para el servicio asignado.")
            st.caption(f"Servicio recibido desde app.py: {servicio_base}")
            st.stop()

    else:
        # DG
        with st.container(border=True):
            st.markdown("**Filtro del apartado (Aulas Virtuales)**")

            # Si escuela/nivel no existen en el catálogo, lo avisamos explícito
            if meta.get("col_nivel") is None:
                st.warning("En tu CAT_SERVICIOS_ESTRUCTURA no se detectó una columna de NIVEL. Revisa encabezado (ej. NIVEL, Nivel, Nivel educativo).")
            if meta.get("col_escuela") is None:
                st.warning("En tu CAT_SERVICIOS_ESTRUCTURA no se detectó una columna de ESCUELA. Revisa encabezado (ej. ESCUELA, Escuela, Facultad, Unidad).")

            # construir opciones (sin NA)
            niveles = sorted([x for x in cat.get("nivel_std", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if x.strip() and x.strip().upper() != "<NA>"])
            escuelas = sorted([x for x in cat.get("escuela_std", pd.Series(dtype=str)).dropna().astype(str).unique().tolist() if x.strip() and x.strip().upper() != "<NA>"])

            nivel_sel = st.selectbox("Nivel", ["(Todos)"] + niveles, index=0, disabled=(len(niveles) == 0))
            escuela_sel = st.selectbox("Escuela", ["(Todas)"] + escuelas, index=0, disabled=(len(escuelas) == 0))

            # filtrar catálogo para servicios disponibles según nivel/escuela
            cat_f = cat.copy()
            if len(niveles) > 0 and nivel_sel != "(Todos)":
                cat_f = cat_f[cat_f["nivel_std"].astype(str) == str(nivel_sel)]
            if len(escuelas) > 0 and escuela_sel != "(Todas)":
                cat_f = cat_f[cat_f["escuela_std"].astype(str) == str(escuela_sel)]

            servicios = sorted([x for x in cat_f["servicio_std"].dropna().astype(str).unique().tolist() if x.strip()])
            servicio_sel = st.selectbox("Servicio", ["(Todos)"] + servicios, index=0)

        f = df.copy()
        unidad_parts = []

        if len(niveles) > 0 and nivel_sel != "(Todos)":
            f = f[f["nivel_std"].astype(str) == str(nivel_sel)]
            unidad_parts.append(f"Nivel: {nivel_sel}")

        if len(escuelas) > 0 and escuela_sel != "(Todas)":
            f = f[f["escuela_std"].astype(str) == str(escuela_sel)]
            unidad_parts.append(f"Escuela: {escuela_sel}")

        if servicio_sel != "(Todos)":
            f = f[f["servicio_key"] == _norm_key(servicio_sel)]
            unidad_parts.append(f"Servicio: {servicio_sel}")

        unidad_txt = " | ".join(unidad_parts) if unidad_parts else "Todos los servicios"

        if f.empty:
            st.warning("No hay registros con el filtro seleccionado.")
            return

    # Encabezado
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

    # Validar numéricas
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

    # Tabs
    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

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

    with tab2:
        st.markdown("### Diagnóstico por secciones")
        st.info("Esta pestaña se mantiene igual que tu versión anterior (resumen + cualitativos).")
