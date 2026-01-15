# encuesta_calidad.py
import pandas as pd
import streamlit as st
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
    "SAT": "Plataforma SEAC",  # PREPA: SAT -> SEAC
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

SHEET_PROCESADO = "PROCESADO"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"  # opcional


# ============================================================
# Helpers (mismos criterios que tu versión funcional; sin gráficas)
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _wrap_text(s: str, width: int = 40, max_lines: int = 3) -> str:
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


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _ensure_prepa_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Servicio" not in out.columns:
        out["Servicio"] = "Preparatoria"
    if "Carrera_Catalogo" not in out.columns:
        out["Carrera_Catalogo"] = "Preparatoria"
    return out


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


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    if vista == "Dirección General":
        return ""
    c = (carrera or "").strip().lower()
    if c == "preparatoria":
        return "Preparatoria"
    if c.startswith("licenciatura ejecutiva:") or c.startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"


def _best_carrera_col(df: pd.DataFrame) -> str | None:
    """
    Para Dirección General: elegir una sola columna para filtrar Carrera/Servicio,
    sin duplicar filtros.
    """
    candidates = [
        "Carrera_Catalogo",
        "Servicio",
        "Selecciona el programa académico que estudias",  # Virtual típico
        "Servicio de procedencia",  # Escolar típico (si quedó)
        "Programa",
        "Carrera",
    ]
    for c in candidates:
        if c in df.columns:
            vals = df[c].dropna().astype(str).str.strip()
            if vals.nunique() >= 2:
                return c
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _auto_classify_numcols(df: pd.DataFrame, num_cols: list[str]) -> tuple[list[str], list[str]]:
    """
    Clasifica columnas *_num por rango real de valores.
      - max > 1  => Likert (1–5)
      - max <= 1 => Sí/No (0/1)
    """
    if not num_cols:
        return [], []
    dnum = df[num_cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max(axis=0, skipna=True)
    likert_cols = [c for c in num_cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1.0]
    yesno_cols = [c for c in num_cols if c not in likert_cols]
    return likert_cols, yesno_cols


# ============================================================
# Carga desde Google Sheets (por URL según modalidad)
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm(t): t for t in titles}

    def resolve(sheet_name: str) -> str | None:
        return titles_norm.get(norm(sheet_name))

    ws_pro = resolve(SHEET_PROCESADO)
    ws_map = resolve(SHEET_MAPA)
    ws_cat = resolve(SHEET_CATALOGO)  # opcional

    missing = []
    if not ws_pro:
        missing.append(SHEET_PROCESADO)
    if not ws_map:
        missing.append(SHEET_MAPA)

    if missing:
        raise ValueError(
            "No encontré estas pestañas: "
            + ", ".join(missing)
            + " | Pestañas disponibles: "
            + ", ".join(titles)
        )

    def ws_to_df(ws_title: str) -> pd.DataFrame:
        ws = sh.worksheet(ws_title)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = [h.strip() for h in values[0]]
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df = ws_to_df(ws_pro)
    mapa = ws_to_df(ws_map)
    catalogo = ws_to_df(ws_cat) if ws_cat else pd.DataFrame()
    return df, mapa, catalogo


