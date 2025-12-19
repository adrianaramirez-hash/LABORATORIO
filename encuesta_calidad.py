import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Etiquetas completas por sección (fallback)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinación",
    "SER": "Servicios (Administrativos/Generales)",
    "ACD": "Servicios académicos",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

# Virtual: columna real del formulario (si no hay Carrera_Catalogo/Servicio)
VIRTUAL_PROGRAMA_COL = "Selecciona el programa académico que estudias"
ESCOLAR_SERVICIO_COL = "Servicio de procedencia"


# ============================================================
# Helpers
# ============================================================
def _section_from_numcol(col: str) -> str:
    """
    Espera algo tipo:
      DIR_ESC_01_num -> DIR
      SER_ESC_05_num -> SER
      ACD_...        -> ACD
    """
    if not col:
        return "OTR"
    return str(col).split("_", 1)[0] if "_" in str(col) else "OTR"


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
        df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3))
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

    df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_horizontal, max_lines=3))
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
# Google Sheets
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
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
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    df = ws_to_df(sh.worksheet(resolved["PROCESADO"]))
    mapa = ws_to_df(sh.worksheet(resolved["Mapa_Preguntas"]))
    catalogo = ws_to_df(sh.worksheet(resolved["Catalogo_Servicio"]))
    return df, mapa, catalogo


def _get_url_for_modalidad(modalidad: str) -> str:
    url_keys = {
        "Virtual / Mixto": "EC_VIRTUAL_URL",
        "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
        "Preparatoria": "EC_PREPA_URL",
    }
    key = url_keys.get(modalidad)
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
    out = df.copy()
    if "Servicio" not in out.columns:
        out["Servicio"] = "Preparatoria"
    if "Carrera_Catalogo" not in out.columns:
        out["Carrera_Catalogo"] = "Preparatoria"
    return out


def _merge_catalogo_virtual(df: pd.DataFrame, catalogo: pd.DataFrame) -> pd.DataFrame:
    """
    Si Virtual no trae Servicio/Carrera_Catalogo, lo construimos
    a partir de Catalogo_Servicio usando el programa académico del formulario.
    """
    out = df.copy()

    # Si ya hay columnas estándar, no hacemos nada
    if "Servicio" in out.columns and "Carrera_Catalogo" in out.columns:
        return out

    if VIRTUAL_PROGRAMA_COL not in out.columns:
        # Sin programa no podemos mapear; al menos creamos Carrera_Catalogo desde algo existente si se puede
        if "Servicio" in out.columns and "Carrera_Catalogo" not in out.columns:
            out["Carrera_Catalogo"] = out["Servicio"]
        return out

    cat = catalogo.copy()
    cat.columns = [str(c).strip().lower() for c in cat.columns]

    # Esperamos: programa, servicio, (opcional) carrera
    if "programa" not in cat.columns or "servicio" not in cat.columns:
        # No hay forma de mapear, dejamos Carrera_Catalogo = programa
        out["Carrera_Catalogo"] = out[VIRTUAL_PROGRAMA_COL].astype(str)
        out["Servicio"] = out.get("Servicio", "SIN_CLASIFICAR")
        return out

    cat["programa"] = cat["programa"].astype(str).str.strip()
    cat["servicio"] = cat["servicio"].astype(str).str.strip()

    out[VIRTUAL_PROGRAMA_COL] = out[VIRTUAL_PROGRAMA_COL].astype(str).str.strip()

    cols = ["programa", "servicio"]
    if "carrera" in cat.columns:
        cat["carrera"] = cat["carrera"].astype(str).str.strip()
        cols.append("carrera")

    out = out.merge(cat[cols], how="left", left_on=VIRTUAL_PROGRAMA_COL, right_on="programa")
    out.drop(columns=["programa"], inplace=True, errors="ignore")

    # Estandarizamos
    if "Servicio" not in out.columns:
        out.rename(columns={"servicio": "Servicio"}, inplace=True)
    else:
        # Si ya existe Servicio, solo llenamos vacíos con el catálogo
        out["Servicio"] = out["Servicio"].fillna(out.get("servicio"))
        out.drop(columns=["servicio"], inplace=True, errors="ignore")

    if "Carrera_Catalogo" not in out.columns:
        if "carrera" in out.columns:
            out.rename(columns={"carrera": "Carrera_Catalogo"}, inplace=True)
        else:
            out["Carrera_Catalogo"] = out[VIRTUAL_PROGRAMA_COL].astype(str)

    out["Servicio"] = out["Servicio"].fillna("SIN_CLASIFICAR")
    out["Carrera_Catalogo"] = out["Carrera_Catalogo"].fillna(out[VIRTUAL_PROGRAMA_COL].astype(str))
    return out


