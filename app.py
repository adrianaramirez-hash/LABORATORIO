# app.py
import streamlit as st

import encuesta_calidad
import observacion_clases
import aulas_virtuales
import indice_reprobacion  # ✅ NUEVO
from examenes_departamentales import render_examenes_departamentales

import pandas as pd
import gspread
import json
import re
from google.oauth2.service_account import Credentials

# ============================================================
# Configuración básica (antes de cualquier st.*)
# ============================================================
st.set_page_config(page_title="Dirección Académica", layout="wide")

DEBUG = False

# ============================================================
# ACCESOS: Sheet donde está la pestaña ACCESOS (tu URL)
# ============================================================
ACCESOS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1CK7nphUH9YS2JqSWRhrgamYoQdgJCsn5tERA-WnwXes/edit?gid=770892546#gid=770892546"
ACCESOS_GID = 770892546
ACCESOS_TAB_NAME = "ACCESOS"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ============================================================
# Módulos: claves internas (columna MODULOS) y nombres visibles
# ============================================================
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
        raise ValueError("No pude extraer el ID del Google Sheet desde la URL.")
    return m.group(1)

def _first_nonempty_row_index(values: list[list[str]]) -> int:
    for i, row in enumerate(values):
        if any(str(c).strip() for c in row):
            return i
    return 0

def _load_creds_dict() -> dict:
    raw = st.secrets["gcp_service_account_json"]
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)

def _goto_seccion(nombre_seccion: str):
    st.session_state["seccion_forzada"] = nombre_seccion
    st.rerun()

def _parse_modulos_cell(modulos_cell: str) -> set[str]:
    """
    Reglas:
      - ALL => acceso total
      - "a,b,c" => set {"a","b","c"}
      - vacío => set vacío (sin módulos)
    """
    if modulos_cell is None:
        return set()
    txt = str(modulos_cell).strip()
    if not txt:
        return set()
    if txt.upper() == "ALL":
        return {"ALL"}
    parts = [p.strip() for p in txt.split(",") if p.strip()]
    return set(parts)

def _is_modulo_visible(mod_key: str) -> bool:
    permitted = st.session_state.get("user_modulos", set())
    allow_all = st.session_state.get("user_allow_all", False)
    if allow_all:
        return True
    return mod_key in permitted

def _placeholder_en_construccion(titulo: str):
    st.subheader(titulo)
    st.warning("📝 En construcción")
    st.caption("Este módulo se habilitará próximamente.")

    st.markdown("**Módulos disponibles:**")

    # Botones solo si el usuario tiene permitido ese módulo
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if _is_modulo_visible(MOD_KEY_BY_SECCION["Observación de clases"]):
            if st.button("🔎 Observación de clases", use_container_width=True, key=f"btn_oc_{titulo}"):
                _goto_seccion("Observación de clases")
        else:
            st.button("🔎 Observación de clases", use_container_width=True, disabled=True, key=f"btn_oc_dis_{titulo}")

    with c2:
        if _is_modulo_visible(MOD_KEY_BY_SECCION["Encuesta de calidad"]):
            if st.button("📋 Encuesta de calidad", use_container_width=True, key=f"btn_ec_{titulo}"):
                _goto_seccion("Encuesta de calidad")
        else:
            st.button("📋 Encuesta de calidad", use_container_width=True, disabled=True, key=f"btn_ec_dis_{titulo}")

    with c3:
        if _is_modulo_visible(MOD_KEY_BY_SECCION["Exámenes departamentales"]):
            if st.button("🧾 Exámenes departamentales", use_container_width=True, key=f"btn_ed_{titulo}"):
                _goto_seccion("Exámenes departamentales")
        else:
            st.button("🧾 Exámenes departamentales", use_container_width=True, disabled=True, key=f"btn_ed_dis_{titulo}")

    with c4:
        if _is_modulo_visible(MOD_KEY_BY_SECCION["Aulas virtuales"]):
            if st.button("🧑‍🏫 Aulas virtuales", use_container_width=True, key=f"btn_av_{titulo}"):
                _goto_seccion("Aulas virtuales")
        else:
            st.button("🧑‍🏫 Aulas virtuales", use_container_width=True, disabled=True, key=f"btn_av_dis_{titulo}")

