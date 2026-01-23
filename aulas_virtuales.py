# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread
import re

from catalogos import mapear_carrera_id

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


# ============================================================
# CATÁLOGO MAESTRO (desde session_state)
# ============================================================
def _get_catalogo_carreras_df() -> pd.DataFrame:
    df_cat = st.session_state.get("df_cat_carreras")
    if df_cat is None or getattr(df_cat, "empty", True):
        return pd.DataFrame()
    return df_cat


@st.cache_data(show_spinner=False, ttl=3600)
def _build_id_to_nombre_map(df_cat: pd.DataFrame) -> dict:
    if df_cat is None or df_cat.empty:
        return {}
    d = {}
    if "carrera_id" in df_cat.columns and "nombre_oficial" in df_cat.columns:
        for _, r in df_cat[["carrera_id", "nombre_oficial"]].dropna().iterrows():
            d[str(r["carrera_id"]).strip()] = str(r["nombre_oficial"]).strip()
    return d


def _safe_mapear_carrera_id(texto: str, df_cat: pd.DataFrame):
    if not texto or df_cat is None or df_cat.empty:
        return None
    try:
        return mapear_carrera_id(texto, df_cat)
    except Exception:
        return None


def _nombre_oficial_from_id(carrera_id: str | None, id_to_nombre: dict) -> str:
    if not carrera_id:
        return ""
    return id_to_nombre.get(str(carrera_id).strip(), "")


# ============================================================
# Helpers
# ============================================================
def _get_av_url() -> str:
    url = st.secrets.get("AV_URL", "").strip()
    if not url:
        raise KeyError("Falta configurar AV_URL en Secrets.")
    return url


def _norm_sheet_title(x: str) -> str:
    return str(x).strip().lower().replace(" ", "").replace("_", "")


def _clean_service_name(x: str) -> str:
    """Normalización suave para nombres de servicio/carrera."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _slug(s: str) -> str:
    s = str(s or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("_", "")
    return s


# ---- Unidades compactadas (IDs técnicos) ----
UNIDADES_ALIASES = {
    "EDN": "EDN",
    "ECDG": "ECDG",
    "EDG": "ECDG",  # por si alguien lo escribió así
    "EJEC": "EJEC",
    "EJECUTIVAS": "EJEC",
    "LICENCIATURASEJECUTIVAS": "EJEC",
    "LICENCIATURAEJECUTIVA": "EJEC",
    # si decides usarlo después:
    "EDUCON": "EDUCON",
}

UNIDAD_LABEL = {
    "EDN": "EDN",
    "ECDG": "ECDG",
    "EJEC": "EJEC",
    "EDUCON": "EDUCON",
}


def _detect_unidad_id(texto_servicio: str) -> str | None:
    """
    Regresa el ID técnico de unidad compactada si aplica:
    EDN / ECDG / EJEC / EDUCON (si existe).
    """
    k = _slug(texto_servicio)
    if not k:
        return None
    # casos exactos o directos
    if k in UNIDADES_ALIASES:
        return UNIDADES_ALIASES[k]
    # casos tipo "Licenciaturas Ejecutivas" con espacios/variantes
    # ya queda cubierto por slug
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
(Se mantiene igual que tu versión actual)
            """
        )


# ============================================================
# Render
# ============================================================
def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")
# ===== DIAGNÓSTICO DURO DE CONTEXTO =====
st.error("DIAGNÓSTICO ACTIVO – AULAS VIRTUALES")

st.write("Vista recibida:", vista)
st.write("Carrera recibida desde app.py:", carrera)

st.write("Session df_cat_carreras existe:",
         "df_cat_carreras" in st.session_state)

if "df_cat_carreras" in st.session_state:
    st.write("CAT_CARRERAS filas:",
             len(st.session_state["df_cat_carreras"]))
