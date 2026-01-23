# app.py
import streamlit as st

import encuesta_calidad
import observacion_clases
import aulas_virtuales
import indice_reprobacion
import evaluacion_docente
from examenes_departamentales import render_examenes_departamentales

import pandas as pd
import gspread
import json
import re
from google.oauth2.service_account import Credentials
from catalogos import cargar_cat_carreras_desde_gsheets

# ============================================================
# Configuración básica
# ============================================================
st.set_page_config(page_title="Dirección Académica", layout="wide")
DEBUG = False

# ============================================================
# ACCESOS
# ============================================================
ACCESOS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1CK7nphUH9YS2JqSWRhrgamYoQdgJCsn5tERA-WnwXes/edit?gid=770892546#gid=770892546"
ACCESOS_GID = 770892546
ACCESOS_TAB_NAME = "ACCESOS"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

MOD_KEY_BY_SECCION = {
    "Encuesta de calidad": "encuesta_calidad",
    "Observación de clases": "observacion_clases",
    "Evaluación docente": "evaluacion_docente",
    "Capacitaciones": "capacitaciones",
    "Índice de reprobación": "indice_reprobacion",
    "Titulación": "titulacion",
    "Ceneval": "ceneval",
    "Exámenes departamentales": "examenes_departamentales",
    "Aulas virtuales": "aulas_virtuales",
}

# ============================================================
# Helpers
# ============================================================
def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url or "")
    if not m:
        raise ValueError("No pude extraer el ID del Google Sheet.")
    return m.group(1)

def _first_nonempty_row_index(values):
    for i, row in enumerate(values):
        if any(str(c).strip() for c in row):
            return i
    return 0

def _load_creds_dict() -> dict:
    raw = st.secrets["gcp_service_account_json"]
    return json.loads(raw) if isinstance(raw, str) else dict(raw)

def _norm_email(s: str) -> str:
    return str(s or "").replace("\u00A0", "").replace("\u200B", "").strip().lower()

def _parse_servicios_cell(cell: str) -> list[str]:
    if not cell:
        return []
    return [p.strip() for p in re.split(r"[,\|]", str(cell)) if p.strip()]

def _parse_modulos_cell(cell: str) -> set[str]:
    if not cell:
        return set()
    if str(cell).upper() == "ALL":
        return {"ALL"}
    return set(p.strip() for p in str(cell).split(",") if p.strip())

# ============================================================
# gspread
# ============================================================
@st.cache_resource(show_spinner=False)
def get_gspread_client():
    creds = Credentials.from_service_account_info(
        _load_creds_dict(), scopes=SCOPES
    )
    return gspread.authorize(creds)

# ============================================================
# ACCESOS
# ============================================================
@st.cache_data(ttl=120, show_spinner=False)
def cargar_accesos_df():
    client = get_gspread_client()
    sheet_id = _extract_sheet_id(ACCESOS_SHEET_URL)
    sh = client.open_by_key(sheet_id)

    try:
        ws = sh.worksheet(ACCESOS_TAB_NAME)
    except Exception:
        ws = sh.get_worksheet_by_id(ACCESOS_GID)

    values = ws.get_all_values()
    header_idx = _first_nonempty_row_index(values)
    header = [h.strip().upper() for h in values[header_idx]]
    data = values[header_idx + 1 :]

    df = pd.DataFrame(data, columns=header)

    for col in [
        "EMAIL",
        "ROL",
        "SERVICIO_ASIGNADO",
        "AV_TIPO",
        "AV_VALOR",
        "ACTIVO",
        "MODULOS",
    ]:
        if col not in df.columns:
            df[col] = ""

    df["EMAIL"] = df["EMAIL"].apply(_norm_email)
    df["ROL"] = df["ROL"].str.upper().str.strip()
    df["AV_TIPO"] = df["AV_TIPO"].str.upper().str.strip()
    df["AV_VALOR"] = df["AV_VALOR"].str.strip()
    df["MODULOS"] = df["MODULOS"].str.strip()

    df["ACTIVO"] = (
        df["ACTIVO"]
        .astype(str)
        .str.upper()
        .isin(["TRUE", "1", "SI", "SÍ", "YES", "ACTIVO"])
    )

    df = df[df["EMAIL"] != ""]
    df = df[df["ACTIVO"]]

    return df

def resolver_permiso_por_email(email: str, df: pd.DataFrame) -> dict:
    email = _norm_email(email)
    fila = df[df["EMAIL"] == email]

    if fila.empty:
        return {"ok": False, "mensaje": "Usuario no habilitado."}

    row = fila.iloc[0]
    modulos = _parse_modulos_cell(row["MODULOS"])

    return {
        "ok": True,
        "rol": row["ROL"],
        "servicios": _parse_servicios_cell(row["SERVICIO_ASIGNADO"]),
        "av_tipo": row["AV_TIPO"],
        "av_valor": row["AV_VALOR"],
        "modulos": modulos,
    }

# ============================================================
# Login
# ============================================================
st.subheader("Acceso")

if not getattr(st.user, "is_logged_in", False):
    if st.button("Iniciar sesión con Google"):
        st.login("google")
    st.stop()

user_email = _norm_email(getattr(st.user, "email", ""))

cargar_accesos_df.clear()
df_accesos = cargar_accesos_df()
res = resolver_permiso_por_email(user_email, df_accesos)

if not res["ok"]:
    st.error(res["mensaje"])
    st.stop()

st.session_state["user_email"] = user_email
st.session_state["user_rol"] = res["rol"]
st.session_state["user_servicios"] = res["servicios"]
st.session_state["user_modulos"] = res["modulos"]
st.session_state["user_allow_all"] = "ALL" in res["modulos"]
st.session_state["av_tipo"] = res.get("av_tipo", "")
st.session_state["av_valor"] = res.get("av_valor", "")

st.success(f"Sesión activa: {user_email}")
st.divider()

# ============================================================
# Contexto
# ============================================================
ROL = st.session_state["user_rol"]
vista = "Dirección General" if ROL == "DG" else "Director de carrera"

carrera = None
if ROL == "DC":
    servicios = st.session_state.get("user_servicios", [])
    carrera = servicios[0] if servicios else None

# ============================================================
# Menú
# ============================================================
SECCIONES = list(MOD_KEY_BY_SECCION.keys())
seccion = st.selectbox("Selecciona el apartado:", SECCIONES)

# ============================================================
# Router
# ============================================================
if seccion == "Encuesta de calidad":
    encuesta_calidad.render_encuesta_calidad(vista=vista, carrera=carrera)

elif seccion == "Observación de clases":
    observacion_clases.render_observacion_clases(vista=vista, carrera=carrera)

elif seccion == "Evaluación docente":
    evaluacion_docente.render_evaluacion_docente(vista=vista, carrera=carrera)

elif seccion == "Índice de reprobación":
    indice_reprobacion.render_indice_reprobacion(vista=vista, carrera=carrera)

elif seccion == "Exámenes departamentales":
    render_examenes_departamentales(
        "https://docs.google.com/spreadsheets/d/1GqlE9SOkSNCdA9mi65hk45uuLAao8GHHoresiyhRfQU/edit",
        vista=vista,
        carrera=carrera,
    )

elif seccion == "Aulas virtuales":
    aulas_virtuales.mostrar(vista=vista, carrera=carrera)

else:
    st.info("Módulo en construcción.")
