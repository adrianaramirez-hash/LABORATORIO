import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import json

# =========================
# CONFIG
# =========================
MODALIDADES = ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"]

URL_KEYS = {
    "Virtual / Mixto": "EC_VIRTUAL_URL",
    "Escolarizado / Ejecutivas": "EC_ESCOLAR_URL",
    "Preparatoria": "EC_PREPA_URL",
}

SHEET_RESPUESTAS = "Respuestas"
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"

KEY_BY_MODALIDAD = {
    "Virtual / Mixto": "Selecciona el programa académico que estudias",
    "Escolarizado / Ejecutivas": "Servicio de procedencia",
}

SECTION_LABELS = {
    "DIR": "Director/Coordinador",
    "SER": "Servicios",
    "ACD": "Servicios académicos",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7


# =========================
# AUTH (compatible con gcp_service_account y gcp_service_account_json)
# =========================
def _get_service_account_dict():
    """
    Soporta:
    - [gcp_service_account] (tabla)
    - [gcp_service_account_json] (tabla)
    - gcp_service_account_json = "{...}" (string JSON)
    """
    if "gcp_service_account" in st.secrets:
        return dict(st.secrets["gcp_service_account"])

    val = st.secrets.get("gcp_service_account_json", None)
    if val is None:
        raise KeyError('Falta secret: "gcp_service_account" o "gcp_service_account_json".')

    if isinstance(val, str):
        return json.loads(val)

    return dict(val)


def _get_gspread_client():
    sa = _get_service_account_dict()
    return gspread.service_account_from_dict(sa)


# =========================
# Helpers
# =========================
def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)


def _wrap_text(s, width=18, max_lines=3):
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


def _section_from_numcol(col):
    return col.split("_", 1)[0] if "_" in col else "OTROS"


def _normalize(s):
    return str(s).strip().lower() if pd.notna(s) else ""


def _normalize_series(s):
    return s.astype(str).map(lambda x: str(x).strip().lower())


def _make_unique_headers(raw_headers):
    seen = {}
    out = []
    for h in raw_headers:
        base = (h or "").strip()
        if base == "":
            base = "SIN_TITULO"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base} ({seen[base]})")
    return out


def _ws_to_df(ws):
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _make_unique_headers(values[0])
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


@st.cache_data(show_spinner=False, ttl=600)
def _load_from_gsheets_by_url(url):
    gc = _get_gspread_client()
    sh = gc.open_by_url(url)

    ws_resp = sh.worksheet(SHEET_RESPUESTAS)
    ws_map = sh.worksheet(SHEET_MAPA)
    ws_cat = sh.worksheet(SHEET_CATALOGO)

    df = _ws_to_df(ws_resp)
    mapa = _ws_to_df(ws_map)
    catalogo = _ws_to_df(ws_cat)
    return df, mapa, catalogo


def _merge_catalogo(df, catalogo, key_col_df):
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

    out = out.merge(cat[["programa", "servicio"]], how="left", left_on=key_col_df, right_on="programa")
    out.drop(columns=["programa"], inplace=True, errors="ignore")
    out.rename(columns={"servicio": "Servicio"}, inplace=True)
    out["Servicio"] = out["Servicio"].fillna("SIN_CLASIFICAR")
    return out


def _mean_numeric(series):
    return pd.to_numeric(series, errors="coerce").mean()


def _detect_yesno_num_cols(df, num_cols):
    yesno, likert = [], []
    for c in num_cols:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if s.empty:
            likert.append(c)
            continue
        uniq = set(s.unique().tolist())
        if uniq.issubset({0, 1}):
            yesno.append(c)
        else:
            likert.append(c)
    return yesno, likert


def _bar_chart_auto(
    df_in, category_col, value_col, value_domain, value_title, tooltip_cols,
    max_vertical, wrap_width_vertical=18, wrap_width_horizontal=30,
    height_per_row=28, base_height=260, hide_category_labels=True
):
    if df_in.empty:
        return None

    df = df_in.copy()
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[value_col])
    if df.empty:
        return None

    n = len(df)

    if n <= max_vertical:
        df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3))
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"),
                        axis=alt.Axis(title=None, labels=not hide_category_labels, ticks=not hide_category_labels)),
                y=alt.Y(f"{value_col}:Q", scale=alt.Scale(domain=value_domain),
                        axis=alt.Axis(title=value_title)),
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
            y=alt.Y("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"),
                    axis=alt.Axis(title=None, labels=not hide_category_labels, ticks=not hide_category_labels)),
            x=alt.X(f"{value_col}:Q", scale=alt.Scale(domain=value_domain),
                    axis=alt.Axis(title=value_title)),
            tooltip=tooltip_cols,
        )
        .properties(height=dynamic_height)
    )


