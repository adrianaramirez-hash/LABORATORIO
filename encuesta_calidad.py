# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread
import textwrap
import re
from collections import Counter

# ============================================================
# Etiquetas de secciones (fallback)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinación",
    "SER": "Servicios institucionales",
    "ADM": "Soporte administrativo",
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Soporte académico (SEAC)",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",
    "MAT": "Materiales",
    "UDL": "Comunicación con UDL",
    "COM": "Comunicación con compañeros",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

MAX_VERTICAL_QUESTIONS = 7
MAX_VERTICAL_SECTIONS = 7

# ============================================================
# Hojas
# ============================================================
SHEET_PROCESADO_DEFAULT = "PROCESADO"        # DG / DC
SHEET_PROCESADO_DF = "VISTA_FINANZAS_NUM"    # DF (ya numérica, con encabezados “humanos”)
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"  # opcional

# ============================================================
# Stopwords básicas ES
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
# Helpers base
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


# ============================================================
# Charts
# ============================================================
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
                x=alt.X("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"), axis=cat_axis_vertical),
                y=alt.Y(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), axis=alt.Axis(title=value_title)),
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
            y=alt.Y("_cat_wrapped:N", sort=alt.SortField(field=value_col, order="descending"), axis=cat_axis_horizontal),
            x=alt.X(f"{value_col}:Q", scale=alt.Scale(domain=value_domain), axis=alt.Axis(title=value_title)),
            tooltip=tooltip_cols,
        )
        .properties(height=dynamic_height)
    )


