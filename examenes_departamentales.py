# examenes_departamentales.py
import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd
import streamlit as st
import altair as alt
import gspread

# ✅ Catálogo maestro (CAT_CARRERAS) para variantes
from catalogos import resolver_carrera

SHEET_BASE = "BASE_CONSOLIDADA"
SHEET_RESP = "RESPUESTAS_LARGAS"
SHEET_CATALOGO = "CATALOGO_EXAMENES"  # opcional (mapea display<->canon por versión)


# ============================================================
# Helpers de carga
# ============================================================
def _dedupe_headers(headers):
    seen = {}
    out = []
    for h in headers:
        h = str(h).strip()
        if h in seen:
            seen[h] += 1
            out.append(f"{h}__{seen[h]}")
        else:
            seen[h] = 1
            out.append(h)
    return out


def _ws_to_df(sh, ws_title):
    ws = sh.worksheet(ws_title)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _dedupe_headers([h.strip() for h in values[0]])
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url):
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    titles = [ws.title for ws in sh.worksheets()]
    missing = [s for s in [SHEET_BASE, SHEET_RESP] if s not in titles]
    if missing:
        raise ValueError(
            f"No encontré pestañas requeridas: {', '.join(missing)}. "
            f"Pestañas disponibles: {', '.join(titles)}"
        )

    base = _ws_to_df(sh, SHEET_BASE)
    resp = _ws_to_df(sh, SHEET_RESP)

    catalogo = pd.DataFrame()
    if SHEET_CATALOGO in titles:
        catalogo = _ws_to_df(sh, SHEET_CATALOGO)

    return base, resp, catalogo


def _as_str(df, col):
    return df[col].astype(str).str.strip()


def _pick_date_col(df):
    for c in [
        "Fecha",
        "fecha",
        "Marca temporal",
        "Marca Temporal",
        "Timestamp",
        "timestamp",
        "Aplicación",
        "Aplicacion",
    ]:
        if c in df.columns:
            return c
    return None


def _infer_year_from_version(version_value):
    if not version_value:
        return None
    m = re.search(r"(20\d{2})", str(version_value))
    return int(m.group(1)) if m else None


# ============================================================
# Normalización de texto / opción (para comparar respuestas texto vs A/B/C/D)
# ============================================================
def _norm_text(x):
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass

    s = str(x).strip().lower()
    if not s or s == "nan":
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[“”\"'’`]", "", s)
    s = re.sub(r"[^\w\s\.\,\-\(\)\:\;\/]", "", s)
    return s.strip()


