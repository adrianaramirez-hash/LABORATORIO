import streamlit as st
import pandas as pd

def mostrar(vista: str, carrera: str | None = None):
    st.subheader("Aulas virtuales")

    st.write(f"Vista: **{vista}**")
    if carrera:
        st.write(f"Carrera/Servicio seleccionado: **{carrera}**")
    else:
        st.write("Carrera/Servicio seleccionado: *no aplica para esta vista*")

    st.info(
        "Conexión a Google Sheets pendiente para este módulo. "
        "En el siguiente paso conectaremos la hoja del formulario y el catálogo."
    )
