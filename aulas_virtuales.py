# aulas_virtuales.py
import streamlit as st
import pandas as pd
import gspread

SHEET_FORM = "AULAS_VIRTUALES_FORM"
SHEET_CATALOGO = "CAT_SERVICIOS_ESTRUCTURA"


def _get_av_url() -> str:
    url = st.secrets.get("AV_URL", "").strip()
    if not url:
        raise KeyError("Falta configurar AV_URL en Secrets.")
    return url


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm(t): t for t in titles}

    def resolve(sheet_name: str) -> str | None:
        return titles_norm.get(norm(sheet_name))

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


def _yesno_to_num(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.strip().str.lower()
    x = x.replace({"sí": "si"})  # normaliza acento
    return x.map({"si": 1, "no": 0})


def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    # ---------------------------
    # Carga
    # ---------------------------
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

    # Validaciones mínimas
    if "Indica el servicio" not in df_av.columns:
        st.error("En AULAS_VIRTUALES_FORM falta la columna exacta: 'Indica el servicio'")
        st.dataframe(df_av.head(20), use_container_width=True)
        return

    if "servicio" not in cat.columns:
        st.error("En CAT_SERVICIOS_ESTRUCTURA falta la columna exacta: 'servicio'")
        st.dataframe(cat.head(20), use_container_width=True)
        return

    # Normalización
    df_av = df_av.copy()
    cat = cat.copy()

    df_av["servicio_std"] = df_av["Indica el servicio"].astype(str).str.strip()
    cat["servicio_std"] = cat["servicio"].astype(str).str.strip()

    for col in ["escuela", "nivel", "tipo_unidad"]:
        if col not in cat.columns:
            cat[col] = pd.NA

    # Enriquecer
    df_av = df_av.merge(
        cat[["servicio_std", "escuela", "nivel", "tipo_unidad"]],
        on="servicio_std",
        how="left"
    )

    st.caption(
        "Nota: En este apartado el levantamiento se reporta por 'Servicio'. "
        "La selección superior se conserva para mantener la lógica de navegación."
    )

    # ---------------------------
    # Segundo selector (interno)
    # ---------------------------
    with st.container(border=True):
        st.markdown("**Filtro del apartado (Aulas Virtuales)**")

        servicio_base = (carrera or "").strip()

        # Escuela del servicio base (si existe)
        escuela_base = None
        if servicio_base:
            fila_base = cat[cat["servicio_std"] == servicio_base]
            if not fila_base.empty:
                escuela_base = fila_base.iloc[0].get("escuela")

        # Opciones
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

        # Default
        default_idx = 0
        if servicio_base and servicio_base in opciones:
            default_idx = opciones.index(servicio_base)

        servicio_av_sel = st.selectbox(
            "Selecciona el servicio a analizar (Aulas Virtuales)",
            options=opciones,
            index=default_idx
        )

    # Filtrado
    if servicio_av_sel.startswith("Todos los servicios de "):
        df_f = df_av[df_av["escuela"] == escuela_base].copy()
    else:
        df_f = df_av[df_av["servicio_std"] == servicio_av_sel].copy()

    st.write(f"Registros filtrados: **{len(df_f)}**")

    # KPIs básicos (si existen columnas)
    col_alumnos = "Los alumnos de su materia ¿Utilizan el Aula Virtual?"
    col_docente = "Usted como docente, ¿Utiliza el aula Virtual?"
    col_def = "¿Incluyó en su Aula Virtual la Definición del Curso con las secciones obligatorias al inicio del ciclo escolar?"
    col_bloques = "Durante el ciclo, ¿añadió las sesiones y bloques semanales en el Aula Virtual para registrar el desarrollo de sus clases?"

    kpis = []
    if col_alumnos in df_f.columns:
        kpis.append(("Alumnos usan Aula Virtual", col_alumnos))
    if col_docente in df_f.columns:
        kpis.append(("Docente usa Aula Virtual", col_docente))
    if col_def in df_f.columns:
        kpis.append(("Incluyó Definición del Curso", col_def))
    if col_bloques in df_f.columns:
        kpis.append(("Agregó sesiones/bloques", col_bloques))

    if kpis:
        cols = st.columns(len(kpis))
        for i, (label, colname) in enumerate(kpis):
            v = _yesno_to_num(df_f[colname])
            pct = (v.mean(skipna=True) * 100) if v.notna().any() else None
            cols[i].metric(label, f"{pct:.1f}%" if pct is not None else "—")

    st.divider()
    st.dataframe(df_f.head(30), use_container_width=True)
