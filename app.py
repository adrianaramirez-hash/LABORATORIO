import streamlit as st
import pandas as pd
import gspread
import json
from google.oauth2.service_account import Credentials

import encuesta_calidad

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="Dirección Académica", layout="wide")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

SHEET_ESCOLAR_PROCESADO = "PROCESADO"
COL_CARRERA_CATALOGO = "Carrera_Catalogo"

# Fallback (solo si falla lectura de Google Sheets)
FALLBACK_CARRERAS = [
    "Actuación",
    "Administración de Empresas",
    "Cine y TV Digital",
    "Comunicación Multimedia",
    "Contaduría",
    "Creación y Gestión de Empresas Turísticas",
    "Derecho",
    "Diseño de Modas",
    "Diseño Gráfico",
    "Finanzas",
    "Gastronomía",
    "Mercadotecnia",
    "Nutrición",
    "Pedagogía",
    "Psicología",
    "Tecnologías de la Información",
    "Lic. Ejecutiva: Administración de Empresas",
    "Lic. Ejecutiva: Contaduría",
    "Lic. Ejecutiva: Derecho",
    "Lic. Ejecutiva: Informática",
    "Lic. Ejecutiva: Mercadotecnia",
    "Lic. Ejecutiva: Pedagogía",
    "Maestría en Administración de Negocios (MBA)",
    "Maestría en Derecho Corporativo",
    "Maestría en Desarrollo del Potencial Humano y Organizacional",
    "Maestría en Odontología Legal y Forense",
    "Maestría en Psicoterapia Familiar",
    "Maestría en Psicoterapia Psicoanalítica",
    "Maestría en Administración de Recursos Humanos",
    "Maestría en Finanzas",
    "Maestría: Tecnología e Innovación Educativa",
    "Maestría en Educación Especial",
    "Maestría: Entrenamiento Deportivo",
    "Licenciatura Entrenamiento Deportivo",
]


# =========================
# HELPERS: Google Sheets
# =========================
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


def _make_unique_headers(raw_headers: list[str]) -> list[str]:
    """
    Evita choques por encabezados duplicados.
    """
    seen = {}
    out = []
    for h in raw_headers:
        base = (h or "").strip()
        if base == "":
            base = "SIN_TITULO"
        seen[base] = seen.get(base, 0) + 1
        if seen[base] == 1:
            out.append(base)
        else:
            out.append(f"{base} ({seen[base]})")
    return out


@st.cache_data(ttl=600, show_spinner=False)
def _load_carreras_catalogo_from_escolar() -> list[str]:
    """
    Lee Carrera_Catalogo desde:
    Secrets -> EC_ESCOLAR_URL -> pestaña PROCESADO
    """
    url = (st.secrets.get("EC_ESCOLAR_URL", "") or "").strip()
    if not url:
        raise KeyError('Falta configurar "EC_ESCOLAR_URL" en Secrets.')

    client = _get_gspread_client()
    sh = client.open_by_url(url)
    ws = sh.worksheet(SHEET_ESCOLAR_PROCESADO)

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return []

    headers = _make_unique_headers(values[0])
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    if COL_CARRERA_CATALOGO not in df.columns:
        raise KeyError(
            f"No encontré la columna '{COL_CARRERA_CATALOGO}' en {SHEET_ESCOLAR_PROCESADO}."
        )

    carreras = (
        df[COL_CARRERA_CATALOGO]
        .dropna()
        .astype(str)
        .map(lambda s: s.strip())
        .loc[lambda s: s != ""]
        .unique()
        .tolist()
    )

    carreras = sorted(carreras)
    return carreras


# =========================
# UI: Header
# =========================
logo_url = "udl_logo.png"

col1, col2 = st.columns([1, 4])
with col1:
    st.image(logo_url, use_container_width=True)

with col2:
    st.title("Dirección Académica")
    st.write("Seguimiento del Plan Anual.")

st.divider()

# =========================
# UI: Vista + Carrera
# =========================
vista = st.selectbox(
    "Selecciona la vista:",
    ["Dirección General", "Director de carrera"],
)

carrera = None
if vista == "Director de carrera":
    # Intentar cargar lista real desde PROCESADO (Escolarizado)
    try:
        carreras_disponibles = _load_carreras_catalogo_from_escolar()
        if not carreras_disponibles:
            carreras_disponibles = FALLBACK_CARRERAS
            st.warning(
                "No se detectaron carreras en PROCESADO (Escolarizado). "
                "Usando lista local de respaldo."
            )
    except Exception as e:
        carreras_disponibles = FALLBACK_CARRERAS
        st.warning(
            "No se pudo cargar la lista de carreras desde PROCESADO (Escolarizado). "
            "Usando lista local de respaldo."
        )
        st.caption(f"Detalle técnico: {type(e).__name__}: {e}")

    carrera = st.selectbox("Selecciona la carrera:", carreras_disponibles)

st.divider()

# =========================
# UI: Menú de secciones
# =========================
seccion = st.selectbox(
    "Selecciona el apartado del plan anual que deseas revisar:",
    [
        "Observación de clases",
        "Encuesta de calidad",
        "Evaluación docente",
        "Capacitaciones",
        "Índice de reprobación",
        "Titulación",
        "Ceneval",
        "Exámenes departamentales",
        "Aulas virtuales",
    ],
)

st.divider()

# =========================
# Router
# =========================
if seccion == "Encuesta de calidad":
    encuesta_calidad.render_encuesta_calidad(vista=vista, carrera=carrera)

elif seccion == "Observación de clases":
    st.warning(
        "Observación de clases está temporalmente deshabilitado mientras estabilizamos la conexión a datos."
    )

elif seccion == "Evaluación docente":
    st.info("Módulo en construcción: Evaluación docente")

else:
    st.subheader("Panel inicial")
    st.write(f"Vista actual: **{vista}**")
    if carrera:
        st.write(f"Carrera seleccionada: **{carrera}**")
    else:
        st.write("Carrera seleccionada: *no aplica para esta vista*")
    st.write(f"Apartado seleccionado: **{seccion}**")
    st.info(
        "En los siguientes pasos conectaremos esta sección con la información en Google Sheets "
        "para mostrar análisis específicos según la vista seleccionada."
    )
