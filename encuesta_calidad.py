# encuesta_calidad.py
import pandas as pd
import streamlit as st
import altair as alt  # (se queda importado por si lo usas en otros tabs)
import gspread
import re

# ============================================================
# Etiquetas de secciones (fallback)
# ============================================================
SECTION_LABELS = {
    "DIR": "Director/Coordinación",
    "SER": "Servicios (Administrativos/Generales)",
    "ADM": "Acceso a soporte administrativo",
    "ACD": "Servicios académicos",
    "APR": "Aprendizaje",
    "EVA": "Evaluación del conocimiento",
    "SEAC": "Plataforma SEAC",
    "PLAT": "Plataforma SEAC",
    "SAT": "Plataforma SEAC",  # PREPA: SAT -> SEAC
    "MAT": "Materiales en la plataforma",
    "UDL": "Comunicación con la Universidad",
    "COM": "Comunicación con compañeros",
    "INS": "Instalaciones y equipo tecnológico",
    "AMB": "Ambiente escolar",
    "REC": "Recomendación / Satisfacción",
    "OTR": "Otros",
}

# ============================================================
# Nombres de pestañas por rol
# ============================================================
SHEET_PROCESADO_DEFAULT = "PROCESADO"        # DG / DC
SHEET_PROCESADO_DF = "VISTA_FINANZAS_NUM"    # DF
SHEET_MAPA = "Mapa_Preguntas"
SHEET_CATALOGO = "Catalogo_Servicio"  # opcional


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
        "Selecciona el programa académico que estudias",  # Virtual
        "Servicio de procedencia",                        # Escolar
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


def _id_swap_variant(header_id: str) -> str | None:
    parts = str(header_id).strip().split("_")
    if len(parts) >= 3:
        parts2 = parts[:]
        parts2[0], parts2[1] = parts2[1], parts2[0]
        return "_".join(parts2)
    return None


def _resolve_numeric_col(df: pd.DataFrame, row: pd.Series) -> str | None:
    hid = str(row.get("header_id", "") or "").strip()
    hraw = str(row.get("header_raw", "") or "").strip()

    candidates = []
    if hid:
        candidates.append(f"{hid}_num")
        sv = _id_swap_variant(hid)
        if sv:
            candidates.append(f"{sv}_num")
        candidates.append(hid)

    for c in candidates:
        if c in df.columns:
            return c

    if hraw and hraw in df.columns:
        s = pd.to_numeric(df[hraw], errors="coerce")
        if s.notna().any():
            return hraw

    return None


def _resolve_text_col(df: pd.DataFrame, row: pd.Series) -> str | None:
    hid = str(row.get("header_id", "") or "").strip()
    hraw = str(row.get("header_raw", "") or "").strip()

    candidates = []
    if hid:
        candidates.append(f"{hid}_txt")
        sv = _id_swap_variant(hid)
        if sv:
            candidates.append(f"{sv}_txt")
        candidates.append(hid)

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


def _mean_numeric(series: pd.Series):
    return pd.to_numeric(series, errors="coerce").mean()


def _section_avg_likert(f: pd.DataFrame, m_sec: pd.DataFrame) -> float | None:
    """
    Promedio de sección usando SOLO preguntas no ABIERTA
    y SOLO columnas tipo Likert (detectadas por max>1).
    """
    m2 = m_sec.copy()
    m2["tipo"] = m2.get("tipo", "").astype(str).str.upper()
    m2 = m2[m2["tipo"] != "ABIERTA"].copy()
    cols = []
    for _, rr in m2.iterrows():
        cc = _resolve_numeric_col(f, rr)
        if cc and cc in f.columns:
            cols.append(cc)
    cols = list(dict.fromkeys(cols))
    if not cols:
        return None

    vals = pd.to_numeric(f[cols].stack(), errors="coerce")
    vals = vals[vals.notna()]
    if vals.empty:
        return None

    # Likert: max>1 (para no mezclar 0/1)
    if float(vals.max()) <= 1.0:
        return None

    return float(vals.mean())


