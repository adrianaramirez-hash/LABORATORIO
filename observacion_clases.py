import streamlit as st
import pandas as pd
import gspread
import json
from collections.abc import Mapping
from google.oauth2.service_account import Credentials
import altair as alt

from catalogos import mapear_carrera_id

# --------------------------------------------------
# CONEXIÓN A GOOGLE SHEETS
# --------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


@st.cache_data(ttl=300, show_spinner=False)
def cargar_datos_desde_sheets():
    """
    Carga datos desde Google Sheets:
      - Hoja: 'Respuestas de formulario 1'
      - Hoja: 'Cortes'

    Robusto con st.secrets:
      - gcp_service_account_json puede venir como dict / AttrDict (Mapping) o string JSON.
    """
    raw = st.secrets["gcp_service_account_json"]
    creds_dict = dict(raw) if isinstance(raw, Mapping) else json.loads(raw)

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    # URL desde secrets (NO hardcode)
    SPREADSHEET_URL = st.secrets.get("OC_SHEET_URL", "").strip()
    if not SPREADSHEET_URL:
        raise KeyError("Falta configurar OC_SHEET_URL en Secrets (URL del Google Sheet).")

    sh = client.open_by_url(SPREADSHEET_URL)

    # Hoja de respuestas
    ws_resp = sh.worksheet("Respuestas de formulario 1")
    datos_resp = ws_resp.get_all_records()
    df_resp = pd.DataFrame(datos_resp)

    # Hoja de cortes
    ws_cortes = sh.worksheet("Cortes")
    datos_cortes = ws_cortes.get_all_records()
    df_cortes = pd.DataFrame(datos_cortes)

    return df_resp, df_cortes


# --------------------------------------------------
# CATÁLOGO (desde session_state)
# --------------------------------------------------
def _get_catalogo_carreras_df() -> pd.DataFrame:
    """
    Toma el catálogo maestro desde session_state.
    Requiere que app.py haga:
      st.session_state["df_cat_carreras"] = df_cat_carreras
    """
    df_cat = st.session_state.get("df_cat_carreras")
    if df_cat is None or getattr(df_cat, "empty", True):
        return pd.DataFrame()
    return df_cat


@st.cache_data(show_spinner=False, ttl=3600)
def _build_id_to_nombre_map(df_cat: pd.DataFrame) -> dict:
    if df_cat is None or df_cat.empty:
        return {}
    # Esperamos columnas: carrera_id, nombre_oficial
    d = {}
    if "carrera_id" in df_cat.columns and "nombre_oficial" in df_cat.columns:
        for _, r in df_cat[["carrera_id", "nombre_oficial"]].dropna().iterrows():
            d[str(r["carrera_id"]).strip()] = str(r["nombre_oficial"]).strip()
    return d


def _safe_mapear_carrera_id(area_text: str, df_cat: pd.DataFrame):
    if not area_text or df_cat.empty:
        return None
    try:
        return mapear_carrera_id(area_text, df_cat)
    except Exception:
        return None


def _nombre_oficial_from_id(carrera_id: str | None, id_to_nombre: dict) -> str:
    if not carrera_id:
        return ""
    return id_to_nombre.get(str(carrera_id).strip(), "")