# ============================================================
# Google Sheets loader
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
# Mapa "nuevo" -> resolver columnas reales del dataframe
# Encabezado mapa esperado (tu caso):
# modalidad, header_raw, header_id, section_code, section_name, tipo, escala_min, escala_max, driver_name, keywords
# ============================================================
def _clean_str(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip()

def _parse_keywords_pipe(s: str) -> list[str]:
    s = _clean_str(s)
    if not s:
        return []
    parts = [p.strip().lower() for p in s.split("|") if p.strip()]
    return parts

def _resolve_col_from_header_id(df: pd.DataFrame, header_id: str, tipo: str) -> str | None:
    """
    Intenta mapear header_id del mapa a columna real en PROCESADO.
    Casos cubiertos:
    - Virtual: columnas ya vienen como header_id + _num (muchas ya incluyen _num en el propio nombre)
    - Escolarizados/Prepa: a veces open usa ESC_DIR_05_txt y num usa DIR_ESC_01_num (swap tokens)
    """
    hid = _clean_str(header_id)
    if not hid:
        return None

    tipo_u = _clean_str(tipo).upper()

    # posibles sufijos
    want_txt = tipo_u in ["ABIERTA", "ABIERTO", "OPEN", "TEXTO", "TEXT"]
    suffixes = ["_txt"] if want_txt else ["_num"]

    candidates = []

    # 1) directo
    for suf in suffixes:
        candidates.append(f"{hid}{suf}")

    # 2) si ya viene con sufijo incluido
    candidates.append(hid)

    # 3) swap tokens si viene ESC_DIR_01 -> DIR_ESC_01
    parts = hid.split("_")
    if len(parts) == 3:
        a, b, c = parts
        swapped = f"{b}_{a}_{c}"
        for suf in suffixes:
            candidates.append(f"{swapped}{suf}")
        candidates.append(swapped)

    # 4) algunos num en tu PROCESADO están como SECTION_MODALIDAD_XX_num (DIR_ESC_01_num)
    # ya cubierto con swap. Para virtual suele ser tal cual.

    for col in candidates:
        if col in df.columns:
            return col
    return None

def _prepare_mapa_nuevo(mapa: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    need = {"header_raw", "tipo", "section_code"}
    if not need.issubset(set(mapa.columns)):
        raise ValueError("La hoja 'Mapa_Preguntas' debe traer al menos: header_raw, tipo, section_code.")

    m = mapa.copy()

    # normalizar columnas clave
    for c in ["modalidad", "header_raw", "header_id", "section_code", "section_name", "tipo", "driver_name", "keywords"]:
        if c in m.columns:
            m[c] = m[c].astype(str).map(_clean_str)

    # section_name fallback
    if "section_name" not in m.columns:
        m["section_name"] = m["section_code"].map(SECTION_LABELS).fillna(m["section_code"])
    else:
        m["section_name"] = m["section_name"].where(m["section_name"] != "", m["section_code"])
        m.loc[m["section_name"].str.len() <= 4, "section_name"] = (
            m.loc[m["section_name"].str.len() <= 4, "section_code"].map(SECTION_LABELS).fillna(m["section_name"])
        )

    # resolver columna real por header_id si existe
    if "header_id" in m.columns:
        m["_col"] = m.apply(lambda r: _resolve_col_from_header_id(df, r.get("header_id", ""), r.get("tipo", "")), axis=1)
    else:
        m["_col"] = None

    # fallback: si no hay header_id, intentar match por nombre exacto con header_raw (poco común)
    m.loc[m["_col"].isna(), "_col"] = m.loc[m["_col"].isna(), "header_raw"].apply(lambda x: x if x in df.columns else None)

    # exists
    m["exists"] = m["_col"].notna()
    return m


# ============================================================
# Comentarios "bonitos": selector por pregunta (texto humano)
# ============================================================
def _render_comentarios_bonitos(
    f: pd.DataFrame,
    mapa_ok: pd.DataFrame,
    fecha_col: str | None,
    carrera_col: str | None,
    title: str = "Comentarios y respuestas abiertas",
):
    st.markdown(f"### {title}")

    open_map = mapa_ok[mapa_ok["tipo"].str.upper().isin(["ABIERTA", "ABIERTO", "OPEN", "TEXTO", "TEXT"])].copy()
    open_map = open_map[open_map["exists"]].copy()
    if open_map.empty:
        st.info("No hay preguntas abiertas disponibles (revisa mapa y columnas *_txt).")
        return

    # opciones: mostrar header_raw (humano) pero guardar _col (real)
    options = []
    for _, r in open_map.iterrows():
        human = r["header_raw"]
        sec = r.get("section_name", "")
        if sec:
            label = f"[{sec}] {human}"
        else:
            label = human
        options.append((label, r["_col"], r.get("keywords", "")))

    c1, c2, c3 = st.columns([2.6, 1.2, 1.4])
    with c1:
        label_sel = st.selectbox("Pregunta abierta", [o[0] for o in options])
    with c2:
        min_chars = st.number_input("Mín. caracteres", min_value=0, max_value=500, value=10, step=5)
    with c3:
        mode = st.selectbox("Modo búsqueda", ["Contiene", "Regex"], index=0)

    col_sel = dict((o[0], o[1]) for o in options)[label_sel]
    kw_default = dict((o[0], o[2]) for o in options).get(label_sel, "")

    c4, c5, c6, c7 = st.columns([2.0, 1.0, 1.4, 1.6])
    with c4:
        query = st.text_input("Buscar (palabra/frase)", value="")
    with c5:
        require_all = st.checkbox("Todas las palabras", value=False)
    with c6:
        show_n = st.number_input("Mostrar N", min_value=10, max_value=2000, value=300, step=50)
    with c7:
        use_kw = st.checkbox("Usar keywords del mapa", value=False, help="Aplica el diccionario de keywords del mapa para filtrar automáticamente.")

    # textos
    s = f[col_sel].dropna().astype(str)
    s = s[s.str.strip() != ""]
    base = f.loc[s.index].copy()
    base["_texto"] = s

    if min_chars and min_chars > 0:
        base = base[base["_texto"].astype(str).str.len() >= int(min_chars)]

    # keywords dictionary
    if use_kw and _clean_str(kw_default):
        kws = _parse_keywords_pipe(kw_default)
        if kws:
            rx = re.compile(r"(" + "|".join([re.escape(k) for k in kws]) + r")", flags=re.IGNORECASE)
            base = base[base["_texto"].astype(str).apply(lambda x: bool(rx.search(x)))]

    # búsqueda manual
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

    if fecha_col and fecha_col in base.columns and pd.api.types.is_datetime64_any_dtype(base[fecha_col]):
        base = base.sort_values(fecha_col, ascending=False)

    # resumen compacto (más estético)
    with st.expander("Resumen del texto", expanded=True):
        texts = base["_texto"].astype(str).tolist()
        lens = [len(t) for t in texts]
        cA, cB, cC, cD = st.columns(4)
        cA.metric("Comentarios", f"{len(texts)}")
        cB.metric("Long. promedio", f"{(sum(lens)/len(lens)):.0f}" if lens else "—")
        cC.metric("Mediana", f"{pd.Series(lens).median():.0f}" if lens else "—")
        cD.metric("Máximo", f"{max(lens)}" if lens else "—")

        toks = []
        for t in texts:
            toks.extend(_tokenize_es(t, min_len=3))
        cnt = Counter(toks)
        top = cnt.most_common(20)
        if top:
            top_df = pd.DataFrame(top, columns=["Palabra", "Frecuencia"])
            st.dataframe(top_df, use_container_width=True, height=280)

    # tabla detalle
    st.divider()
    st.markdown("#### Detalle")
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
# UI: Por sección (con tarjetas + comentarios por sección)
# ============================================================
def _render_por_seccion_con_tarjetas(
    f: pd.DataFrame,
    mapa_ok: pd.DataFrame,
    fecha_col: str | None,
    carrera_col: str | None,
):
    st.markdown("### Secciones (promedio + detalle + comentarios)")

    # construir agregados por sección
    sec_rows = []
    for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
        # num cols para Likert (promedio 1-5) y yes/no
        g_num = g[g["tipo"].str.upper().isin(["LIKERT", "YESNO", "SI/NO", "SINO"])]
        cols_num = [c for c in g_num["_col"].tolist() if c and c in f.columns and str(c).endswith("_num")]
        if not cols_num:
            continue

        # separar likert/yesno por rango real (en este subset)
        likert_cols, yesno_cols = _auto_classify_numcols(f, cols_num)

        sec_avg = None
        if likert_cols:
            sec_avg = pd.to_numeric(f[likert_cols].stack(), errors="coerce").mean()

        sec_rows.append({
            "section_code": sec_code,
            "section_name": sec_name,
            "avg_likert": float(sec_avg) if pd.notna(sec_avg) else None,
            "n_q_likert": int(len(likert_cols)),
            "n_q_yesno": int(len(yesno_cols)),
        })

    if not sec_rows:
        st.warning("No pude construir secciones con tu mapa. Revisa que el mapa tenga header_id y que existan columnas *_num en PROCESADO.")
        return

    # orden: peores arriba (más útil)
    sec_df = pd.DataFrame(sec_rows)
    sec_df["_sort"] = sec_df["avg_likert"].fillna(999)
    sec_df = sec_df.sort_values(["_sort", "section_name"], ascending=[True, True]).drop(columns=["_sort"])

    # vista tipo “tarjeta” (expanders por sección)
    for _, r in sec_df.iterrows():
        sec_code = r["section_code"]
        sec_name = r["section_name"]
        avg = r["avg_likert"]

        title = f"{sec_name}"
        if avg is not None:
            title += f"  •  Promedio: {avg:.2f}"
        else:
            title += "  •  Promedio: —"

        with st.expander(title, expanded=False):
            mm = mapa_ok[mapa_ok["section_code"] == sec_code].copy()

            # ---- Bloque 1: Promedios por pregunta (humanos)
            st.markdown("#### Preguntas (promedio)")
            qrows = []
            for _, m in mm.iterrows():
                col = m["_col"]
                if not col or col not in f.columns:
                    continue
                tipo = _clean_str(m.get("tipo", "")).upper()
                if not str(col).endswith("_num"):
                    continue

                mean_val = _mean_numeric(f[col])
                if pd.isna(mean_val):
                    continue

                # clasificar
                if float(pd.to_numeric(f[col], errors="coerce").max(skipna=True) or 0) <= 1.0:
                    qrows.append({"Pregunta": m["header_raw"], "Métrica": "% Sí", "Valor": float(mean_val) * 100})
                else:
                    qrows.append({"Pregunta": m["header_raw"], "Métrica": "Promedio", "Valor": float(mean_val)})

            if qrows:
                qdf = pd.DataFrame(qrows)

                # Likert
                ql = qdf[qdf["Métrica"] == "Promedio"].copy()
                if not ql.empty:
                    ql = ql.sort_values("Valor", ascending=True).reset_index(drop=True)
                    st.dataframe(ql.rename(columns={"Valor": "Promedio"}), use_container_width=True, height=280)

                # Yes/No
                qy = qdf[qdf["Métrica"] == "% Sí"].copy()
                if not qy.empty:
                    qy = qy.sort_values("Valor", ascending=True).reset_index(drop=True)
                    st.dataframe(qy.rename(columns={"Valor": "% Sí"}), use_container_width=True, height=220)

            else:
                st.info("Sin datos numéricos para esta sección con los filtros actuales.")

            st.divider()

            # ---- Bloque 2: Comentarios de esta sección (todas sus ABIERTAS)
            st.markdown("#### Comentarios de la sección")

            open_mm = mm[mm["tipo"].str.upper().isin(["ABIERTA", "ABIERTO", "OPEN", "TEXTO", "TEXT"])].copy()
            open_mm = open_mm[open_mm["exists"]].copy()

            if open_mm.empty:
                st.info("Esta sección no tiene preguntas abiertas en el mapa.")
                continue

            open_options = []
            for _, m in open_mm.iterrows():
                open_options.append((m["header_raw"], m["_col"], m.get("keywords", "")))

            c1, c2, c3 = st.columns([2.4, 1.2, 1.4])
            with c1:
                sel_label = st.selectbox("Pregunta abierta (sección)", [o[0] for o in open_options], key=f"open_{sec_code}")
            with c2:
                min_chars = st.number_input("Mín. caracteres", min_value=0, max_value=500, value=10, step=5, key=f"min_{sec_code}")
            with c3:
                use_kw = st.checkbox("Usar diccionario (keywords)", value=True, key=f"kw_{sec_code}")

            sel_col = dict((o[0], o[1]) for o in open_options)[sel_label]
            sel_kw = dict((o[0], o[2]) for o in open_options).get(sel_label, "")

            c4, c5, c6 = st.columns([2.2, 1.2, 1.6])
            with c4:
                query = st.text_input("Buscar (palabra/frase)", value="", key=f"q_{sec_code}")
            with c5:
                mode = st.selectbox("Modo", ["Contiene", "Regex"], index=0, key=f"mode_{sec_code}")
            with c6:
                show_n = st.number_input("Mostrar N", min_value=10, max_value=2000, value=150, step=50, key=f"n_{sec_code}")

            s = f[sel_col].dropna().astype(str)
            s = s[s.str.strip() != ""]
            base = f.loc[s.index].copy()
            base["_texto"] = s

            if min_chars and min_chars > 0:
                base = base[base["_texto"].astype(str).str.len() >= int(min_chars)]

            if use_kw and _clean_str(sel_kw):
                kws = _parse_keywords_pipe(sel_kw)
                if kws:
                    rx = re.compile(r"(" + "|".join([re.escape(k) for k in kws]) + r")", flags=re.IGNORECASE)
                    base = base[base["_texto"].astype(str).apply(lambda x: bool(rx.search(x)))]

            q = (query or "").strip()
            if q:
                if mode == "Regex":
                    try:
                        rx = re.compile(q, flags=re.IGNORECASE)
                        base = base[base["_texto"].astype(str).apply(lambda x: bool(rx.search(x)))]
                    except re.error:
                        st.warning("Regex inválida. Cambia a modo 'Contiene' o corrige tu patrón.")
                else:
                    base = base[base["_texto"].str.contains(q, case=False, na=False)]

            st.caption(f"Comentarios filtrados: **{len(base)}**")
            if base.empty:
                st.info("No hay comentarios con los filtros actuales.")
                continue

            # mostrar tabla bonita
            cols_show = []
            if fecha_col and fecha_col in base.columns:
                cols_show.append(fecha_col)
            if carrera_col and carrera_col in base.columns:
                cols_show.append(carrera_col)
            cols_show.append("_texto")

            show = base[cols_show].rename(columns={"_texto": "Comentario"})
            st.dataframe(show.head(int(show_n)), use_container_width=True, height=420)


# ============================================================
# Render principal
# ============================================================
def render_encuesta_calidad(vista: str | None = None, carrera: str | None = None):
    st.subheader("Encuesta de calidad")

    if not vista:
        vista = "Dirección General"
    vista = str(vista).strip()

    # ---------------------------
    # Modalidad
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

    # fecha
    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Preparar mapa (NUEVO)
    # ---------------------------
    try:
        mapa2 = _prepare_mapa_nuevo(mapa, df)
    except Exception as e:
        st.error(str(e))
        st.stop()

    mapa_ok = mapa2[mapa2["exists"]].copy()
    if mapa_ok.empty:
        st.warning("Tu Mapa_Preguntas no logró mapear columnas reales del PROCESADO. Revisa header_id vs nombres de columnas.")
        st.dataframe(mapa2.head(50), use_container_width=True)
        return

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
                    st.info("No encontré columna válida para filtrar por Carrera/Servicio.")
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
    # DG/DC (PROCESADO)
    # =========================================================
    if vista != "Dirección Finanzas":
        # num cols reales del mapa
        num_cols = [c for c in mapa_ok["_col"].tolist() if c and c in f.columns and str(c).endswith("_num")]
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
                cols = [c for c in g["_col"].tolist() if c in f.columns and str(c).endswith("_num")]
                if not cols:
                    continue
                sec_likert, _sec_yesno = _auto_classify_numcols(f, cols)
                if not sec_likert:
                    continue
                val = pd.to_numeric(f[sec_likert].stack(), errors="coerce").mean()
                if pd.isna(val):
                    continue
                rows.append({"Sección": sec_name, "Promedio": float(val), "Preguntas": len(sec_likert)})

            if not rows:
                st.info("No hay datos suficientes para calcular promedios por sección (Likert) con los filtros actuales.")
            else:
                sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=True)
                st.dataframe(sec_df, use_container_width=True)

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

        # ---------------------------
        # Por sección (TARJETAS + comentarios por sección)
        # ---------------------------
        with tab2:
            carrera_col3 = _best_carrera_col(f)
            _render_por_seccion_con_tarjetas(
                f=f,
                mapa_ok=mapa_ok,
                fecha_col=fecha_col,
                carrera_col=carrera_col3,
            )

        # ---------------------------
        # Comparativo entre carreras (solo DG)
        # ---------------------------
        if tab4 is not None:
            with tab4:
                st.markdown("### Comparativo entre carreras por sección (Likert)")
                carrera_col2 = _best_carrera_col(f)
                if not carrera_col2:
                    st.warning("No se encontró una columna válida para Carrera/Servicio en PROCESADO.")
                else:
                    if carrera_param_fija:
                        st.info("Para comparar entre carreras, selecciona '(Todas)' en Carrera/Servicio.")
                    else:
                        for (sec_code, sec_name), g in mapa_ok.groupby(["section_code", "section_name"]):
                            cols = [c for c in g["_col"].tolist() if c in f.columns and str(c).endswith("_num")]
                            if not cols:
                                continue
                            sec_likert, _sec_yesno = _auto_classify_numcols(f, cols)
                            if not sec_likert:
                                continue

                            rows = []
                            for carrera_val, df_c in f.groupby(carrera_col2):
                                vals = pd.to_numeric(df_c[sec_likert].stack(), errors="coerce")
                                mean_val = vals.mean()
                                if pd.isna(mean_val):
                                    continue
                                rows.append({
                                    "Carrera/Servicio": str(carrera_val).strip(),
                                    "Promedio": round(float(mean_val), 2),
                                    "Respuestas": int(len(df_c)),
                                })

                            if not rows:
                                continue

                            sec_comp = pd.DataFrame(rows).sort_values("Promedio", ascending=True).reset_index(drop=True)

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
        # Comentarios (global, BONITO)
        # ---------------------------
        with tab3:
            carrera_col3 = _best_carrera_col(f)
            _render_comentarios_bonitos(
                f=f,
                mapa_ok=mapa_ok,
                fecha_col=fecha_col,
                carrera_col=carrera_col3,
                title="Comentarios (por pregunta abierta, con texto humano)",
            )

        return

    # =========================================================
    # DF (VISTA_FINANZAS_NUM)
    # =========================================================
    open_cols_df = []
    # si DF no trae mapa con header_id útil, igual intentamos extraer abiertos por heurística
    for c in f.columns:
        if any(k in str(c).lower() for k in ["¿por qué", "por qué", "comentario", "sugerencia", "escríbelo", "escribelo", "descr"]):
            open_cols_df.append(c)

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
        st.warning("No encontré columnas numéricas en VISTA_FINANZAS_NUM.")
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
            rows = []
            for col in likert_cols:
                mean_val = _mean_numeric(f[col])
                if pd.isna(mean_val):
                    continue
                rows.append({"Pregunta": col, "Promedio": float(mean_val)})

            d = pd.DataFrame(rows).sort_values("Promedio", ascending=True) if rows else pd.DataFrame()
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

        if yesno_cols:
            st.divider()
            rows = []
            for col in yesno_cols:
                mean_val = _mean_numeric(f[col])
                if pd.isna(mean_val):
                    continue
                rows.append({"Pregunta": col, "% Sí": float(mean_val) * 100})

            d = pd.DataFrame(rows).sort_values("% Sí", ascending=True) if rows else pd.DataFrame()
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
        # DF sin mapa bonito, dejamos simple (o lo ajustamos luego)
        if not open_cols_df:
            st.info("No se detectaron columnas de comentarios con la heurística actual.")
            return
        col_sel = st.selectbox("Campo abierto", open_cols_df)
        s = f[col_sel].dropna().astype(str)
        s = s[s.str.strip() != ""]
        base = f.loc[s.index].copy()
        base["_texto"] = s
        st.caption(f"Entradas con texto: **{len(base)}**")
        st.dataframe(base[["_texto"]].rename(columns={"_texto": "Comentario"}).head(300), use_container_width=True, height=520)
