# app.py
import streamlit as st
import importlib

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

    # ✅ NUEVO
    "Seguimiento de Inscripciones": "seguimiento_inscripciones",
}


def cargar_modulo_y_ejecutar(module_key: str):
    """
    Importa dinámicamente el módulo (por nombre de archivo) y ejecuta run().
    - module_key debe coincidir con el nombre del archivo .py (sin .py)
      Ej: 'encuesta_calidad' -> encuesta_calidad.py
    """
    try:
        mod = importlib.import_module(module_key)
    except ModuleNotFoundError:
        st.error(
            f"No se encontró el archivo del módulo: **{module_key}.py**\n\n"
            f"Verifica que exista en la misma carpeta del ecosistema (o en el PYTHONPATH)."
        )
        st.stop()
    except Exception as e:
        st.error(f"Error importando el módulo **{module_key}**: {e}")
        st.stop()

    if not hasattr(mod, "run"):
        st.error(
            f"El módulo **{module_key}.py** existe, pero no tiene la función **run()**.\n\n"
            f"Agrega:\n\n"
            f"```python\n"
            f"def run():\n"
            f"    ...\n"
            f"```"
        )
        st.stop()

    try:
        mod.run()
    except Exception as e:
        st.error(f"Error ejecutando **{module_key}.run()**: {e}")
        st.stop()


def main():
    st.set_page_config(page_title="Ecosistema Dirección Académica", layout="wide")

    st.sidebar.title("Ecosistema")
    seccion_visible = st.sidebar.radio(
        "Módulo",
        list(MOD_KEY_BY_SECCION.keys()),
        index=0
    )

    module_key = MOD_KEY_BY_SECCION[seccion_visible]
    cargar_modulo_y_ejecutar(module_key)


if __name__ == "__main__":
    main()
