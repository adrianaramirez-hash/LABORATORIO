# aulas_virtuales.py
import streamlit as st
import pandas as pd
import altair as alt
import gspread
import re
from typing import Tuple

SHEET_FORM = "AULAS_VIRTUALES_FORM"
SHEET_CATALOGO = "CAT_SERVICIOS_ESTRUCTURA"


# -----------------------------
# Utilidades
# -----------------------------
def _get_av_url() -> str:
    url = st.secrets.get("AV_URL", "").strip()
    if not url:
        raise KeyError("Falta configurar AV_URL en Secrets.")
    return url


def _norm(s: str) -> str:
    return str(s).strip().lower().replace(" ", "").replace("_", "")


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _to_datetime_safe(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _yesno_to_num(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.strip().str.lower()
    x = x.replace({"sí": "si"})
    return x.map({"si": 1, "no": 0})


def _map_frecuencia_to_num(s: pd.Series) -> pd.Series:
    """
    Mapea textos típicos a 0–5 usando keywords (robusto a variaciones).
    """
    x = s.astype(str).str.strip().str.lower().replace({"sí": "si"})

    def f(v: str):
        if v in ["nan", "none", ""]:
            return pd.NA
        if "nunca" in v:
            return 0
        if "casi nunca" in v or "rara vez" in v:
            return 1
        if "mensual" in v or "cada mes" in v:
            return 2
        if "quinc" in v or "cada 15" in v:
            return 3
        if "seman" in v or "cada semana" in v:
            return 4
        if "diar" in v or "cada clase" in v or "cada sesión" in v or "cada sesion" in v or "varias veces" in v:
            return 5
        return pd.NA

    return x.apply(f)


def _map_utilidad_to_num(s: pd.Series) -> pd.Series:
    """
    Mapea utilidad a escala 0–3.
    """
    x = s.astype(str).str.strip().str.lower().replace({"sí": "si"})

    def f(v: str):
        if v in ["nan", "none", ""]:
            return pd.NA
        if "nada" in v:
            return 0
        if "poco" in v:
            return 1
        # "muy útil" -> 3
        if "muy" in v:
            return 3
        # "útil" (o "util") -> 2
        if "útil" in v or "util" in v:
            return 2
        return pd.NA

    return x.apply(f)


def _count_multiselect(s: pd.Series) -> pd.Series:
    """
    Cuenta opciones en respuestas tipo multiselección separadas por coma.
    """
    x = s.astype(str).str.strip()

    def f(v: str):
        if v.lower() in ["nan", "none", ""]:
            return pd.NA
        parts = [p.strip() for p in v.split(",") if p.strip()]
        return len(parts) if parts else pd.NA

    return x.apply(f)


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
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


# -----------------------------
# Clasificación rápida de texto (categorías)
# -----------------------------
def _classify_text(series: pd.Series, categories: dict) -> pd.DataFrame:
    """
    Clasifica texto por categorías vía keywords.
    Regresa dataframe con conteos por categoría.
    """
    textos = series.dropna().astype(str).str.strip()
    textos = textos[textos != ""]
    if textos.empty:
        return pd.DataFrame(columns=["Categoría", "Conteo"])

    counts = {k: 0 for k in categories.keys()}
    for t in textos:
        tl = t.lower()
        matched = False
        for cat, keys in categories.items():
            for kw in keys:
                if kw in tl:
                    counts[cat] += 1
                    matched = True
                    break
        # si no match, lo ignoramos (evita “Otros” inflado). Se puede agregar si lo quieres.

    out = (
        pd.DataFrame([{"Categoría": k, "Conteo": v} for k, v in counts.items()])
        .sort_values("Conteo", ascending=False)
        .reset_index(drop=True)
    )
    return out


def _bar(df: pd.DataFrame, x: str, y: str, title: str):
    if df is None or df.empty:
        st.info("Sin datos para graficar.")
        return
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:N", sort="-y", title=None),
            y=alt.Y(f"{y}:Q", title=None),
            tooltip=[x, y],
        )
        .properties(height=320, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


# -----------------------------
# Render principal
# -----------------------------
def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    # 1) Carga
    try:
        url = _get_av_url()
        with st.spinner("Cargando Aulas Virtuales (Google Sheets)…"):
            df_av, cat = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar los datos de Aulas Virtuales.")
        st.exception(e)
        st.stop()

    if df_av.empty:
        st.warning("La hoja AULAS_VIRTUALES_FORM está vacía.")
        return
    if cat.empty:
        st.warning("La hoja CAT_SERVICIOS_ESTRUCTURA está vacía.")
        return

    # 2) Validaciones mínimas
    if "Indica el servicio" not in df_av.columns:
        st.error("En AULAS_VIRTUALES_FORM falta la columna exacta: 'Indica el servicio'")
        st.dataframe(df_av.head(20), use_container_width=True)
        return
    if "servicio" not in cat.columns:
        st.error("En CAT_SERVICIOS_ESTRUCTURA falta la columna exacta: 'servicio'")
        st.dataframe(cat.head(20), use_container_width=True)
        return

    df_av = df_av.copy()
    cat = cat.copy()

    # 3) Fecha (para filtro de año)
    fecha_col = _pick_fecha_col(df_av)
    if fecha_col:
        df_av[fecha_col] = _to_datetime_safe(df_av[fecha_col])

    # 4) Normalización + join de catálogo
    df_av["servicio_std"] = df_av["Indica el servicio"].astype(str).str.strip()
    cat["servicio_std"] = cat["servicio"].astype(str).str.strip()

    for col in ["escuela", "nivel", "tipo_unidad"]:
        if col not in cat.columns:
            cat[col] = pd.NA

    df_av = df_av.merge(
        cat[["servicio_std", "escuela", "nivel", "tipo_unidad"]],
        on="servicio_std",
        how="left"
    )

    st.caption(
        "Este levantamiento se reporta por 'Servicio'. "
        "La selección superior se conserva para mantener la lógica de navegación."
    )

    # 5) Filtros
    years = ["(Todos)"]
    if fecha_col and df_av[fecha_col].notna().any():
        years += sorted(df_av[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    # Selector interno (2ª selección) + Año
    with st.container(border=True):
        c1, c2 = st.columns([2.2, 1.0])

        # ---- Año
        with c2:
            year_sel = st.selectbox("Año", years, index=0)

        # ---- Servicio / Escuela
        with c1:
            servicio_base = (carrera or "").strip()

            # Escuela del servicio base
            escuela_base = None
            if servicio_base:
                fila_base = cat[cat["servicio_std"] == servicio_base]
                if not fila_base.empty:
                    escuela_base = fila_base.iloc[0].get("escuela")

            servicios_disponibles = (
                cat["servicio_std"].dropna().astype(str).str.strip().unique().tolist()
            )
            servicios_disponibles = sorted(set([s for s in servicios_disponibles if s]))

            # Si hay escuela_base, permitir "Todos los servicios de mi escuela"
            if escuela_base and str(escuela_base).strip().lower() not in ["nan", "none", ""]:
                servicios_escuela = (
                    cat[cat["escuela"] == escuela_base]["servicio_std"]
                    .dropna().astype(str).str.strip().unique().tolist()
                )
                servicios_escuela = sorted(set([s for s in servicios_escuela if s]))
                opciones = [f"Todos los servicios de {escuela_base}"] + servicios_escuela
            else:
                # En Dirección General puede analizar cualquiera; en Director, también lo dejamos abierto
                opciones = servicios_disponibles

            default_idx = 0
            if servicio_base and servicio_base in opciones:
                default_idx = opciones.index(servicio_base)

            servicio_av_sel = st.selectbox(
                "Servicio a analizar (Aulas Virtuales)",
                options=opciones,
                index=default_idx
            )

    # 6) Aplicar filtros
    f = df_av.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if servicio_av_sel.startswith("Todos los servicios de "):
        # Si no hay escuela_base, se quedará vacío; pero solo aparece si hay escuela_base
        f = f[f["escuela"] == escuela_base].copy()
    else:
        f = f[f["servicio_std"] == servicio_av_sel].copy()

    if f.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # 7) Conversión numérica (derivados)
    df_num = f.copy()

    col_alumnos = "Los alumnos de su materia ¿Utilizan el Aula Virtual?"
    col_docente = "Usted como docente, ¿Utiliza el aula Virtual?"
    col_def = "¿Incluyó en su Aula Virtual la Definición del Curso con las secciones obligatorias al inicio del ciclo escolar?"
    col_bloques = "Durante el ciclo, ¿añadió las sesiones y bloques semanales en el Aula Virtual para registrar el desarrollo de sus clases?"
    col_formato_alt = "¿Le gustaría que se implementara otra forma o formato alternativo para la planeación docente?"
    col_freq = "¿Con qué frecuencia actualizó los bloques semanales?"
    col_util = "¿Considera que el uso de Aulas Virtuales como herramienta de planeación docente es útil para usted y para sus estudiantes?"
    col_secciones = "En relación con las secciones de la Definición del Curso, indique cuáles completó:"

    for col, out in [
        (col_alumnos, "alumnos_usa_n"),
        (col_docente, "docente_usa_n"),
        (col_def, "definicion_incluida_n"),
        (col_bloques, "bloques_agregados_n"),
        (col_formato_alt, "quiere_formato_alt_n"),
    ]:
        if col in df_num.columns:
            df_num[out] = _yesno_to_num(df_num[col])

    if col_freq in df_num.columns:
        df_num["freq_actualizacion_n"] = _map_frecuencia_to_num(df_num[col_freq])

    if col_util in df_num.columns:
        df_num["utilidad_n"] = _map_utilidad_to_num(df_num[col_util])

    if col_secciones in df_num.columns:
        df_num["definicion_secciones_count"] = _count_multiselect(df_num[col_secciones])

    # 8) Tabs de presentación
    tab1, tab2, tab3 = st.tabs(["Resumen", "Hallazgos (texto)", "Detalle"])

    # -------------------
    # TAB 1: Resumen
    # -------------------
    with tab1:
        st.caption(f"Registros filtrados: **{len(df_num)}**")

        # KPIs (tarjetas)
        kpis = []

        def _pct(colname: str):
            if colname not in df_num.columns:
                return None
            s = pd.to_numeric(df_num[colname], errors="coerce")
            if s.notna().any():
                return float(s.mean() * 100)
            return None

        def _avg(colname: str):
            if colname not in df_num.columns:
                return None
            s = pd.to_numeric(df_num[colname], errors="coerce")
            if s.notna().any():
                return float(s.mean())
            return None

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        v1 = _pct("alumnos_usa_n")
        v2 = _pct("docente_usa_n")
        v3 = _pct("definicion_incluida_n")
        v4 = _pct("bloques_agregados_n")
        v5 = _avg("freq_actualizacion_n")  # 0–5
        v6 = _avg("utilidad_n")           # 0–3

        c1.metric("% Alumnos usan AV", f"{v1:.1f}%" if v1 is not None else "—")
        c2.metric("% Docente usa AV", f"{v2:.1f}%" if v2 is not None else "—")
        c3.metric("% Definición incluida", f"{v3:.1f}%" if v3 is not None else "—")
        c4.metric("% Sesiones/bloques", f"{v4:.1f}%" if v4 is not None else "—")
        c5.metric("Frecuencia (0–5)", f"{v5:.2f}" if v5 is not None else "—")
        c6.metric("Utilidad (0–3)", f"{v6:.2f}" if v6 is not None else "—")

        st.divider()

        # Gráficos: distribuciones (concretos)
        # Distribución frecuencia
        if "freq_actualizacion_n" in df_num.columns:
            df_freq = (
                pd.to_numeric(df_num["freq_actualizacion_n"], errors="coerce")
                .dropna()
                .value_counts()
                .sort_index()
                .reset_index()
            )
            df_freq.columns = ["Nivel", "Conteo"]
            df_freq["Nivel"] = df_freq["Nivel"].astype(int).astype(str)
            _bar(df_freq, "Nivel", "Conteo", "Distribución de frecuencia de actualización (0–5)")

        # Distribución utilidad
        if "utilidad_n" in df_num.columns:
            df_ut = (
                pd.to_numeric(df_num["utilidad_n"], errors="coerce")
                .dropna()
                .value_counts()
                .sort_index()
                .reset_index()
            )
            df_ut.columns = ["Nivel", "Conteo"]
            df_ut["Nivel"] = df_ut["Nivel"].astype(int).astype(str)
            _bar(df_ut, "Nivel", "Conteo", "Distribución de utilidad percibida (0–3)")

        # Secciones completadas
        if "definicion_secciones_count" in df_num.columns:
            df_sc = (
                pd.to_numeric(df_num["definicion_secciones_count"], errors="coerce")
                .dropna()
                .value_counts()
                .sort_index()
                .reset_index()
            )
            df_sc.columns = ["Secciones completadas", "Conteo"]
            df_sc["Secciones completadas"] = df_sc["Secciones completadas"].astype(int).astype(str)
            _bar(df_sc, "Secciones completadas", "Conteo", "Definición del curso: conteo de secciones completadas")

    # -------------------
    # TAB 2: Hallazgos texto
    # -------------------
    with tab2:
        st.markdown("#### Clasificación de respuestas abiertas (conteos por tema)")

        # Columnas de texto
        col_benef = "En caso de considerarlo útil, ¿Qué beneficios principales identifica?"
        col_lim = "En caso de considerarlo poco útil o nada útil, ¿Qué limitaciones o dificultades ha encontrado?"
        col_mej = "¿Qué mejoras sugiere para optimizar el uso de las Aulas Virtuales en la planeación docente?"

        BENEF = {
            "Organización/planeación": ["organiza", "orden", "planea", "estructura", "planificación", "planificacion"],
            "Acceso a materiales": ["material", "recurso", "archivo", "documento", "contenido"],
            "Comunicación": ["comunica", "mensaje", "avis", "contact", "foro", "chat"],
            "Seguimiento/evidencia": ["seguimiento", "evidencia", "registro", "control", "historial"],
            "Evaluación/tareas": ["tarea", "evalu", "califica", "examen", "actividad"],
        }
        LIM = {
            "Falta de tiempo": ["tiempo", "carga", "trabajo", "satur", "ocupado"],
            "Capacitación": ["capacit", "no sé", "no se", "desconoz", "aprender", "entren"],
            "Problemas técnicos": ["falla", "error", "lento", "plataforma", "se cae", "no abre"],
            "Resistencia/hábito": ["resistencia", "costumbre", "prefier", "no me gusta"],
            "Acceso/tecnología": ["internet", "equipo", "comput", "celular", "acceso"],
        }
        MEJ = {
            "Capacitación/acompañamiento": ["capacit", "taller", "acompañ", "apoyo", "guía", "guia"],
            "Plantillas/estándares": ["plantilla", "formato", "estándar", "estandar", "estructura"],
            "Mejoras técnicas": ["mejor", "rápido", "rapido", "erro", "interfaz", "plataforma"],
            "Lineamientos/seguimiento": ["lineamiento", "regla", "oblig", "supervis", "seguimiento"],
            "Simplificación": ["simple", "sencill", "fácil", "facil", "menos"],
        }

        colA, colB = st.columns(2)

        with colA:
            if col_benef in df_num.columns:
                df_b = _classify_text(df_num[col_benef], BENEF)
                _bar(df_b[df_b["Conteo"] > 0], "Categoría", "Conteo", "Beneficios (clasificación por tema)")
            else:
                st.info("No encontré la columna de Beneficios en el sheet.")

        with colB:
            if col_lim in df_num.columns:
                df_l = _classify_text(df_num[col_lim], LIM)
                _bar(df_l[df_l["Conteo"] > 0], "Categoría", "Conteo", "Limitaciones (clasificación por tema)")
            else:
                st.info("No encontré la columna de Limitaciones en el sheet.")

        st.divider()

        if col_mej in df_num.columns:
            df_m = _classify_text(df_num[col_mej], MEJ)
            _bar(df_m[df_m["Conteo"] > 0], "Categoría", "Conteo", "Mejoras sugeridas (clasificación por tema)")
        else:
            st.info("No encontré la columna de Mejoras en el sheet.")

    # -------------------
    # TAB 3: Detalle
    # -------------------
    with tab3:
        st.markdown("#### Detalle (respaldo)")
        st.caption("Incluye columnas numéricas derivadas para análisis.")

        # Descarga CSV del filtrado
        csv = df_num.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Descargar CSV (filtrado)",
            data=csv,
            file_name="aulas_virtuales_filtrado.csv",
            mime="text/csv",
        )

        st.dataframe(df_num, use_container_width=True)
