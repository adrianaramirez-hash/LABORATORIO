# app.py
import streamlit as st
import encuesta_calidad
import observacion_clases
import aulas_virtuales
from examenes_departamentales import render_examenes_departamentales

# ============================================================
# Configuración básica (antes de cualquier st.*)
# ============================================================
st.set_page_config(page_title="Dirección Académica", layout="wide")

# ============================================================
# “Guard rails” para que NUNCA se quede en blanco
# ============================================================
# NOTA: Quitamos st.title/st.caption aquí para que NO se repita el encabezado.
st.divider()

# Muestra rápidamente si faltan secrets (sin exponer valores)
try:
    secretos_disponibles = list(st.secrets.keys())
    st.info(
        f"Secrets detectados: {', '.join(secretos_disponibles) if secretos_disponibles else '(ninguno)'}"
    )
except Exception as e:
    st.error("No fue posible leer st.secrets.")
    st.exception(e)

# ============================================================
# Header (logo + título)  ✅ solo escudo + Dirección Académica
# ============================================================
logo_url = "udl_logo.png"
try:
    col1, col2 = st.columns([1, 5], vertical_alignment="center")
    with col1:
        st.image(logo_url, width=140)  # ajusta aquí el tamaño del escudo (120–170 recomendado)
    with col2:
        st.markdown("# Dirección Académica")
        st.caption("Seguimiento del Plan Anual.")
except Exception as e:
    st.warning("No se pudo cargar el logo (esto no detiene la app).")
    st.exception(e)

st.divider()

# ============================================================
# Selector de vista
# ============================================================
try:
    vista = st.selectbox(
        "Selecciona la vista:",
        ["Dirección General", "Director de carrera"],
    )
except Exception as e:
    st.error("Error creando selector de vista.")
    st.exception(e)
    st.stop()

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
    "Centro de Idiomas",
]

# ============================================================
# Selección / asignación de carrera
# - Hoy se simula con un selectbox.
# - Mañana (con control de acceso) aquí se asignará automáticamente.
# ============================================================
carrera = None
try:
    if vista == "Director de carrera":
        carrera = st.selectbox("Selecciona la carrera:", CATALOGO_CARRERAS)
except Exception as e:
    st.error("Error creando selector de carrera.")
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

except Exception as e:
    st.error("Ocurrió un error al cargar el apartado seleccionado.")
    st.exception(e)
    st.stop()
