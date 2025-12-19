import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap

# ============================================================
# Fallback de nombres de sección (si Mapa_Preguntas no trae section_name)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinación",
    "SER": "Servicios",
    "ACD": "Servicios académicos",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

# Sí/No típicos (ajusta si tus _num son distintos)
YESNO_COLS = {
    "REC_Recomendaria_num",
    "REC_Volveria_num",
    "UDL_ViasComunicacion_num",
    "ADM_ContactoExiste_num",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7


# ============================================================
# Utilidades
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


def _make_unique_headers(headers: list[str]) -> list[str]:
    """
    Seguridad extra: si un sheet trae encabezados duplicados, los hacemos únicos.
    PROCESADO idealmente NO debe tener duplicados, pero esto evita crash si pasa.
    """
    out = []
    seen = {}
    for h in headers:
        base = (h or "").strip()
        if base == "":
            base = "SIN_TITULO"
        seen[base] = seen.get(base, 0) + 1
        if seen[base] == 1:
            out.append(base)
        else:
            out.append(f"{base} ({seen[base]})")
    return out


# ============================================================
# Google Sheets: URLs por modalidad (desde Secrets)
# ============================================================
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


def _resolver_modalidad_auto(vista: str, carrera: str | None) -> str:
    # Regla práctica para director:
    # - si selecciona "Preparatoria" en app.py -> Preparatoria
    # - si empieza con "Licenciatura Ejecutiva:" -> Escolarizado/Ejecutivas
    # - default -> Escolarizado/Ejecutivas (y si no hay datos, daremos fallback para que elija)
    if vista == "Dirección General":
        return ""
    if (carrera or "").strip().lower() == "preparatoria":
        return "Preparatoria"
    if (carrera or "").strip().lower().startswith("licenciatura ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def ws_to_df(ws_name: str) -> pd.DataFrame:
        ws = sh.worksheet(ws_name)
        values = ws.get_all_values()
        if not values:
            return pd.DataFrame()
        headers = _make_unique_headers(values[0])
        rows = values[1:]
        return pd.DataFrame(rows, columns=headers).replace("", pd.NA)

    # Requeridas para tu módulo
    df = ws_to_df("PROCESADO")
    mapa = ws_to_df("Mapa_Preguntas")

    # Catalogo_Servicio es opcional con la opción B, pero lo cargamos si existe
    try:
        catalogo = ws_to_df("Catalogo_Servicio")
    except Exception:
        catalogo = pd.DataFrame()

    return df, mapa, catalogo


# ============================================================
# Columnas “carrera/programa” por modalidad (Opción B)
# ============================================================
def _career_col_for_modalidad(modalidad: str, df: pd.DataFrame) -> str | None:
    """
    Devuelve la columna que se usará para el selector "Carrera/Programa" en Dirección General.
    - Virtual/Mixto: usa directamente el campo del formulario.
    - Escolarizado/Ejecutivas: usa Servicio de procedencia.
    - Prepa: no aplica.
    """
    if modalidad == "Virtual / Mixto":
        c = "Selecciona el programa académico que estudias"
        return c if c in df.columns else None

    if modalidad == "Escolarizado / Ejecutivas":
        # preferencia explícita por tu caso
        c = "Servicio de procedencia"
        if c in df.columns:
            return c
        # fallbacks si tu PROCESADO estandariza
        for altc in ["Carrera_Catalogo", "Servicio", "Indica el servicio"]:
            if altc in df.columns:
                return altc
        return None

    # Prepa: no pedimos carrera
    return None


def _pick_fecha_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _year_options(df: pd.DataFrame, modalidad: str) -> list[int]:
    """
    Prioridad:
    1) Columna 'Anio' si existe.
    2) Año derivado de columna de fecha.
    """
    if "Anio" in df.columns:
        y = pd.to_numeric(df["Anio"], errors="coerce").dropna().astype(int).unique().tolist()
        return sorted(y)

    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        d = df.copy()
        d[fecha_col] = _to_datetime_safe(d[fecha_col])
        years = d[fecha_col].dropna().dt.year.unique().tolist()
        years = [int(v) for v in years if pd.notna(v)]
        return sorted(years)

    return []


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
        modalidad = st.selectbox("Modalidad", _modalidades(), index=0)
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    # ---------------------------
    # Carga (PROCESADO + Mapa)
    # ---------------------------
    try:
        url = _get_url_for_modalidad(modalidad)
        df, mapa, _catalogo = _load_from_gsheets_by_url(url)
    except Exception as e:
        st.error("No se pudieron cargar los datos desde Google Sheets.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja PROCESADO está vacía (o no se encontró).")
        # Para Director: permitir escoger modalidad si auto falló
        if vista != "Dirección General":
            st.info("Si esta carrera pertenece a otra modalidad, cambia la modalidad manualmente.")
            modalidad_alt = st.selectbox("Modalidad (manual)", _modalidades(), index=1, key="modalidad_manual")
            if modalidad_alt != modalidad:
                url = _get_url_for_modalidad(modalidad_alt)
                df, mapa, _catalogo = _load_from_gsheets_by_url(url)
                modalidad = modalidad_alt
        if df.empty:
            return

    # ---------------------------
    # Normalización de fecha
    # ---------------------------
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Validación de mapa mínimo
    # ---------------------------
    required_cols = {"header_exacto", "scale_code", "header_num"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_exacto, scale_code, header_num.")
        return

    mapa = mapa.copy()
    mapa["header_num"] = mapa["header_num"].astype(str).str.strip()

    # Sección: usar section_name si existe; si no, derivar de prefijo
    mapa["section_code"] = mapa["header_num"].apply(_section_from_numcol)
    if "section_name" in mapa.columns:
        mapa["section_name"] = mapa["section_name"].fillna("").astype(str).str.strip()
        mapa.loc[mapa["section_name"] == "", "section_name"] = (
            mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
        )
    else:
        mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])

    mapa["exists"] = mapa["header_num"].isin(df.columns)
    mapa_ok = mapa[mapa["exists"]].copy()

    # Columnas numéricas
    num_cols = [c for c in df.columns if str(c).endswith("_num")]
    likert_cols = [c for c in num_cols if not _is_yesno_col(c)]
    yesno_cols = [c for c in num_cols if _is_yesno_col(c)]

    # ---------------------------
    # Filtros (sin duplicar)
    # ---------------------------
    years = _year_options(df, modalidad)
    years_ui = ["(Todos)"] + years if years else ["(Todos)"]

    if vista == "Dirección General":
        # Selector carrera/programa depende de modalidad (opción B)
        carrera_col = _career_col_for_modalidad(modalidad, df)

        c1, c2, c3 = st.columns([1.25, 1.0, 2.25])
        with c1:
            st.selectbox("Modalidad", _modalidades(), index=_modalidades().index(modalidad), disabled=True)
        with c2:
            year_sel = st.selectbox("Año", years_ui, index=0)

        with c3:
            if carrera_col:
                opciones = ["(Todas)"] + sorted(df[carrera_col].dropna().astype(str).unique().tolist())
                carrera_sel = st.selectbox("Carrera/Programa", opciones, index=0)
            else:
                carrera_sel = "(Todas)"
                st.write("Carrera/Programa: —")

    else:
        # Director: solo año; carrera fija (sin volver a pedir)
        c1, c2 = st.columns([2.2, 1.0])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True)
        with c2:
            year_sel = st.selectbox("Año", years_ui, index=0)
        carrera_sel = carrera

    st.divider()

    # ---------------------------
    # Aplicar filtros
    # ---------------------------
    f = df.copy()

    # Año
    if year_sel != "(Todos)":
        y = int(year_sel)
        if "Anio" in f.columns:
            f["Anio"] = pd.to_numeric(f["Anio"], errors="coerce")
            f = f[f["Anio"] == y]
        elif fecha_col:
            f = f[f[fecha_col].dt.year == y]

    # Carrera/Programa
    if vista == "Dirección General":
        carrera_col = _career_col_for_modalidad(modalidad, f)
        if carrera_sel != "(Todas)" and carrera_col:
            f = f[f[carrera_col].astype(str) == str(carrera_sel)]
    else:
        # Director: filtramos por el texto seleccionado arriba, pero en Virtual el campo es distinto.
        if carrera_sel:
            if modalidad == "Virtual / Mixto":
                c = "Selecciona el programa académico que estudias"
                if c in f.columns:
                    f = f[f[c].astype(str) == str(carrera_sel)]
            elif modalidad == "Escolarizado / Ejecutivas":
                c = "Servicio de procedencia"
                if c in f.columns:
                    f = f[f[c].astype(str) == str(carrera_sel)]
                else:
                    # fallback si tu PROCESADO trae Carrera_Catalogo
                    if "Carrera_Catalogo" in f.columns:
                        f = f[f["Carrera_Catalogo"].astype(str) == str(carrera_sel)]
            else:
                # Prepa: no filtra adicional
                pass

    st.caption(f"Modalidad: **{modalidad}** | Registros filtrados: **{len(f)}**")

    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # ---------------------------
    # Tabs
    # ---------------------------
    tab1, tab2, tab3 = st.tabs(["Resumen", "Por sección", "Comentarios"])

    # ---------------------------
    # TAB 1: Resumen
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
            st.dataframe(f.head(50), use_container_width=True)
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
            wrap_width_vertical=18,
            wrap_width_horizontal=34,
            base_height=320,
            hide_category_labels=True,
        )
        if sec_chart is not None:
            st.altair_chart(sec_chart, use_container_width=True)

    # ---------------------------
    # TAB 2: Por sección
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

        sec_df2 = pd.DataFrame(rows)
        if "Promedio" in sec_df2.columns:
            sec_df2 = sec_df2.sort_values("Promedio", ascending=False)

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
                        wrap_width_horizontal=42,
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
                        wrap_width_horizontal=42,
                        base_height=320,
                        hide_category_labels=True,
                    )
                    if chart_y is not None:
                        st.altair_chart(chart_y, use_container_width=True)

    # ---------------------------
    # TAB 3: Comentarios
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
            st.dataframe(f.head(50), use_container_width=True)
            return

        col_sel = st.selectbox("Selecciona el campo a revisar", open_cols)
        textos = f[col_sel].dropna().astype(str)
        textos = textos[textos.str.strip() != ""]

        st.caption(f"Entradas con texto: {len(textos)}")
        st.dataframe(pd.DataFrame({col_sel: textos}), use_container_width=True)