def _normalize_letter(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass

    s = str(x).strip().upper()
    if not s or s == "NAN":
        return None

    if s in {"1", "2", "3", "4"}:
        return {"1": "A", "2": "B", "3": "C", "4": "D"}[s]

    m = re.search(r"\b([ABCD])\b", s)
    if m:
        return m.group(1)

    m2 = re.match(r"^([ABCD])[\)\.\:\-\s]", s)
    if m2:
        return m2.group(1)

    return None


def _best_match_letter(resp_text, optA, optB, optC, optD, threshold=0.86):
    r = _norm_text(resp_text)
    if not r:
        return None

    opts = {
        "A": _norm_text(optA),
        "B": _norm_text(optB),
        "C": _norm_text(optC),
        "D": _norm_text(optD),
    }

    for k, v in opts.items():
        if v and r == v:
            return k

    best_k = None
    best_score = 0.0
    for k, v in opts.items():
        if not v:
            continue
        score = SequenceMatcher(None, r, v).ratio()
        if score > best_score:
            best_score = score
            best_k = k

    return best_k if best_score >= threshold else None


# ============================================================
# Catálogo interno del módulo (opcional): resolver display -> canon por versión
# ============================================================
def _clean_key(s):
    """Clave comparable (sin acentos, minúsculas, espacios normalizados)."""
    if s is None:
        return ""
    try:
        if pd.isna(s):
            return ""
    except Exception:
        pass
    s = str(s).strip().lower()
    if not s or s == "nan":
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _catalogo_build_maps(catalogo: pd.DataFrame):
    """
    Regresa:
      - map_display_to_canon[(display_key, version_key)] = canon_value
      - map_canon_to_display[(canon_key, version_key)] = display_value
    """
    if catalogo is None or catalogo.empty:
        return {}, {}

    needed = {"Carrera", "Version"}
    if not needed.issubset(set(catalogo.columns)):
        return {}, {}

    cat = catalogo.copy()
    for c in ["Carrera", "Version"]:
        cat[c] = cat[c].astype(str).str.strip()

    display_col = "Notas (opcional)" if "Notas (opcional)" in cat.columns else None
    if display_col:
        cat[display_col] = cat[display_col].astype(str).str.strip()
        cat.loc[cat[display_col].isin(["", "nan", "NaN"]), display_col] = pd.NA

    map_display_to_canon = {}
    map_canon_to_display = {}

    for _, r in cat.iterrows():
        canon = str(r.get("Carrera", "")).strip()
        ver = str(r.get("Version", "")).strip()

        if not canon or canon.lower() == "nan" or not ver or ver.lower() == "nan":
            continue

        display = canon
        if display_col and pd.notna(r.get(display_col)):
            display = str(r.get(display_col)).strip()

        dk = _clean_key(display)
        ck = _clean_key(canon)
        vk = _clean_key(ver)

        if dk and vk:
            map_display_to_canon[(dk, vk)] = canon

        if ck and vk:
            map_canon_to_display[(ck, vk)] = display

    return map_display_to_canon, map_canon_to_display


def _resolve_canon_from_display(display_value, version_value, map_display_to_canon):
    if not display_value or not version_value:
        return None
    dk = _clean_key(display_value)
    vk = _clean_key(version_value)
    return map_display_to_canon.get((dk, vk))


def _display_from_canon(canon_value, version_value, map_canon_to_display):
    if not canon_value or not version_value:
        return canon_value
    ck = _clean_key(canon_value)
    vk = _clean_key(version_value)
    return map_canon_to_display.get((ck, vk), canon_value)


# ============================================================
# Catálogo maestro (CAT_CARRERAS): variantes
# ============================================================
def _resolver_variantes_desde_catalogo_maestro(display_input: str):
    """
    Usa catalogos.resolver_carrera() para obtener variantes del CAT_CARRERAS.
    Regresa: (canon_display, variantes_list) o (None, None) si no resuelve.
    """
    try:
        info = resolver_carrera(display_input)
    except Exception:
        info = None

    if not info:
        return None, None

    variantes = info.get("variantes") or []
    variantes = [str(x).strip() for x in variantes if str(x).strip()]
    if not variantes:
        return None, None

    canon_display = info.get("nombre_oficial") or display_input
    canon_display = str(canon_display).strip() if canon_display else display_input
    return canon_display, variantes


# ============================================================
# Gráfica
# ============================================================
def _bar_h(df, cat, val, title):
    if df is None or df.empty:
        return None
    height = min(900, max(280, len(df) * 26))
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(f"{cat}:N", sort="-x", title=None),
            x=alt.X(f"{val}:Q", title=title),
            tooltip=[
                alt.Tooltip(cat, title=cat),
                alt.Tooltip(val, title=title, format=".2f"),
            ],
        )
        .properties(height=height)
    )


# ============================================================
# Examen (listados) — sin mencionar "sin respuestas" en la UI
# ============================================================
def _pick_question_col(base: pd.DataFrame):
    candidates = [
        "Pregunta",
        "Enunciado",
        "Reactivo",
        "Ítem",
        "Item",
        "Texto",
        "Planteamiento",
    ]
    for c in candidates:
        if c in base.columns:
            return c
    for c in base.columns:
        ck = _clean_key(c)
        if "pregunt" in ck or "enunci" in ck or "reactiv" in ck:
            return c
    return None