@st.cache_data(ttl=120, show_spinner=False)
def cargar_accesos_df() -> tuple[pd.DataFrame, str]:
    creds_dict = _load_creds_dict()
    sa_email = creds_dict.get("client_email", "")

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)

    sheet_id = _extract_sheet_id(ACCESOS_SHEET_URL)
    sh = client.open_by_key(sheet_id)

    ws = None
    try:
        ws = sh.worksheet(ACCESOS_TAB_NAME)
    except Exception:
        ws = None

    if ws is None:
        try:
            ws = sh.get_worksheet_by_id(ACCESOS_GID)
        except Exception:
            ws = None

    if ws is None:
        ws = sh.sheet1

    values = ws.get_all_values()
    if not values or len(values) < 1:
        return pd.DataFrame(columns=["EMAIL", "ROL", "SERVICIO_ASIGNADO", "ACTIVO", "MODULOS"]), sa_email

    header_idx = _first_nonempty_row_index(values)
    header = [str(c).strip() for c in values[header_idx]]
    data = values[header_idx + 1 :]

    max_cols = max(len(header), max((len(r) for r in data), default=len(header)))
    header = header + [""] * (max_cols - len(header))
    header = [h if h else f"COL_{i+1}" for i, h in enumerate(header)]

    norm_data = []
    for r in data:
        r = [str(c) for c in r]
        r = r + [""] * (max_cols - len(r))
        norm_data.append(r[:max_cols])

    df = pd.DataFrame(norm_data, columns=header)
    df.columns = [str(c).strip().upper() for c in df.columns]

    for col in ["EMAIL", "ROL", "SERVICIO_ASIGNADO", "ACTIVO", "MODULOS"]:
        if col not in df.columns:
            df[col] = ""

    df["EMAIL"] = df["EMAIL"].astype(str).str.strip().str.lower()
    df["ROL"] = df["ROL"].astype(str).str.strip().str.upper()
    df["SERVICIO_ASIGNADO"] = df["SERVICIO_ASIGNADO"].astype(str).str.strip()
    df["MODULOS"] = df["MODULOS"].astype(str).str.strip()

    activo_raw = df["ACTIVO"].astype(str).str.strip().str.upper()
    df["ACTIVO"] = activo_raw.isin(["TRUE", "1", "SI", "SÍ", "YES", "ACTIVO"])

    df = df[df["EMAIL"] != ""]
    df = df[df["ACTIVO"]]

    return df, sa_email

def resolver_permiso_por_email(email: str, df_accesos: pd.DataFrame) -> dict:
    email_norm = (email or "").strip().lower()
    if not email_norm:
        return {"ok": False, "rol": None, "servicio": None, "modulos": set(), "mensaje": "Captura tu correo."}

    fila = df_accesos[df_accesos["EMAIL"] == email_norm]
    if fila.empty:
        return {
            "ok": False,
            "rol": None,
            "servicio": None,
            "modulos": set(),
            "mensaje": "Acceso no encontrado o inactivo. Verifica tu correo en ACCESOS.",
        }

    rol = str(fila.iloc[0]["ROL"]).strip().upper()
    servicio = str(fila.iloc[0]["SERVICIO_ASIGNADO"]).strip()
    modulos = _parse_modulos_cell(fila.iloc[0].get("MODULOS", ""))

    if rol not in ["DG", "DC"]:
        return {"ok": False, "rol": None, "servicio": None, "modulos": set(), "mensaje": "ROL inválido en ACCESOS. Usa DG o DC."}

    if rol == "DC" and not servicio:
        return {"ok": False, "rol": None, "servicio": None, "modulos": set(), "mensaje": "Falta SERVICIO_ASIGNADO (ROL=DC)."}

    # Seguridad: si no tiene módulos asignados, no verá ninguno (debe venir ALL o lista)
    if not modulos:
        return {
            "ok": False,
            "rol": None,
            "servicio": None,
            "modulos": set(),
            "mensaje": "Tu usuario no tiene MODULOS asignados en ACCESOS. Coloca ALL o una lista (ej. observacion_clases,aulas_virtuales).",
        }

    return {
        "ok": True,
        "rol": rol,
        "servicio": (servicio if rol == "DC" else None),
        "modulos": modulos,
        "mensaje": "OK",
    }

# ============================================================
# Header (logo + título)
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
# LOGIN / ACCESO
# ============================================================
st.subheader("Acceso")

if "user_rol" in st.session_state:
    c1, c2 = st.columns([4, 1], vertical_alignment="center")
    with c1:
        st.success(f"Sesión activa: {st.session_state.get('user_email','')}")
    with c2:
        if st.button("Salir", use_container_width=True):
            for k in ["user_email", "user_rol", "user_servicio", "user_modulos", "user_allow_all"]:
                st.session_state.pop(k, None)
            st.rerun()
