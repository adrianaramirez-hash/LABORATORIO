# app.py
import streamlit as st

import encuesta_calidad
import observacion_clases
import aulas_virtuales
from examenes_departamentales import render_examenes_departamentales

# --- NUEVO: dependencias para leer ACCESOS desde Google Sheets ---
import pandas as pd
import gspread
import json
from google.oauth2.service_account import Credentials

# ============================================================
# Configuración básica (antes de cualquier st.*)
# ============================================================
st.set_page_config(page_title="Dirección Académica", layout="wide")

# ============================================================
# Debug (estética limpia por defecto)
# - Cambia a True solo cuando necesites diagnosticar secrets/errores.
# ============================================================
DEBUG = False

# ============================================================
# URL (fijo) del Google Sheet donde está la pestaña ACCESOS
# ============================================================
ACCESOS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1CK7nphUH9YS2JqSWRhrgamYoQdgJCsn5tERA-WnwXes/edit"

# ============================================================
# Scopes (lectura)
# ============================================================
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ============================================================
# Helpers: leer ACCESOS
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def cargar_accesos_df() -> pd.DataFrame:
    raw = st.secrets["gcp_service_account_json"]
    creds_dict = dict(raw) if isinstance(raw, dict) else json.loads(raw)

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    sh = client.open_by_url(ACCESOS_SHEET_URL)
    ws = sh.worksheet("ACCESOS")
    df = pd.DataFrame(ws.get_all_records())

    # Normalización defensiva
    df.columns = [str(c).strip().upper() for c in df.columns]
    for col in ["EMAIL", "ROL", "SERVICIO_ASIGNADO", "ACTIVO"]:
        if col not in df.columns:
            df[col] = ""

    df["EMAIL"] = df["EMAIL"].astype(str).str.strip().str.lower()
    df["ROL"] = df["ROL"].astype(str).str.strip().str.upper()
    df["SERVICIO_ASIGNADO"] = df["SERVICIO_ASIGNADO"].astype(str).str.strip()

    # ACTIVO a boolean robusto
    activo_raw = df["ACTIVO"].astype(str).str.strip().str.upper()
    df["ACTIVO"] = activo_raw.isin(["TRUE", "1", "SI", "SÍ", "YES", "ACTIVO"])

    # Filtra solo activos y emails válidos
    df = df[df["ACTIVO"] & (df["EMAIL"] != "")]
    return df


def resolver_permiso_por_email(email: str, df_accesos: pd.DataFrame) -> dict:
    """
    Retorna dict:
      {
        "ok": bool,
        "rol": "DG"|"DC",
        "servicio": str|None,
        "mensaje": str
      }
    """
    email_norm = (email or "").strip().lower()
    if not email_norm:
        return {"ok": False, "rol": None, "servicio": None, "mensaje": "Captura tu correo."}

    fila = df_accesos[df_accesos["EMAIL"] == email_norm]
    if fila.empty:
        return {
            "ok": False,
            "rol": None,
            "servicio": None,
            "mensaje": "Acceso no encontrado. Verifica tu correo o solicita alta en ACCESOS.",
        }

    rol = str(fila.iloc[0]["ROL"]).strip().upper()
    servicio = str(fila.iloc[0]["SERVICIO_ASIGNADO"]).strip()

    if rol not in ["DG", "DC"]:
        return {
            "ok": False,
            "rol": None,
            "servicio": None,
            "mensaje": "ROL inválido en ACCESOS. Usa DG o DC.",
        }

    if rol == "DC" and not servicio:
        return {
            "ok": False,
            "rol": None,
            "servicio": None,
            "mensaje": "Falta SERVICIO_ASIGNADO para este usuario (ROL=DC).",
        }

    return {"ok": True, "rol": rol, "servicio": (servicio if rol == "DC" else None), "mensaje": "OK"}


# ============================================================
# Header (logo + título)  ✅ solo escudo + Dirección Académica
# ============================================================
logo_url = "udl_logo.png"
try:
    col1, col2 = st.columns([1, 5], vertical_alignment="center")
    with col1:
        st.image(logo_url, width=140)
    with col2:
        st.markdown("# Dirección Académica")
        st.caption("Seguimiento del Plan Anual.")
except Exception as e:
    st.warning("No se pudo cargar el logo (esto no detiene la app).")
    if DEBUG:
        st.exception(e)

st.divider()

# ============================================================
# Diagnóstico de secrets (oculto en producción)
# ============================================================
if DEBUG:
    try:
        secretos_disponibles = list(st.secrets.keys())
        st.info(
            f"Secrets detectados: {', '.join(secretos_disponibles) if secretos_disponibles else '(ninguno)'}"
        )
    except Exception as e:
        st.error("No fue posible leer st.secrets.")
        st.exception(e)

# ============================================================
# LOGIN / ACCESO (NUEVO)
# ============================================================
with st.sidebar:
    st.subheader("Acceso")
    email_input = st.text_input("Correo institucional:", value=st.session_state.get("user_email", ""))
    colA, colB = st.columns(2)
    with colA:
        entrar = st.button("Entrar", use_container_width=True)
    with colB:
        salir = st.button("Salir", use_container_width=True)

if salir:
    for k in ["user_email", "user_rol", "user_servicio"]:
        st.session_state.pop(k, None)
    st.rerun()

