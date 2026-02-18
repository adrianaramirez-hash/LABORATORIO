# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import re
from collections import Counter

# ============================================================
# Etiquetas de secciones (fallback si el mapa no trae section_name)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinador",
    "SER": "Servicios institucionales",
    "ADM": "Soporte administrativo",
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Plataforma SEAC",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",
    "MAT": "Materiales",
    "UDL": "Comunicación con la UDL",
    "COM": "Comunicación con compañeros",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

# ============================================================
# Nombres de pestañas por rol
# ============================================================
SHEET_PROCESADO_DEFAULT = "PROCESADO"         # DG / DC
SHEET_PROCESADO_DF = "VISTA_FINANZAS_NUM"     # DF (ya numérica, con encabezados “humanos”)
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"          # opcional

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

    # Vertical
    if n <= max_vertical:
        df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_vertical, max_lines=3))
        return (
            alt.Chart(df)
            .mark_bar()
            .encode(
                x=alt.X("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"), axis=cat_axis_vertical),
                y=alt.Y(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), axis=alt.Axis(title=value_title)),
                tooltip=tooltip_cols,
            )
            .properties(height=max(320, base_height))
        )

    # Horizontal
    df["_cat_wrapped"] = df[category_col].apply(lambda x: _wrap_text(x, width=wrap_width_horizontal, max_lines=3))
    dynamic_height = max(base_height, n * height_per_row)

    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"), axis=cat_axis_horizontal),
            x=alt.X(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), axis=alt.Axis(title=value_title)),
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
    if vista in ["Dirección General", "Dirección Finanzas"]:
        return ""
    c = (carrera or "").strip().lower()
    if c == "preparatoria":
        return "Preparatoria"
    if c.startswith("licenciatura ejecutiva:") or c.startswith("lic. ejecutiva:"):
        return "Escolarizado / Ejecutivas"
    return "Escolarizado / Ejecutivas"

def _best_carrera_col(df: pd.DataFrame):
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

def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _tokenize_es(s: str, min_len: int = 3) -> list[str]:
    s = _normalize_text(s)
    s = re.sub(r"[^\wáéíóúüñ]+", " ", s, flags=re.IGNORECASE)
    toks = [t for t in s.split() if len(t) >= min_len and t not in STOPWORDS_ES]
    return toks