def _build_public_exam_df(base_f: pd.DataFrame):
    if base_f is None or base_f.empty:
        return pd.DataFrame()

    qcol = _pick_question_col(base_f)

    cols = ["Area", "Materia", "ID_reactivo"]
    if qcol:
        cols.append(qcol)
    for c in ["A", "B", "C", "D"]:
        if c in base_f.columns:
            cols.append(c)

    out = base_f.copy()

    for sensitive in ["Clave", "Clave_letter"]:
        if sensitive in out.columns:
            out = out.drop(columns=[sensitive], errors="ignore")

    # si viene mezclado por variantes, igual dedup por Version+ID
    dedupe_subset = [c for c in ["Version", "ID_reactivo"] if c in out.columns]
    if dedupe_subset:
        out = out.drop_duplicates(subset=dedupe_subset, keep="first")
    else:
        out = out.drop_duplicates(keep="first")

    keep = [c for c in cols if c in out.columns]
    out = out[keep].copy()

    if qcol and qcol in out.columns:
        out = out.rename(columns={qcol: "Pregunta"})

    return out


def _download_df_buttons(df, filename_prefix: str):
    if df is None or getattr(df, "empty", True):
        return
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="Descargar CSV",
        data=csv,
        file_name=f"{filename_prefix}.csv",
        mime="text/csv",
    )


def _render_tab_examen_por_area(exam_pub, filename_prefix: str):
    if exam_pub is None or getattr(exam_pub, "empty", True):
        st.warning("No hay reactivos para mostrar en esta versión/carrera.")
        return

    st.markdown("#### Conteo de reactivos por área")
    resumen = (
        exam_pub.groupby("Area", as_index=False)["ID_reactivo"]
        .nunique()
        .rename(columns={"ID_reactivo": "Reactivos"})
        .sort_values("Reactivos", ascending=False)
    )
    st.dataframe(resumen, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Reactivos incluidos")
    detalle_cols = [
        c
        for c in ["Area", "Materia", "ID_reactivo", "Pregunta", "A", "B", "C", "D"]
        if c in exam_pub.columns
    ]
    detalle = exam_pub[detalle_cols].sort_values(["Area", "Materia", "ID_reactivo"])
    st.dataframe(detalle, use_container_width=True, hide_index=True)

    st.divider()
    _download_df_buttons(detalle, filename_prefix=filename_prefix + "_examen_por_area")


def _render_tab_examen_por_materia(exam_pub, filename_prefix: str):
    if exam_pub is None or getattr(exam_pub, "empty", True):
        st.warning("No hay reactivos para mostrar en esta versión/carrera.")
        return

    materias = sorted(
        [
            m
            for m in exam_pub["Materia"].dropna().unique().tolist()
            if str(m).strip() and str(m).lower() != "nan"
        ]
    )
    if not materias:
        st.warning("No encontré materias para listar.")
        return

    sel_m = st.selectbox("Materia", materias, index=0)
    df_m = exam_pub[exam_pub["Materia"] == sel_m].copy()

    st.caption(f"Reactivos incluidos en **{sel_m}**: **{df_m['ID_reactivo'].nunique():,}**")

    cols = [c for c in ["Materia", "Area", "ID_reactivo", "Pregunta", "A", "B", "C", "D"] if c in df_m.columns]
    df_m = df_m[cols].sort_values(["Area", "ID_reactivo"])
    st.dataframe(df_m, use_container_width=True, hide_index=True)

    st.divider()
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(sel_m))[:80]
    _download_df_buttons(df_m, filename_prefix=filename_prefix + f"_materia_{safe_name}")


