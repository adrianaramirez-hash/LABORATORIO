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
def _to_datetime_safe(s):
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

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

def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()

def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _tokenize_es(s: str, min_len: int = 3) -> list[str]:
    s = _normalize_text(s)
    s = re.sub(r"[^\wáéíóúüñ]+", " ", s, flags=re.IGNORECASE)
    toks = [t for t in s.split() if len(t) >= min_len and t not in STOPWORDS_ES]
    return toks

def _id_swap_variant(header_id: str) -> str | None:
    """
    Para casos como:
      ESC_DIR_01  -> DIR_ESC_01
    Si no cumple patrón A_B_C, regresa None.
    """
    if not header_id:
        return None
    parts = str(header_id).strip().split("_")
    if len(parts) >= 3:
        parts2 = parts[:]
        parts2[0], parts2[1] = parts2[1], parts2[0]
        return "_".join(parts2)
    return None

def _resolve_numeric_col(df: pd.DataFrame, row: pd.Series) -> str | None:
    """
    Resolver columna numérica en df para un item del mapa.
    Prioridad:
      1) header_id + "_num"
      2) swap(header_id) + "_num"
      3) header_raw exacto si existe y es numérico (DF/human headers)
    """
    hid = str(row.get("header_id", "") or "").strip()
    hraw = str(row.get("header_raw", "") or "").strip()

    candidates = []
    if hid:
        candidates.append(f"{hid}_num")
        sv = _id_swap_variant(hid)
        if sv:
            candidates.append(f"{sv}_num")
        # por si ya trae sufijo
        candidates.append(hid)

    # A veces ya viene como DIR_ESC_01_num en header_id
    for c in candidates:
        if c in df.columns:
            return c

    if hraw and hraw in df.columns:
        s = pd.to_numeric(df[hraw], errors="coerce")
        if s.notna().any():
            return hraw

    return None

def _resolve_text_col(df: pd.DataFrame, row: pd.Series) -> str | None:
    """
    Resolver columna de texto en df para un item ABIERTA del mapa.
    Prioridad:
      1) header_id + "_txt"
      2) swap(header_id) + "_txt"
      3) header_raw exacto si existe
    """
    hid = str(row.get("header_id", "") or "").strip()
    hraw = str(row.get("header_raw", "") or "").strip()

    candidates = []
    if hid:
        candidates.append(f"{hid}_txt")
        sv = _id_swap_variant(hid)
        if sv:
            candidates.append(f"{sv}_txt")
        candidates.append(hid)  # por si la col ya es texto sin sufijo

    for c in candidates:
        if c in df.columns:
            return c

    if hraw and hraw in df.columns:
        return hraw

    return None

def _safe_section_name(sec_code: str, sec_name: str | None):
    sec_name = (sec_name or "").strip()
    if not sec_name or sec_name == sec_code or len(sec_name) <= 4:
        return SECTION_LABELS.get(sec_code, sec_name or sec_code)
    return sec_name

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

def _make_keywords_list(m_sec: pd.DataFrame) -> list[str]:
    toks = []
    if "keywords" not in m_sec.columns:
        return toks
    for x in m_sec["keywords"].fillna("").astype(str).tolist():
        x = x.strip()
        if not x:
            continue
        parts = [p.strip() for p in x.split("|") if p.strip()]
        toks.extend(parts)
    # uniq preservando orden
    seen = set()
    out = []
    for t in toks:
        tl = t.lower()
        if tl in seen:
            continue
        seen.add(tl)
        out.append(t)
    return out[:12]  # máximo 12 chips por estética