def _safe_str(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip()

def _infer_col_from_map_row(m: pd.Series, df_cols: set[str]) -> str:
    """
    Resuelve la columna REAL en PROCESADO/VISTA_* a partir del mapa.
    Soporta:
      - header_num ya listo
      - header_id tipo: ESC_DIR_01  -> intenta DIR_ESC_01_num, ESC_DIR_01_txt, etc.
    """
    # 1) Si header_num existe y está en df, úsalo
    header_num = _safe_str(m.get("header_num"))
    if header_num and header_num in df_cols:
        return header_num

    # 2) Si header_num existe pero no está, intenta usarlo como base (por si falta sufijo)
    if header_num:
        candidates = [header_num]
        if not header_num.endswith(("_num", "_txt")):
            candidates += [header_num + "_num", header_num + "_txt"]
        for c in candidates:
            if c in df_cols:
                return c

    # 3) Derivar desde header_id
    header_id = _safe_str(m.get("header_id"))
    if header_id:
        parts = header_id.split("_")
        # caso típico: MOD_SEC_01 (ESC_DIR_01)
        if len(parts) >= 3:
            mod = parts[0]
            sec = parts[1]
            num = "_".join(parts[2:])

            # posibles (porque en tus datos hay inconsistencia: num= DIR_ESC_01_num y txt= ESC_DIR_05_txt)
            candidates = [
                f"{sec}_{mod}_{num}_num",
                f"{sec}_{mod}_{num}_txt",
                f"{mod}_{sec}_{num}_num",
                f"{mod}_{sec}_{num}_txt",
                f"{header_id}_num",
                f"{header_id}_txt",
            ]
        else:
            candidates = [header_id, f"{header_id}_num", f"{header_id}_txt"]

        for c in candidates:
            if c in df_cols:
                return c

    # 4) Último recurso: vacío
    return ""

def _normalize_scale_code(x: str) -> str:
    s = _safe_str(x).upper()
    s = s.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    if "ABIERT" in s or "TEXTO" in s or "OPEN" in s:
        return "ABIERTA"
    if "YES" in s or "SINO" in s or "SI/NO" in s:
        return "YESNO"
    if "LIK" in s or "ESCALA" in s:
        return "LIKERT"
    return s or "OTR"

# ============================================================
# Comentarios (UI estética, sin IDs visibles)
# ============================================================
def _render_comentarios(
    df_in: pd.DataFrame,
    open_items: list[dict],
    fecha_col: str | None,
    carrera_col: str | None,
    title: str = "Comentarios y respuestas abiertas",
):
    st.markdown(f"### {title}")

    if not open_items:
        st.info("No se detectaron campos abiertos (comentarios/por qué/sugerencias).")
        return

    def _fmt_option(opt: dict) -> str:
        sec = (opt.get("section") or "").strip()
        drv = (opt.get("driver") or "").strip()
        lab = (opt.get("label") or opt.get("col") or "").strip()
        if sec and drv:
            return f"{sec} · {drv} — {lab}"
        if sec:
            return f"{sec} — {lab}"
        return lab

    c1, c2, c3 = st.columns([2.6, 1.2, 1.2])
    with c1:
        sel = st.selectbox("Campo abierto", options=open_items, format_func=_fmt_option)
    with c2:
        min_chars = st.number_input("Mín. caracteres", min_value=0, max_value=500, value=10, step=5)
    with c3:
        mode = st.selectbox("Búsqueda", ["Contiene", "Regex"], index=0)

    c4, c5, c6 = st.columns([2.2, 1.2, 1.6])
    with c4:
        query = st.text_input("Buscar (palabra/frase)", value="")
    with c5:
        require_all = st.checkbox("Todas las palabras", value=False)
    with c6:
        show_n = st.number_input("Mostrar N", min_value=10, max_value=2000, value=200, step=50)

    col_sel = sel.get("col")
    label_sel = _fmt_option(sel)

    if not col_sel or col_sel not in df_in.columns:
        st.warning("El campo seleccionado no existe en los datos filtrados. Revisa el mapa y la hoja.")
        return

    s = df_in[col_sel].dropna().astype(str)
    s = s[s.str.strip() != ""]
    base = df_in.loc[s.index].copy()
    base["_texto"] = s

    if min_chars and min_chars > 0:
        base = base[base["_texto"].astype(str).str.len() >= int(min_chars)]

    q = (query or "").strip()
    if q:
        if mode == "Regex":
            try:
                rx = re.compile(q, flags=re.IGNORECASE)
                base = base[base["_texto"].astype(str).apply(lambda x: bool(rx.search(x)))]
            except re.error:
                st.warning("Regex inválida. Cambia a 'Contiene' o corrige tu patrón.")
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
    st.caption(f"Campo: **{label_sel}**  |  Comentarios filtrados: **{total}**")
    if total == 0:
        st.info("No hay comentarios con los filtros actuales.")
        return

    if fecha_col and fecha_col in base.columns and pd.api.types.is_datetime64_any_dtype(base[fecha_col]):
        base = base.sort_values(fecha_col, ascending=False)

    with st.expander("Resumen del texto", expanded=True):
        texts = base["_texto"].astype(str).tolist()
        lens = [len(t) for t in texts]

        a, b, c, d = st.columns(4)
        a.metric("Comentarios", f"{len(texts)}")
        b.metric("Longitud promedio", f"{(sum(lens)/len(lens)):.0f}" if lens else "—")
        c.metric("Mediana", f"{pd.Series(lens).median():.0f}" if lens else "—")
        d.metric("Máximo", f"{max(lens)}" if lens else "—")

        toks = []
        for t in texts:
            toks.extend(_tokenize_es(t, min_len=3))
        cnt = Counter(toks)
        top = cnt.most_common(20)

        if top:
            top_df = pd.DataFrame(top, columns=["Palabra", "Frecuencia"])
            st.dataframe(top_df, use_container_width=True, height=300)
        else:
            st.info("No se pudieron extraer palabras relevantes con los filtros actuales.")

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

    st.divider()
    st.markdown("#### Comentarios (detalle)")

    cols_to_show = []
    if fecha_col and fecha_col in base.columns:
        cols_to_show.append(fecha_col)
    if carrera_col and carrera_col in base.columns:
        cols_to_show.append(carrera_col)
    cols_to_show.append("_texto")

    show = base[cols_to_show].rename(columns={"_texto": "Comentario"})
    st.dataframe(show.head(int(show_n)), use_container_width=True, height=520)

    csv = show.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar CSV (comentarios filtrados)",
        data=csv,
        file_name="comentarios_filtrados.csv",
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
    ws_cat = resolve(SHEET_CATALOGO)

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
    st.subheader("Encuesta de calidad")

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
    # Elegir pestaña a leer
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
        df = _ensure_prepa_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Normalizar MAPA (soporta mapa viejo y mapa nuevo)
    # ---------------------------
    mapa = mapa.copy()

    # columnas mínimas aceptadas: header_exacto + scale_code, y alguna de (header_num o header_id)
    if "header_exacto" not in mapa.columns and "header_raw" in mapa.columns:
        mapa["header_exacto"] = mapa["header_raw"]

    required_any = {"header_exacto", "scale_code"}
    if not required_any.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer al menos: header_exacto (o header_raw) y scale_code.")
        return
    if ("header_num" not in mapa.columns) and ("header_id" not in mapa.columns):
        st.error("La hoja 'Mapa_Preguntas' debe traer: header_num o header_id (idealmente ambas).")
        return

    # limpiar strings
    for c in ["modalidad", "header_raw", "header_exacto", "header_id", "scale_code", "header_num", "section_code", "section_name", "driver_name", "keywords"]:
        if c in mapa.columns:
            mapa[c] = mapa[c].astype(str).fillna("").map(lambda x: str(x).strip())

    # scale normalizado
    mapa["scale_code_norm"] = mapa["scale_code"].apply(_normalize_scale_code)

    # section_code: si existe úsalo; si no, intenta derivar de header_num, luego de header_id
    if "section_code" not in mapa.columns or (mapa["section_code"].astype(str).str.strip() == "").all():
        def _sec_from_any(row):
            hn = _safe_str(row.get("header_num"))
            if hn and "_" in hn:
                return hn.split("_", 1)[0]
            hid = _safe_str(row.get("header_id"))
            if hid:
                parts = hid.split("_")
                if len(parts) >= 2:
                    return parts[1]
            return "OTR"
        mapa["section_code"] = mapa.apply(_sec_from_any, axis=1)

    # section_name fallback
    if "section_name" not in mapa.columns or (mapa["section_name"].astype(str).str.strip() == "").all():
        mapa["section_name"] = mapa["section_code"].map(SECTION_LABELS).fillna(mapa["section_code"])
    else:
        # si viene abreviado o vacío, usa fallback
        mapa["section_name"] = mapa["section_name"].astype(str).str.strip()
        mask_abbrev = (mapa["section_name"] == "") | (mapa["section_name"].str.len() <= 4)
        mapa.loc[mask_abbrev, "section_name"] = mapa.loc[mask_abbrev, "section_code"].map(SECTION_LABELS).fillna(mapa.loc[mask_abbrev, "section_code"])

    # resolver columna real
    df_cols = set(df.columns)
    mapa["col_resuelta"] = mapa.apply(lambda r: _infer_col_from_map_row(r, df_cols), axis=1)

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
                st.text_input("Carrera/Servicio (fijo)", value=carrera_sel, disabled=True)
            else:
                if carrera_col:
                    opts = ["(Todas)"] + sorted(df[carrera_col].dropna().astype(str).str.strip().unique().tolist())
                    carrera_sel = st.selectbox("Carrera/Servicio", opts, index=0)
                else:
                    st.info("No encontré columna válida para Carrera/Servicio.")
                    carrera_col = None
                    carrera_sel = "(Todas)"
    else:
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
                st.warning("No encontré columnas para filtrar por carrera.")
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
    # CAMINO DG/DC (PROCESADO): usa *_num y *_txt, pero YA CON MAPA NUEVO
    # =========================================================
    if vista != "Dirección Finanzas":
        # mapa_ok: solo filas con col_resuelta existente
        mapa_ok = mapa[mapa["col_resuelta"].astype(str).str.strip() != ""].copy()

        # num cols detectadas
        num_cols = [c for c in f.columns if str(c).endswith("_num")]
        if not num_cols:
            st.warning("No encontré columnas *_num en PROCESADO. Verifica que tu PROCESADO tenga numéricos.")
            st.dataframe(f.head(30), use_container_width=True)
            return

        likert_cols, yesno_cols = _auto_classify_numcols(f, num_cols)

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
                cols = []
                for _, m in g.iterrows():
                    col = m["col_resuelta"]
                    if col in f.columns and col in likert_cols:
                        cols.append(col)
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
                # Solo preguntas YESNO del mapa (si las hay); si no, muestra las yesno_cols detectadas
                mapa_yesno = mapa_ok[mapa_ok["scale_code_norm"] == "YESNO"].copy()
                if not mapa_yesno.empty:
                    it = mapa_yesno.iterrows()
                    for _, m in it:
                        col = m["col_resuelta"]
                        if col not in f.columns or col not in yesno_cols:
                            continue
                        mean_val = _mean_numeric(f[col])
                        if pd.isna(mean_val):
                            continue
                        yn_rows.append({"Pregunta": m["header_exacto"], "% Sí": float(mean_val) * 100})
                else:
                    for col in yesno_cols:
                        mean_val = _mean_numeric(f[col])
                        if pd.isna(mean_val):
                            continue
                        yn_rows.append({"Pregunta": col, "% Sí": float(mean_val) * 100})

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
        # Por sección (MEJORADO: incluye ABIERTAS por sección)
        # ---------------------------
        with tab2:
            st.markdown("### Por sección (promedio + preguntas + respuestas abiertas)")

            # Tabla resumen secciones (Likert)
            sec_rows = []
            for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                cols = [m["col_resuelta"] for _, m in g.iterrows() if m["col_resuelta"] in f.columns and m["col_resuelta"] in likert_cols]
                if not cols:
                    continue
                val = pd.to_numeric(f[cols].stack(), errors="coerce").mean()
                if pd.isna(val):
                    continue
                sec_rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(cols), "sec_code": sec_code})

            sec_df2 = pd.DataFrame(sec_rows).sort_values("Promedio", ascending=False) if sec_rows else pd.DataFrame()
            if sec_df2.empty:
                st.info("No hay secciones Likert disponibles con los filtros actuales.")
            else:
                for _, r in sec_df2.iterrows():
                    sec_code = r["sec_code"]
                    sec_name = r["Sección"]
                    sec_avg = r["Promedio"]

                    with st.expander(f"{sec_name} — Promedio: {sec_avg:.2f}", expanded=False):
                        mm = mapa_ok[mapa_ok["section_code"] == sec_code].copy()

                        # 1) Preguntas numéricas
                        qrows = []
                        for _, m in mm.iterrows():
                            col = m["col_resuelta"]
                            if not col or col not in f.columns:
                                continue

                            if col in yesno_cols:
                                mean_val = _mean_numeric(f[col])
                                if pd.isna(mean_val):
                                    continue
                                qrows.append({"Pregunta": m["header_exacto"], "Valor": float(mean_val) * 100, "Tipo": "Sí/No"})
                            elif col in likert_cols:
                                mean_val = _mean_numeric(f[col])
                                if pd.isna(mean_val):
                                    continue
                                qrows.append({"Pregunta": m["header_exacto"], "Valor": float(mean_val), "Tipo": "Likert"})

                        qdf = pd.DataFrame(qrows)

                        if not qdf.empty:
                            qdf_l = qdf[qdf["Tipo"] == "Likert"].copy()
                            if not qdf_l.empty:
                                qdf_l = qdf_l.sort_values("Valor", ascending=False)
                                st.markdown("**Preguntas Likert (1–5)**")
                                show_l = qdf_l.rename(columns={"Valor": "Promedio"})[["Pregunta", "Promedio"]].reset_index(drop=True)
                                st.dataframe(show_l, use_container_width=True)

                            qdf_y = qdf[qdf["Tipo"] == "Sí/No"].copy()
                            if not qdf_y.empty:
                                qdf_y = qdf_y.sort_values("Valor", ascending=False)
                                st.markdown("**Preguntas Sí/No**")
                                show_y = qdf_y.rename(columns={"Valor": "% Sí"})[["Pregunta", "% Sí"]].reset_index(drop=True)
                                st.dataframe(show_y, use_container_width=True)
                        else:
                            st.info("Sin preguntas numéricas para esta sección con los filtros actuales.")

                        # 2) ABIERTAS por sección (lo que pediste)
                        open_items = []
                        mm_open = mm[mm["scale_code_norm"] == "ABIERTA"].copy()
                        for _, m in mm_open.iterrows():
                            col = m["col_resuelta"]
                            if col and col in f.columns:
                                open_items.append({
                                    "col": col,
                                    "label": m.get("header_exacto", col),
                                    "section": sec_name,
                                    "driver": m.get("driver_name", ""),
                                })

                        if open_items:
                            st.divider()
                            st.markdown("**Respuestas abiertas de esta sección**")
                            carrera_col3 = _best_carrera_col(f)
                            _render_comentarios(
                                df_in=f,
                                open_items=open_items,
                                fecha_col=fecha_col,
                                carrera_col=carrera_col3,
                                title="",
                            )
                        else:
                            st.caption("Sin preguntas abiertas registradas en el mapa para esta sección.")

        # ---------------------------
        # Comparativo entre carreras (solo DG)
        # ---------------------------
        if tab4 is not None:
            with tab4:
                st.markdown("### Comparativo entre carreras por sección")
                st.caption("Promedios Likert (1–5) por sección (respeta filtro de Año).")

                carrera_col2 = _best_carrera_col(f)
                if not carrera_col2:
                    st.warning("No se encontró una columna válida para identificar Carrera/Servicio.")
                else:
                    if carrera_param_fija:
                        st.info("Para ver el comparativo entre carreras, selecciona **(Todas)**.")
                    else:
                        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                            cols = [m["col_resuelta"] for _, m in g.iterrows() if m["col_resuelta"] in f.columns and m["col_resuelta"] in likert_cols]
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

                            sec_comp = pd.DataFrame(rows).sort_values("Promedio", ascending=False).reset_index(drop=True)
                            with st.expander(f"{sec_name}", expanded=False):
                                st.dataframe(sec_comp, use_container_width=True)

        # ---------------------------
        # Comentarios (DG/DC) — ESTÉTICOS + sin IDs
        # ---------------------------
        with tab3:
            # ABIERTAS desde mapa
            open_items = []
            m_open = mapa_ok[mapa_ok["scale_code_norm"] == "ABIERTA"].copy()

            for _, m in m_open.iterrows():
                col = _safe_str(m.get("col_resuelta"))
                if not col or col not in f.columns:
                    continue

                label = _safe_str(m.get("header_exacto")) or col
                section = _safe_str(m.get("section_name"))
                driver = _safe_str(m.get("driver_name"))

                open_items.append({"col": col, "label": label, "section": section, "driver": driver})

            # fallback por si acaso
            if not open_items:
                fallback_cols = [
                    c for c in f.columns
                    if (not str(c).endswith("_num"))
                    and any(k in str(c).lower() for k in ["¿por qué", "por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "descr"])
                ]
                open_items = [{"col": c, "label": c, "section": "", "driver": ""} for c in fallback_cols]

            carrera_col3 = _best_carrera_col(f)
            _render_comentarios(
                df_in=f,
                open_items=open_items,
                fecha_col=fecha_col,
                carrera_col=carrera_col3,
                title="Comentarios y respuestas abiertas",
            )

        return

    # =========================================================
    # CAMINO DF (Dirección Finanzas): sin *_num; columnas “humanas” ya numéricas
    # =========================================================
    open_cols_df = [c for c in f.columns if any(k in str(c).lower() for k in ["¿por qué", "por qué", "comentario", "sugerencia", "escríbelo", "escribelo"])]

    base_exclude = set()
    for c in ["Marca temporal", "Marca Temporal", "Dirección de correo electrónico"]:
        if c in f.columns:
            base_exclude.add(c)

    num_candidates = []
    for c in f.columns:
        if c in base_exclude or c in open_cols_df:
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

    with tab3:
        open_items = [{"col": c, "label": c, "section": "", "driver": ""} for c in open_cols_df]
        carrera_col_df = _best_carrera_col(f)
        _render_comentarios(
            df_in=f,
            open_items=open_items,
            fecha_col=fecha_col,
            carrera_col=carrera_col_df,
            title="Comentarios y respuestas abiertas",
        )