def _render_section_questions_table(f: pd.DataFrame, m_sec: pd.DataFrame):
    """
    Tabla compacta de preguntas (sin gráficas), ordenada peor -> mejor.
    """
    rows = []
    for _, r in m_sec.iterrows():
        tipo = str(r.get("tipo", "") or "").strip().upper()
        if tipo == "ABIERTA":
            continue

        col = _resolve_numeric_col(f, r)
        if not col or col not in f.columns:
            continue

        s = pd.to_numeric(f[col], errors="coerce")
        if not s.notna().any():
            continue

        label = str(r.get("driver_name", "") or "").strip()
        if not label:
            label = str(r.get("header_raw", "") or "").strip()
        if not label:
            label = str(r.get("header_id", "") or "").strip()

        # Preferencia: si escala_max <=1 => Sí/No (%Sí), si no => Promedio
        escala_max = r.get("escala_max", None)
        try:
            escala_max = float(escala_max) if escala_max not in (None, "", pd.NA) else None
        except Exception:
            escala_max = None

        mean_val = float(s.mean())
        if escala_max is not None and escala_max <= 1.0:
            rows.append({"Pregunta": label, "% Sí": round(mean_val * 100.0, 1), "_sort": mean_val})
        else:
            rows.append({"Pregunta": label, "Promedio": round(mean_val, 2), "_sort": mean_val})

    if not rows:
        st.info("No hay preguntas numéricas detectables en esta sección con los filtros actuales.")
        return

    out = pd.DataFrame(rows)
    metric_col = "% Sí" if "% Sí" in out.columns else "Promedio"
    out = out.sort_values(metric_col, ascending=True).reset_index(drop=True)

    height = min(520, 56 + 28 * min(len(out), 14))
    st.dataframe(out.drop(columns=["_sort"], errors="ignore"), use_container_width=True, height=height)


def _render_section_comments_simple(
    f: pd.DataFrame,
    m_sec: pd.DataFrame,
    fecha_col: str | None,
    carrera_col: str | None,
    sec_key: str,
):
    """
    Comentarios por sección:
      - Un buscador único (contiene)
      - Un desplegable: Ver "Filtrados" o "Todos"
      - Tabla SOLO con resultados (sin filas en blanco)
    """
    m_open = m_sec.copy()
    m_open["tipo"] = m_open.get("tipo", "").astype(str).str.upper()
    m_open = m_open[m_open["tipo"] == "ABIERTA"].copy()
    if m_open.empty:
        st.caption("Sin preguntas abiertas registradas en esta sección.")
        return

    open_cols = []
    for _, r in m_open.iterrows():
        c = _resolve_text_col(f, r)
        if c and c in f.columns:
            open_cols.append(c)

    open_cols = list(dict.fromkeys(open_cols))
    if not open_cols:
        st.caption("No se detectaron columnas de comentarios para esta sección.")
        return

    # Desplegable para ver todos / filtrados
    modo = st.selectbox(
        "Vista",
        ["Filtrar (buscar palabra/frase)", "Ver todos"],
        index=0,
        key=f"modo_{sec_key}",
    )

    q = ""
    if modo.startswith("Filtrar"):
        q = st.text_input(
            "Buscar",
            value=st.session_state.get(f"q_{sec_key}", ""),
            key=f"q_{sec_key}",
            placeholder="Ej. SEAC, baños, cobranzas, profesor…",
        ).strip()
    else:
        # Si están en "Ver todos", limpiamos el query visualmente
        st.session_state[f"q_{sec_key}"] = ""

    # Long de comentarios
    pieces = []
    for c in open_cols:
        s = f[c].dropna().astype(str)
        s = s[s.str.strip() != ""]
        if s.empty:
            continue
        base = f.loc[s.index].copy()
        base["_comentario"] = s
        pieces.append(base)

    if not pieces:
        st.caption("No hay comentarios en esta sección con los filtros actuales.")
        return

    long = pd.concat(pieces, axis=0, ignore_index=False)

    if fecha_col and fecha_col in long.columns and pd.api.types.is_datetime64_any_dtype(long[fecha_col]):
        long = long.sort_values(fecha_col, ascending=False)

    if modo.startswith("Filtrar") and q:
        long = long[long["_comentario"].astype(str).str.contains(q, case=False, na=False)]
        st.caption(f"Comentarios filtrados: **{len(long)}**")
    elif modo.startswith("Filtrar") and not q:
        # Si no buscan nada, mostramos vacío (para que sea claro)
        st.caption("Comentarios filtrados: **0**")
        st.info("Escribe una palabra o frase para filtrar comentarios.")
        return
    else:
        # Ver todos
        st.caption(f"Comentarios totales: **{len(long)}**")

    if long.empty:
        st.info("Sin resultados.")
        return

    # Construir tabla final
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

    if "Carrera/Servicio" not in show.columns:
        show.insert(1, "Carrera/Servicio", "—")
    if "Marca temporal" not in show.columns:
        show.insert(0, "Marca temporal", pd.NaT)

    n = len(show)
    height = min(520, 56 + 28 * min(n, 14))
    st.dataframe(show, use_container_width=True, height=height)