def _render_section_questions_table(
    f: pd.DataFrame,
    m_sec: pd.DataFrame,
    df_kind: str,
):
    """
    Tabla compacta de preguntas (sin charts).
    df_kind: "DGDC" o "DF" (solo etiqueta; la resolución es igual).
    """
    rows = []
    for _, r in m_sec.iterrows():
        tipo = str(r.get("tipo", "") or "").strip().upper()
        if tipo == "ABIERTA":
            continue

        col = _resolve_numeric_col(f, r)
        if not col:
            continue

        s = pd.to_numeric(f[col], errors="coerce")
        if not s.notna().any():
            continue

        # Etiqueta bonita
        label = str(r.get("driver_name", "") or "").strip()
        if not label:
            label = str(r.get("header_raw", "") or "").strip()
        if not label:
            label = str(r.get("header_id", "") or "").strip()

        # Detectar si es 0/1 o 1-5 (o usar escala_max si viene)
        escala_max = r.get("escala_max", None)
        try:
            escala_max = float(escala_max) if escala_max not in (None, "", pd.NA) else None
        except Exception:
            escala_max = None

        mean_val = float(s.mean())
        if escala_max is not None and escala_max <= 1.0:
            # Sí/No
            val_show = round(mean_val * 100.0, 1)
            metric = "% Sí"
            sort_val = val_show
        else:
            # Likert
            val_show = round(mean_val, 2)
            metric = "Promedio"
            sort_val = val_show

        rows.append({
            "Pregunta": label,
            metric: val_show,
            "_sort": sort_val,
        })

    if not rows:
        st.info("No hay preguntas numéricas detectables en esta sección con los filtros actuales.")
        return

    out = pd.DataFrame(rows)

    # Orden: peores primero (para foco)
    # Para Likert: menor es peor. Para %Sí: menor es peor también.
    metric_col = "% Sí" if "% Sí" in out.columns else "Promedio"
    out = out.sort_values(metric_col, ascending=True).reset_index(drop=True)

    # Tabla compacta
    st.dataframe(
        out.drop(columns=["_sort"], errors="ignore"),
        use_container_width=True,
        height=min(520, 64 + 28 * min(len(out), 14)),
    )

