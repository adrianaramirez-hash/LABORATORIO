import streamlit as st
import pandas as pd

def mostrar(df_av: pd.DataFrame, cat_servicios: pd.DataFrame, carrera_sel: str):
    st.subheader("Aulas virtuales")

    if df_av is None or df_av.empty:
        st.warning("No hay datos de Aulas Virtuales cargados.")
        return

    if cat_servicios is None or cat_servicios.empty:
        st.warning("No hay catálogo de servicios cargado (CAT_SERVICIOS_ESTRUCTURA).")
        return

    # 1) Normalización mínima
    if "Indica el servicio" not in df_av.columns:
        st.error("En el dataframe de Aulas Virtuales falta la columna: 'Indica el servicio'")
        return

    if "servicio" not in cat_servicios.columns:
        st.error("En el catálogo falta la columna: 'servicio'")
        return

    df_av = df_av.copy()
    cat_servicios = cat_servicios.copy()

    df_av["servicio_std"] = df_av["Indica el servicio"].astype(str).str.strip()
    cat_servicios["servicio_std"] = cat_servicios["servicio"].astype(str).str.strip()

    # 2) Join para agregar escuela/nivel (si existen en catálogo)
    # (Si en tu catálogo aún no están, no pasa nada; se crean como NaN)
    for col in ["escuela", "nivel", "tipo_unidad"]:
        if col not in cat_servicios.columns:
            cat_servicios[col] = None

    df_av = df_av.merge(
        cat_servicios[["servicio_std", "escuela", "nivel", "tipo_unidad"]],
        on="servicio_std",
        how="left"
    )

    st.caption(
        "Nota: Este apartado se reporta por 'Servicio'. "
        "La selección superior se conserva para mantener la lógica de navegación."
    )

    # 3) Selector interno del módulo (sin romper tu orden)
    with st.container(border=True):
        st.markdown("**Filtro del apartado**")

        servicio_base = str(carrera_sel).strip()

        escuela_base = None
        fila_base = cat_servicios[cat_servicios["servicio_std"] == servicio_base]
        if not fila_base.empty:
            escuela_base = fila_base.iloc[0].get("escuela")

        # Opciones del selector interno
        if escuela_base and str(escuela_base).strip().lower() not in ["nan", "none", ""]:
            servicios_escuela = (
                cat_servicios[cat_servicios["escuela"] == escuela_base]["servicio_std"]
                .dropna().unique().tolist()
            )
            servicios_escuela = sorted(set([s.strip() for s in servicios_escuela if s.strip()]))

            opciones = [f"Todos los servicios de {escuela_base}"] + servicios_escuela
            default_idx = 1 if servicio_base in servicios_escuela else 0
        else:
            opciones = [servicio_base]
            default_idx = 0

        servicio_av_sel = st.selectbox(
            "Servicio a analizar (Aulas Virtuales)",
            options=opciones,
            index=default_idx
        )

    # 4) Aplicar filtro
    if servicio_av_sel.startswith("Todos los servicios de"):
        df_f = df_av[df_av["escuela"] == escuela_base].copy()
    else:
        df_f = df_av[df_av["servicio_std"] == servicio_av_sel].copy()

    # 5) Mostrar algo básico para validar que ya funciona
    st.write(f"Respuestas consideradas: {len(df_f)}")
    st.dataframe(df_f.head(20))