# =========================
# Render final
# =========================
def render_encuesta_calidad(vista=None, carrera=None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"

    # Modalidad
    if vista == "Dirección General":
        modalidad = st.selectbox("Modalidad", MODALIDADES, index=1)
    else:
        # Director: modalidad automática (simple)
        if carrera and _normalize(carrera) == "preparatoria":
            modalidad = "Preparatoria"
        elif carrera and _normalize(carrera).startswith("licenciatura ejecutiva:"):
            modalidad = "Escolarizado / Ejecutivas"
        else:
            modalidad = "Escolarizado / Ejecutivas"
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    # URL
    url_key = URL_KEYS.get(modalidad)
    url = (st.secrets.get(url_key, "") or "").strip()
    if not url:
        st.error(f"Falta configurar {url_key} en Secrets.")
        return

    # Load
    with st.spinner("Cargando datos (Google Sheets)…"):
        df, mapa, catalogo = _load_from_gsheets_by_url(url)

    if df.empty:
        st.warning("La pestaña 'Respuestas' está vacía.")
        return

    # Fecha
    fecha_col = "Marca temporal" if "Marca temporal" in df.columns else None
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # Merge catálogo solo para Virtual/Escolar
    if modalidad != "Preparatoria":
        key_col = KEY_BY_MODALIDAD.get(modalidad)
        if not key_col or key_col not in df.columns:
            st.error(f"No encuentro la columna llave '{key_col}' en Respuestas.")
            return
        df = _merge_catalogo(df, catalogo, key_col_df=key_col)

    # Validar mapa
    required = {"header_exacto", "scale_code", "header_num"}
    if not required.issubset(set(mapa.columns)):
        st.error("Mapa_Preguntas debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["section_code"] = mapa["header_num"].astype(str).apply(_section_from_numcol)
    mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Num cols
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    yesno_cols, likert_cols = _detect_yesno_num_cols(df, num_cols)

    # =========================
    # FILTROS (sin duplicados)
    # =========================
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years = ["(Todos)"] + sorted(df[fecha_col].dropna().dt.year.unique().astype(int).tolist(), reverse=True)

    if vista == "Dirección General":
        if modalidad == "Preparatoria":
            year_sel = st.selectbox("Año", years, index=0)
            servicio_sel = None
        else:
            c1, c2 = st.columns([1.2, 1.0])
            with c1:
                servicios = ["(Todos)"] + sorted(df["Servicio"].dropna().unique().tolist())
                servicio_sel = st.selectbox("Servicio/Carrera", servicios, index=0)
            with c2:
                year_sel = st.selectbox("Año", years, index=0)
    else:
        # Director: solo año
        year_sel = st.selectbox("Año", years, index=0)
        servicio_sel = None

    st.divider()

    # Aplicar filtros
    f = df.copy()

    if year_sel != "(Todos)" and fecha_col:
        f = f[f[fecha_col].dt.year == int(year_sel)]

    if vista == "Dirección General":
        if modalidad != "Preparatoria" and servicio_sel and servicio_sel != "(Todos)":
            f = f[f["Servicio"] == servicio_sel]
    else:
        if modalidad != "Preparatoria":
            if not carrera:
                st.info("Selecciona una carrera/servicio arriba.")
                return
            f = f[_normalize_series(f["Servicio"]) == _normalize(carrera)]

    st.caption(f"Registros filtrados: **{len(f)}**")
    if f.empty:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # =========================
    # VISTAS
    # =========================
    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

    # ---- Resumen
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
            st.info("No hay datos suficientes para promedios por sección.")
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

    # ---- Por sección
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

                    if col in yesno_cols:
                        qrows.append({"Pregunta": m["header_exacto"], "% Sí": mean_val * 100, "Tipo": "Sí/No"})
                    else:
                        qrows.append({"Pregunta": m["header_exacto"], "Promedio": mean_val, "Tipo": "Likert"})

                qdf = pd.DataFrame(qrows)
                if qdf.empty:
                    st.info("Sin datos para esta sección.")
                    continue

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
                        tooltip_cols=[alt.Tooltip("Promedio:Q", format=".2f"), alt.Tooltip("Pregunta:N", title="Pregunta")],
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
                        tooltip_cols=[alt.Tooltip("% Sí:Q", format=".1f"), alt.Tooltip("Pregunta:N", title="Pregunta")],
                        max_vertical=MAX_VERTICAL_QUESTIONS,
                        wrap_width_vertical=18,
                        wrap_width_horizontal=34,
                        base_height=320,
                        hide_category_labels=True,
                    )
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # ---- Comentarios
    with tab3:
        st.markdown("### Comentarios y respuestas abiertas")

        open_cols = [
            c for c in f.columns
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