# ============================================================
# Carga desde Google Sheets
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
    # SIDEBAR: filtros principales
    # ---------------------------
    with st.sidebar:
        st.markdown("### Filtros")

        if vista in ["Dirección General", "Dirección Finanzas"]:
            modalidad = st.selectbox(
                "Modalidad",
                ["Virtual / Mixto", "Escolarizado / Ejecutivas", "Preparatoria"],
                index=0,
                key="sb_modalidad",
            )
        else:
            modalidad = _resolver_modalidad_auto(vista, carrera)
            st.write(f"**Modalidad:** {modalidad}")

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

    fecha_col = _pick_fecha_col(df)
    if fecha_col:
        df[fecha_col] = _to_datetime_safe(df[fecha_col])

    # ---------------------------
    # Mapa NUEVO (tu encabezado)
    # ---------------------------
    mapa = mapa.copy()
    if "header_raw" not in mapa.columns and "header_exacto" in mapa.columns:
        mapa["header_raw"] = mapa["header_exacto"]

    required_cols = {"header_raw", "header_id", "section_code", "tipo"}
    if not required_cols.issubset(set(mapa.columns)):
        st.error("La hoja 'Mapa_Preguntas' debe traer al menos: header_raw, header_id, section_code, tipo.")
        return

    for c in ["modalidad", "header_raw", "header_id", "section_code", "section_name", "tipo", "driver_name", "keywords", "escala_max"]:
        if c in mapa.columns:
            mapa[c] = mapa[c].fillna("").astype(str).str.strip()

    if "section_name" not in mapa.columns:
        mapa["section_name"] = mapa["section_code"]
    mapa["section_name"] = mapa.apply(lambda r: _safe_section_name(r["section_code"], r.get("section_name", "")), axis=1)

    # ---------------------------
    # Filtrar mapa por modalidad (si aplica)
    # ---------------------------
    mapa_use = mapa.copy()
    if "modalidad" in mapa_use.columns and mapa_use["modalidad"].astype(str).str.strip().ne("").any():
        mod_map = {
            "Escolarizado / Ejecutivas": "ESCOLARIZADOS",
            "Preparatoria": "PREPA",
            "Virtual / Mixto": "VIRTUAL",
        }
        tag = mod_map.get(modalidad, "")
        if tag:
            mapa_use = mapa_use[mapa_use["modalidad"].astype(str).str.upper().str.strip() == tag].copy()

    # ---------------------------
    # Sidebar: Año + Carrera/Servicio
    # ---------------------------
    years = ["(Todos)"]
    if fecha_col and df[fecha_col].notna().any():
        years += sorted(df[fecha_col].dt.year.dropna().unique().astype(int).tolist(), reverse=True)

    carrera_param_fija = (carrera is not None) and str(carrera).strip() != ""

    carrera_col = _best_carrera_col(df)
    carrera_sel = "(Todas)"

    with st.sidebar:
        year_sel = st.selectbox("Año", years, index=0, key="sb_year")

        if vista in ["Dirección General", "Dirección Finanzas"]:
            if carrera_param_fija:
                carrera_sel = str(carrera).strip()
                st.text_input("Carrera/Servicio", value=carrera_sel, disabled=True, key="sb_carrera_fija")
            else:
                if carrera_col:
                    opts = ["(Todas)"] + sorted(df[carrera_col].dropna().astype(str).str.strip().unique().tolist())
                    carrera_sel = st.selectbox("Carrera/Servicio", opts, index=0, key="sb_carrera")
                else:
                    st.caption("Sin columna válida de Carrera/Servicio.")
                    carrera_sel = "(Todas)"
        else:
            # DC
            carrera_sel = (carrera or "").strip()
            st.text_input("Carrera/Servicio", value=carrera_sel, disabled=True, key="sb_carrera_dc")

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
    # Tabs
    # ---------------------------
    if vista == "Dirección General":
        tab1, tab2, tab4 = st.tabs(["Resumen", "Por sección", "Comparativo entre carreras"])
    else:
        tab1, tab2 = st.tabs(["Resumen", "Por sección"])
        tab4 = None

    # =========================================================
    # Resumen
    # =========================================================
    with tab1:
        c1, c2, c3 = st.columns(3)
        c1.metric("Respuestas", f"{len(f)}")

        # Detectar columnas numéricas disponibles
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

        # Likert global (solo cols con max>1)
        if num_cols:
            dnum = f[num_cols].apply(pd.to_numeric, errors="coerce")
            maxs = dnum.max(axis=0, skipna=True)
            likert_cols = [c for c in num_cols if pd.notna(maxs.get(c)) and float(maxs.get(c)) > 1.0]
            yesno_cols = [c for c in num_cols if c not in likert_cols]
        else:
            likert_cols, yesno_cols = [], []

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
        for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
            m_sec = g.copy()
            avg = _section_avg_likert(f, m_sec)
            if avg is None:
                continue
            rows.append({"Sección": sec_name, "Promedio": round(avg, 2)})

        if not rows:
            st.info("No hay datos suficientes para calcular promedios por sección (Likert) con los filtros actuales.")
        else:
            sec_df = pd.DataFrame(rows).sort_values("Promedio", ascending=True).reset_index(drop=True)
            height = min(520, 56 + 28 * min(len(sec_df), 14))
            st.dataframe(sec_df, use_container_width=True, height=height)

    # =========================================================
    # Por sección (nombre + promedio en el encabezado, tablas, buscador, ver todos)
    # =========================================================
    with tab2:
        # Lista de secciones
        sec_list = []
        for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
            sec_list.append((sec_name, sec_code))
        if not sec_list:
            st.warning("No hay secciones en Mapa_Preguntas para esta modalidad.")
            return

        sec_list = sorted(sec_list, key=lambda x: x[0].lower())

        carrera_col_here = _best_carrera_col(f)

        for sec_name, sec_code in sec_list:
            m_sec = mapa_use[mapa_use["section_code"] == sec_code].copy()
            if m_sec.empty:
                continue

            avg = _section_avg_likert(f, m_sec)
            avg_txt = f"{avg:.2f}" if isinstance(avg, float) else "—"

            with st.expander(f"{sec_name} — Promedio: {avg_txt}", expanded=False):
                # Preguntas (tabla)
                _render_section_questions_table(f=f, m_sec=m_sec)

                st.divider()

                # Comentarios (buscador + ver todos)
                _render_section_comments_simple(
                    f=f,
                    m_sec=m_sec,
                    fecha_col=fecha_col,
                    carrera_col=carrera_col_here,
                    sec_key=f"{modalidad}_{vista}_{sec_code}",
                )

    # =========================================================
    # Comparativo entre carreras (solo DG)
    # =========================================================
    if tab4 is not None:
        with tab4:
            st.markdown("### Comparativo entre carreras por sección")

            carrera_col2 = _best_carrera_col(f)
            if not carrera_col2:
                st.warning("No se encontró una columna válida para identificar Carrera/Servicio.")
                return

            if carrera_param_fija or carrera_sel != "(Todas)":
                st.info("Para ver el comparativo entre carreras, selecciona **(Todas)** en Carrera/Servicio.")
                return

            for (sec_code, sec_name), g in mapa_use.groupby(["section_code", "section_name"]):
                m_sec = g.copy()
                m_sec["tipo"] = m_sec.get("tipo", "").astype(str).str.upper()
                m_sec = m_sec[m_sec["tipo"] != "ABIERTA"].copy()

                cols = []
                for _, rr in m_sec.iterrows():
                    cc = _resolve_numeric_col(f, rr)
                    if cc and cc in f.columns:
                        cols.append(cc)
                cols = list(dict.fromkeys(cols))
                if not cols:
                    continue

                rows = []
                for carrera_val, df_c in f.groupby(carrera_col2):
                    vals = pd.to_numeric(df_c[cols].stack(), errors="coerce")
                    vals = vals[vals.notna()]
                    if vals.empty:
                        continue
                    if float(vals.max()) <= 1.0:
                        continue
                    rows.append({
                        "Carrera/Servicio": str(carrera_val).strip(),
                        "Promedio": round(float(vals.mean()), 2),
                        "Respuestas": int(len(df_c)),
                    })

                if not rows:
                    continue

                sec_comp = pd.DataFrame(rows).sort_values("Promedio", ascending=True).reset_index(drop=True)

                with st.expander(sec_name, expanded=False):
                    height = min(520, 56 + 28 * min(len(sec_comp), 14))
                    st.dataframe(sec_comp, use_container_width=True, height=height)
