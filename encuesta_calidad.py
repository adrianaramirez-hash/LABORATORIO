# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import re
from collections import Counter

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

# ============================================================
# Nombres de pestañas por rol
# ============================================================
SHEET_PROCESADO_DEFAULT = "PROCESADO"        # DG / DC
SHEET_PROCESADO_DF = "VISTA_FINANZAS_NUM"    # DF (ya numérica, con encabezados “humanos”)
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"  # opcional


# ============================================================
# Stopwords básicas ES (ligeras, sin librerías)
# ============================================================
STOPWORDS_ES = set("""
a al algo algunas algunos ante antes como con contra cual cuales cuando de del desde donde dos el ella ellas
ellos en entre era erais eran eras eres es esa esas ese eso esos esta estaba estabais estaban estabas
estad estada estadas estado estados estais estamos estan estar estara estaran estaras estare estareis
estaremos estaria estarian estarias estariais estariamos estarias este esto estos estoy estuve estuvimos
estuvieron estuviste estuvisteis estuviéramos estuviéramos fui fuimos fueron fuiste fuisteis ha habeis
habia habiais habian habias habida habidas habido habidos habiendo hablan hablas hable hableis hablemos
habra habran habras habre habreis habremos habria habrian habrias habeis habia han has hasta hay haya
hayan hayas he hemos hice hicimos hicieron hiciste hicisteis id la las le les lo los mas me mi mia mias
mio mios mis mucha muchas mucho muchos muy nada ni no nos nosotras nosotros nuestra nuestras nuestro
nuestros o os otra otras otro otros para pero poca pocas poco pocos por porque que quien quienes se sea
sean seas sera seran seras sere sereis seremos seria serian serias si sido siempre siendo sin sobre sois
solamente solo somos son soy su sus suya suyas suyo suyos tambien te teneis tenemos tener tenga tengan
tengas tengo tenia teniais tenian tenias tenido teniendo tenia tiene tienen tienes toda todas todo todos
tu tus un una unas uno unos usted ustedes va vais vamos van vaya vayan vayas voy y ya
""".split())


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
    """
    - Si el número de categorías es pequeño, muestra barras verticales.
    - Si es grande, muestra barras horizontales con altura dinámica.
    - Wrapping del texto para evitar etiquetas truncadas.
    """
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

    # Vertical
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

    # Horizontal
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


def _pick_fecha_col(df: pd.DataFrame):
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
    """
    - DG: modalidad la elige el usuario
    - DC: infiere (heurística)
    - DF: modalidad la elige el usuario (porque DF tiene 3 fuentes)
    """
    if vista in ["Dirección General", "Dirección Finanzas"]:
        return ""
    c = (carrera or "").strip().lower()
    if c == "preparatoria":
        return "Preparatoria"
    if c.startswith("licenciatura ejecutiva:") or c.startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"


