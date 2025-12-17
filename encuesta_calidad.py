import pandas as pd
import streamlit as st
import altair as alt
import gspread


# ---------------------------
# Config visible (nombres completos)
# ---------------------------
SECTION_LABELS = {
    "DIR": "Director/Coordinador",
    "APR": "Aprendizaje",
    "MAT": "Materiales en la plataforma",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Soporte académico / SEAC",
    "ADM": "Acceso a soporte administrativo",
    "COM": "Comunicación con compañeros",
    "PLAT": "Plataforma SEAC",
    "UDL": "Comunicación con la universidad",
    "REC": "Recomendación",
}

YESNO_COLS = {
    "ADM_ContactoExiste_num",
    "REC_Volveria_num",
    "REC_Recomendaria_num",
    "UDL_ViasComunicacion_num",
}


def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTROS"


def _is_yesno_col(col: str) -> bool:
    return col in YESNO_COLS


def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


@st.cache_data(show_spinner=False)
def _load_from_gsheets(sheet_id: str):
    sa = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_key(sheet_id)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    targets = {
        "Respuestas": "Respuestas",
        "Mapa_Preguntas": "Mapa_Preguntas",
        "Catalogo_Servicio": "Catalogo_Servicio",
    }
    targets_norm = {k: norm(v) for k, v in targets.items()}

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm(t): t for t in titles}

    missing = []
    resolved = {}
    for key, tnorm in targets_norm.items():
        if tnorm in titles_norm:
            resolved[key] = titles_norm[tnorm]
        else:
            missing.append(targets[key])

    if missing:
        raise ValueError(
            "No encontré estas pestañas: "
            + ", ".join(missing)
            + " | Pestañas disponibles para el service account: "
            + ", ".join(titles)
        )

    ws_resp = sh.worksheet(resolved["Respuestas"])
    ws_map = sh.worksheet(resolved["Mapa_Preguntas"])
    ws_cat = sh.worksheet(resolved["Catalogo_Servicio"])

    def make_unique_headers(raw_headers):
        # Evita crash por duplicados tipo "¿Por qué?"
        seen_base = {}
        used_final = set()
        out = []
        prev_nonempty = ""

        for h in raw_headers:
            base = (h or "").strip()
            if base == "":
                base = "SIN_TITULO"

            seen_base[base] = seen_base.get(base, 0) + 1
            is_dup = seen_base[base] > 1

            if base.lower().startswith("¿por qué") and (is_dup or base in used_final):
                candidate = f"{base} — {prev_nonempty}" if prev_nonempty else base
            elif is_dup or base in used_final:
                candidate = f"{base} ({seen_base[base]})"
            else:
                candidate = base

            while candidate in used_final:
                candidate = f"{candidate}*"

            out.append(candidate)
            used_final.add(candidate)

            if base != "SIN_TITULO":
                prev_nonempty = base

        return out

    def ws_to_df(ws):
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = make_unique_headers(values[0])
        rows = values[1:]
        df_local = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
        return df_local

    df = ws_to_df(ws_resp)
    mapa = ws_to_df(ws_map)
    catalogo = ws_to_df(ws_cat)
    return df, mapa, catalogo