# --------------------------------------------------
# FUNCIONES DE APOYO
# --------------------------------------------------
def respuesta_a_puntos(valor):
    """Convierte una respuesta (Sí / No / Sin evidencias / número) a puntos (1–3)."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip().lower()
    if texto in ["sí", "si", "x"]:
        return 3
    if "sin evidencia" in texto or "sin evidencias" in texto:
        return 2
    if texto == "no":
        return 1
    try:
        num = float(texto)
        return num
    except ValueError:
        return None


def clasificar_por_puntos(total_puntos):
    """Clasifica según el total o el promedio de puntos."""
    if pd.isna(total_puntos):
        return ""
    if total_puntos >= 97:
        return "Consolidado"
    elif total_puntos >= 76:
        return "En proceso"
    else:
        return "No consolidado"


def asignar_corte(fecha, df_cortes):
    """Devuelve el nombre de Corte según el rango de fechas en df_cortes."""
    if pd.isna(fecha) or df_cortes.empty:
        return "Sin corte"
    for _, fila in df_cortes.iterrows():
        fi = fila.get("Fecha_inicio")
        ff = fila.get("Fecha_fin")
        if pd.notna(fi) and pd.notna(ff) and fi <= fecha <= ff:
            return str(fila.get("Corte"))
    return "Sin corte"


def obtener_texto(fila, posibles_nombres):
    """Devuelve el valor de la primera columna que exista con texto no vacío."""
    for nombre in posibles_nombres:
        if nombre in fila.index:
            valor = fila[nombre]
            if isinstance(valor, str) and valor.strip():
                return valor
    return ""


# --------------------------------------------------
# FUNCIÓN PRINCIPAL DEL DASHBOARD
# --------------------------------------------------
def render_observacion_clases(vista: str = "Dirección General", carrera: str | None = None):
    # --------------------------------------------------
    # CARGA DE DATOS
    # --------------------------------------------------
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            df_respuestas, df_cortes = cargar_datos_desde_sheets()
    except Exception as e:
        st.error("No se pudieron cargar los datos desde Google Sheets.")
        st.exception(e)
        st.stop()

    if df_respuestas.empty:
        st.warning("La hoja de respuestas está vacía.")
        st.stop()

    st.subheader("Observación de clases — Reportes por corte")

    # --------------------------------------------------
    # LIMPIEZA BÁSICA DE DATOS
    # --------------------------------------------------
    col_fecha = "Fecha" if "Fecha" in df_respuestas.columns else "Marca temporal"
    df_respuestas[col_fecha] = pd.to_datetime(df_respuestas[col_fecha], errors="coerce", dayfirst=True)

    # Columnas clave
    COL_SERVICIO = "Indica el servicio"
    COL_DOCENTE = "Nombre del docente"

    for col in [COL_SERVICIO, COL_DOCENTE]:
        if col not in df_respuestas.columns:
            st.error(f"No se encontró la columna '{col}' en la hoja de respuestas.")
            st.stop()

    # --------------------------------------------------
    # CATÁLOGO: construir CARRERA_ID y NOMBRE_OFICIAL
    # --------------------------------------------------
    df_cat = _get_catalogo_carreras_df()
    id_to_nombre = _build_id_to_nombre_map(df_cat) if not df_cat.empty else {}

    df_respuestas = df_respuestas.copy()
    if not df_cat.empty:
        df_respuestas["CARRERA_ID"] = df_respuestas[COL_SERVICIO].astype(str).apply(lambda x: _safe_mapear_carrera_id(x, df_cat))
        df_respuestas["NOMBRE_OFICIAL"] = df_respuestas["CARRERA_ID"].apply(lambda cid: _nombre_oficial_from_id(cid, id_to_nombre))  # para mostrar consistente
    else:
        df_respuestas["CARRERA_ID"] = None
        df_respuestas["NOMBRE_OFICIAL"] = df_respuestas[COL_SERVICIO].astype(str)

    # Hoja de cortes: convertir fechas (dayfirst=True)
    if not df_cortes.empty:
        df_cortes["Fecha_inicio"] = pd.to_datetime(df_cortes["Fecha_inicio"], errors="coerce", dayfirst=True)
        df_cortes["Fecha_fin"] = pd.to_datetime(df_cortes["Fecha_fin"], errors="coerce", dayfirst=True)
    else:
        df_cortes = pd.DataFrame(columns=["Corte", "Fecha_inicio", "Fecha_fin"])

    # Crear columna de Corte para cada observación
    df_respuestas["Corte"] = df_respuestas[col_fecha].apply(lambda f: asignar_corte(f, df_cortes))

    # --------------------------------------------------
    # SELECCIÓN DE COLUMNAS DE PUNTAJE
    # --------------------------------------------------
    todas_cols = list(df_respuestas.columns)

    # Ajustado al esquema actual: de M a AZ
    start_idx = 12  # columna M (índice 12)
    end_idx = 52    # hasta AZ (exclusivo)
    cols_puntaje = todas_cols[start_idx:end_idx]

    AREAS = {
        "A. Planeación de sesión en el aula virtual": cols_puntaje[0:14],
        "B. Presentación y desarrollo de la sesión": cols_puntaje[14:30],
        "C. Dinámicas interpersonales": cols_puntaje[30:34],
        "D. Administración de la sesión": cols_puntaje[34:40],
    }

    NUM_REACTIVOS = len(cols_puntaje)
    PUNTAJE_MAX_REACTIVO = 3
    PUNTAJE_MAX_OBS = NUM_REACTIVOS * PUNTAJE_MAX_REACTIVO if NUM_REACTIVOS > 0 else 0

    # --------------------------------------------------
    # CÁLCULO DE PUNTOS Y CLASIFICACIÓN (EN TODO EL DF)
    # --------------------------------------------------
    def calcular_total_puntos_fila(row):
        total = 0
        for col in cols_puntaje:
            if col not in row.index:
                continue
            puntos = respuesta_a_puntos(row[col])
            if puntos is not None:
                total += puntos
        return total

    df_respuestas["Total_puntos_observación"] = df_respuestas.apply(calcular_total_puntos_fila, axis=1)
    df_respuestas["Clasificación_observación"] = df_respuestas["Total_puntos_observación"].apply(clasificar_por_puntos)

    # --------------------------------------------------
    # CUADRO DE INFORMACIÓN SOBRE PUNTAJE
    # --------------------------------------------------
    with st.expander("ℹ️ ¿Cómo se calcula el puntaje y la clasificación?", expanded=False):
        if PUNTAJE_MAX_OBS > 0:
            st.markdown(
                f"""