def _best_carrera_col(df: pd.DataFrame):
    """
    Elegir una sola columna para filtrar Carrera/Servicio.
    """
    candidates = [
        "Carrera_Catalogo",
        "Servicio",
        "Selecciona el programa académico que estudias",  # Virtual típico
        "Servicio de procedencia",                        # Escolar típico
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
    """
    Clasifica columnas numéricas por rango real de valores:
      - max > 1  => Likert (1–5)
      - max <= 1 => Sí/No (0/1)
    """
    if not cols:
        return [], []
    dnum = df[cols].apply(pd.to_numeric, errors="coerce")
    maxs = dnum.max(axis=0, skipna=True)
    likert_cols = [c for c in cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1.0]
    yesno_cols = [c for c in cols if c not in likert_cols]
    return likert_cols, yesno_cols


def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _tokenize_es(s: str, min_len: int = 3) -> list[str]:
    s = _normalize_text(s)
    s = re.sub(r"[^\wáéíóúüñ]+", " ", s, flags=re.IGNORECASE)
    toks = [t for t in s.split() if len(t) >= min_len and t not in STOPWORDS_ES]
    return toks


def _render_comentarios(
    df_in: pd.DataFrame,
    open_cols: list[str],
    fecha_col: str | None,
    carrera_col: str | None,
    title: str = "Comentarios y respuestas abiertas",
):
    st.markdown(f"### {title}")

    if not open_cols:
        st.info("No se detectaron columnas de comentarios con la heurística actual.")
        return

    # Controles
    c1, c2, c3 = st.columns([2.4, 1.2, 1.4])
    with c1:
        col_sel = st.selectbox("Campo abierto", open_cols)
    with c2:
        min_chars = st.number_input("Mín. caracteres", min_value=0, max_value=500, value=10, step=5)
    with c3:
        mode = st.selectbox("Modo búsqueda", ["Contiene", "Regex"], index=0)

    c4, c5, c6 = st.columns([2.2, 1.2, 1.6])
    with c4:
        query = st.text_input("Buscar (palabra/frase)", value="")
    with c5:
        require_all = st.checkbox("Todas las palabras", value=False)
    with c6:
        show_n = st.number_input("Mostrar N", min_value=10, max_value=2000, value=300, step=50)

    # Preparar textos
    s = df_in[col_sel].dropna().astype(str)
    s = s[s.str.strip() != ""]
    base = df_in.loc[s.index].copy()
    base["_texto"] = s

    # Filtro por longitud
    if min_chars and min_chars > 0:
        base = base[base["_texto"].astype(str).str.len() >= int(min_chars)]

    # Filtro por búsqueda
    q = (query or "").strip()
    if q:
        if mode == "Regex":
            try:
                rx = re.compile(q, flags=re.IGNORECASE)
                base = base[base["_texto"].astype(str).apply(lambda x: bool(rx.search(x)))]
            except re.error:
                st.warning("Regex inválida. Cambia a modo 'Contiene' o corrige tu patrón.")
        else:
            if require_all:
                parts = [p for p in re.split(r"\s+", q) if p]
                mask = True
                for p in parts:
                    mask = mask & base["_texto"].str.contains(re.escape(p), case=False, na=False)
                base = base[mask]
            else:
                base = base[base["_texto"].str.contains(q, case=False, na=False)]

    total = len(base)
    st.caption(f"Entradas con texto (filtradas): **{total}**")
    if total == 0:
        st.info("No hay comentarios con los filtros actuales.")
        return

    # Ordenar por fecha si existe, si no, dejar como está
    if fecha_col and fecha_col in base.columns and pd.api.types.is_datetime64_any_dtype(base[fecha_col]):
        base = base.sort_values(fecha_col, ascending=False)

    # Resumen rápido
    with st.expander("Resumen del texto (rápido)", expanded=True):
        texts = base["_texto"].astype(str).tolist()
        lens = [len(t) for t in texts]
        cA, cB, cC, cD = st.columns(4)
        cA.metric("Comentarios", f"{len(texts)}")
        cB.metric("Longitud promedio", f"{(sum(lens)/len(lens)):.0f}" if lens else "—")
        cC.metric("Mediana", f"{pd.Series(lens).median():.0f}" if lens else "—")
        cD.metric("Máximo", f"{max(lens)}" if lens else "—")

        # Top palabras
        toks = []
        for t in texts:
            toks.extend(_tokenize_es(t, min_len=3))
        cnt = Counter(toks)
        top = cnt.most_common(30)
        if top:
            top_df = pd.DataFrame(top, columns=["Palabra", "Frecuencia"])
            st.dataframe(top_df, use_container_width=True, height=360)

            ch = (
                alt.Chart(top_df.head(20))
                .mark_bar()
                .encode(
                    y=alt.Y("Palabra:N", sort="-x", title=None),
                    x=alt.X("Frecuencia:Q", title="Frecuencia"),
                    tooltip=["Palabra", "Frecuencia"],
                )
                .properties(height=420)
            )
            st.altair_chart(ch, use_container_width=True)
        else:
            st.info("No se pudieron extraer palabras (revisa stopwords / longitud mínima).")

    # Resumen por carrera/servicio si existe
    if carrera_col and carrera_col in base.columns:
        with st.expander("Resumen por Carrera/Servicio", expanded=False):
            tmp = base.copy()
            tmp["_len"] = tmp["_texto"].astype(str).str.len()
            grp = (
                tmp.groupby(carrera_col, dropna=False)
                .agg(Comentarios=("_texto", "count"), Longitud_prom=("_len", "mean"))
                .reset_index()
            )
            grp[carrera_col] = grp[carrera_col].astype(str).str.strip()
            grp["Longitud_prom"] = grp["Longitud_prom"].round(0).astype(int)
            grp = grp.sort_values("Comentarios", ascending=False)
            st.dataframe(grp, use_container_width=True)

    # Tabla detalle (limitada)
    st.divider()
    st.markdown("#### Detalle (tabla)")
    show = base.copy()
    cols_to_show = []
    if fecha_col and fecha_col in show.columns:
        cols_to_show.append(fecha_col)
    if carrera_col and carrera_col in show.columns:
        cols_to_show.append(carrera_col)
    cols_to_show.append("_texto")

    show = show[cols_to_show].rename(columns={"_texto": col_sel})
    st.dataframe(show.head(int(show_n)), use_container_width=True, height=520)

    # Descarga CSV
    csv = show.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar CSV (comentarios filtrados)",
        data=csv,
        file_name=f"comentarios_{re.sub(r'[^a-zA-Z0-9]+','_', col_sel)[:40]}.csv",
        mime="text/csv",
        use_container_width=True,
    )


