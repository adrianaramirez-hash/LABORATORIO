# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Etiquetas de secciones (fallback si Mapa_Preguntas no trae section_name)
# ============================================================
SECTION_LABELS = {
    # Director / coordinación
    "DIR": "Director/Coordinación",

    # Servicios generales / administrativos
    "SER": "Servicios (Administrativos/Generales)",
    "ADM": "Acceso a soporte administrativo",

    # Académico
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del conocimiento",

    # SEAC / Plataforma
    "SEAC": "Plataforma SEAC",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",  # <<< PREPA: sat -> SEAC

    # Materiales / comunicación
    "MAT": "Materiales en la plataforma",
    "UDL": "Comunicación con la Universidad",
    "COM": "Comunicación con compañeros",

    # Instalaciones / ambiente
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",

    # Cierre
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

# Sí/No a num (0/1) si existieran en tu PROCESADO (puedes ampliar)
YESNO_COLS = {
    "REC_Recomendaria_num",
    "REC_Volveria_num",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


def _is_yesno_col(col: str) -> bool:
    return col in YESNO_COLS


def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _wrap_text(s: str, width: int = 18, max_lines: int = 3) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    if not s:
        return ""
    lines = textwrap.wrap(s, width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    kept[-1] = (kept[-1][:-1] + "…") if len(kept[-1]) >= 1 else "…"
    return "\n".join(kept)


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _bar_chart_auto(
    df_in: pd.DataFrame,
    category_col: str,
    value_col: str,
    value_domain: list,
    value_title: str,
    tooltip_cols: list,
    max_vertical: int,
    wrap_width_vertical: int = 18,
    wrap_width_horizontal: int = 30,
    height_per_row: int = 28,
    base_height: int = 260,
    hide_category_labels: bool = True,
):
    if df_in is None or df_in.empty:
        return None

    df = df_in.copy()
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[value_col])
    if df.empty:
        return None

    n = len(df)

    cat_axis_vertical = alt.Axis(
        title=None,
        labels=not hide_category_labels,
        ticks=not hide_category_labels,
        labelAngle=0,
        labelLimit=0,
    )
    cat_axis_horizontal = alt.Axis(
        title=None,
        labels=not hide_category_labels,
        ticks=not hide_category_labels,
        labelLimit=0,
    )

    if n <= max_vertical:
        df["_cat_wrapped"] = df[category_col].apply(
            lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3)
        )
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X(
                    "_cat_wrapped:N",
                    sort=alt.SortField(field=value_col, order="descending"),
                    axis=cat_axis_vertical,
                ),
                y=alt.Y(
                    f"{value_col}:Q",
                    scale=alt.Scale(domain=value_domain),
                    axis=alt.Axis(title=value_title),
                ),
                tooltip=tooltip_cols,
            )
            .properties(height=max(320, base_height))
        )

    df["_cat_wrapped"] = df[category_col].apply(
        lambda x: _wrap_text(x, width=wrap_width_horizontal, max_lines=3)
    )
    dynamic_height = max(base_height, n * height_per_row)

    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(
                "_cat_wrapped:N",
                sort=alt.SortField(field=value_col, order="descending"),
                axis=cat_axis_horizontal,
            ),
            x=alt.X(
                f"{value_col}:Q",
                scale=alt.Scale(domain=value_domain),
                axis=alt.Axis(title=value_title),
            ),
            tooltip=tooltip_cols,
        )
        .properties(height=dynamic_height)
    )


# ============================================================
# Carga desde Google Sheets (por URL según modalidad)
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    # gcp_service_account_json debe estar como dict en secrets (o mapeable a dict)
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    targets = {
        "PROCESADO": "PROCESADO",
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
            + " | Pestañas disponibles: "
            + ", ".join(titles)
        )

    def ws_to_df(ws):
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = values[0]
        rows = values[1:]
        # PROCESADO debe tener encabezados únicos (incluye _num) para evitar duplicados.
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df = ws_to_df(sh.worksheet(resolved["PROCESADO"]))
    mapa = ws_to_df(sh.worksheet(resolved["Mapa_Preguntas"]))
    catalogo = ws_to_df(sh.worksheet(resolved["Catalogo_Servicio"]))
    return df, mapa, catalogo