# Si no hay sesión, intentar iniciar con botón
if entrar:
    try:
        df_accesos = cargar_accesos_df()
        res = resolver_permiso_por_email(email_input, df_accesos)

        if not res["ok"]:
            st.error(res["mensaje"])
            st.stop()

        st.session_state["user_email"] = (email_input or "").strip().lower()
        st.session_state["user_rol"] = res["rol"]           # DG / DC
        st.session_state["user_servicio"] = res["servicio"] # None si DG
        st.rerun()

    except Exception as e:
        st.error("No fue posible validar el acceso. Revisa credenciales/permisos del Sheet de ACCESOS.")
        if DEBUG:
            st.exception(e)
        st.stop()

# Si sigue sin sesión, detener
if "user_rol" not in st.session_state:
    st.info("Ingresa tu correo institucional y da clic en Entrar.")
    st.stop()

# ============================================================
# Vista/carrera AUTOMÁTICAS por rol (NUEVO)
# - DG: puede ver Todo y elegir servicio
# - DC: solo su servicio; no se permite cambiar
# ============================================================
ROL = st.session_state["user_rol"]
SERVICIO_DC = st.session_state.get("user_servicio")

# ============================================================
# Catálogo de carreras
# ============================================================
CATALOGO_CARRERAS = [
    "Preparatoria",
    "Actuación",
    "Administración de empresas",
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
    "Licenciatura Ejecutiva: Administración de Empresas",
    "Licenciatura Ejecutiva: Contaduría",
    "Licenciatura Ejecutiva: Derecho",
    "Licenciatura Ejecutiva: Informática",
    "Licenciatura Ejecutiva: Mercadotecnia",
    "Licenciatura Ejecutiva: Pedagogía",
    "Maestría en Administración de Negocios (MBA)",
    "Maestría en Derecho Corporativo",
    "Maestría en Desarrollo del Potencial Humano y Organizacional (Coaching)",
    "Maestría en Odontología Legal y Forense",
    "Maestría en Psicoterapia Familiar",
    "Maestría en Psicoterapia Psicoanalítica",
    "Maestría en Administración de Recursos Humanos",
    "Maestría en Finanzas",
    "Maestría en Educación Especial",
    "Maestría: Dirección de Recursos Humanos",
    "Maestría: Finanzas",
    "Maestría: Gestión de Tecnologías de la Información",
    "Maestría: Docencia",
    "Maestría: Educación Especial",
    "Maestría: Entrenamiento Deportivo",
    "Maestría: Tecnología e Innovación Educativa",
    "Licenciatura Entrenamiento Deportivo",
    "EDUCON",
    "Centro de Idiomas",
]

# Mapea rol → vista que ya usan los módulos
if ROL == "DG":
    vista = "Dirección General"
else:
    vista = "Director de carrera"

# Selector de servicio: DG sí; DC no
carrera = None
try:
    if ROL == "DG":
        opciones = ["Todos"] + CATALOGO_CARRERAS
        sel = st.selectbox("Servicio / carrera:", opciones, index=0)
        carrera = None if sel == "Todos" else sel
    else:
        carrera = SERVICIO_DC
        st.info(f"Acceso limitado a: **{carrera}**")
except Exception as e:
    st.error("Error configurando acceso por rol.")
    if DEBUG:
        st.exception(e)
    st.stop()

st.divider()

# ============================================================
# Menú de apartados (Plan anual)
# ============================================================
try:
    seccion = st.selectbox(
        "Selecciona el apartado del plan anual que deseas revisar:",
        [
            "Encuesta de calidad",
            "Observación de clases",
            "Evaluación docente",
            "Capacitaciones",
            "Índice de reprobación",
            "Titulación",
            "Ceneval",
            "Exámenes departamentales",
            "Aulas virtuales",
        ],
    )
except Exception as e:
    st.error("Error creando selector de apartado.")
    if DEBUG:
        st.exception(e)
    st.stop()

st.divider()

# ============================================================
# Router (con manejo de errores visible)
# ============================================================
try:
    if seccion == "Encuesta de calidad":
        st.subheader("Encuesta de calidad")
        encuesta_calidad.render_encuesta_calidad(vista=vista, carrera=carrera)

    elif seccion == "Observación de clases":
        st.subheader("Observación de clases")
        observacion_clases.render_observacion_clases(vista=vista, carrera=carrera)

    elif seccion == "Evaluación docente":
        st.info("Módulo en construcción: Evaluación docente")

    elif seccion == "Exámenes departamentales":
        st.subheader("Exámenes departamentales")
        render_examenes_departamentales(
            "https://docs.google.com/spreadsheets/d/1GqlE9SOkSNCdA9mi65hk45uuLAao8GHHoresiyhRfQU/edit",
            vista=vista,
            carrera=carrera,
        )

    elif seccion == "Aulas virtuales":
        st.subheader("Aulas virtuales")
        aulas_virtuales.mostrar(vista=vista, carrera=carrera)

    else:
        st.subheader("Panel inicial")
        st.write(f"Rol: **{ROL}**")
        st.write(f"Vista actual: **{vista}**")
        if carrera:
            st.write(f"Servicio seleccionado: **{carrera}**")
        else:
            st.write("Servicio seleccionado: **Todos**")
        st.write(f"Apartado seleccionado: **{seccion}**")

except Exception as e:
    st.error("Ocurrió un error al cargar el apartado seleccionado.")
    if DEBUG:
        st.exception(e)
    st.stop()
