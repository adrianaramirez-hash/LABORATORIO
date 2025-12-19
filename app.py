import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# =========================
# CONFIG
# =========================
MODALIDADES = ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"]

# Claves de URL en Secrets
URL_KEYS = {
    "Virtual / Mixto": "EC_VIRTUAL_URL",
    "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
    "Preparatoria": "EC_PREPA_URL",
}

# Pestañas requeridas
SHEET_RESPUESTAS = "Respuestas"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"

# Llaves por modalidad para cruzar contra Catalogo_Servicio.programa
KEY_BY_MODALIDAD = {
    "Virtual / Mixto": "Selecciona el programa académico que estudias",
    "Escolarizado / Ejecutivas": "Servicio de procedencia",
    # Prepa: general, no requiere llave
}

# Etiquetas de secciones basadas en prefijo de columnas *_num (ajusta si tu mapa usa otros prefijos)
SECTION_LABELS = {
    "DIR": "Director/Coordinador",
    "SER": "Servicios",
    "ACD": "Servicios académicos",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación",
}

# Auto-layout charts
MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7


# =========================
# Helpers generales
# =========================
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


def _section_from_numcol(col: str) -> str:
    return col.split("_", 1)[0] if "_" in col else "OTROS"


def _normalize(s: object) -> str:
    return str(s).strip().lower() if pd.notna(s) else ""


def _pick_years(df: pd.DataFrame, fecha_col: str) -> list:
    if fecha_col not in df.columns:
        return ["(Todos)"]
    if df[fecha_col].isna().all():
        return ["(Todos)"]
    years = sorted(df[fecha_col].dropna().dt.year.unique().astype(int).tolist(), reverse=True)
    return ["(Todos)"] + years


def _make_unique_headers(raw_headers: list[str]) -> list[str]:
    seen = {}
    out = []
    for h in raw_headers:
        base = (h or "").strip()
        if base == "":
            base = "SIN_TITULO"
        seen[base] = seen.get(base, 0) + 1
        if seen[base] == 1:
            out.append(base)
        else:
            out.append(f"{base} ({seen[base]})")
    return out