def _ensure_escolar_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Escolarizadas: si la gente usa 'Servicio de procedencia' pero no existe 'Carrera_Catalogo',
    lo normalizamos a Carrera_Catalogo = Servicio de procedencia.
    """
    out = df.copy()

    # Si existe Servicio de procedencia y no existe Carrera_Catalogo, creamos Carrera_Catalogo
    if "Carrera_Catalogo" not in out.columns and ESCOLAR_SERVICIO_COL in out.columns:
        out["Carrera_Catalogo"] = out[ESCOLAR_SERVICIO_COL].astype(str)

    # Si no existe Servicio pero sí existe Servicio de procedencia, creamos Servicio
    if "Servicio" not in out.columns and ESCOLAR_SERVICIO_COL in out.columns:
        out["Servicio"] = out[ESCOLAR_SERVICIO_COL].astype(str)

    return out


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    if vista == "Dirección General":
        return ""
    car = (carrera or "").strip().lower()
    if car == "preparatoria":
        return "Preparatoria"
    if car.startswith("licenciatura ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    # Por ahora default (si luego nos pasas catálogo de qué es Virtual, se ajusta)
    return "Escolarizado / Ejecutivas"


def _infer_yesno_cols(df: pd.DataFrame, num_cols: list[str]) -> list[str]:
    """
    Detecta columnas binarias 0/1 (Sí/No) automáticamente.
    """
    yesno = []
    for c in num_cols:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if s.empty:
            continue
        uniq = set(s.unique().tolist())
        if uniq.issubset({0, 1}):
            yesno.append(c)
    return yesno


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # ---------------------------
    # Modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", _modalidades(), index=0, key="ec_modalidad")
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

    # Normalización por modalidad
    if modalidad == "Preparatoria":
        df = _ensure_prepa_columns(df)
    elif modalidad == "Virtual / Mixto":
        df = _merge_catalogo_virtual(df, catalogo)
    else:
        df = _ensure_escolar_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Validación del mapa
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["header_num"] = mapa["header_num"].astype(str).str.strip()

    # section_name completo: usarlo si existe, si no fallback a SECTION_LABELS
    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)

    if "section_name" in mapa.columns:
        mapa["section_name"] = mapa["section_name"].fillna("").astype(str).str.strip()
        mapa.loc[mapa["section_name"] == "", "section_name"] = (
            mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
        )
    else:
        mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])

    # Mapa solo con columnas que existan en df
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Num cols
    num_cols = [c for c in df.columns if str(c).endswith("_num")]

    # Detectar Sí/No real en tu data
    yesno_cols = _infer_yesno_cols(df, num_cols)
    likert_cols = [c for c in num_cols if c not in yesno_cols]

    # ---------------------------
    # Filtros (SIN duplicar Servicio/Carrera)
    # Reglas acordadas:
    # - Dirección General: Modalidad + Año + (opcional) Carrera (catálogo)
    # - Director de carrera: Año y Carrera fija (de app.py)
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    if vista == "Dirección General":
        carreras = ["(Todas)"]
        if "Carrera_Catalogo" in df.columns:
            carreras += sorted(df["Carrera_Catalogo"].dropna().astype(str).unique().tolist())

        c1, c2 = st.columns([1.1, 2.2])
        with c1:
            year_sel = st.selectbox("Año", years, index=0, key="ec_year")
        with c2:
            # Para Prepa solo hay "Preparatoria" y se ve igual (no estorba).
            carrera_sel = st.selectbox("Carrera", carreras, index=0, key="ec_carrera_dg")
    else:
        c1, c2 = st.columns([2.2, 1.1])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True, key="ec_carrera_fija")
        with c2:
            year_sel = st.selectbox("Año", years, index=0, key="ec_year_dir")
        carrera_sel = carrera

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
            # Si realmente no hay Sí/No en este instrumento, quedará —
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

                    is_yesno = col in yesno_cols
                    qrows.append(
                        {
                            "Pregunta": m["header_exacto"],
                            "Promedio": float(mean_val) if not is_yesno else None,
                            "% Sí": float(mean_val) * 100 if is_yesno else None,
                            "Tipo": "Sí/No" if is_yesno else "Likert",
                        }
                    )

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

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols, key="ec_open_col")
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