def _get_url_for_modalidad(modalidad: str) -> str:
    URL_KEYS = {
        "Virtual / Mixto": "EC_VIRTUAL_URL",
        "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
        "Preparatoria": "EC_PREPA_URL",
    }
    key = URL_KEYS.get(modalidad)
    if not key:
        raise KeyError(f"Modalidad no reconocida: {modalidad}")
    url = st.secrets.get(key, "").strip()
    if not url:
        raise KeyError(f"Falta configurar {key} en Secrets.")
    return url


def _modalidades():
    return ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"]


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _ensure_prepa_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Prepa no trae servicio/carrera; forzamos columnas estándar si faltan
    out = df.copy()
    if "Servicio" not in out.columns:
        out["Servicio"] = "Preparatoria"
    if "Carrera_Catalogo" not in out.columns:
        out["Carrera_Catalogo"] = "Preparatoria"
    return out


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    # Regla práctica para director:
    # - si selecciona "Preparatoria" en app.py -> Preparatoria
    # - si empieza con "Licenciatura Ejecutiva:" -> Escolarizado/Ejecutivas
    # - default -> Escolarizado/Ejecutivas
    if vista == "Dirección General":
        return ""
    if (carrera or "").strip().lower() == "preparatoria":
        return "Preparatoria"
    if (carrera or "").strip().lower().startswith("licenciatura ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # ---------------------------
    # Selección de modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", _modalidades(), index=0)
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    url = _get_url_for_modalidad(modalidad)

    # ---------------------------
    # Carga
    # ---------------------------
    with st.spinner("Cargando datos (Google Sheets)…"):
        df, mapa, catalogo = _load_from_gsheets_by_url(url)

    if df.empty:
        st.warning("La hoja PROCESADO está vacía.")
        return

    # Prepa: normalizar columnas para filtros
    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Mapa mínimo
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()

    # Sección: usar section_name si existe en Mapa_Preguntas; si no, fallback por prefijo.
    mapa["section_code"] = mapa["header_num"].astype(str).apply(_section_from_numcol)
    if "section_name" in mapa.columns:
        mapa["section_name"] = mapa["section_name"].fillna("").astype(str).str.strip()
        mapa.loc[mapa["section_name"] == "", "section_name"] = (
            mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
        )
    else:
        mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])

    # ============================
    # PARCHE: forzar nombres completos si vienen abreviados
    # (ej. "DIR", "SER", "SAT")
    # ============================
    mapa["section_name"] = mapa["section_name"].astype(str).str.strip()
    mask_abbrev = (mapa["section_name"] == mapa["section_code"]) | (mapa["section_name"].str.len() <= 4)
    mapa.loc[mask_abbrev, "section_name"] = (
        mapa.loc[mask_abbrev, "section_code"].map(SECTION_LABELS).fillna(mapa.loc[mask_abbrev, "section_code"])
    )

    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Num cols
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    likert_cols = [c for c in num_cols if not _is_yesno_col(c)]
    yesno_cols = [c for c in num_cols if _is_yesno_col(c)]

    # ---------------------------
    # Filtros (sin duplicar)
    # ---------------------------
    # Reglas:
    # - Dirección General: Modalidad + Año + Carrera/Programa (solo una)
    # - Director de carrera: Año (y su carrera viene de app.py)
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    if vista == "Dirección General":
        carreras = ["(Todas)"]
        if "Carrera_Catalogo" in df.columns:
            carreras += sorted(df["Carrera_Catalogo"].dropna().astype(str).unique().tolist())

        c1, c2, c3 = st.columns([1.3, 1.0, 2.2])
        with c1:
            st.markdown(f"**Modalidad:** {modalidad}")
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        with c3:
            carrera_sel = st.selectbox("Carrera", carreras, index=0)
    else:
        c1, c2 = st.columns([1.2, 2.8])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True)
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        carrera_sel = carrera  # fija

    st.divider()

    # ---------------------------
    # Aplicar filtros
    # ---------------------------
    f = df.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if vista == "Director de carrera":
        if not carrera_sel:
            st.info("Selecciona una carrera arriba para ver resultados.")
            return
        if "Carrera_Catalogo" in f.columns:
            f = f[f["Carrera_Catalogo"].astype(str) == str(carrera_sel)]
    else:
        if carrera_sel != "(Todas)" and "Carrera_Catalogo" in f.columns:
            f = f[f["Carrera_Catalogo"].astype(str) == str(carrera_sel)]

    st.caption(f"Hoja usada: **PROCESADO** | Registros filtrados: **{len(f)}**")

    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # ---------------------------
    # Tabs
    # ---------------------------
    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

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
            rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols), "sec_code": sec_code})

        if not rows:
            st.info("No hay datos suficientes para calcular promedios por sección con los filtros actuales.")
            return

        sec_df = pd.DataFrame(rows)
        if "Promedio" in sec_df.columns:
            sec_df = sec_df.sort_values("Promedio", ascending=False)

        st.dataframe(sec_df.drop(columns=["sec_code"], errors="ignore"), use_container_width=True)

        sec_chart = _bar_chart_auto(
            df_in=sec_df,
            category_col="Sección",
            value_col="Promedio",
            value_domain=[1, 5],
            value_title="Promedio",
            tooltip_cols=["Sección", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
            max_vertical=MAX_VERTICAL_SECTIONS,
            wrap_width_vertical=16,
            wrap_width_horizontal=28,
            base_height=300,
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
            rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols), "sec_code": sec_code})

        if not rows:
            st.info("No hay datos suficientes para mostrar secciones con los filtros actuales.")
            return

        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False)

        for _, r in sec_df2.iterrows():
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
                            "Promedio": float(mean_val) if not _is_yesno_col(col) else None,
                            "% Sí": float(mean_val) * 100 if _is_yesno_col(col) else None,
                            "Tipo": "Sí/No" if _is_yesno_col(col) else "Likert",
                        }
                    )

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

                # Likert
                qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                if not qdf_l.empty:
                    qdf_l = qdf_l.sort_values("Promedio", ascending=False)
                    st.markdown("**Preguntas Likert (1–5)**")

                    show_l = qdf_l[["Pregunta", "Promedio"]].reset_index(drop=True)
                    st.dataframe(show_l, use_container_width=True)

                    chart_l = _bar_chart_auto(
                        df_in=show_l,
                        category_col="Pregunta",
                        value_col="Promedio",
                        value_domain=[1, 5],
                        value_title="Promedio",
                        tooltip_cols=[
                            alt.Tooltip("Promedio:Q", format=".2f"),
                            alt.Tooltip("Pregunta:N", title="Pregunta"),
                        ],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=18,
                        wrap_width_horizontal=34,
                        base_height=320,
                        hide_category_labels=True,
                    )
                    if chart_l is not None:
                        st.altair_chart(chart_l, use_container_width=True)

                # Sí/No
                qdf_y = qdf[qdf["Tipo"] == "Sí/No"].copy()
                if not qdf_y.empty:
                    qdf_y = qdf_y.sort_values("% Sí", ascending=False)
                    st.markdown("**Preguntas Sí/No**")

                    show_y = qdf_y[["Pregunta", "% Sí"]].reset_index(drop=True)
                    st.dataframe(show_y, use_container_width=True)

                    chart_y = _bar_chart_auto(
                        df_in=show_y,
                        category_col="Pregunta",
                        value_col="% Sí",
                        value_domain=[0, 100],
                        value_title="% Sí",
                        tooltip_cols=[
                            alt.Tooltip("% Sí:Q", format=".1f"),
                            alt.Tooltip("Pregunta:N", title="Pregunta"),
                        ],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=18,
                        wrap_width_horizontal=34,
                        base_height=320,
                        hide_category_labels=True,
                    )
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
            if (not str(c).endswith("_num"))
            and any(k in str(c).lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo"])
        ]

        if not open_cols:
            st.info("No detecté columnas de comentarios con la heurística actual.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
