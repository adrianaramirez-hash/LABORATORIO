# ============================================================
# NUEVO: utilidades para “examen sin respuestas”
# (AJUSTE: quitar el texto "sin respuestas" en botones/labels)
# ============================================================

def _download_df_buttons(df: pd.DataFrame, filename_prefix: str):
    """
    Descarga el listado del examen (sin incluir claves/justificaciones).
    Nota: intencionalmente NO usamos el texto "sin respuestas" en la UI.
    """
    if df is None or df.empty:
        return
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Descargar CSV",
        data=csv,
        file_name=f"{filename_prefix}.csv",
        mime="text/csv",
    )


def _render_tab_examen_por_area(exam_pub: pd.DataFrame, filename_prefix: str):
    if exam_pub is None or exam_pub.empty:
        st.warning("No hay reactivos para mostrar en esta versión/carrera.")
        return

    st.markdown("#### Conteo de reactivos por área")
    resumen = (
        exam_pub.groupby("Area", as_index=False)["ID_reactivo"]
        .nunique()
        .rename(columns={"ID_reactivo": "Reactivos"})
        .sort_values("Reactivos", ascending=False)
    )
    st.dataframe(resumen, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Reactivos incluidos")
    detalle_cols = [
        c for c in ["Area", "Materia", "ID_reactivo", "Pregunta", "A", "B", "C", "D"]
        if c in exam_pub.columns
    ]
    detalle = exam_pub[detalle_cols].sort_values(["Area", "Materia", "ID_reactivo"])
    st.dataframe(detalle, use_container_width=True, hide_index=True)

    st.divider()
    _download_df_buttons(detalle, filename_prefix=filename_prefix + "_examen_por_area")


def _render_tab_examen_por_materia(exam_pub: pd.DataFrame, filename_prefix: str):
    if exam_pub is None or exam_pub.empty:
        st.warning("No hay reactivos para mostrar en esta versión/carrera.")
        return

    materias = sorted(
        [
            m
            for m in exam_pub["Materia"].dropna().unique().tolist()
            if str(m).strip() and str(m).lower() != "nan"
        ]
    )
    if not materias:
        st.warning("No encontré materias para listar.")
        return

    sel_m = st.selectbox("Materia", materias, index=0)
    df_m = exam_pub[exam_pub["Materia"] == sel_m].copy()

    st.caption(f"Reactivos incluidos en **{sel_m}**: **{df_m['ID_reactivo'].nunique():,}**")

    cols = [c for c in ["Materia", "Area", "ID_reactivo", "Pregunta", "A", "B", "C", "D"] if c in df_m.columns]
    df_m = df_m[cols].sort_values(["Area", "ID_reactivo"])

    st.dataframe(df_m, use_container_width=True, hide_index=True)

    st.divider()
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(sel_m))[:80]
    _download_df_buttons(df_m, filename_prefix=filename_prefix + f"_materia_{safe_name}")