def _merge_catalogo(df: pd.DataFrame, catalogo: pd.DataFrame) -> pd.DataFrame:
    cat = catalogo.copy()
    cat.columns = [c.strip().lower() for c in cat.columns]

    if "programa" not in cat.columns or "servicio" not in cat.columns:
        return df

    key = "Selecciona el programa académico que estudias"
    if key not in df.columns:
        return df

    cat["programa"] = cat["programa"].astype(str).str.strip()
    cat["servicio"] = cat["servicio"].astype(str).str.strip()

    out = df.copy()
    out[key] = out[key].astype(str).str.strip()

    cols = ["programa", "servicio"]
    if "carrera" in cat.columns:
        cat["carrera"] = cat["carrera"].astype(str).str.strip()
        cols.append("carrera")

    out = out.merge(cat[cols], how="left", left_on=key, right_on="programa")
    out.drop(columns=["programa"], inplace=True, errors="ignore")

    out.rename(columns={"servicio": "Servicio"}, inplace=True)
    if "carrera" in out.columns:
        out.rename(columns={"carrera": "Carrera_Catalogo"}, inplace=True)

    out["Servicio"] = out["Servicio"].fillna("SIN_CLASIFICAR")
    return out


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _chart_likert_by_question(df_q: pd.DataFrame):
    # df_q columns: Pregunta, Promedio
    if df_q.empty:
        return None
    base = df_q.copy()
    base["Promedio"] = pd.to_numeric(base["Promedio"], errors="coerce")
    base = base.dropna(subset=["Promedio"])
    if base.empty:
        return None

    return (
        alt.Chart(base)
        .mark_bar()
        .encode(
            x=alt.X("Promedio:Q", scale=alt.Scale(domain=[1, 5])),
            y=alt.Y("Pregunta:N", sort="-x"),
            tooltip=[alt.Tooltip("Promedio:Q", format=".2f"), "Pregunta:N"],
        )
    )


