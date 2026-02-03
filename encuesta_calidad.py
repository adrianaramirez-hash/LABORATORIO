import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import re

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

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

SHEET_PROCESADO = "PROCESADO"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"  # opcional

# ============================================================
# DF (Finanzas) - Fuente directa (ya numérica)
# ============================================================
FINANZAS_SHEET_ID = "11qszwEcEA6vvy7XYGo-w_WkqPp1kxoNG5GfJB_Wcc4A"
FINANZAS_SHEET_NAME = "VISTA_FINANZAS_NUM"

# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTR"


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
    candidates = [
        "Carrera_Catalogo",
        "Servicio",
        "Selecciona el programa académico que estudias",
        "Servicio de procedencia",
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


def _auto_classify_numcols(df: pd.DataFrame, cols: list[str]) -> tuple[list[str], list[str]]:
    if not cols:
        return [], []
    dnum = df[cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max(axis=0, skipna=True)
    likert_cols = [c for c in cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1.0]
    yesno_cols = [c for c in cols if c not in likert_cols]
    return likert_cols, yesno_cols


def _is_open_text_col(colname: str) -> bool:
    s = str(colname).strip().lower()
    return any(k in s for k in ["¿por qué", "por qué", "comentario", "sugerencia", "escríbelo", "escribelo"])


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
# Carga DF (Finanzas) desde VISTA_FINANZAS_NUM
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_finanzas_num():
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_key(FINANZAS_SHEET_ID)
    ws = sh.worksheet(FINANZAS_SHEET_NAME)

    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
    return df


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    # Normalizar vista
    if not vista:
        vista = "Dirección General"
    vista = str(vista).strip()

    # ========================================================
    # CAMINO DF (Dirección Finanzas): usa VISTA_FINANZAS_NUM
    # ========================================================
    if vista == "Dirección Finanzas":
        st.caption("Vista restringida para Dirección de Finanzas (solo datos administrativos autorizados).")

        try:
            with st.spinner("Cargando datos (Finanzas)…"):
                df = _load_finanzas_num()
        except Exception as e:
            st.error("No se pudo cargar la hoja VISTA_FINANZAS_NUM.")
            st.exception(e)
            return

        if df.empty:
            st.warning("La hoja VISTA_FINANZAS_NUM está vacía.")
            return

        # Fecha
        fecha_col = _pick_fecha_col(df)
        if fecha_col:
            df[fecha_col] = _to_datetime_safe(df[fecha_col])

        # Filtro por año (opcional)
        years = ["(Todos)"]
        if fecha_col and df[fecha_col].notna().any():
            years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

        c1, c2 = st.columns([2.4, 1.2])
        with c1:
            st.markdown("**Fuente:** VISTA_FINANZAS_NUM")
        with c2:
            year_sel = st.selectbox("Año", years, index=0)

        f = df.copy()
        if year_sel != "(Todos)" and fecha_col:
            f = f[f[fecha_col].dt.year == int(year_sel)]

        st.divider()
        st.caption(f"Registros filtrados: **{len(f)}**")
        if len(f) == 0:
            st.warning("No hay registros con los filtros seleccionados.")
            return

        # Detectar columnas numéricas candidatas:
        # - excluimos timestamp/programa
        # - excluimos abiertas
        base_exclude = set()
        for c in ["Marca temporal", "Marca Temporal", "Selecciona el programa académico que estudias"]:
            if c in f.columns:
                base_exclude.add(c)

        num_candidates = []
        for c in f.columns:
            if c in base_exclude:
                continue
            if _is_open_text_col(c):
                continue
            # si tiene al menos un número parseable, lo tomamos
            s = pd.to_numeric(f[c], errors="coerce")
            if s.notna().any():
                num_candidates.append(c)

        if not num_candidates:
            st.warning("No encontré columnas numéricas en VISTA_FINANZAS_NUM (revisa que el script haya convertido).")
            st.dataframe(f.head(30), use_container_width=True)
            return

        # Clasificar Likert vs Sí/No
        likert_cols, yesno_cols = _auto_classify_numcols(f, num_candidates)

        tab1, tab2 = st.tabs(["Resumen", "Por pregunta"])

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

            if likert_cols:
                st.markdown("### Likert (1–5) — Promedio por pregunta")
                rows = []
                for col in likert_cols:
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue
                    rows.append({"Pregunta": col, "Promedio": float(mean_val)})

                d = pd.DataFrame(rows).sort_values("Promedio", ascending=False) if rows else pd.DataFrame()
                if not d.empty:
                    st.dataframe(d, use_container_width=True)
                    ch = _bar_chart_auto(
                        df_in=d,
                        category_col="Pregunta",
                        value_col="Promedio",
                        value_domain=[1, 5],
                        value_title="Promedio",
                        tooltip_cols=[alt.Tooltip("Promedio:Q", format=".2f"), alt.Tooltip("Pregunta:N")],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
                        hide_category_labels=True,
                    )
                    if ch is not None:
                        st.altair_chart(ch, use_container_width=True)
                else:
                    st.info("Sin datos Likert suficientes con los filtros actuales.")

            if yesno_cols:
                st.divider()
                st.markdown("### Sí/No — % Sí por pregunta")
                rows = []
                for col in yesno_cols:
                    mean_val = _mean_numeric(f[col])
                    if pd.isna(mean_val):
                        continue
                    rows.append({"Pregunta": col, "% Sí": float(mean_val) * 100})

                d = pd.DataFrame(rows).sort_values("% Sí", ascending=False) if rows else pd.DataFrame()
                if not d.empty:
                    st.dataframe(d, use_container_width=True)
                    ch = _bar_chart_auto(
                        df_in=d,
                        category_col="Pregunta",
                        value_col="% Sí",
                        value_domain=[0, 100],
                        value_title="% Sí",
                        tooltip_cols=[alt.Tooltip("% Sí:Q", format=".1f"), alt.Tooltip("Pregunta:N")],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
                        hide_category_labels=True,
                    )
                    if ch is not None:
                        st.altair_chart(ch, use_container_width=True)
                else:
                    st.info("Sin datos Sí/No suficientes con los filtros actuales.")

        # ---------------------------
        # Por pregunta (detalle)
        # ---------------------------
        with tab2:
            st.markdown("### Detalle por pregunta")
            tipo_sel = st.radio("Tipo", ["Likert (1–5)", "Sí/No (0–1)"], horizontal=True)

            cols = likert_cols if "Likert" in tipo_sel else yesno_cols
            if not cols:
                st.info("No hay preguntas de este tipo con los filtros actuales.")
                return

            pregunta = st.selectbox("Pregunta", cols)
            s = pd.to_numeric(f[pregunta], errors="coerce").dropna()
            st.caption(f"Respuestas válidas: {len(s)}")

            if "Likert" in tipo_sel:
                st.metric("Promedio", f"{s.mean():.2f}" if len(s) else "—")
            else:
                st.metric("% Sí", f"{(s.mean() * 100):.1f}%" if len(s) else "—")

            # distribución simple
            dist = s.value_counts(dropna=True).sort_index()
            dist_df = dist.reset_index()
            dist_df.columns = ["Valor", "Frecuencia"]

            ch = (
                alt.Chart(dist_df)
                .mark_bar()
                .encode(
                    x=alt.X("Valor:O", title="Valor"),
                    y=alt.Y("Frecuencia:Q", title="Frecuencia"),
                    tooltip=["Valor", "Frecuencia"],
                )
                .properties(height=320)
            )
            st.altair_chart(ch, use_container_width=True)

        return  # IMPORTANTE: DF termina aquí

    # ========================================================
    # CAMINO ORIGINAL (DG / DC): PROCESADO + MAPA
    # ========================================================

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

    # Clasificación robusta Likert vs Sí/No
    likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

    # ---------------------------
    # Filtros
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    carrera_param_fija = (carrera is not None) and str(carrera).strip() != ""

    if vista == "Dirección General":
        carrera_col = _best_carrera_col(df)
        carrera_sel = "(Todas)"

        c1, c2, c3 = st.columns([1.2, 1.0, 2.8])
        with c1:
            st.markdown(f"**Modalidad:** {modalidad}")
        with c2:
            year_sel = st.selectbox("Año", years, index=0)
        with c3:
            if carrera_param_fija:
                carrera_sel = str(carrera).strip()
                st.text_input("Carrera/Servicio (fijo por selección superior)", value=carrera_sel, disabled=True)
            else:
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
        if carrera_param_fija:
            if carrera_col:
                f = f[f[carrera_col].astype(str).str.strip() == str(carrera_sel).strip()]
            else:
                candidates = [c for c in ["Carrera_Catalogo", "Servicio", "Selecciona el programa académico que estudias"] if c in f.columns]
                if candidates:
                    target = str(carrera_sel).strip()
                    mask = False
                    for c in candidates:
                        mask = mask | (f[c].astype(str).str.strip() == target)
                    f = f[mask]
        else:
            if carrera_col and carrera_sel != "(Todas)":
                f = f[f[carrera_col].astype(str).str.strip() == str(carrera_sel).strip()]
    else:
        if modalidad != "Preparatoria":
            candidates = [c for c in ["Carrera_Catalogo", "Servicio", "Selecciona el programa académico que estudias"] if c in f.columns]
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
    # Tabs
    # ---------------------------
    if vista == "Dirección General":
        tab1, tab2, tab4, tab3 = st.tabs(["Resumen", "Por sección", "Comparativo entre carreras", "Comentarios"])
    else:
        tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])
        tab4 = None

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
            st.info("No hay datos suficientes para calcular promedios por sección (Likert) con los filtros actuales.")
        else:
            sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
            st.dataframe(sec_df.drop(columns=["sec_code"], errors="ignore"), use_container_width=True)

            sec_chart = _bar_chart_auto(
                df_in=sec_df,
                category_col="Sección",
                value_col="Promedio",
                value_domain=[1, 5],
                value_title="Promedio",
                tooltip_cols=["Sección", alt.Tooltip("Promedio:Q", format=".2f"), "Preguntas"],
                max_vertical=MAX_VERTICAL_SECTIONS,
                wrap_width_vertical=22,
                wrap_width_horizontal=36,
                base_height=320,
                hide_category_labels=True,
            )
            if sec_chart is not None:
                st.altair_chart(sec_chart, use_container_width=True)

        if yesno_cols:
            st.divider()
            st.markdown("### Sí/No (por pregunta) — % Sí")

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
            if not yn_df.empty:
                st.dataframe(yn_df, use_container_width=True)
                yn_chart = _bar_chart_auto(
                    df_in=yn_df,
                    category_col="Pregunta",
                    value_col="% Sí",
                    value_domain=[0, 100],
                    value_title="% Sí",
                    tooltip_cols=[alt.Tooltip("% Sí:Q", format=".1f"), alt.Tooltip("Pregunta:N")],
                    max_vertical=MAX_VERTICAL_QUESTIONS,
                    wrap_width_vertical=24,
                    wrap_width_horizontal=40,
                    base_height=340,
                    hide_category_labels=True,
                )
                if yn_chart is not None:
                    st.altair_chart(yn_chart, use_container_width=True)

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

        if not rows and not yesno_cols:
            st.info("No hay datos suficientes para mostrar secciones con los filtros actuales.")
            return

        sec_df2 = pd.DataFrame(rows).sort_values("Promedio", ascending=False) if rows else pd.DataFrame()

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
                        qrows.append({"Pregunta": m["header_exacto"], "% Sí": float(mean_val) * 100, "Tipo": "Sí/No"})
                    elif col in likert_cols:
                        qrows.append({"Pregunta": m["header_exacto"], "Promedio": float(mean_val), "Tipo": "Likert"})

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección con los filtros actuales.")
                    continue

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
                        tooltip_cols=[alt.Tooltip("Promedio:Q", format=".2f"), alt.Tooltip("Pregunta:N", title="Pregunta")],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
                        hide_category_labels=True,
                    )
                    if chart_l is not None:
                        st.altair_chart(chart_l, use_container_width=True)

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
                        tooltip_cols=[alt.Tooltip("% Sí:Q", format=".1f"), alt.Tooltip("Pregunta:N", title="Pregunta")],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=24,
                        wrap_width_horizontal=40,
                        base_height=340,
                        hide_category_labels=True,
                    )
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # ---------------------------
    # Comparativo (solo DG)
    # ---------------------------
    if tab4 is not None:
        with tab4:
            st.markdown("### Comparativo entre carreras por sección")
            st.caption(
                "Promedios Likert (1–5) por sección, comparando todas las carreras/servicios "
                "de la modalidad seleccionada."
            )

            carrera_col = _best_carrera_col(f)
            if not carrera_col:
                st.warning("No se encontró una columna válida para identificar Carrera/Servicio en PROCESADO.")
            else:
                if carrera_param_fija:
                    st.info("Para ver el comparativo entre carreras, selecciona Todos.")
                else:
                    for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                        cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
                        if not cols:
                            continue

                        rows = []
                        for carrera_val, df_c in f.groupby(carrera_col):
                            vals = pd.to_numeric(df_c[cols].stack(), errors="coerce")
                            mean_val = vals.mean()
                            if pd.isna(mean_val):
                                continue
                            rows.append({
                                "Carrera/Servicio": str(carrera_val).strip(),
                                "Promedio": round(float(mean_val), 2),
                                "Respuestas": int(len(df_c)),
                                "Preguntas": int(len(cols)),
                            })

                        if not rows:
                            continue

                        sec_comp = pd.DataFrame(rows).sort_values("Promedio", ascending=False).reset_index(drop=True)

                        with st.expander(f"{sec_name}", expanded=False):
                            st.dataframe(sec_comp, use_container_width=True)
                            chart = _bar_chart_auto(
                                df_in=sec_comp,
                                category_col="Carrera/Servicio",
                                value_col="Promedio",
                                value_domain=[1, 5],
                                value_title="Promedio",
                                tooltip_cols=[
                                    alt.Tooltip("Carrera/Servicio:N", title="Carrera/Servicio"),
                                    alt.Tooltip("Promedio:Q", format=".2f"),
                                    "Respuestas",
                                    "Preguntas",
                                ],
                                max_vertical=MAX_VERTICAL_SECTIONS,
                                wrap_width_vertical=20,
                                wrap_width_horizontal=36,
                                base_height=320,
                                hide_category_labels=True,
                            )
                            if chart is not None:
                                st.altair_chart(chart, use_container_width=True)

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
