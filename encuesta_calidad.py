import streamlit as st
import pandas as pd
import altair as alt
import time

def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.title("🧪 PRUEBA — Encuesta de calidad (LABORATORIO)")
    st.success("Si ves esto, la app SÍ está tomando cambios desde encuesta_calidad.py")
    st.write("Hora del servidor:", time.strftime("%Y-%m-%d %H:%M:%S"))

    st.divider()
    st.markdown("### Parámetros recibidos")
    st.code(f"vista={vista!r}\ncarrera={carrera!r}")

    st.divider()
    st.markdown("### Controles de prueba")
    n = st.slider("Número de filas", 5, 50, 12)
    seed = st.number_input("Seed", value=1, step=1)

    df = pd.DataFrame({
        "Sección": ["SEAC", "Docencia", "Instalaciones", "Administración"] * 20,
        "Puntaje": [(i % 5) + 1 for i in range(80)],
        "Comentario": [f"Comentario demo #{i} (seed={seed})" for i in range(80)]
    }).head(int(n))

    st.dataframe(df, use_container_width=True)

    st.divider()
    st.markdown("### Gráfico de prueba")
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y("Sección:N", sort="-x"),
            x=alt.X("mean(Puntaje):Q", title="Promedio (demo)"),
            tooltip=["Sección", alt.Tooltip("mean(Puntaje):Q", title="Promedio", format=".2f")],
        )
        .properties(height=240)
    )
    st.altair_chart(chart, use_container_width=True)

    st.info("✅ Si este módulo aparece, ya podemos volver a tu código real y mejorar comentarios.")