def _chart_yesno_by_question(df_q: pd.DataFrame):
    # df_q columns: Pregunta, % Sí
    if df_q.empty:
        return None
    base = df_q.copy()
    base["% Sí"] = pd.to_numeric(base["% Sí"], errors="coerce")
    base = base.dropna(subset=["% Sí"])
    if base.empty:
        return None

    return (
        alt.Chart(base)
        .mark_bar()
        .encode(
            x=alt.X("% Sí:Q", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("Pregunta:N", sort="-x"),
            tooltip=[alt.Tooltip("% Sí:Q", format=".1f"), "Pregunta:N"],
        )
    )


def render_encuesta_calidad(vista: str, carrera: str | None):
    st.subheader("Encuesta de calidad")

    sheet_id = st.secrets.get("app", {}).get("sheet_id", "")
    if not sheet_id:
        st.error("Falta app.sheet_id en Secrets.")
        return

    with st.spinner("Cargando datos oficiales (Google Sheets)…"):
        df, mapa, catalogo = _load_from_gsheets(sheet_id)

    df = _merge_catalogo(df, catalogo)

    if "Marca temporal" in df.columns:
        df["Marca temporal"] = _to_datetime_safe(df["Marca temporal"])

    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' no trae: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["section_code"] = mapa["header_num"].astype(str).apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    num_cols = [c for c in df.columns if c.endswith("_num")]
    likert_cols = [c for c in num_cols if not _is_yesno_col(c)]
    yesno_cols = [c for c in num_cols if _is_yesno_col(c)]

    # ---------------------------
    # Filtros (limpios: sin Programa; Carrera solo en Dirección General)
    # ---------------------------
    with st.sidebar:
        st.markdown("### Filtros – Encuesta de calidad")

        if "Servicio" in df.columns:
            servicios = ["(Todos)"] + sorted(df["Servicio"].dropna().unique().tolist())
            servicio_sel = st.selectbox("Servicio", servicios, index=0)
        else:
            servicio_sel = "(Todos)"

        if vista == "Dirección General" and "Carrera_Catalogo" in df.columns:
            carreras = ["(Todas)"] + sorted(df["Carrera_Catalogo"].dropna().unique().tolist())
            carrera_sel = st.selectbox("Carrera", carreras, index=0)
        else:
            carrera_sel = "(Todas)"

        if "Marca temporal" in df.columns and df["Marca temporal"].notna().any():
            dmin = df["Marca temporal"].min().date()
            dmax = df["Marca temporal"].max().date()
            dr = st.date_input("Rango de fechas", value=(dmin, dmax))
        else:
            dr = None

    f = df.copy()

    if servicio_sel != "(Todos)" and "Servicio" in f.columns:
        f = f[f["Servicio"] == servicio_sel]

    if vista == "Director de carrera" and carrera and "Carrera_Catalogo" in f.columns:
        f = f[f["Carrera_Catalogo"] == carrera]

    if vista == "Dirección General" and carrera_sel != "(Todas)" and "Carrera_Catalogo" in f.columns:
        f = f[f["Carrera_Catalogo"] == carrera_sel]

    if dr and "Marca temporal" in f.columns and len(dr) == 2:
        f = f[(f["Marca temporal"].dt.date >= dr[0]) & (f["Marca temporal"].dt.date <= dr[1])]

    st.caption(f"Registros filtrados: **{len(f)}**")

    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

    # ---------------------------
    # Resumen (global + promedios por sección)
    # ---------------------------
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", f"{len(f)}")

        if likert_cols:
            overall = pd.to_numeric(f[likert_cols].stack(), errors="coerce").mean()
            c2.metric("Promedio global (Likert 1–5)", f"{overall:.2f}" if pd.notna(overall) else "—")
        else:
            c2.metric("Promedio global (Likert 1–5)", "—")

        if yesno_cols:
            pct_yes = pd.to_numeric(f[yesno_cols].stack(), errors="coerce").mean() * 100
            c3.metric("% Sí (preguntas Sí/No)", f"{pct_yes:.1f}%" if pd.notna(pct_yes) else "—")
        else:
            c3.metric("% Sí (preguntas Sí/No)", "—")

        st.divider()
        st.markdown("### Promedio por sección (Likert 1–5)")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        if sec_df.empty:
            st.info("No hay datos suficientes para calcular promedios por sección con los filtros actuales.")
            return

        st.dataframe(sec_df.drop(columns=["sec_code"]), use_container_width=True)

        st.altair_chart(
            alt.Chart(sec_df).mark_bar().encode(
                x=alt.X("Sección:N", sort="-y"),
                y=alt.Y("Promedio:Q"),
                tooltip=["Sección", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
            ),
            use_container_width=True,
        )

    # ---------------------------
    # Por sección (PROMEDIO + barras por pregunta dentro de cada sección)
    # ---------------------------
    with tab2:
        st.markdown("### Desglose por sección (comparativo de preguntas)")

        # recalcular sec_df por seguridad (aunque exista)
        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})
        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        for _, r in sec_df.iterrows():
            sec_code = r["sec_code"]
            sec_name = r["Sección"]
            sec_avg = r["Promedio"]

            with st.expander(f"{sec_name} — Promedio: {sec_avg:.2f}", expanded=False):
                mm = mapa_ok[mapa_ok["section_code"] == sec_code].copy()

                qrows = []
                for _, m in mm.iterrows():
                    col = m["header_num"]
                    if col not in f.columns:
                        continue
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue

                    qrows.append(
                        {
                            "Pregunta": m["header_exacto"],
                            "Promedio": mean_val if not _is_yesno_col(col) else None,
                            "% Sí": (mean_val * 100) if _is_yesno_col(col) else None,
                            "Tipo": "Sí/No" if _is_yesno_col(col) else "Likert",
                        }
                    )

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

                # Likert: tabla + gráfica (barras por pregunta)
                qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                if not qdf_l.empty:
                    qdf_l = qdf_l.sort_values("Promedio", ascending=False)
                    st.markdown("**Preguntas Likert (1–5)**")

                    show_l = qdf_l[["Pregunta", "Promedio"]].reset_index(drop=True)
                    st.dataframe(show_l, use_container_width=True)

                    chart_l = _chart_likert_by_question(show_l)
                    if chart_l is not None:
                        st.altair_chart(chart_l, use_container_width=True)

                # Sí/No: tabla + gráfica (barras por pregunta)
                qdf_y = qdf[qdf["Tipo"] == "Sí/No"].copy()
                if not qdf_y.empty:
                    qdf_y = qdf_y.sort_values("% Sí", ascending=False)
                    st.markdown("**Preguntas Sí/No**")

                    show_y = qdf_y[["Pregunta", "% Sí"]].reset_index(drop=True)
                    st.dataframe(show_y, use_container_width=True)

                    chart_y = _chart_yesno_by_question(show_y)
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # ---------------------------
    # Comentarios
    # ---------------------------
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        open_cols = [
            c
            for c in f.columns
            if (not c.endswith("_num"))
            and any(k in c.lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo"])
        ]

        if not open_cols:
            st.info("No detecté columnas de comentarios con la heurística actual.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