# ============================================================
# Core
# ============================================================
def _prepare(base, resp):
    required_base = {"Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"}
    required_resp = {"Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo", "Respuesta_alumno"}

    if not required_base.issubset(set(base.columns)):
        raise ValueError(f"BASE_CONSOLIDADA debe contener: {sorted(required_base)}")
    if not required_resp.issubset(set(resp.columns)):
        raise ValueError(f"RESPUESTAS_LARGAS debe contener: {sorted(required_resp)}")

    option_cols = [c for c in ["A", "B", "C", "D"] if c in base.columns]
    if len(option_cols) < 4:
        raise ValueError(
            "BASE_CONSOLIDADA debe incluir las columnas de opciones A, B, C y D "
            "para poder mapear respuestas en texto a una letra."
        )

    base = base.copy()
    resp = resp.copy()

    for c in ["Carrera", "Version", "ID_reactivo", "Area", "Materia"]:
        base[c] = _as_str(base, c)
    for c in ["Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo"]:
        resp[c] = _as_str(resp, c)

    base["Puntos"] = pd.to_numeric(base["Puntos"], errors="coerce").fillna(1.0)
    base = base.drop_duplicates(subset=["Carrera", "Version", "ID_reactivo"], keep="first")
    base["Clave_letter"] = base["Clave"].apply(_normalize_letter)

    resp["AlumnoID"] = resp["Matricula"].where(
        resp["Matricula"].notna()
        & (resp["Matricula"] != "")
        & (resp["Matricula"].str.lower() != "nan"),
        resp["Correo"],
    ).astype(str).str.strip()

    keep_cols = [
        "Carrera",
        "Version",
        "ID_reactivo",
        "Area",
        "Materia",
        "Clave_letter",
        "Puntos",
        "A",
        "B",
        "C",
        "D",
    ]
    df = resp.merge(base[keep_cols], on=["Carrera", "Version", "ID_reactivo"], how="left")

    df["Match_base"] = df["Clave_letter"].notna()
    df["Resp_letter_direct"] = df["Respuesta_alumno"].apply(_normalize_letter)
    df["Resp_letter_text"] = df.apply(
        lambda r: _best_match_letter(
            r.get("Respuesta_alumno"), r.get("A"), r.get("B"), r.get("C"), r.get("D")
        ),
        axis=1,
    )
    df["Resp_letter"] = df["Resp_letter_direct"].fillna(df["Resp_letter_text"])
    df["Respondida_valida"] = df["Resp_letter"].notna().astype(int)

    df["Acierto"] = (
        (df["Match_base"])
        & (df["Resp_letter"].notna())
        & (df["Clave_letter"].notna())
        & (df["Resp_letter"] == df["Clave_letter"])
    ).astype(int)

    df["Puntos_obtenidos"] = df["Acierto"].astype(float) * df["Puntos"].fillna(0).astype(float)

    puntos_posibles_cv = (
        base.groupby(["Carrera", "Version"], as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_examen"})
    )
    df = df.merge(puntos_posibles_cv, on=["Carrera", "Version"], how="left")

    n_reactivos_cv = (
        base.groupby(["Carrera", "Version"], as_index=False)["ID_reactivo"]
        .nunique()
        .rename(columns={"ID_reactivo": "Reactivos_examen"})
    )
    df = df.merge(n_reactivos_cv, on=["Carrera", "Version"], how="left")

    by_alumno = (
        df.groupby(["AlumnoID", "Carrera", "Version"], as_index=False)
        .agg(
            Puntos_obtenidos=("Puntos_obtenidos", "sum"),
            Puntos_posibles_examen=("Puntos_posibles_examen", "first"),
            Reactivos_examen=("Reactivos_examen", "first"),
            Respondidas_validas=("Respondida_valida", "sum"),
        )
    )

    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"])
        if pd.notna(r["Puntos_posibles_examen"]) and float(r["Puntos_posibles_examen"]) > 0
        else None,
        axis=1,
    )

    by_alumno["Promedio_0_10"] = pd.to_numeric(by_alumno["Score"], errors="coerce") * 10.0
    by_alumno["Porcentaje_0_100"] = pd.to_numeric(by_alumno["Score"], errors="coerce") * 100.0
    by_alumno["Cobertura"] = (
        by_alumno.apply(
            lambda r: (r["Respondidas_validas"] / r["Reactivos_examen"])
            if pd.notna(r["Reactivos_examen"]) and float(r["Reactivos_examen"]) > 0
            else None,
            axis=1,
        )
        * 100.0
    )

    return base, resp, df, by_alumno


