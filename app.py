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


def ejecutar_modulo(module_key: str):
    """
    Compatibilidad TOTAL con tu ecosistema actual:
    - Si el módulo trae run(): lo llama.
    - Si NO trae run(): lo ejecuta como script (recargando el import).
    """
    try:
        # Importa o recupera el módulo
        mod = importlib.import_module(module_key)

        # Si ya está importado, lo recargamos para que se vuelva a ejecutar su código top-level
        mod = importlib.reload(mod)

    except ModuleNotFoundError:
        st.error(
            f"No se encontró el módulo: **{module_key}.py**\n\n"
            "Verifica que el archivo exista con ese nombre exacto."
        )
        st.stop()
    except Exception as e:
        st.error(f"Error importando/recargando **{module_key}**: {e}")
        st.stop()

    # Si tiene run(), úsalo (por si en el futuro migras a esa estructura)
    if hasattr(mod, "run"):
        try:
            mod.run()
        except Exception as e:
            st.error(f"Error ejecutando **{module_key}.run()**: {e}")
            st.stop()
    # Si NO tiene run(), ya se ejecutó al hacer import/reload (modo script)
    else:
        # No hacemos nada: el contenido del módulo ya se renderizó
        pass


def main():
    st.set_page_config(page_title="Ecosistema Dirección Académica", layout="wide")

    st.sidebar.title("Ecosistema")
    seccion_visible = st.sidebar.radio(
        "Módulo",
        list(MOD_KEY_BY_SECCION.keys()),
        index=0
    )

    module_key = MOD_KEY_BY_SECCION[seccion_visible]
    ejecutar_modulo(module_key)


if __name__ == "__main__":
    main()