# ============================================================
# Render principal (TABLAS ÚNICAMENTE)
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # ---------------------------
    # Selección de modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox(
            "Modalidad",
            ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
            index=0,
        )
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    url = _get_url_for_modalidad(modalidad)

    # ---------------------------
    # Carga
    # ---------------------------
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            df, mapa, _catalogo = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar las hojas requeridas (PROCESADO / Mapa_Preguntas).")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja PROCESADO está vacía.")
        return

    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Validación mapa
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["header_num"] = mapa["header_num"].astype(str).str.strip()
    mapa["scale_code"] = mapa["scale_code"].astype(str).str.strip()
    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)

    if "section_name" in mapa.columns:
        mapa["section_name"] = mapa["section_name"].fillna("").astype(str).str.strip()
        mapa.loc[mapa["section_name"] == "", "section_name"] = mapa["section_code"]
    else:
        mapa["section_name"] = mapa["section_code"]

    # Parche nombres completos
    mapa["section_name"] = mapa["section_name"].astype(str).str.strip()
    mask_abbrev = (mapa["section_name"] == mapa["section_code"]) | (mapa["section_name"].str.len() <= 4)
    mapa.loc[mask_abbrev, "section_name"] = (
        mapa.loc[mask_abbrev, "section_code"].map(SECTION_LABELS).fillna(mapa.loc[mask_abbrev, "section_code"])
    )

    # Solo preguntas existentes
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Columnas numéricas
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    if not num_cols:
        st.warning("No encontré columnas *_num en PROCESADO. Verifica que tu PROCESADO tenga numéricos.")
        st.dataframe(df.head(30), use_container_width=True)
        return

    likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

    # ---------------------------
    # Filtros
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    if vista == "Dirección General":
        carrera_col = _best_carrera_col(df)
        carrera_sel = "(Todas)"

        c1, c2, c3 = st.columns([1.2, 1.0, 2.8])
        with c1:
            st.markdown(f"**Modalidad:** {modalidad}")
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        with c3:
            if carrera_col:
                opts = ["(Todas)"] + sorted(df[carrera_col].dropna().astype(str).str.strip().unique().tolist())
                carrera_sel = st.selectbox("Carrera/Servicio", opts, index=0)
            else:
                st.info("No encontré una columna válida para filtrar por Carrera/Servicio en PROCESADO.")
                carrera_col = None
                carrera_sel = "(Todas)"
    else:
        c1, c2 = st.columns([2.4, 1.2])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True)
        with c2:
            year_sel = st.selectbox("Año", years, index=0)

        carrera_col = None
        carrera_sel = (carrera or "").strip()

    st.divider()

    # ---------------------------
    # Aplicar filtros
    # ---------------------------
    f = df.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if vista == "Dirección General":
        if carrera_col and carrera_sel != "(Todas)":
            f = f[f[carrera_col].astype(str).str.strip() == str(carrera_sel).strip()]
    else:
        if modalidad == "Preparatoria":
            pass
        else:
            candidates = [
                c for c in ["Carrera_Catalogo", "Servicio", "Selecciona el programa académico que estudias"]
                if c in f.columns
            ]
            if not candidates:
                st.warning("No encontré columnas para filtrar por carrera en esta modalidad.")
                return

            target = str(carrera_sel).strip()
            mask = False
            for c in candidates:
                mask = mask | (f[c].astype(str).str.strip() == target)
            f = f[mask]

    st.caption(f"Hoja usada: **PROCESADO** | Registros filtrados: **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # ---------------------------
    # Tabs (sin gráficas)
    # ---------------------------
    tabs = ["Resumen", "Por sección"]
    if vista == "Dirección General":
        tabs.append("Comparativo entre carreras")
    tabs.append("Comentarios")
    tab_objs = st.tabs(tabs)

    tab1 = tab_objs[0]
    tab2 = tab_objs[1]
    tab_comp = tab_objs[2] if vista == "Dirección General" else None
    tab3 = tab_objs[-1]

    # ---------------------------
    # Resumen (solo tablas/metrics)
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
        st.markdown("### Promedio por sección (Likert) — tabla")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols)})

        if not rows:
            st.info("No hay datos suficientes para calcular promedios por sección (Likert) con los filtros actuales.")
        else:
            sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
            st.dataframe(sec_df, use_container_width=True)

        if yesno_cols:
            st.divider()
            st.markdown("### Sí/No (por pregunta) — % Sí (tabla)")

            yn_rows = []
            for _, m in mapa_ok.iterrows():
                col = m["header_num"]
                if col not in yesno_cols or col not in f.columns:
                    continue
                mean_val = _mean_numeric(f[col])
                if pd.isna(mean_val):
                    continue
                yn_rows.append({"Pregunta": m["header_exacto"], "% Sí": float(mean_val) * 100})

            yn_df = pd.DataFrame(yn_rows).sort_values("% Sí", ascending=False) if yn_rows else pd.DataFrame()
            if yn_df.empty:
                st.info("No hay datos Sí/No con los filtros actuales.")
            else:
                yn_df["Pregunta"] = yn_df["Pregunta"].apply(lambda x: _wrap_text(x, width=60, max_lines=3))
                st.dataframe(yn_df, use_container_width=True)

    # ---------------------------
    # Por sección (tablas por sección, sin charts)
    # ---------------------------
    with tab2:
        st.markdown("### Desglose por sección (preguntas) — tablas")

        rows = []
        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
            if not cols:
                continue
            val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
            if pd.isna(val):
                continue
            rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols), "sec_code": sec_code})

        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False) if rows else pd.DataFrame()

        if sec_df2.empty and not yesno_cols:
            st.info("No hay datos suficientes para mostrar secciones con los filtros actuales.")
            return

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

                    if col in yesno_cols:
                        qrows.append({"Tipo": "Sí/No", "Pregunta": m["header_exacto"], "% Sí": float(mean_val) * 100})
                    elif col in likert_cols:
                        qrows.append({"Tipo": "Likert", "Pregunta": m["header_exacto"], "Promedio": float(mean_val)})

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

                qdf["Pregunta"] = qdf["Pregunta"].apply(lambda x: _wrap_text(x, width=70, max_lines=4))

                qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                if not qdf_l.empty:
                    qdf_l = qdf_l.sort_values("Promedio", ascending=False)
                    st.markdown("**Preguntas Likert (1–5)**")
                    st.dataframe(qdf_l[["Pregunta", "Promedio"]].reset_index(drop=True), use_container_width=True)

                qdf_y = qdf[qdf["Tipo"] == "Sí/No"].copy()
                if not qdf_y.empty:
                    qdf_y = qdf_y.sort_values("% Sí", ascending=False)
                    st.markdown("**Preguntas Sí/No**")
                    st.dataframe(qdf_y[["Pregunta", "% Sí"]].reset_index(drop=True), use_container_width=True)

    # ---------------------------
    # Comparativo entre carreras (tablas únicamente)
    # ---------------------------
    if tab_comp:
        with tab_comp:
            st.markdown("### Comparativo entre carreras (por sección) — tablas")
            carrera_col = _best_carrera_col(f)
            if not carrera_col:
                st.warning("No encontré una columna válida de Carrera/Servicio para hacer el comparativo.")
                return

            # Si DG filtró a una carrera específica dentro del módulo, ya no hay comparativo real
            if vista == "Dirección General":
                # Cuando carrera_sel != (Todas), el usuario ya acotó
                if "carrera_sel" in locals() and carrera_sel != "(Todas)":
                    st.info("Para ver comparativo entre carreras, selecciona **(Todas)** en el filtro Carrera/Servicio.")
                    return

            # Armamos una tabla por sección
            for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                cols_l = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
                cols_y = [c for c in g["header_num"].tolist() if c in f.columns and c in yesno_cols]

                if not cols_l and not cols_y:
                    continue

                rows_comp = []
                for car, dfc in f.groupby(carrera_col):
                    car = str(car).strip()
                    if not car:
                        continue

                    row = {"Carrera": car}

                    if cols_l:
                        val_l = pd.to_numeric(dfc[cols_l].stack(), errors="coerce").mean()
                        row["Promedio Likert"] = float(val_l) if pd.notna(val_l) else pd.NA

                    if cols_y:
                        val_y = pd.to_numeric(dfc[cols_y].stack(), errors="coerce").mean() * 100
                        row["% Sí (Sí/No)"] = float(val_y) if pd.notna(val_y) else pd.NA

                    rows_comp.append(row)

                if not rows_comp:
                    continue

                comp_df = pd.DataFrame(rows_comp)

                # Orden: si hay Likert, ordenar por Likert; si no, por % Sí
                if "Promedio Likert" in comp_df.columns:
                    comp_df = comp_df.sort_values("Promedio Likert", ascending=False, na_position="last")
                elif "% Sí (Sí/No)" in comp_df.columns:
                    comp_df = comp_df.sort_values("% Sí (Sí/No)", ascending=False, na_position="last")

                st.subheader(sec_name)
                st.dataframe(comp_df.reset_index(drop=True), use_container_width=True)

    # ---------------------------
    # Comentarios
    # ---------------------------
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        open_cols = [
            c
            for c in f.columns
            if (not str(c).endswith("_num"))
            and any(k in str(c).lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "descr"])
        ]

        if not open_cols:
            st.info("No detecté columnas de comentarios con la heurística actual.")
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