def _render_section_comments_simple(
    f: pd.DataFrame,
    m_sec: pd.DataFrame,
    fecha_col: str | None,
    carrera_col: str | None,
    sec_key: str,
):
    """
    Comentarios por sección: SOLO buscador + chips + tabla (sin controles extra).
    Busca automáticamente en TODAS las preguntas ABIERTA de la sección.
    """
    # Columnas abiertas de la sección
    m_open = m_sec.copy()
    m_open["tipo"] = m_open.get("tipo", "").astype(str).str.upper()
    m_open = m_open[m_open["tipo"] == "ABIERTA"].copy()
    if m_open.empty:
        st.caption("Sin preguntas abiertas registradas en esta sección.")
        return

    open_cols = []
    open_labels = []
    for _, r in m_open.iterrows():
        c = _resolve_text_col(f, r)
        if c and c in f.columns:
            open_cols.append(c)
            lab = str(r.get("driver_name", "") or "").strip()
            if not lab:
                lab = str(r.get("header_raw", "") or "").strip()
            open_labels.append(lab or c)

    if not open_cols:
        st.caption("No se detectaron columnas de comentarios para esta sección.")
        return

    # ============ UI: chips + buscador único ============
    kws = _make_keywords_list(m_open)  # keywords típicamente viven en la fila ABIERTA (o en varias)
    if kws:
        st.caption("Temas rápidos:")
        cols = st.columns(min(len(kws), 6))
        for i, kw in enumerate(kws):
            with cols[i % len(cols)]:
                if st.button(kw, key=f"kw_{sec_key}_{i}", use_container_width=True):
                    st.session_state[f"q_{sec_key}"] = kw

    q = st.text_input(
        "Buscar en comentarios de esta sección",
        value=st.session_state.get(f"q_{sec_key}", ""),
        key=f"q_{sec_key}",
        placeholder="Ej. SEAC, baños, cobranzas, profesor…",
    ).strip()

    # ============ Construir dataset long de comentarios ============
    pieces = []
    for c, lab in zip(open_cols, open_labels):
        s = f[c].dropna().astype(str)
        s = s[s.str.strip() != ""]
        if s.empty:
            continue
        base = f.loc[s.index].copy()
        base["_comentario"] = s
        base["_campo"] = lab
        pieces.append(base)

    if not pieces:
        st.caption("No hay comentarios en esta sección con los filtros actuales.")
        return

    long = pd.concat(pieces, axis=0, ignore_index=False)
    # Ordenar por fecha si existe
    if fecha_col and fecha_col in long.columns and pd.api.types.is_datetime64_any_dtype(long[fecha_col]):
        long = long.sort_values(fecha_col, ascending=False)

    # Filtro simple: contiene (SIEMPRE)
    if q:
        mask = long["_comentario"].astype(str).str.contains(q, case=False, na=False)
        long = long[mask]
        st.caption(f"Comentarios filtrados: **{len(long)}**")
    else:
        # Sin query: mostrar más recientes (limitado)
        long = long.head(60)
        st.caption(f"Mostrando más recientes: **{len(long)}**")

    if long.empty:
        st.info("Sin resultados con esa búsqueda.")
        return

    # Columnas a mostrar (sin “filas en blanco” y con altura dinámica)
    cols_show = []
    if fecha_col and fecha_col in long.columns:
        cols_show.append(fecha_col)
    if carrera_col and carrera_col in long.columns:
        cols_show.append(carrera_col)
    cols_show.append("_comentario")

    show = long[cols_show].rename(columns={
        fecha_col: "Marca temporal" if fecha_col else "Marca temporal",
        carrera_col: "Carrera/Servicio" if carrera_col else "Carrera/Servicio",
        "_comentario": "Comentario",
    })

    # Si no existe carrera_col, intentar con una alternativa
    if "Carrera/Servicio" not in show.columns:
        show.insert(1, "Carrera/Servicio", "—")

    # Asegurar el nombre de fecha
    if "Marca temporal" not in show.columns:
        show.insert(0, "Marca temporal", pd.NaT)

    # Height dinámico, sin “tabla gigante”
    n = len(show)
    height = min(520, 56 + 28 * min(n, 14))

    st.dataframe(show, use_container_width=True, height=height)

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
    DG/DC:
      - Tabs: Resumen | Por sección | Comparativo (solo DG)
      - "Por sección": tablas (sin gráficas) + comentarios por sección (buscador único + chips)
      - Se elimina la pestaña "Comentarios"
    DF:
      - Tabs: Resumen | Por sección
      - "Por sección": tablas + comentarios por sección (igual)
    Usa Mapa_Preguntas NUEVO:
      modalidad, header_raw, header_id, section_code, section_name, tipo, escala_min, escala_max, driver_name, keywords
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
        df = _ensure_prepa_columns(df)

    # Fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Validación mapa (NUEVO)
    # ---------------------------
    mapa = mapa.copy()
    # Compat: si viene header_exacto, lo mapeamos a header_raw
    if "header_raw" not in mapa.columns and "header_exacto" in mapa.columns:
        mapa["header_raw"] = mapa["header_exacto"]

    required_cols = {"header_raw", "header_id", "section_code", "tipo"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer al menos: header_raw, header_id, section_code, tipo.")
        return

    # Normalizar strings
    for c in ["modalidad", "header_raw", "header_id", "section_code", "section_name", "tipo", "driver_name", "keywords"]:
        if c in mapa.columns:
            mapa[c] = mapa[c].fillna("").astype(str).str.strip()

    # section_name fallback
    if "section_name" not in mapa.columns:
        mapa["section_name"] = mapa["section_code"]
    mapa["section_name"] = mapa.apply(lambda r: _safe_section_name(r["section_code"], r.get("section_name", "")), axis=1)

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

    # ---------------------------
    # Filtrar mapa por modalidad (si existe la columna)
    # ---------------------------
    mapa_use = mapa.copy()
    if "modalidad" in mapa_use.columns and mapa_use["modalidad"].astype(str).str.strip().ne("").any():
        # Normalización mínima: usar prefijos que ya manejas en tu mapa (ESCOLARIZADOS, PREPA, VIRTUAL)
        # Mapeo desde selector UI:
        mod_map = {
            "Escolarizado / Ejecutivas": "ESCOLARIZADOS",
            "Preparatoria": "PREPA",
            "Virtual / Mixto": "VIRTUAL",
        }
        tag = mod_map.get(modalidad, "")
        if tag:
            mapa_use = mapa_use[mapa_use["modalidad"].astype(str).str.upper().str.strip() == tag].copy()

    # ---------------------------
    # Tabs por vista
    # ---------------------------
    if vista == "Dirección General":
        tab1, tab2, tab4 = st.tabs(["Resumen", "Por sección", "Comparativo entre carreras"])
    elif vista == "Dirección Finanzas":
        tab1, tab2 = st.tabs(["Resumen", "Por sección"])
        tab4 = None
    else:
        tab1, tab2 = st.tabs(["Resumen", "Por sección"])
        tab4 = None

    # =========================================================
    # RESUMEN (simple, se mantiene)
    # =========================================================
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", f"{len(f)}")

        # Detectar columnas numéricas disponibles (DG/DC: *_num; DF: numéricas en general)
        if vista != "Dirección Finanzas":
            num_cols = [c for c in f.columns if str(c).endswith("_num")]
        else:
            base_exclude = set()
            for c in ["Marca temporal", "Marca Temporal", "Dirección de correo electrónico"]:
                if c in f.columns:
                    base_exclude.add(c)
            num_cols = []
            for c in f.columns:
                if c in base_exclude:
                    continue
                s = pd.to_numeric(f[c], errors="coerce")
                if s.notna().any():
                    num_cols.append(c)

        likert_cols, yesno_cols = _auto_classify_numcols(f, num_cols)

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
        # Promedio por sección usando mapa + resolver
        rows = []
        for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
            # Solo no-abiertas
            g2 = g.copy()
            g2["tipo"] = g2["tipo"].astype(str).str.upper()
            g2 = g2[g2["tipo"] != "ABIERTA"]
            cols = []
            for _, rr in g2.iterrows():
                cc = _resolve_numeric_col(f, rr)
                if cc:
                    cols.append(cc)
            cols = [c for c in cols if c in f.columns]
            if not cols:
                continue
            vals = pd.to_numeric(f[cols].stack(), errors="coerce")
            if not vals.notna().any():
                continue
            mean_val = float(vals.mean())
            # Si es escala 0/1, esto no aplica como Likert; filtramos: max > 1
            if float(vals.max()) <= 1.0:
                continue
            rows.append({"Sección": sec_name, "Promedio": round(mean_val, 2), "Preguntas": len(cols), "_sec": sec_code})

        if not rows:
            st.info("No hay datos suficientes para calcular promedios por sección (Likert) con los filtros actuales.")
        else:
            sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=True).reset_index(drop=True)
            st.dataframe(
                sec_df.drop(columns=["_sec"], errors="ignore"),
                use_container_width=True,
                height=min(520, 64 + 28 * min(len(sec_df), 14)),
            )

    # =========================================================
    # POR SECCIÓN (nuevo: tablas + comentarios simples + chips)
    # =========================================================
    with tab2:
        st.markdown("### Por sección")
        st.caption("Tablas sin gráficas. Comentarios: buscador único por sección + chips de temas.")

        # Construir lista de secciones disponibles
        sec_list = []
        for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
            sec_list.append((sec_name, sec_code))
        if not sec_list:
            st.warning("No hay secciones en Mapa_Preguntas para esta modalidad.")
            return

        # Orden alfabético por nombre
        sec_list = sorted(sec_list, key=lambda x: x[0].lower())

        # Render por expander
        for sec_name, sec_code in sec_list:
            m_sec = mapa_use[mapa_use["section_code"] == sec_code].copy()
            if m_sec.empty:
                continue

            with st.expander(sec_name, expanded=False):
                # 1) Tabla de preguntas (sin gráficas)
                st.markdown("**Preguntas (promedio)**")
                _render_section_questions_table(f=f, m_sec=m_sec, df_kind=("DF" if vista == "Dirección Finanzas" else "DGDC"))

                st.divider()

                # 2) Comentarios por sección: SOLO buscador + chips + tabla
                st.markdown("**Comentarios de la sección**")
                carrera_col_here = _best_carrera_col(f)
                _render_section_comments_simple(
                    f=f,
                    m_sec=m_sec,
                    fecha_col=fecha_col,
                    carrera_col=carrera_col_here,
                    sec_key=f"{modalidad}_{vista}_{sec_code}",
                )

    # =========================================================
    # COMPARATIVO ENTRE CARRERAS (solo DG) — se mantiene
    # =========================================================
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
                st.warning("No se encontró una columna válida para identificar Carrera/Servicio.")
            else:
                if carrera_param_fija:
                    st.info("Para ver el comparativo entre carreras, selecciona **(Todas)** en el selector superior.")
                else:
                    for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
                        g2 = g.copy()
                        g2["tipo"] = g2["tipo"].astype(str).str.upper()
                        g2 = g2[g2["tipo"] != "ABIERTA"]

                        cols = []
                        for _, rr in g2.iterrows():
                            cc = _resolve_numeric_col(f, rr)
                            if cc:
                                cols.append(cc)
                        cols = [c for c in cols if c in f.columns]
                        if not cols:
                            continue

                        rows = []
                        for carrera_val, df_c in f.groupby(carrera_col2):
                            vals = pd.to_numeric(df_c[cols].stack(), errors="coerce")
                            if not vals.notna().any():
                                continue
                            # Solo Likert
                            if float(vals.max()) <= 1.0:
                                continue
                            mean_val = float(vals.mean())
                            rows.append({
                                "Carrera/Servicio": str(carrera_val).strip(),
                                "Promedio": round(mean_val, 2),
                                "Respuestas": int(len(df_c)),
                                "Preguntas": int(len(cols)),
                            })

                        if not rows:
                            continue

                        sec_comp = (
                            pd.DataFrame(rows)
                            .sort_values("Promedio", ascending=True)
                            .reset_index(drop=True)
                        )

                        with st.expander(sec_name, expanded=False):
                            st.dataframe(
                                sec_comp,
                                use_container_width=True,
                                height=min(520, 64 + 28 * min(len(sec_comp), 14)),
                            )