else:
    st.write("CAT_CARRERAS NO cargado en session_state")

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
    # Catálogo maestro carreras (CAT_CARRERAS) -> CARRERA_ID / NOMBRE_OFICIAL
    # ---------------------------
    df_cat_carreras = _get_catalogo_carreras_df()
    id_to_nombre = _build_id_to_nombre_map(df_cat_carreras) if not df_cat_carreras.empty else {}

    df = df.copy()

    # UNIDAD_ID (compactadas) siempre se calcula, exista o no CAT_CARRERAS
    df["UNIDAD_ID"] = df["Indica el servicio"].astype(str).apply(_detect_unidad_id)

    if not df_cat_carreras.empty:
        df["CARRERA_ID"] = df["Indica el servicio"].astype(str).apply(
            lambda x: _safe_mapear_carrera_id(x, df_cat_carreras)
        )
        df["NOMBRE_OFICIAL"] = df["CARRERA_ID"].apply(
            lambda cid: _nombre_oficial_from_id(cid, id_to_nombre)
        ).fillna("")
        df.loc[df["NOMBRE_OFICIAL"].astype(str).str.strip() == "", "NOMBRE_OFICIAL"] = df["Indica el servicio"].astype(str)
    else:
        df["CARRERA_ID"] = None
        df["NOMBRE_OFICIAL"] = df["Indica el servicio"].astype(str)

    # LLAVE ÚNICA DE FILTRO:
    # - Si es unidad compactada: usa UNIDAD_ID
    # - Si no, usa CARRERA_ID
    df["FILTRO_ID"] = df["UNIDAD_ID"].fillna(df["CARRERA_ID"])

    # Etiqueta para selector DG
    def _filtro_label(row) -> str:
        uid = row.get("UNIDAD_ID")
        cid = row.get("CARRERA_ID")
        if isinstance(uid, str) and uid.strip():
            return UNIDAD_LABEL.get(uid.strip(), uid.strip())
        if isinstance(cid, str) and cid.strip():
            return str(row.get("NOMBRE_OFICIAL") or cid).strip()
        # sin mapear
        raw = str(row.get("Indica el servicio") or "").strip()
        return f"(Sin mapear) {raw}" if raw else "(Sin mapear)"

    df["FILTRO_LABEL"] = df.apply(_filtro_label, axis=1)

    # ---------------------------
    # Normalización + join de catálogo interno (CAT_SERVICIOS_ESTRUCTURA)
    # ---------------------------
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

    # ---------------------------
    # Fecha
    # ---------------------------
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Filtros
    # ---------------------------
    if vista != "Dirección General":
        if not (carrera or "").strip():
            st.error("Vista DC: no se recibió la carrera/servicio asignado.")
            st.stop()

        carrera_in = str(carrera).strip()
        unidad_dc = _detect_unidad_id(carrera_in)

        if unidad_dc:
            # DC por unidad compactada
            f = df[df["UNIDAD_ID"] == unidad_dc].copy()
            unidad_txt = f"Unidad: {UNIDAD_LABEL.get(unidad_dc, unidad_dc)}"
        else:
            # DC por carrera normal (preferencia: carrera_id si hay catálogo)
            if not df_cat_carreras.empty:
                carrera_id_fix = _safe_mapear_carrera_id(carrera_in, df_cat_carreras)
            else:
                carrera_id_fix = None

            if carrera_id_fix:
                f = df[df["CARRERA_ID"] == carrera_id_fix].copy()
                unidad_txt = f"Servicio: {id_to_nombre.get(carrera_id_fix, carrera_in)}"
            else:
                # fallback a texto exacto
                servicio_base = _clean_service_name(carrera_in)
                f = df[df["servicio_std"] == servicio_base].copy()
                unidad_txt = f"Servicio: {servicio_base}"

        if f.empty:
            st.warning("No hay registros para el servicio asignado.")
            st.caption(f"Servicio recibido desde app.py: {carrera_in}")
            st.stop()

    else:
        # DG: selector por FILTRO_LABEL (incluye compactadas)
        with st.container(border=True):
            st.markdown("**Filtro del apartado (Aulas Virtuales)**")

            labels = df["FILTRO_LABEL"].dropna().astype(str).str.strip()
            labels = [x for x in sorted(set(labels.tolist())) if x]

            opciones = ["(Todos)"] + labels

            sel = st.selectbox(
                "Servicio / Unidad a analizar (Aulas Virtuales)",
                options=opciones,
                index=0,
            )

        if sel == "(Todos)":
            f = df.copy()
            unidad_txt = "Todos los servicios / unidades"
        else:
            f = df[df["FILTRO_LABEL"].astype(str).str.strip() == sel].copy()
            unidad_txt = f"Filtro: {sel}"

        if f.empty:
            st.warning("No hay registros con el filtro seleccionado.")
            return

    # ---------------------------
    # Encabezado ejecutivo
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

    # ---------------------------
    # Cálculos base
    # ---------------------------
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
    alt_si = _pct_eq(fx[NUM_COLS["formato_alt"]], 1)

    # ---------------------------
    # Tabs
    # ---------------------------
    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

    with tab1:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
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
