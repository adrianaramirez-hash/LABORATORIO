import streamlit as st

# Configuración básica de la página
st.set_page_config(page_title="Dirección Académica", layout="wide")

# Escudo de la UDL desde el repositorio
logo_url = "udl_logo.png"

# Encabezado con escudo + texto
col1, col2 = st.columns([1, 4])

with col1:
    st.image(logo_url, use_container_width=True)

with col2:
    st.title("Dirección Académica")
    st.write("Seguimiento del Plan Anual.")

st.divider()

# Selector de vista (AJUSTE: solo quedan 2 vistas)
vista = st.selectbox(
    "Selecciona la vista:",
    ["Dirección General", "Director de carrera"],
)

carrera = None
if vista == "Director de carrera":
    carrera = st.selectbox(
        "Selecciona la carrera:",
        [
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
            "Maestría en Educación Especial",
        ],
    )

st.divider()

# Menú desplegable de secciones
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

# Panel inicial: resumen de selección
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
