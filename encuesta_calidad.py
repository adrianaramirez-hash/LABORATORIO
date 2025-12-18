import streamlit as st
import pandas as pd
import gspread
import json
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Modalidades disponibles en el módulo
MODALIDADES = [
    "Virtual / Mixto",
    "Escolarizado / Ejecutivas",
    "Preparatoria",
]

# Keys en Secrets
URL_KEYS = {
    "Virtual / Mixto": "EC_VIRTUAL_URL",
    "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
    "Preparatoria": "EC_PREPA_URL",
}

SHEET_PROCESADO = "PROCESADO"
SHEET_CATALOGO = "Catalogo_Servicio"


# ----------------------------
# Conexión Google Sheets
# ----------------------------
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


def _open_sheet_by_modalidad(modalidad: str) -> gspread.Spreadsheet:
    key = URL_KEYS.get(modalidad)
    if not key:
        raise ValueError(f"Modalidad no reconocida: {modalidad}")

    url = st.secrets.get(key, "").strip()
    if not url:
        raise KeyError(f"Falta configurar {key} en Secrets.")

    client = _get_gspread_client()
    return client.open_by_url(url)


@st.cache_data(ttl=300)
def _read_ws_df_by_url(url: str, worksheet_name: str) -> pd.DataFrame:
    """
    Cache por URL+worksheet para no consumir API de más.
    """
    client = _get_gspread_client()
    sh = client.open_by_url(url)
    ws = sh.worksheet(worksheet_name)
    data = ws.get_all_records()
    return pd.DataFrame(data)


def _get_url_for_modalidad(modalidad: str) -> str:
    key = URL_KEYS.get(modalidad)
    url = st.secrets.get(key, "").strip()
    if not url:
        raise KeyError(f"Falta configurar {key} en Secrets.")
    return url


# ----------------------------
# Utilidades de filtrado
# ----------------------------
def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _pick_servicio_col(df: pd.DataFrame) -> str | None:
    # Prioridades típicas en tus datos
    for c in ["Servicio", "Indica el servicio", "Carrera_Catalogo", "Carrera", "Programa"]:
        if c in df.columns:
            return c
    return None


def _normalize(s: object) -> str:
    return str(s).strip().lower() if pd.notna(s) else ""


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    """
    Reglas acordadas:
    - Dirección General: elige manualmente en el módulo.
    - Director de carrera:
        1) Si empieza con 'Lic. Ejecutiva:' -> Escolarizado/Ejecutivas
        2) Si pertenece a Virtual/Mixto (lista futura) -> Virtual/Mixto
        3) Default -> Escolarizado/Ejecutivas
    """
    if vista == "Dirección General":
        return ""

    if carrera and _normalize(carrera).startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"

    # Si después nos pasas la lista de carreras Virtual/Mixto, la insertamos aquí.
    return "Escolarizado / Ejecutivas"


def _year_options(df: pd.DataFrame, fecha_col: str) -> list[int]:
    d = df.copy()
    d[fecha_col] = pd.to_datetime(d[fecha_col], errors="coerce", dayfirst=True)
    years = sorted([int(y) for y in d[fecha_col].dropna().dt.year.unique().tolist()])
    return years


# ----------------------------
# Entry point del módulo
# ----------------------------
def render_encuesta_calidad(vista: str, carrera: str | None):
    st.header("Encuesta de calidad")

    # 1) Selección / resolución de modalidad
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", MODALIDADES, index=1)
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    # 2) Carga de PROCESADO
    try:
        url = _get_url_for_modalidad(modalidad)
        df = _read_ws_df_by_url(url, SHEET_PROCESADO)
    except Exception as e:
        st.error("No se pudo cargar la hoja PROCESADO del Google Sheet.")
        st.exception(e)
        st.stop()

    if df.empty:
        st.warning("PROCESADO está vacío.")
        st.stop()

    # 3) Año (filtro anual)
    fecha_col = _pick_fecha_col(df)
    if not fecha_col:
        st.warning("No encontré columna de fecha (Marca temporal/Fecha) en PROCESADO. No puedo filtrar por año.")
        # Aun así mostramos preview básico
        st.dataframe(df.head(30), use_container_width=True)
        st.stop()

    years = _year_options(df, fecha_col)
    if not years:
        st.warning("No pude detectar años válidos en la columna de fecha.")
        st.dataframe(df.head(30), use_container_width=True)
        st.stop()

    year_sel = st.selectbox("Año", years, index=len(years) - 1)

    dff = df.copy()
    dff[fecha_col] = pd.to_datetime(dff[fecha_col], errors="coerce", dayfirst=True)
    dff = dff[dff[fecha_col].dt.year == year_sel]

    # 4) Servicio/Carrera (solo Dirección General, y solo si existe columna)
    serv_col = _pick_servicio_col(dff)

    if vista == "Director de carrera":
        # filtro fijo a carrera si hay columna de servicio
        if serv_col and carrera:
            # igualamos por texto exacto; si te conviene normalizar, lo hacemos después
            dff = dff[dff[serv_col].astype(str) == str(carrera)]
            st.caption(f"Filtro fijo: **{serv_col} = {carrera}**")
    else:
        # Dirección General: selector si existe columna
        if serv_col:
            opciones = sorted([x for x in dff[serv_col].dropna().astype(str).unique().tolist()])
            opciones = ["Todos"] + opciones
            sel = st.selectbox("Servicio/Carrera", opciones)
            if sel != "Todos":
                dff = dff[dff[serv_col].astype(str) == sel]
        else:
            st.info("No se encontró columna Servicio/Carrera en PROCESADO para habilitar ese filtro.")

    # 5) Smoke test + KPIs base (para confirmar que ya está bien conectado)
    if dff.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        st.stop()

    st.divider()
    st.subheader("Resumen del filtro")
    st.write(f"- Modalidad: **{modalidad}**")
    st.write(f"- Año: **{year_sel}**")
    st.write(f"- Registros: **{len(dff)}**")

    # Rango de fechas dentro del año
    fmin = dff[fecha_col].min()
    fmax = dff[fecha_col].max()
    if pd.notna(fmin) and pd.notna(fmax):
        st.caption(f"Rango de fechas: {fmin.date()} a {fmax.date()}")

    # Identificar columnas numéricas (_num) para KPIs
    num_cols = [c for c in dff.columns if str(c).endswith("_num")]
    if not num_cols:
        st.warning("No encontré columnas *_num en PROCESADO. Verifica que el procesamiento haya generado numéricos.")
        st.dataframe(dff.head(30), use_container_width=True)
        st.stop()

    # Promedio general (sobre todos los reactivos numéricos)
    dff_num = dff[num_cols].apply(pd.to_numeric, errors="coerce")
    promedio_general = float(dff_num.stack().mean()) if dff_num.size else None
    n_validos = int(dff_num.stack().notna().sum())
    n_tot = int(dff_num.size)
    pct_na = (1 - (n_validos / n_tot)) * 100 if n_tot else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Promedio general", f"{promedio_general:.2f}" if promedio_general is not None else "—")
    with c2:
        st.metric("Respuestas válidas (num)", f"{n_validos:,}")
    with c3:
        st.metric("% No aplica / NA", f"{pct_na:.1f}%")

    st.divider()
    st.subheader("Vista previa (PROCESADO filtrado)")
    st.dataframe(dff.head(50), use_container_width=True)

    st.info(
        "Conectividad lista. El siguiente paso es construir los resultados por Sección/Reactivo "
        "usando la hoja Mapa_Preguntas para agrupar y etiquetar."
    )