# ============================================================
# Carga desde Google Sheets (por URL según modalidad)
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str, sheet_procesado: str):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm(t): t for t in titles}

    def resolve(sheet_name: str) -> str | None:
        return titles_norm.get(norm(sheet_name))

    ws_pro = resolve(sheet_procesado)
    ws_map = resolve(SHEET_MAPA)
    ws_cat = resolve(SHEET_CATALOGO)  # opcional

    missing = []
    if not ws_pro:
        missing.append(sheet_procesado)
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
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    """
    DG/DC: usa PROCESADO + Mapa_Preguntas (comportamiento original).
    DF: usa VISTA_FINANZAS_NUM (por modalidad) + Mapa_Preguntas, y mantiene filtros de modalidad y carrera.
    """
    st.subheader("Encuesta de calidad")

    # Normalizar vista
    if not vista:
        vista = "Dirección General"
    vista = str(vista).strip()

    # ---------------------------
    # Selección de modalidad
    # ---------------------------
    if vista in ["Dirección General", "Dirección Finanzas"]:
        modalidad = st.selectbox(
            "Modalidad",
            ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
            index=0,
        )
        if vista == "Dirección Finanzas":
            st.caption("Vista restringida para Dirección de Finanzas (solo lo autorizado).")
    else:
        modalidad = _resolver_modalidad_auto(vista, carrera)
        st.caption(f"Modalidad asignada automáticamente: **{modalidad}**")

    url = _get_url_for_modalidad(modalidad)

    # ---------------------------
    # Elegir pestaña a leer (PROCESADO vs VISTA_FINANZAS_NUM)
    # ---------------------------
    sheet_pro = SHEET_PROCESADO_DF if vista == "Dirección Finanzas" else SHEET_PROCESADO_DEFAULT

    # ---------------------------
    # Carga
    # ---------------------------
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            df, mapa, _catalogo = _load_from_gsheets_by_url(url, sheet_pro)
    except Exception as e:
        st.error(f"No se pudieron cargar las hojas requeridas ({sheet_pro} / {SHEET_MAPA}).")
        st.exception(e)
        return

    if df.empty:
        st.warning(f"La hoja {sheet_pro} está vacía.")
        return

    if modalidad == "Preparatoria" and sheet_pro == SHEET_PROCESADO_DEFAULT:
        # Solo aplica al pipeline original; DF ya trae sus columnas “humanas”
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
    for c in ["header_exacto", "scale_code", "header_num"]:
        if c in mapa.columns:
            mapa[c] = mapa[c].astype(str).str.strip()

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

    # ---------------------------
    # Filtros: Año + Carrera/Servicio
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    carrera_param_fija = (carrera is not None) and str(carrera).strip() != ""

    if vista in ["Dirección General", "Dirección Finanzas"]:
        carrera_col = _best_carrera_col(df)
        carrera_sel = "(Todas)"

        c1, c2, c3 = st.columns([1.2, 1.0, 2.8])
        with c1:
            st.markdown(f"**Modalidad:** {modalidad}")
            st.caption(f"Fuente: **{sheet_pro}**")
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
                    st.info("No encontré una columna válida para filtrar por Carrera/Servicio en esta hoja.")
                    carrera_col = None
                    carrera_sel = "(Todas)"
    else:
        # DC
        c1, c2 = st.columns([2.4, 1.2])
        with c1:
            st.text_input("Carrera (fija por vista)", value=(carrera or ""), disabled=True)
            st.caption(f"Fuente: **{sheet_pro}**")
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

    if vista in ["Dirección General", "Dirección Finanzas"]:
        if carrera_param_fija:
            if carrera_col:
                f = f[f[carrera_col].astype(str).str.strip() == str(carrera_sel).strip()]
            else:
                candidates = [c for c in ["Carrera_Catalogo", "Servicio", "Servicio de procedencia", "Selecciona el programa académico que estudias"] if c in f.columns]
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
            candidates = [c for c in ["Carrera_Catalogo", "Servicio", "Servicio de procedencia", "Selecciona el programa académico que estudias"] if c in f.columns]
            if not candidates:
                st.warning("No encontré columnas para filtrar por carrera en esta modalidad.")
                return

            target = str(carrera_sel).strip()
            mask = False
            for c in candidates:
                mask = mask | (f[c].astype(str).str.strip() == target)
            f = f[mask]

    st.caption(f"Hoja usada: **{sheet_pro}** | Registros filtrados: **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # =========================================================
    # CAMINO DG/DC (original): requiere columnas *_num
    # =========================================================
    if vista != "Dirección Finanzas":
        # Solo preguntas existentes (por header_num)
        mapa["exists"] = mapa["header_num"].isin(df.columns)
        mapa_ok = mapa[mapa["exists"]].copy()

        # Columnas numéricas *_num
        num_cols = [c for c in df.columns if str(c).endswith("_num")]
        if not num_cols:
            st.warning("No encontré columnas *_num en PROCESADO. Verifica que tu PROCESADO tenga numéricos.")
            st.dataframe(df.head(30), use_container_width=True)
            return

        # Clasificación Likert vs Sí/No
        likert_cols, yesno_cols = _auto_classify_numcols(df, num_cols)

        # Tabs
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

            # Sí/No: resumen por pregunta
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
        # Comparativo entre carreras (solo DG)
        # ---------------------------
        if tab4 is not None:
            with tab4:
                st.markdown("### Comparativo entre carreras por sección")
                st.caption(
                    "Promedios Likert (1–5) por sección, comparando todas las carreras/servicios "
                    "de la modalidad seleccionada. (Se considera el filtro de Año; el filtro de Carrera "
                    "solo se usa si viene fijo desde el selector superior)."
                )

                carrera_col2 = _best_carrera_col(f)
                if not carrera_col2:
                    st.warning("No se encontró una columna válida para identificar Carrera/Servicio en PROCESADO.")
                else:
                    if carrera_param_fija:
                        st.info("Para ver el comparativo entre carreras, selecciona **Todos** en el selector superior (o '(Todas)' dentro del módulo).")
                    else:
                        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                            cols = [c for c in g["header_num"].tolist() if c in f.columns and c in likert_cols]
                            if not cols:
                                continue

                            rows = []
                            for carrera_val, df_c in f.groupby(carrera_col2):
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

                            sec_comp = (
                                pd.DataFrame(rows)
                                .sort_values("Promedio", ascending=False)
                                .reset_index(drop=True)
                            )

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
        # Comentarios (DG/DC) — MEJORADOS
        # ---------------------------
        with tab3:
            open_cols = [
                c
                for c in f.columns
                if (not str(c).endswith("_num"))
                and any(k in str(c).lower() for k in ["¿por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "descr"])
            ]

            carrera_col3 = _best_carrera_col(f)
            _render_comentarios(
                df_in=f,
                open_cols=open_cols,
                fecha_col=fecha_col,
                carrera_col=carrera_col3,
                title="Comentarios y respuestas abiertas (mejorado)",
            )

        return

    # =========================================================
    # CAMINO DF (Dirección Finanzas): sin *_num; columnas “humanas” ya numéricas
    # =========================================================
    open_cols_df = [
        c for c in f.columns
        if any(k in str(c).lower() for k in ["¿por qué", "por qué", "comentario", "sugerencia", "escríbelo", "escribelo"])
    ]

    base_exclude = set()
    for c in ["Marca temporal", "Marca Temporal", "Dirección de correo electrónico"]:
        if c in f.columns:
            base_exclude.add(c)

    num_candidates = []
    for c in f.columns:
        if c in base_exclude:
            continue
        if c in open_cols_df:
            continue
        s = pd.to_numeric(f[c], errors="coerce")
        if s.notna().any():
            num_candidates.append(c)

    if not num_candidates:
        st.warning("No encontré columnas numéricas en VISTA_FINANZAS_NUM (revisa que el script haya convertido).")
        st.dataframe(f.head(30), use_container_width=True)
        return

    likert_cols, yesno_cols = _auto_classify_numcols(f, num_candidates)

    tab1, tab2, tab3 = st.tabs(["Resumen", "Por pregunta", "Comentarios"])

    # ---------------------------
    # Resumen (DF)
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
    # Por pregunta (DF)
    # ---------------------------
    with tab2:
        st.markdown("### Detalle por pregunta")
        tipo_sel = st.radio("Tipo", ["Likert (1–5)", "Sí/No (0–1)"], horizontal=True)

        cols = likert_cols if "Likert" in tipo_sel else yesno_cols
        if not cols:
            st.info("No hay preguntas de este tipo con los filtros actuales.")
        else:
            pregunta = st.selectbox("Pregunta", cols)
            s = pd.to_numeric(f[pregunta], errors="coerce").dropna()
            st.caption(f"Respuestas válidas: {len(s)}")

            if "Likert" in tipo_sel:
                st.metric("Promedio", f"{s.mean():.2f}" if len(s) else "—")
            else:
                st.metric("% Sí", f"{(s.mean() * 100):.1f}%" if len(s) else "—")

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

    # ---------------------------
    # Comentarios (DF) — MEJORADOS
    # ---------------------------
    with tab3:
        carrera_col_df = _best_carrera_col(f)
        _render_comentarios(
            df_in=f,
            open_cols=open_cols_df,
            fecha_col=fecha_col,
            carrera_col=carrera_col_df,
            title="Comentarios y respuestas abiertas (mejorado)",
        )
