import streamlit as st

st.set_page_config(page_title="Dirección Académica", layout="wide")

# Import seguro (evita pantalla en blanco)
try:
    import encuesta_calidad
except Exception as e:
    st.error("Falló el import de encuesta_calidad.py.")
    st.exception(e)
    st.stop()

# Escudo
logo_url = "udl_logo.png"
col1, col2 = st.columns([1, 4])
with col1:
    st.image(logo_url, use_container_width=True)
with col2:
    st.title("Dirección Académica")
    st.write("Seguimiento del Plan Anual.")

st.divider()

vista = st.selectbox("Selecciona la vista:", ["Dirección General", "Director de carrera"])

carrera = None
if vista == "Director de carrera":
    carrera = st.selectbox(
        "Selecciona la carrera:",
        [
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
            "Maestría en Administración de Negocios (MBA",
            "Maestría en Derecho Corporativo",
            "Maestría en Desarrollo del Potencial Humano y Organizacional (Coaching",
            "Maestría en Odontología Legal y Forense",
            "Maestría en Psicoterapia Familiar",
            "Maestría en Psicoterapia Psicoanalítica",
            "Maestría en Administración de Recursos Humanos",
            "Maestría en Finanzas",
            "Maestría en Educación Especial",
            "Maestría: Dirección de Recursos Humanos",
            "Maestría:Finanzas",
            "Maestría:Gestión de Tecnologías de la Información",
            "Maestría:Docencia",
            "Maestría:Educación Especial",
            "Maestría: Entrenamiento Deportivo",
            "Maestría: Tecnología e Innovación Educativa",
            "Licenciatura Entrenamiento Deportivo",
        ],
    )

st.divider()

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

st.divider()

if seccion == "Encuesta de calidad":
    encuesta_calidad.render_encuesta_calidad(vista=vista, carrera=carrera)
elif seccion == "Observación de clases":
    st.warning("Observación de clases está temporalmente deshabilitado.")
else:
    st.info("Módulo en construcción.")