**Instrumento de observación**

- Número de reactivos evaluados: **{NUM_REACTIVOS}**  
- Puntaje por respuesta:
  - **Sí** → 3 puntos  
  - **Sin evidencia** → 2 puntos  
  - **No** → 1 punto  

- Puntaje máximo por observación (si se contestan todos los reactivos):  
  **{PUNTAJE_MAX_OBS} puntos**

**Clasificación (observación y docente)**  

- **Consolidado** → 97 puntos o más  
- **En proceso** → de 76 a 96 puntos  
- **No consolidado** → 75 puntos o menos  

En el caso de los **docentes**, se usa el **promedio de puntos por observación** dentro del filtro seleccionado.
"""
            )
        else:
            st.write("No fue posible calcular el puntaje máximo porque no se detectaron columnas de rúbrica.")

    st.divider()

    # --------------------------------------------------
    # FILTROS
    # --------------------------------------------------
    st.markdown("### Filtros")

    # Opciones de cortes
    opciones_cortes = ["Todos los cortes"]
    if not df_cortes.empty and "Corte" in df_cortes.columns:
        opciones_cortes += list(df_cortes["Corte"].astype(str))

    # Agregamos explícitamente la opción "Sin corte" si existe en los datos
    if "Sin corte" in df_respuestas["Corte"].unique():
        opciones_cortes.append("Sin corte")

    col_f1, col_f2, col_f3 = st.columns(3)

    with col_f1:
        corte_seleccionado = st.selectbox("Corte", opciones_cortes)

    # Dataframe base para construir opciones de servicio
    df_para_filtros = df_respuestas.copy()
    if corte_seleccionado != "Todos los cortes":
        df_para_filtros = df_para_filtros[df_para_filtros["Corte"] == corte_seleccionado]

    # Vista Director: fijar por CARRERA_ID si hay catálogo; si no, cae a texto
    carrera_id_fix = None
    if vista == "Director de carrera" and carrera:
        if not df_cat.empty:
            carrera_id_fix = _safe_mapear_carrera_id(str(carrera), df_cat)

    if carrera_id_fix:
        df_para_filtros = df_para_filtros[df_para_filtros["CARRERA_ID"] == carrera_id_fix]
    elif vista == "Director de carrera" and carrera:
        # fallback (solo si no hubo catálogo o no mapeó)
        df_para_filtros = df_para_filtros[df_para_filtros[COL_SERVICIO].astype(str).str.strip() == str(carrera).strip()]

    # Selector de servicio (DG) con nombres oficiales si hay catálogo
    if vista == "Director de carrera" and carrera:
        with col_f2:
            st.markdown(f"**Servicio:** {carrera} (vista Director de carrera)")
        servicio_seleccionado = "(director)"
        servicio_id_sel = None
    else:
        if not df_cat.empty:
            # Mostramos oficiales; incluimos solo los que existan en datos filtrables
            ids_presentes = sorted(df_para_filtros["CARRERA_ID"].dropna().astype(str).unique().tolist())
            oficiales_presentes = [id_to_nombre.get(cid, cid) for cid in ids_presentes]
            opciones_serv = ["Todos los servicios"] + sorted(set([o for o in oficiales_presentes if o]))
            with col_f2:
                servicio_sel_oficial = st.selectbox("Servicio", opciones_serv)
            servicio_seleccionado = servicio_sel_oficial
            servicio_id_sel = None
            if servicio_sel_oficial != "Todos los servicios":
                # obtener id por nombre seleccionado (invertimos contra el diccionario)
                inv = {v: k for k, v in id_to_nombre.items() if v}
                servicio_id_sel = inv.get(servicio_sel_oficial)
        else:
            servicios_base = sorted(df_para_filtros[COL_SERVICIO].dropna().unique().tolist())
            servicios_disponibles = ["Todos los servicios"] + servicios_base
            with col_f2:
                servicio_seleccionado = st.selectbox("Servicio", servicios_disponibles)
            servicio_id_sel = None

    # Filtro adicional opcional: tipo de observación (si existe la columna)
    tipo_obs_col = None
    if "Tipo de observación" in df_respuestas.columns:
        tipo_obs_col = "Tipo de observación"
    elif "Tipo de observación " in df_respuestas.columns:
        tipo_obs_col = "Tipo de observación "

    if tipo_obs_col:
        tipos_disponibles = ["Todos los tipos"] + sorted(df_para_filtros[tipo_obs_col].dropna().unique().tolist())
        with col_f3:
            tipo_seleccionado = st.selectbox("Tipo de observación", tipos_disponibles)
    else:
        tipo_seleccionado = "Todos los tipos"

    # --------------------------------------------------
    # APLICAR FILTROS
    # --------------------------------------------------
    df_filtrado = df_respuestas.copy()

    # Filtro por corte
    if corte_seleccionado != "Todos los cortes":
        df_filtrado = df_filtrado[df_filtrado["Corte"] == corte_seleccionado]

    # Filtro por servicio
    if carrera_id_fix:
        df_filtrado = df_filtrado[df_filtrado["CARRERA_ID"] == carrera_id_fix]
    elif vista == "Director de carrera" and carrera:
        df_filtrado = df_filtrado[df_filtrado[COL_SERVICIO].astype(str).str.strip() == str(carrera).strip()]
    else:
        if not df_cat.empty and servicio_id_sel and servicio_seleccionado != "Todos los servicios":
            df_filtrado = df_filtrado[df_filtrado["CARRERA_ID"].astype(str) == str(servicio_id_sel)]
        elif df_cat.empty and servicio_seleccionado != "Todos los servicios":
            df_filtrado = df_filtrado[df_filtrado[COL_SERVICIO] == servicio_seleccionado]

    # Filtro por tipo de observación
    if tipo_seleccionado != "Todos los tipos" and tipo_obs_col:
        df_filtrado = df_filtrado[df_filtrado[tipo_obs_col] == tipo_seleccionado]

    if df_filtrado.empty:
        st.warning("No hay observaciones para el filtro seleccionado.")
        st.stop()

    rango_fechas = df_filtrado[col_fecha].agg(["min", "max"])
    st.caption(
        f"Observaciones en el filtro actual: **{len(df_filtrado)}**  "
        f"| Rango de fechas: {rango_fechas['min'].date() if pd.notna(rango_fechas['min']) else '—'} "
        f"a {rango_fechas['max'].date() if pd.notna(rango_fechas['max']) else '—'}"
    )

    st.divider()

    # --------------------------------------------------
    # KPIs GENERALES
    # --------------------------------------------------
    df_base = df_filtrado.copy()
    total_obs = len(df_base)

    n_consol = (df_base["Clasificación_observación"] == "Consolidado").sum()
    n_proceso = (df_base["Clasificación_observación"] == "En proceso").sum()
    n_no = (df_base["Clasificación_observación"] == "No consolidado").sum()

    pct_consol = n_consol * 100 / total_obs if total_obs > 0 else 0
    pct_proceso = n_proceso * 100 / total_obs if total_obs > 0 else 0
    pct_no = n_no * 100 / total_obs if total_obs > 0 else 0

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    with col_kpi1:
        st.metric("Obs. totales", total_obs)
    with col_kpi2:
        st.metric("% Consolidado", f"{pct_consol:.0f} %")
    with col_kpi3:
        st.metric("% En proceso", f"{pct_proceso:.0f} %")
    with col_kpi4:
        st.metric("% No consolidado", f"{pct_no:.0f} %")

    st.divider()

    # --------------------------------------------------
    # TABS PRINCIPALES
    # --------------------------------------------------
    tab_resumen, tab_servicios, tab_docentes, tab_detalle = st.tabs(
        ["Resumen general", "Por servicio", "Por docente", "Detalle por docente"]
    )

    # --------------------------------------------------
    # TAB 1: RESUMEN GENERAL (Evolución por corte)
    # --------------------------------------------------
    with tab_resumen:
        st.subheader("Evolución de la clasificación por corte")

        df_trend = df_respuestas.copy()

        # Aplicar filtros de servicio
        if carrera_id_fix:
            df_trend = df_trend[df_trend["CARRERA_ID"] == carrera_id_fix]
        elif vista == "Director de carrera" and carrera:
            df_trend = df_trend[df_trend[COL_SERVICIO].astype(str).str.strip() == str(carrera).strip()]
        else:
            if not df_cat.empty and servicio_id_sel and servicio_seleccionado != "Todos los servicios":
                df_trend = df_trend[df_trend["CARRERA_ID"].astype(str) == str(servicio_id_sel)]
            elif df_cat.empty and servicio_seleccionado != "Todos los servicios":
                df_trend = df_trend[df_trend[COL_SERVICIO] == servicio_seleccionado]

        # Filtro por tipo de observación
        if tipo_seleccionado != "Todos los tipos" and tipo_obs_col:
            df_trend = df_trend[df_trend[tipo_obs_col] == tipo_seleccionado]

        # Para la gráfica excluimos "Sin corte"
        df_trend = df_trend[df_trend["Corte"] != "Sin corte"]

        if not df_trend.empty:
            df_graf_cortes = (
                df_trend.groupby(["Corte", "Clasificación_observación"])
                .size()
                .reset_index(name="conteo")
            )
            totales_corte = df_graf_cortes.groupby("Corte")["conteo"].transform("sum")
            df_graf_cortes["porcentaje"] = df_graf_cortes["conteo"] * 100 / totales_corte

            chart_cortes = (
                alt.Chart(df_graf_cortes)
                .mark_bar()
                .encode(
                    x=alt.X("Corte:N", title="Corte"),
                    y=alt.Y("porcentaje:Q", title="Porcentaje"),
                    color=alt.Color("Clasificación_observación:N", title="Clasificación"),
                    tooltip=[
                        "Corte",
                        "Clasificación_observación",
                        alt.Tooltip("porcentaje:Q", format=".1f", title="Porcentaje (%)"),
                        "conteo",
                    ],
                )
                .properties(height=300)
            )
            st.altair_chart(chart_cortes, use_container_width=True)
        else:
            st.info("No hay información suficiente para mostrar la evolución por corte.")

    # --------------------------------------------------
    # TAB 2: POR SERVICIO
    # --------------------------------------------------
    with tab_servicios:
        st.subheader("Clasificación por servicio")

        if total_obs > 0:
            # Si hay catálogo, agrupamos por NOMBRE_OFICIAL (más limpio)
            group_col = "NOMBRE_OFICIAL" if not df_cat.empty else COL_SERVICIO

            df_graf = (
                df_base.groupby([group_col, "Clasificación_observación"])
                .size()
                .reset_index(name="conteo")
            )

            totales_serv = df_graf.groupby(group_col)["conteo"].transform("sum")
            df_graf["porcentaje"] = df_graf["conteo"] * 100 / totales_serv

            chart = (
                alt.Chart(df_graf)
                .mark_bar()
                .encode(
                    x=alt.X(f"{group_col}:N", title="Servicio"),
                    y=alt.Y("porcentaje:Q", title="Porcentaje"),
                    color=alt.Color("Clasificación_observación:N", title="Clasificación"),
                    tooltip=[
                        group_col,
                        "Clasificación_observación",
                        alt.Tooltip("porcentaje:Q", format=".1f", title="Porcentaje (%)"),
                        "conteo",
                    ],
                )
                .properties(height=300)
            )

            st.altair_chart(chart, use_container_width=True)

        st.markdown("#### Resumen por servicio")

        group_col = "NOMBRE_OFICIAL" if not df_cat.empty else COL_SERVICIO

        resumen_servicio = (
            df_filtrado.groupby(group_col)
            .agg(
                Observaciones=("Total_puntos_observación", "count"),
                Docentes_observados=(COL_DOCENTE, "nunique"),
                Total_puntos=("Total_puntos_observación", "sum"),
            )
            .reset_index()
            .rename(columns={group_col: "Servicio"})
        )

        resumen_servicio["Promedio_puntos_por_obs"] = (
            resumen_servicio["Total_puntos"] / resumen_servicio["Observaciones"]
        )

        st.dataframe(resumen_servicio, use_container_width=True)

    # --------------------------------------------------
    # TAB 3: POR DOCENTE
    # --------------------------------------------------
    with tab_docentes:
        st.subheader("Resumen por docente (en el filtro seleccionado)")

        resumen_docente = (
            df_filtrado.groupby(COL_DOCENTE)
            .agg(
                N_observaciones=("Total_puntos_observación", "count"),
                Total_puntos=("Total_puntos_observación", "sum"),
            )
            .reset_index()
        )

        resumen_docente["Promedio_puntos_por_obs"] = (
            resumen_docente["Total_puntos"] / resumen_docente["N_observaciones"]
        )

        resumen_docente["Clasificación_docente"] = resumen_docente["Promedio_puntos_por_obs"].apply(
            clasificar_por_puntos
        )

        cat_tipo = pd.CategoricalDtype(["Consolidado", "En proceso", "No consolidado"], ordered=True)
        resumen_docente["Clasificación_docente"] = resumen_docente["Clasificación_docente"].astype(cat_tipo)

        resumen_docente = resumen_docente.sort_values(
            ["Clasificación_docente", "Promedio_puntos_por_obs"],
            ascending=[True, False],
        )

        st.dataframe(resumen_docente, use_container_width=True)

    # --------------------------------------------------
    # TAB 4: DETALLE POR DOCENTE
    # --------------------------------------------------
    with tab_detalle:
        st.subheader("Historial y detalle de observaciones por docente")

        resumen_docente = (
            df_filtrado.groupby(COL_DOCENTE)
            .agg(
                N_observaciones=("Total_puntos_observación", "count"),
                Total_puntos=("Total_puntos_observación", "sum"),
            )
            .reset_index()
        )

        docentes_lista = sorted(resumen_docente[COL_DOCENTE].dropna().unique().tolist())

        docente_sel = st.selectbox("Selecciona un docente", ["(ninguno)"] + docentes_lista)

        if docente_sel != "(ninguno)":
            df_doc = df_filtrado[df_filtrado[COL_DOCENTE] == docente_sel].copy()
            df_doc = df_doc.sort_values(col_fecha)

            etiqueta_base = df_doc[col_fecha].dt.strftime("%Y-%m-%d").fillna("sin fecha")
            if "Grupo" in df_doc.columns:
                etiqueta_base = (
                    etiqueta_base
                    + " | "
                    + (df_doc["NOMBRE_OFICIAL"].fillna(df_doc[COL_SERVICIO]).astype(str) if not df_cat.empty else df_doc[COL_SERVICIO].astype(str))
                    + " | Grupo: "
                    + df_doc["Grupo"].astype(str)
                )
            else:
                etiqueta_base = etiqueta_base + " | " + (df_doc["NOMBRE_OFICIAL"].fillna(df_doc[COL_SERVICIO]).astype(str) if not df_cat.empty else df_doc[COL_SERVICIO].astype(str))

            df_doc["Etiqueta_obs"] = etiqueta_base

            cols_hist = [
                col_fecha,
                "NOMBRE_OFICIAL" if not df_cat.empty else COL_SERVICIO,
                COL_SERVICIO if not df_cat.empty else None,  # opcional: mostrar el texto original si quieres
                "Grupo",
                "Total_puntos_observación",
                "Clasificación_observación",
                "Corte",
            ]
            cols_hist = [c for c in cols_hist if c and c in df_doc.columns]

            st.markdown(f"**Observaciones de {docente_sel} en el filtro actual:**")
            st.dataframe(df_doc[cols_hist], use_container_width=True)

            idx_sel = st.selectbox(
                "Elige una observación para ver el detalle por área",
                df_doc.index,
                format_func=lambda i: df_doc.loc[i, "Etiqueta_obs"],
            )

            fila_obs = df_doc.loc[idx_sel]

            # -------------------------
            # Resumen por áreas (todas las observaciones del docente)
            # -------------------------
            def calcular_resumen_areas(df, columnas_area):
                puntos_totales = 0
                max_puntos = 0
                for col in columnas_area:
                    if col in df.columns:
                        serie = df[col].apply(respuesta_a_puntos)
                        puntos_totales += serie.fillna(0).sum()
                        max_puntos += 3 * serie.notna().sum()
                porcentaje = puntos_totales * 100 / max_puntos if max_puntos > 0 else None
                return puntos_totales, max_puntos, porcentaje

            resumen_areas_global = []
            for area, columnas in AREAS.items():
                p_tot, p_max, p_pct = calcular_resumen_areas(df_doc, columnas)
                resumen_areas_global.append(
                    {
                        "Área": area,
                        "Puntos (todas las observaciones)": p_tot,
                        "Máx. posible": p_max,
                        "% logro": p_pct,
                    }
                )

            df_areas_global = pd.DataFrame(resumen_areas_global)

            st.subheader("Resumen por área del docente (todas las observaciones)")
            st.dataframe(df_areas_global, use_container_width=True)

            chart_areas_global = (
                alt.Chart(df_areas_global)
                .mark_bar()
                .encode(
                    x=alt.X("Área:N", title="Área evaluada"),
                    y=alt.Y("% logro:Q", title="% de logro"),
                    tooltip=["Área", "Puntos (todas las observaciones)", "Máx. posible", "% logro"],
                )
                .properties(height=300)
            )
            st.altair_chart(chart_areas_global, use_container_width=True)

            # -------------------------
            # Detalle por área de la observación seleccionada
            # -------------------------
            resumen_areas_obs = []
            for area, columnas in AREAS.items():
                puntos = 0
                max_puntos = 0
                for col in columnas:
                    if col in fila_obs.index:
                        p = respuesta_a_puntos(fila_obs[col])
                        if p is not None:
                            puntos += p
                            max_puntos += 3
                porcentaje = puntos * 100 / max_puntos if max_puntos > 0 else None
                resumen_areas_obs.append(
                    {
                        "Área": area,
                        "Puntos": puntos,
                        "Máx. posible": max_puntos,
                        "% logro": porcentaje,
                    }
                )

            df_areas_obs = pd.DataFrame(resumen_areas_obs)

            st.subheader("Detalle por área de la observación seleccionada")
            st.dataframe(df_areas_obs, use_container_width=True)

            chart_areas_obs = (
                alt.Chart(df_areas_obs)
                .mark_bar()
                .encode(
                    x=alt.X("Área:N", title="Área evaluada"),
                    y=alt.Y("% logro:Q", title="% de logro"),
                    tooltip=["Área", "Puntos", "Máx. posible", "% logro"],
                )
                .properties(height=300)
            )
            st.altair_chart(chart_areas_obs, use_container_width=True)

            # -------------------------
            # Comentarios cualitativos
            # -------------------------
            st.subheader("Comentarios cualitativos de la observación seleccionada")

            fortalezas = obtener_texto(
                fila_obs,
                [
                    "Fortalezas observadas en la sesión",
                    "Fortalezas observadas en la sesión ",
                    "Fortalezas",
                ],
            )
            areas_op = obtener_texto(
                fila_obs,
                [
                    "Áreas de oportunidad observadas en la sesión",
                    "Areas de oportunidad observadas en la sesión",
                    "Áreas de oportunidad",
                ],
            )
            recom = obtener_texto(
                fila_obs,
                [
                    "Recomendaciones generales para la mejora continua",
                    "Recomendaciones generales",
                ],
            )

            st.markdown("**Fortalezas observadas:**")
            st.write(fortalezas if fortalezas else "—")

            st.markdown("**Áreas de oportunidad observadas:**")
            st.write(areas_op if areas_op else "—")

            st.markdown("**Recomendaciones generales para la mejora continua:**")
            st.write(recom if recom else "—")
