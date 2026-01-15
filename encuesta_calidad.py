    # ---------------------------
    # Tabs
    # ---------------------------
    tabs = ["Resumen", "Por sección"]

    if vista == "Dirección General":
        tabs.append("Comparativo entre carreras")

    tabs.append("Comentarios")

    tab_objs = st.tabs(tabs)

    tab1 = tab_objs[0]
    tab2 = tab_objs[1]

    idx = 2
    if vista == "Dirección General":
        tab_comp = tab_objs[idx]
        idx += 1
    else:
        tab_comp = None

    tab3 = tab_objs[idx]

    # ---------------------------
    # Resumen
    # ---------------------------
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", f"{len(f)}")

        if likert_cols:
            overall = pd.to_numeric(f[likert_cols].stack(), errors="coerce").mean()
            c2.metric("Promedio global (Likert)", f"{overall:.2f}" if pd.notna(overall) else "—")
        else:
            c2.metric("Promedio global (Likert)", "—")

        if yesno_cols:
            pct_yes = pd.to_numeric(f[yesno_cols].stack(), errors="coerce").mean() * 100
            c3.metric("% Sí (Sí/No)", f"{pct_yes:.1f}%" if pd.notna(pct_yes) else "—")
        else:
            c3.metric("% Sí (Sí/No)", "—")

        st.divider()
        st.markdown("### Promedio por sección (Likert)")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({
                "Sección": sec_name,
                "Promedio": float(val),
                "Preguntas": len(cols),
                "sec_code": sec_code
            })

        if rows:
            sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
            st.dataframe(sec_df.drop(columns=["sec_code"]), use_container_width=True)

            sec_chart = _bar_chart_auto(
                df_in=sec_df,
                category_col="Sección",
                value_col="Promedio",
                value_domain=[1, 5],
                value_title="Promedio",
                tooltip_cols=[
                    "Sección",
                    alt.Tooltip("Promedio:Q", format=".2f"),
                    "Preguntas"
                ],
                max_vertical=MAX_VERTICAL_SECTIONS,
                wrap_width_vertical=22,
                wrap_width_horizontal=36,
                base_height=320,
                hide_category_labels=True,
            )
            if sec_chart is not None:
                st.altair_chart(sec_chart, use_container_width=True)

    # ---------------------------
    # Por sección
    # ---------------------------
    with tab2:
        st.markdown("### Desglose por sección (preguntas)")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({
                "Sección": sec_name,
                "Promedio": float(val),
                "Preguntas": len(cols),
                "sec_code": sec_code
            })

        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False) if rows else pd.DataFrame()

        for _, r in sec_df2.iterrows():
            with st.expander(f"{r['Sección']} — Promedio: {r['Promedio']:.2f}", expanded=False):
                mm = mapa_ok[mapa_ok["section_code"] == r["sec_code"]]
                qrows = []

                for _, m in mm.iterrows():
                    col = m["header_num"]
                    if col not in f.columns:
                        continue
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue

                    if col in yesno_cols:
                        qrows.append({"Pregunta": m["header_exacto"], "% Sí": mean_val * 100})
                    elif col in likert_cols:
                        qrows.append({"Pregunta": m["header_exacto"], "Promedio": mean_val})

                qdf = pd.DataFrame(qrows)
                if not qdf.empty:
                    st.dataframe(qdf, use_container_width=True)

    # ---------------------------
    # Comparativo entre carreras (solo Dirección General)
    # ---------------------------
    if vista == "Dirección General" and tab_comp is not None:
        with tab_comp:
            st.markdown("### Comparativo entre carreras por sección")
            st.caption(
                "Promedios Likert (1–5) por sección, comparando todas las carreras "
                "de la modalidad seleccionada."
            )

            carrera_col = _best_carrera_col(f)
            if not carrera_col:
                st.warning("No se encontró una columna válida para identificar la carrera.")
                st.stop()

            for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                cols = [
                    c for c in g["header_num"].tolist()
                    if c in f.columns and c in likert_cols
                ]
                if not cols:
                    continue

                rows = []
                for carrera_val, df_c in f.groupby(carrera_col):
                    vals = pd.to_numeric(df_c[cols].stack(), errors="coerce")
                    mean_val = vals.mean()
                    if pd.isna(mean_val):
                        continue

                    rows.append({
                        "Carrera": str(carrera_val),
                        "Promedio": round(float(mean_val), 2),
                        "Respuestas": len(df_c),
                    })

                if not rows:
                    continue

                sec_df = (
                    pd.DataFrame(rows)
                    .sort_values("Promedio", ascending=False)
                    .reset_index(drop=True)
                )

                with st.expander(f"{sec_name}", expanded=False):
                    st.dataframe(sec_df, use_container_width=True)

    # ---------------------------
    # Comentarios
    # ---------------------------
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        open_cols = [
            c for c in f.columns
            if (not str(c).endswith("_num"))
            and any(k in str(c).lower() for k in [
                "¿por qué", "comentario", "sugerencia",
                "escríbelo", "escribelo", "descr"
            ])
        ]

        if not open_cols:
            st.info("No detecté columnas de comentarios con la heurística actual.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