def _agg_by_alumno_for_variantes(ba_f: pd.DataFrame) -> pd.DataFrame:
    """
    Si una carrera viene con variantes (múltiples nombres), agregamos por AlumnoID+Version
    para obtener métricas consistentes.
    """
    if ba_f is None or ba_f.empty:
        return ba_f

    cols_needed = {"AlumnoID", "Version", "Puntos_obtenidos", "Puntos_posibles_examen", "Reactivos_examen", "Respondidas_validas"}
    if not cols_needed.issubset(set(ba_f.columns)):
        return ba_f

    agg = (
        ba_f.groupby(["AlumnoID", "Version"], as_index=False)
        .agg(
            Puntos_obtenidos=("Puntos_obtenidos", "sum"),
            Puntos_posibles_examen=("Puntos_posibles_examen", "first"),
            Reactivos_examen=("Reactivos_examen", "first"),
            Respondidas_validas=("Respondidas_validas", "sum"),
        )
    )

    agg["Score"] = agg.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"])
        if pd.notna(r["Puntos_posibles_examen"]) and float(r["Puntos_posibles_examen"]) > 0
        else None,
        axis=1,
    )

    agg["Promedio_0_10"] = pd.to_numeric(agg["Score"], errors="coerce") * 10.0
    agg["Porcentaje_0_100"] = pd.to_numeric(agg["Score"], errors="coerce") * 100.0
    agg["Cobertura"] = (
        agg.apply(
            lambda r: (r["Respondidas_validas"] / r["Reactivos_examen"])
            if pd.notna(r["Reactivos_examen"]) and float(r["Reactivos_examen"]) > 0
            else None,
            axis=1,
        )
        * 100.0
    )
    return agg


def _detalle_carrera_variantes(df, base, by_alumno, carreras_variantes, version):
    """
    carreras_variantes: lista de nombres posibles (variantes) para la misma carrera.
    """
    carreras_variantes = [str(x).strip() for x in (carreras_variantes or []) if str(x).strip()]
    if not carreras_variantes:
        return None, None, None, 0, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    base_f = base[base["Carrera"].isin(carreras_variantes)].copy()
    df_f = df[df["Carrera"].isin(carreras_variantes)].copy()
    ba_f = by_alumno[by_alumno["Carrera"].isin(carreras_variantes)].copy()

    if version and version != "Todas":
        base_f = base_f[base_f["Version"] == version].copy()
        df_f = df_f[df_f["Version"] == version].copy()
        ba_f = ba_f[ba_f["Version"] == version].copy()

    if base_f.empty or df_f.empty or ba_f.empty:
        return None, None, None, 0, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # ✅ unifica alumnos si hay múltiples variantes
    ba_f2 = _agg_by_alumno_for_variantes(ba_f)

    prom_0_10 = float(pd.to_numeric(ba_f2["Promedio_0_10"], errors="coerce").mean())
    prom_pct = float(pd.to_numeric(ba_f2["Porcentaje_0_100"], errors="coerce").mean())
    cov_pct = float(pd.to_numeric(ba_f2["Cobertura"], errors="coerce").mean())
    n_respondieron = int(ba_f2["AlumnoID"].nunique())

    area_pos = (
        base_f.groupby("Area", as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_area"})
    )
    area_al = (
        df_f.groupby(["AlumnoID", "Area"], as_index=False)["Puntos_obtenidos"]
        .sum()
        .merge(area_pos, on="Area", how="left")
    )
    area_al["Score_area"] = area_al.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_area"])
        if pd.notna(r["Puntos_posibles_area"]) and float(r["Puntos_posibles_area"]) > 0
        else None,
        axis=1,
    )
    area_al["Promedio_area"] = pd.to_numeric(area_al["Score_area"], errors="coerce") * 10.0
    area_df = (
        area_al.groupby("Area", as_index=False)["Promedio_area"]
        .mean()
        .sort_values("Promedio_area", ascending=False)
    )

    mat_pos = (
        base_f.groupby("Materia", as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_materia"})
    )
    mat_al = (
        df_f.groupby(["AlumnoID", "Materia"], as_index=False)["Puntos_obtenidos"]
        .sum()
        .merge(mat_pos, on="Materia", how="left")
    )
    mat_al["Score_materia"] = mat_al.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_materia"])
        if pd.notna(r["Puntos_posibles_materia"]) and float(r["Puntos_posibles_materia"]) > 0
        else None,
        axis=1,
    )
    mat_al["Promedio_materia"] = pd.to_numeric(mat_al["Score_materia"], errors="coerce") * 10.0
    materia_df = (
        mat_al.groupby("Materia", as_index=False)["Promedio_materia"]
        .mean()
        .sort_values("Promedio_materia", ascending=False)
    )

    exam_pub = _build_public_exam_df(base_f)

    return prom_0_10, prom_pct, cov_pct, n_respondieron, area_df, materia_df, exam_pub