def _ws_to_df(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _make_unique_headers(values[0])
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


@st.cache_data(show_spinner=False, ttl=600)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    # st.secrets["gcp_service_account_json"] puede venir como dict-like o string JSON
    # si es string, lo convertimos:
    if isinstance(sa, str):
        import json
        sa = json.loads(sa)

    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    # Resolver pestañas por nombre exacto
    ws_resp = sh.worksheet(SHEET_RESPUESTAS)
    ws_map = sh.worksheet(SHEET_MAPA)
    ws_cat = sh.worksheet(SHEET_CATALOGO)

    df = _ws_to_df(ws_resp)
    mapa = _ws_to_df(ws_map)
    catalogo = _ws_to_df(ws_cat)

    return df, mapa, catalogo


def _merge_catalogo(df: pd.DataFrame, catalogo: pd.DataFrame, key_col_df: str) -> pd.DataFrame:
    """
    Cruza df con catalogo (programa -> servicio).
    """
    if df.empty or catalogo.empty:
        return df

    cat = catalogo.copy()
    cat.columns = [c.strip().lower() for c in cat.columns]

    if "programa" not in cat.columns or "servicio" not in cat.columns:
        return df

    if key_col_df not in df.columns:
        return df

    out = df.copy()

    out[key_col_df] = out[key_col_df].astype(str).str.strip()
    cat["programa"] = cat["programa"].astype(str).str.strip()
    cat["servicio"] = cat["servicio"].astype(str).str.strip()

    out = out.merge(
        cat[["programa", "servicio"]],
        how="left",
        left_on=key_col_df,
        right_on="programa",
    )

    out.drop(columns=["programa"], inplace=True, errors="ignore")

    # Columna estándar única para filtrar
    out.rename(columns={"servicio": "Servicio"}, inplace=True)
    out["Servicio"] = out["Servicio"].fillna("SIN_CLASIFICAR")
    return out


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
    if df_in.empty:
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


def _detect_yesno_num_cols(df: pd.DataFrame, num_cols: list[str]) -> tuple[list[str], list[str]]:
    """
    Separa columnas *_num en:
    - yesno: columnas con valores (0/1) (permitiendo NA)
    - likert: el resto
    """
    yesno = []
    likert = []

    for c in num_cols:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if s.empty:
            # si está vacío, lo tratamos como likert para no falsear métricas
            likert.append(c)
            continue

        uniq = set(s.unique().tolist())
        # Binario estricto
        if uniq.issubset({0, 1}):
            yesno.append(c)
        else:
            likert.append(c)

    return yesno, likert


# =========================
# Render principal
# =========================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # ---------------------------
    # 1) Modalidad
    # ---------------------------
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", MODALIDADES, index=1)
    else:
        # Director: modalidad por carrera (simple y segura)
        if carrera and _normalize(carrera) == "preparatoria":
            modalidad = "Preparatoria"
        elif carrera and _normalize(carrera).startswith("licenciatura ejecutiva:"):
            modalidad = "Escolarizado / Ejecutivas"
        else:
            # Default
            modalidad = "Escolarizado / Ejecutivas"

        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    # ---------------------------
    # 2) Cargar datos por URL
    # ---------------------------
    key = URL_KEYS.get(modalidad)
    if not key:
        st.error("Modalidad no reconocida.")
        return

    url = (st.secrets.get(key, "") or "").strip()
    if not url:
        st.error(f"Falta configurar {key} en Secrets.")
        return

    with st.spinner("Cargando datos (Google Sheets)…"):
        df, mapa, catalogo = _load_from_gsheets_by_url(url)

    if df.empty:
        st.warning("La pestaña 'Respuestas' está vacía.")
        return

    # Fecha
    fecha_col = "Marca temporal" if "Marca temporal" in df.columns else None
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # 3) Cruce con Catalogo_Servicio (solo Virtual y Escolar)
    # ---------------------------
    if modalidad != "Preparatoria":
        key_col = KEY_BY_MODALIDAD.get(modalidad)
        if not key_col:
            st.error("No está definida la columna llave para esta modalidad.")
            return

        df = _merge_catalogo(df, catalogo, key_col_df=key_col)

        if "Servicio" not in df.columns:
            st.error("No se pudo construir la columna estándar 'Servicio' con Catalogo_Servicio.")
            return

    # ---------------------------
    # 4) Validación mapa
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["section_code"] = mapa["header_num"].astype(str).apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # num cols
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    yesno_cols, likert_cols = _detect_yesno_num_cols(df, num_cols)

    # ---------------------------
    # 5) Filtros (sin duplicados)
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years = _pick_years(df, fecha_col)

    if vista == "Dirección General":
        # Dirección General: Modalidad ya seleccionada arriba
        if modalidad == "Preparatoria":
            c1 = st.columns(1)[0]
            with c1:
                year_sel = st.selectbox("Año", years, index=0)
            servicio_sel = None  # no aplica
        else:
            c1, c2 = st.columns([1.2, 1.0])
            with c1:
                servicios = ["(Todos)"] + sorted(df["Servicio"].dropna().unique().tolist())
                servicio_sel = st.selectbox("Servicio/Carrera", servicios, index=0)
            with c2:
                year_sel = st.selectbox("Año", years, index=0)

    else:
        # Director: NO repetir carrera/servicio aquí, solo Año
        c1 = st.columns(1)[0]
        with c1:
            year_sel = st.selectbox("Año", years, index=0)
        servicio_sel = None  # se amarra a carrera arriba

    st.divider()

    # ---------------------------
    # 6) Aplicar filtros
    # ---------------------------
    f = df.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if vista == "Dirección General":
        if modalidad != "Preparatoria":
            if servicio_sel and servicio_sel != "(Todos)":
                f = f[f["Servicio"] == servicio_sel]
    else:
        # Director: filtro fijo
        if modalidad != "Preparatoria":
            if not carrera:
                st.info("Selecciona una carrera/servicio en la parte superior para ver resultados.")
                return

            # Comparación robusta (normalizada)
            f = f[_normalize_series(f["Servicio"]) == _normalize(carrera)]

        else:
            # Prepa: solo aplica el año
            pass

    st.caption(f"Registros filtrados: **{len(f)}**")
    if f.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # ---------------------------
    # 7) Tabs
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
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})

        sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=False)
        if sec_df.empty:
            st.info("No hay datos suficientes para calcular promedios por sección con los filtros actuales.")
            return

        st.dataframe(sec_df.drop(columns=["sec_code"]), use_container_width=True)

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
            rows.append({"Sección": sec_name, "Promedio": val, "Preguntas": len(cols), "sec_code": sec_code})
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

                    # Si/No (binario 0/1)
                    if col in yesno_cols:
                        qrows.append(
                            {
                                "Pregunta": m["header_exacto"],
                                "% Sí": mean_val * 100,
                                "Tipo": "Sí/No",
                            }
                        )
                    else:
                        qrows.append(
                            {
                                "Pregunta": m["header_exacto"],
                                "Promedio": mean_val,
                                "Tipo": "Likert",
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
                    st.markdown("**Preguntas Likert**")
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


def _normalize_series(s: pd.Series) -> pd.Series:
    return s.astype(str).map(lambda x: str(x).strip().lower())