else:
    email_input = st.text_input("Correo institucional:", value=st.session_state.get("user_email", ""))
    if st.button("Entrar", use_container_width=True):
        try:
            df_accesos, _ = cargar_accesos_df()
            res = resolver_permiso_por_email(email_input, df_accesos)

            if not res["ok"]:
                st.error(res["mensaje"])
                st.stop()

            st.session_state["user_email"] = (email_input or "").strip().lower()
            st.session_state["user_rol"] = res["rol"]
            st.session_state["user_servicio"] = res["servicio"]
            st.session_state["user_modulos"] = res["modulos"]
            st.session_state["user_allow_all"] = ("ALL" in res["modulos"])
            st.rerun()

        except Exception as e:
            st.error("No fue posible validar el acceso. Revisa permisos del Google Sheet de ACCESOS.")
            try:
                sa_email = _load_creds_dict().get("client_email", "")
            except Exception:
                sa_email = ""
            if sa_email:
                st.info(f"Comparte el Sheet de ACCESOS con este correo (Viewer): {sa_email}")

            if DEBUG:
                st.exception(e)
            else:
                with st.expander("Ver detalle técnico (para diagnóstico)"):
                    st.write(str(e))
            st.stop()

if "user_rol" not in st.session_state:
    st.stop()

st.divider()

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

ROL = st.session_state["user_rol"]
SERVICIO_DC = st.session_state.get("user_servicio")

vista = "Dirección General" if ROL == "DG" else "Director de carrera"

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
# Menú de apartados (Plan anual) - FILTRADO por MODULOS
# ============================================================
SECCIONES_TODAS = [
    "Encuesta de calidad",
    "Observación de clases",
    "Evaluación docente",
    "Capacitaciones",
    "Índice de reprobación",
    "Titulación",
    "Ceneval",
    "Exámenes departamentales",
    "Aulas virtuales",
]

# Filtrar secciones según permisos
try:
    if st.session_state.get("user_allow_all", False):
        SECCIONES = SECCIONES_TODAS[:]
    else:
        permitted = st.session_state.get("user_modulos", set())
        SECCIONES = [
            s for s in SECCIONES_TODAS
            if MOD_KEY_BY_SECCION.get(s, "") in permitted
        ]

    if not SECCIONES:
        st.error("Tu usuario no tiene módulos habilitados. Revisa la columna MODULOS en ACCESOS.")
        st.stop()
except Exception as e:
    st.error("Error al filtrar módulos por permisos.")
    if DEBUG:
        st.exception(e)
    st.stop()

# Si venimos de un botón (placeholder), forzar selección (solo si existe en SECCIONES)
if "seccion_forzada" in st.session_state:
    forced = st.session_state.get("seccion_forzada")
    st.session_state.pop("seccion_forzada", None)
    if forced in SECCIONES:
        idx_forzada = SECCIONES.index(forced)
    else:
        idx_forzada = 0
else:
    idx_forzada = 0

try:
    seccion = st.selectbox(
        "Selecciona el apartado del plan anual que deseas revisar:",
        SECCIONES,
        index=idx_forzada,
    )
except Exception as e:
    st.error("Error creando selector de apartado.")
    if DEBUG:
        st.exception(e)
    st.stop()

st.divider()

# ============================================================
# Bloqueo duro: por si alguien intenta forzar estado previo
# ============================================================
try:
    key = MOD_KEY_BY_SECCION.get(seccion, "")
    if not key:
        st.error("Sección inválida.")
        st.stop()

    if not st.session_state.get("user_allow_all", False):
        if key not in st.session_state.get("user_modulos", set()):
            st.error("Sin acceso a este módulo.")
            st.stop()
except Exception as e:
    st.error("Error validando permisos del módulo.")
    if DEBUG:
        st.exception(e)
    st.stop()

# ============================================================
# Router
# ============================================================
try:
    if seccion == "Encuesta de calidad":
        st.subheader("Encuesta de calidad")
        encuesta_calidad.render_encuesta_calidad(vista=vista, carrera=carrera)

    elif seccion == "Observación de clases":
        st.subheader("Observación de clases")
        observacion_clases.render_observacion_clases(vista=vista, carrera=carrera)

    elif seccion == "Evaluación docente":
        _placeholder_en_construccion("Evaluación docente")

    elif seccion == "Capacitaciones":
        _placeholder_en_construccion("Capacitaciones")

    elif seccion == "Índice de reprobación":
        st.subheader("Índice de reprobación")
        indice_reprobacion.render_indice_reprobacion(vista=vista, carrera=carrera)

    elif seccion == "Titulación":
        _placeholder_en_construccion("Titulación")

    elif seccion == "Ceneval":
        _placeholder_en_construccion("Ceneval")

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
        st.write(f"Apartado seleccionado: **{seccion}**")

except Exception as e:
    st.error("Ocurrió un error al cargar el apartado seleccionado.")
    if DEBUG:
        st.exception(e)
    st.stop()