# ============================================================
# Render
# ============================================================
def render_examenes_departamentales(spreadsheet_url, vista=None, carrera=None):
    vista_norm = (vista or "").strip().lower()
    es_direccion_general = vista_norm in ["direccion general", "dirección general"]

    st.info("Examen departamental: **Piloto**. Resultados con fines de diagnóstico y mejora continua.")

    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            base, resp, catalogo = _load_from_gsheets_by_url(spreadsheet_url)
            base, resp, df, by_alumno = _prepare(base, resp)
    except Exception as e:
        st.error("No se pudieron cargar/procesar las hojas (BASE_CONSOLIDADA / RESPUESTAS_LARGAS).")
        st.exception(e)
        return

    map_display_to_canon, map_canon_to_display = _catalogo_build_maps(catalogo)

    # Aplicación (año)
    date_col = _pick_date_col(resp)
    year_aplicacion = None
    if date_col:
        dt = pd.to_datetime(resp[date_col], errors="coerce", dayfirst=True)
        if dt.notna().any():
            year_aplicacion = int(dt.dropna().dt.year.mode().iloc[0])
    if year_aplicacion is None:
        sample_version = str(base["Version"].dropna().iloc[0]) if base["Version"].notna().any() else ""
        year_aplicacion = _infer_year_from_version(sample_version)

    st.caption(f"Aplicación: **{year_aplicacion if year_aplicacion else '—'}**")

    versiones = sorted([v for v in base["Version"].dropna().unique().tolist() if v and str(v).lower() != "nan"])
    sel_version = st.selectbox("Aplicación / Versión", ["Todas"] + versiones, index=0)

    base_v = base.copy()
    df_v = df.copy()
    ba_v = by_alumno.copy()
    if sel_version != "Todas":
        base_v = base_v[base_v["Version"] == sel_version].copy()
        df_v = df_v[df_v["Version"] == sel_version].copy()
        ba_v = ba_v[ba_v["Version"] == sel_version].copy()

    # ========================================================
    # Director de carrera
    # ========================================================
    if not es_direccion_general:
        display_input = (carrera or "").strip()
        if not display_input:
            st.warning("No recibí la carrera desde app.py. En vista Director de carrera es obligatoria.")
            return

        if sel_version == "Todas":
            st.warning("En vista Director de carrera, selecciona una Aplicación / Versión específica.")
            return

        # 1) Intento A: catálogo interno del módulo (si existe)
        carrera_canon = _resolve_canon_from_display(display_input, sel_version, map_display_to_canon)

        # 2) Intento B (✅ clave): catálogo maestro CAT_CARRERAS -> variantes
        canon_maestro, variantes_maestro = _resolver_variantes_desde_catalogo_maestro(display_input)

        # Preferencia:
        # - Si el catálogo maestro resolvió variantes => usamos variantes para filtrar
        # - Si no resolvió, pero el catálogo interno sí dio canon => filtramos por canon
        # - Si ninguno resolvió => filtramos por display_input (fallback)
        if variantes_maestro:
            carrera_etiqueta = canon_maestro or display_input
            carreras_variantes = variantes_maestro
        elif carrera_canon:
            carrera_etiqueta = carrera_canon
            carreras_variantes = [carrera_canon]
        else:
            carrera_etiqueta = display_input
            carreras_variantes = [display_input]

        prom_0_10, prom_pct, cov_pct, n_al, area_df, materia_df, exam_pub = _detalle_carrera_variantes(
            df_v, base_v, ba_v, carreras_variantes, sel_version
        )

        if prom_0_10 is None:
            st.error("No encontré datos para esa carrera en la versión seleccionada.")
            st.caption(f"Carrera seleccionada (menú): **{display_input}**")
            st.caption(f"Filtro usado (variantes): **{', '.join(carreras_variantes[:8])}{'…' if len(carreras_variantes) > 8 else ''}**")
            return

        c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.0])
        c1.metric("Promedio (0–10)", f"{prom_0_10:.2f}")
        c2.metric("Porcentaje de acierto", f"{prom_pct:.1f}%")
        c3.metric("Cobertura (respondidas)", f"{cov_pct:.1f}%")
        c4.metric("Alumnos que respondieron", f"{n_al:,}")

        st.divider()
        tab1, tab2, tab3 = st.tabs(["Resultados", "Examen por área", "Examen por materia"])

        with tab1:
            st.markdown(f"### {carrera_etiqueta}")
            st.markdown("### Promedio por área (0–10)")
            st.dataframe(area_df, use_container_width=True, hide_index=True)
            ch_a = _bar_h(area_df, "Area", "Promedio_area", "Promedio (0–10)")
            if ch_a is not None:
                st.altair_chart(ch_a, use_container_width=True)

            st.divider()
            st.markdown("### Promedio por materia (0–10)")
            st.dataframe(materia_df, use_container_width=True, hide_index=True)
            ch_m = _bar_h(materia_df, "Materia", "Promedio_materia", "Promedio (0–10)")
            if ch_m is not None:
                st.altair_chart(ch_m, use_container_width=True)

        with tab2:
            _render_tab_examen_por_area(
                exam_pub=exam_pub,
                filename_prefix=f"examen_{sel_version}_{re.sub(r'[^a-zA-Z0-9_-]+','_', carrera_etiqueta)[:60]}",
            )

        with tab3:
            _render_tab_examen_por_materia(
                exam_pub=exam_pub,
                filename_prefix=f"examen_{sel_version}_{re.sub(r'[^a-zA-Z0-9_-]+','_', carrera_etiqueta)[:60]}",
            )

        return

    # ========================================================
    # Dirección General
    # ========================================================
    prom_inst_0_10 = float(pd.to_numeric(ba_v["Promedio_0_10"], errors="coerce").mean()) if not ba_v.empty else 0.0
    prom_inst_pct = float(pd.to_numeric(ba_v["Porcentaje_0_100"], errors="coerce").mean()) if not ba_v.empty else 0.0
    cov_inst_pct = float(pd.to_numeric(ba_v["Cobertura"], errors="coerce").mean()) if not ba_v.empty else 0.0
    n_resp = int(ba_v["AlumnoID"].nunique()) if not ba_v.empty else 0

    modo = st.radio("Vista", ["Institución (Resumen)", "Por carrera (Detalle)"], horizontal=True, index=0)
    st.divider()

    if modo == "Institución (Resumen)":
        c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.0])
        c1.metric("Promedio (0–10)", f"{prom_inst_0_10:.2f}")
        c2.metric("Porcentaje de acierto", f"{prom_inst_pct:.1f}%")
        c3.metric("Cobertura (respondidas)", f"{cov_inst_pct:.1f}%")
        c4.metric("Alumnos que respondieron", f"{n_resp:,}")

        st.divider()

        resumen = (
            ba_v.groupby("Carrera", as_index=False)
            .agg(
                Promedio_0_10=("Promedio_0_10", "mean"),
                Porcentaje=("Porcentaje_0_100", "mean"),
                Cobertura=("Cobertura", "mean"),
                Alumnos=("AlumnoID", "nunique"),
            )
            .sort_values("Porcentaje", ascending=False)
        )

        if sel_version != "Todas":
            resumen["Carrera (display)"] = resumen["Carrera"].apply(
                lambda c: _display_from_canon(c, sel_version, map_canon_to_display)
            )
        else:
            resumen["Carrera (display)"] = resumen["Carrera"]

        st.markdown("### Resultados por carrera")
        st.dataframe(
            resumen[["Carrera (display)", "Promedio_0_10", "Porcentaje", "Cobertura", "Alumnos"]],
            use_container_width=True,
            hide_index=True,
        )

        ch = _bar_h(
            resumen.rename(columns={"Carrera (display)": "Carrera"}),
            "Carrera",
            "Porcentaje",
            "Porcentaje de acierto (0–100)",
        )
        if ch is not None:
            st.altair_chart(ch, use_container_width=True)
        return

    if sel_version == "Todas":
        st.warning("Para ver detalle por carrera, selecciona una Aplicación / Versión específica.")
        return

    carreras_canon = sorted([c for c in ba_v["Carrera"].dropna().unique().tolist() if c and str(c).lower() != "nan"])
    if not carreras_canon:
        st.warning("No hay carreras con datos para la versión seleccionada.")
        return

    opciones_display = [_display_from_canon(c, sel_version, map_canon_to_display) for c in carreras_canon]
    sel_display = st.selectbox("Selecciona la carrera", opciones_display, index=0)
    idx = opciones_display.index(sel_display)
    sel_carrera_canon = carreras_canon[idx]

    # Para DG en detalle, filtramos por esa "carrera" tal cual (canon del dataset)
    prom_0_10, prom_pct, cov_pct, n_al, area_df, materia_df, exam_pub = _detalle_carrera_variantes(
        df_v, base_v, ba_v, [sel_carrera_canon], sel_version
    )

    c1, c2, c3, c4 = st.columns([1.0, 1.0, 1.0, 1.0])
    c1.metric("Promedio (0–10)", f"{prom_0_10:.2f}" if prom_0_10 is not None else "—")
    c2.metric("Porcentaje de acierto", f"{prom_pct:.1f}%" if prom_pct is not None else "—")
    c3.metric("Cobertura (respondidas)", f"{cov_pct:.1f}%" if cov_pct is not None else "—")
    c4.metric("Alumnos que respondieron", f"{n_al:,}")

    st.divider()
    tab1, tab2, tab3 = st.tabs(["Resultados", "Examen por área", "Examen por materia"])

    with tab1:
        st.markdown("### Promedio por área (0–10)")
        st.dataframe(area_df, use_container_width=True, hide_index=True)
        ch_a = _bar_h(area_df, "Area", "Promedio_area", "Promedio (0–10)")
        if ch_a is not None:
            st.altair_chart(ch_a, use_container_width=True)

        st.divider()
        st.markdown("### Promedio por materia (0–10)")
        st.dataframe(materia_df, use_container_width=True, hide_index=True)
        ch_m = _bar_h(materia_df, "Materia", "Promedio_materia", "Promedio (0–10)")
        if ch_m is not None:
            st.altair_chart(ch_m, use_container_width=True)

    with tab2:
        _render_tab_examen_por_area(
            exam_pub=exam_pub,
            filename_prefix=f"examen_{sel_version}_{sel_carrera_canon}",
        )

    with tab3:
        _render_tab_examen_por_materia(
            exam_pub=exam_pub,
            filename_prefix=f"examen_{sel_version}_{sel_carrera_canon}",
        )
