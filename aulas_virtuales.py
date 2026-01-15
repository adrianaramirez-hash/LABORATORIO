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

# ============================
# Clasificación de textos (B)
# Beneficios / Limitaciones / Mejoras
# ============================

TEXT_COLS = {
    "beneficios": "En caso de considerarlo útil, ¿Qué beneficios principales identifica?",
    "limitaciones": "En caso de considerarlo poco útil o nada útil, ¿Qué limitaciones o dificultades ha encontrado?",
    "mejoras": "¿Qué mejoras sugiere para optimizar el uso de las Aulas Virtuales en la planeación docente?",
}

# Categorías y palabras clave (puedes ajustar con el tiempo)
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


# ============================
# Text mining simple por diccionario
# ============================
def _norm_text(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return str(s).strip().lower()


def _classify_text(s: str, cats: dict) -> str:
    """
    Devuelve la primera categoría cuyo set de keywords aparece en el texto.
    Si no hay match: 'Otros / sin clasificar'
    """
    t = _norm_text(s)
    if not t:
        return ""
    for cat, kws in cats.items():
        for kw in kws:
            if kw in t:
                return cat
    return "Otros / sin clasificar"


def _top_categories(text_series: pd.Series, cats: dict, top_n: int = 6) -> pd.DataFrame:
    """
    Clasifica cada texto, cuenta categorías y devuelve top_n.
    Excluye vacíos.
    """
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

### 1) Uso del Aula Virtual (Alumnos / Docente) — escala 0–2
- **Nunca = 0**
- **A veces = 1**
- **Siempre = 2**

**Promedio (0–2):** promedio aritmético de la columna numérica.  
**% Siempre:** (respuestas con valor **2** / total de respuestas válidas) × 100.

Columnas:
- `alumnos_uso_num`
- `docente_uso_num`

### 2) Definición del curso — escala 0–2
- **No lo realicé = 0**
- **Sí, pero incompletas = 1**
- **Sí, todas las secciones = 2**

**% Definición completa:** valor **2**.  
**% Definición NO realizada:** valor **0**.

Columna:
- `definicion_curso_num`

### 3) Sesiones/Bloques agregados — escala 0–2
- **No lo realicé = 0**
- **Sí, pero de forma parcial = 1**
- **Sí, todas las semanas = 2**

Columna:
- `bloques_agregados_num`

### 4) Frecuencia de actualización — escala 0–3
- **No actualicé = 0**
- **Solo en algunas ocasiones = 1**
- **Quincenalmente = 2**
- **Cada semana = 3**

Columna:
- `frecuencia_actualizacion_num`

### 5) Utilidad percibida — escala 0–3
- **Nada útil = 0**
- **Poco útil = 1**
- **Útil = 2**
- **Muy útil = 3**

Columna:
- `utilidad_num`

### 6) Secciones completadas — conteo 0–5
- Se calcula como el **número de secciones seleccionadas** en la multiselección.
- **“Ninguna” = 0**

Columna:
- `def_secciones_count`

### 7) Formato alternativo — escala 0–1
- **No = 0**
- **Sí = 1**

Columna:
- `formato_alternativo_num`

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
    # Selector interno: servicio / escuela + (Todos)
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

        opciones = ["(Todos)"]

        if escuela_base and str(escuela_base).strip().lower() not in ["nan", "none", ""]:
            servicios_escuela = (
                cat[cat["escuela"] == escuela_base]["servicio_std"]
                .dropna().astype(str).str.strip().unique().tolist()
            )
            servicios_escuela = sorted(set([s for s in servicios_escuela if s]))
            opciones += [f"Todos los servicios de {escuela_base}"] + servicios_escuela
        else:
            opciones += servicios_disponibles

        default_idx = 0
        if vista != "Dirección General" and servicio_base and servicio_base in opciones:
            default_idx = opciones.index(servicio_base)

        servicio_sel = st.selectbox(
            "Servicio a analizar (Aulas Virtuales)",
            options=opciones,
            index=default_idx
        )

    # ---------------------------
    # Filtrado
    # ---------------------------
    if servicio_sel == "(Todos)":
        f = df.copy()
        unidad_txt = "Todos los servicios"
    elif servicio_sel.startswith("Todos los servicios de "):
        f = df[df["escuela"] == escuela_base].copy()
        unidad_txt = f"Escuela: {escuela_base}"
    else:
        f = df[df["servicio_std"] == servicio_sel].copy()
        unidad_txt = f"Servicio: {servicio_sel}"

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

    # Metodología arriba (antes de tabs)
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
    # Tabs (2)
    # ---------------------------
    tab1, tab2 = st.tabs(["Resumen ejecutivo", "Diagnóstico por secciones"])

    # ============================================================
    # TAB 1: Resumen ejecutivo
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
    # TAB 2: Diagnóstico por secciones
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

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Utilidad prom (0–3)", f"{util_avg:.2f}" if util_avg is not None else "—")
        muy = _pct_eq(fx[NUM_COLS["utilidad"]], 3)
        g2.metric("% Muy útil", f"{muy:.1f}%" if muy is not None else "—")
        poco = _pct_eq(fx[NUM_COLS["utilidad"]], 1)
        nada = _pct_eq(fx[NUM_COLS["utilidad"]], 0)
        pn = None
        if poco is not None or nada is not None:
            pn = (poco or 0) + (nada or 0)
        g3.metric("% Poco/Nada útil", f"{pn:.1f}%" if pn is not None else "—")
        g4.metric("% Quiere formato alternativo", f"{alt_si:.1f}%" if alt_si is not None else "—")

        # ============================
        # Texto: Beneficios / Limitaciones / Mejoras por categorías
        # ============================
        st.divider()
        st.markdown("### Hallazgos cualitativos (clasificación por categorías)")

        # Validar si existen columnas de texto
        missing_text_cols = [v for v in TEXT_COLS.values() if v not in f.columns]
        if missing_text_cols:
            st.warning(
                "No se encontraron algunas columnas de texto para clasificar. "
                "Revisa encabezados en tu hoja:\n- " + "\n- ".join(missing_text_cols)
            )
        else:
            cB, cL, cM = st.columns(3)

            with cB:
                st.markdown("**Beneficios (Top categorías)**")
                top_b = _top_categories(f[TEXT_COLS["beneficios"]], CATS_BENEFICIOS, top_n=6)
                _plot_cat_counts(top_b, "Beneficios")
                if not top_b.empty:
                    cat_sel_b = st.selectbox("Ver ejemplos (Beneficios)", ["(Ninguno)"] + top_b["Categoría"].tolist(), key="b_sel")
                    if cat_sel_b != "(Ninguno)":
                        ejemplos = (
                            f[[TEXT_COLS["beneficios"]]]
                            .dropna()
                            .astype(str)
                        )
                        ejemplos = ejemplos[ejemplos[TEXT_COLS["beneficios"]].str.strip() != ""]
                        ejemplos["cat"] = ejemplos[TEXT_COLS["beneficios"]].apply(lambda x: _classify_text(x, CATS_BENEFICIOS))
                        ejemplos = ejemplos[ejemplos["cat"] == cat_sel_b].head(20)
                        st.dataframe(ejemplos[[TEXT_COLS["beneficios"]]].rename(columns={TEXT_COLS["beneficios"]: "Ejemplos"}), use_container_width=True)

            with cL:
                st.markdown("**Limitaciones (Top categorías)**")
                top_l = _top_categories(f[TEXT_COLS["limitaciones"]], CATS_LIMITACIONES, top_n=6)
                _plot_cat_counts(top_l, "Limitaciones")
                if not top_l.empty:
                    cat_sel_l = st.selectbox("Ver ejemplos (Limitaciones)", ["(Ninguno)"] + top_l["Categoría"].tolist(), key="l_sel")
                    if cat_sel_l != "(Ninguno)":
                        ejemplos = (
                            f[[TEXT_COLS["limitaciones"]]]
                            .dropna()
                            .astype(str)
                        )
                        ejemplos = ejemplos[ejemplos[TEXT_COLS["limitaciones"]].str.strip() != ""]
                        ejemplos["cat"] = ejemplos[TEXT_COLS["limitaciones"]].apply(lambda x: _classify_text(x, CATS_LIMITACIONES))
                        ejemplos = ejemplos[ejemplos["cat"] == cat_sel_l].head(20)
                        st.dataframe(ejemplos[[TEXT_COLS["limitaciones"]]].rename(columns={TEXT_COLS["limitaciones"]: "Ejemplos"}), use_container_width=True)

            with cM:
                st.markdown("**Mejoras (Top categorías)**")
                top_m = _top_categories(f[TEXT_COLS["mejoras"]], CATS_MEJORAS, top_n=6)
                _plot_cat_counts(top_m, "Mejoras")
                if not top_m.empty:
                    cat_sel_m = st.selectbox("Ver ejemplos (Mejoras)", ["(Ninguno)"] + top_m["Categoría"].tolist(), key="m_sel")
                    if cat_sel_m != "(Ninguno)":
                        ejemplos = (
                            f[[TEXT_COLS["mejoras"]]]
                            .dropna()
                            .astype(str)
                        )
                        ejemplos = ejemplos[ejemplos[TEXT_COLS["mejoras"]].str.strip() != ""]
                        ejemplos["cat"] = ejemplos[TEXT_COLS["mejoras"]].apply(lambda x: _classify_text(x, CATS_MEJORAS))
                        ejemplos = ejemplos[ejemplos["cat"] == cat_sel_m].head(20)
                        st.dataframe(ejemplos[[TEXT_COLS["mejoras"]]].rename(columns={TEXT_COLS["mejoras"]: "Ejemplos"}), use_container_width=True)

        st.divider()
        st.markdown("### Sección IV. Formato alternativo")

        h1, h2 = st.columns(2)
        h1.metric("% Sí", f"{alt_si:.1f}%" if alt_si is not None else "—")
        h2.metric("% No", f"{(100 - alt_si):.1f}%" if alt_si is not None else "—")
