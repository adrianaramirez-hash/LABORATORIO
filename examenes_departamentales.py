# examenes_departamentales.py
import re
import pandas as pd
import streamlit as st
import altair as alt
import gspread

SHEET_BASE = "BASE_CONSOLIDADA"
SHEET_RESP = "RESPUESTAS_LARGAS"


# ============================================================
# Helpers (misma lógica de carga que encuesta_calidad.py)
# ============================================================
def _dedupe_headers(headers: list[str]) -> list[str]:
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


def _ws_to_df(sh, ws_title: str) -> pd.DataFrame:
    ws = sh.worksheet(ws_title)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _dedupe_headers([h.strip() for h in values[0]])
    rows = values[1:]
    return pd.DataFrame(rows, columns=headers).replace("", pd.NA)


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
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
    return base, resp


def _as_str(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(str).str.strip()


def _pick_date_col(df: pd.DataFrame) -> str | None:
    for c in ["Fecha", "fecha", "Marca temporal", "Marca Temporal", "Timestamp", "timestamp", "Aplicación", "Aplicacion"]:
        if c in df.columns:
            return c
    return None


def _infer_year_from_version(version_value: str) -> int | None:
    if not version_value:
        return None
    m = re.search(r"(20\d{2})", str(version_value))
    return int(m.group(1)) if m else None


def _normalize_option(x) -> str | pd.NA:
    """
    Normaliza clave/respuesta para compararlas:
    - Acepta A/B/C/D aunque venga como "A)", "a.", "Opción A", etc.
    - Acepta 1/2/3/4 -> A/B/C/D
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return pd.NA

    s = str(x).strip().upper()
    if not s or s == "NAN":
        return pd.NA

    # 1-4 => A-D
    if s in {"1", "2", "3", "4"}:
        return {"1": "A", "2": "B", "3": "C", "4": "D"}[s]

    # Extrae primera letra A-D que aparezca
    m = re.search(r"\b([ABCD])\b", s)
    if m:
        return m.group(1)

    # Casos tipo "A)" "A." "A-" al inicio
    m2 = re.match(r"^([ABCD])[\)\.\:\-\s]", s)
    if m2:
        return m2.group(1)

    return pd.NA


def _bar_h(df: pd.DataFrame, cat: str, val: str, title: str):
    if df is None or df.empty:
        return None
    height = min(900, max(280, len(df) * 26))
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(f"{cat}:N", sort="-x", title=None),
            x=alt.X(f"{val}:Q", title=title),
            tooltip=[alt.Tooltip(cat, title=cat), alt.Tooltip(val, title=title, format=".2f")],
        )
        .properties(height=height)
    )


# ============================================================
# Core
# ============================================================
def _prepare(base: pd.DataFrame, resp: pd.DataFrame):
    required_base = {"Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"}
    required_resp = {"Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo", "Respuesta_alumno"}

    if not required_base.issubset(set(base.columns)):
        raise ValueError(f"BASE_CONSOLIDADA debe contener: {sorted(required_base)}")
    if not required_resp.issubset(set(resp.columns)):
        raise ValueError(f"RESPUESTAS_LARGAS debe contener: {sorted(required_resp)}")

    base = base.copy()
    resp = resp.copy()

    for c in ["Carrera", "Version", "ID_reactivo", "Area", "Materia"]:
        base[c] = _as_str(base, c)
    for c in ["Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo"]:
        resp[c] = _as_str(resp, c)

    base["Puntos"] = pd.to_numeric(base["Puntos"], errors="coerce").fillna(1.0)
    base = base.drop_duplicates(subset=["Carrera", "Version", "ID_reactivo"], keep="first")

    # Normaliza claves y respuestas a A/B/C/D
    base["Clave_norm"] = base["Clave"].apply(_normalize_option)
    resp["Resp_norm"] = resp["Respuesta_alumno"].apply(_normalize_option)

    # AlumnoID (solo para agregación)
    resp["AlumnoID"] = resp["Matricula"].where(
        resp["Matricula"].notna() & (resp["Matricula"] != "") & (resp["Matricula"].str.lower() != "nan"),
        resp["Correo"],
    ).astype(str).str.strip()

    # Merge
    df = resp.merge(
        base[["Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave_norm", "Puntos"]],
        on=["Carrera", "Version", "ID_reactivo"],
        how="left",
    )

    # Diagnóstico: match base
    df["Match_base"] = df["Clave_norm"].notna()

    # Acierto (solo si hubo match y ambas normalizadas)
    df["Acierto"] = (
        (df["Match_base"])
        & (df["Resp_norm"].notna())
        & (df["Clave_norm"].notna())
        & (df["Resp_norm"] == df["Clave_norm"])
    ).astype(int)

    df["Puntos_obtenidos"] = df["Acierto"].astype(float) * df["Puntos"].fillna(0).astype(float)

    # Puntos posibles por examen (Carrera+Version)
    puntos_posibles_cv = (
        base.groupby(["Carrera", "Version"], as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_examen"})
    )
    df = df.merge(puntos_posibles_cv, on=["Carrera", "Version"], how="left")

    # Score por alumno (normalizado) -> Promedio 0–10
    # Nota: usamos el denominador del examen completo (base) para esa carrera+version.
    by_alumno = (
        df.groupby(["AlumnoID", "Carrera", "Version"], as_index=False)
        .agg(Puntos_obtenidos=("Puntos_obtenidos", "sum"),
             Puntos_posibles_examen=("Puntos_posibles_examen", "first"))
    )
    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"]) if pd.notna(r["Puntos_posibles_examen"]) and r["Puntos_posibles_examen"] > 0 else pd.NA,
        axis=1,
    )
    by_alumno["Promedio_0_10"] = pd.to_numeric(by_alumno["Score"], errors="coerce") * 10.0

    return base, resp, df, by_alumno


def _detalle_carrera(df: pd.DataFrame, base: pd.DataFrame, by_alumno: pd.DataFrame, carrera: str, version: str | None):
    base_f = base[base["Carrera"] == carrera].copy()
    df_f = df[df["Carrera"] == carrera].copy()
    ba_f = by_alumno[by_alumno["Carrera"] == carrera].copy()

    if version and version != "Todas":
        base_f = base_f[base_f["Version"] == version].copy()
        df_f = df_f[df_f["Version"] == version].copy()
        ba_f = ba_f[ba_f["Version"] == version].copy()

    if base_f.empty or df_f.empty or ba_f.empty:
        return pd.NA, 0, pd.DataFrame(), pd.DataFrame()

    prom_carrera_0_10 = float(pd.to_numeric(ba_f["Promedio_0_10"], errors="coerce").mean())
    n_respondieron = int(ba_f["AlumnoID"].nunique())

    # Área (promedio 0–10)
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
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_area"]) if pd.notna(r["Puntos_posibles_area"]) and r["Puntos_posibles_area"] > 0 else pd.NA,
        axis=1,
    )
    area_al["Promedio_area"] = pd.to_numeric(area_al["Score_area"], errors="coerce") * 10.0
    area_df = (
        area_al.groupby("Area", as_index=False)["Promedio_area"]
        .mean()
        .sort_values("Promedio_area", ascending=False)
    )

    # Materia (promedio 0–10)
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
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_materia"]) if pd.notna(r["Puntos_posibles_materia"]) and r["Puntos_posibles_materia"] > 0 else pd.NA,
        axis=1,
    )
    mat_al["Promedio_materia"] = pd.to_numeric(mat_al["Score_materia"], errors="coerce") * 10.0
    materia_df = (
        mat_al.groupby("Materia", as_index=False)["Promedio_materia"]
        .mean()
        .sort_values("Promedio_materia", ascending=False)
    )

    return prom_carrera_0_10, n_respondieron, area_df, materia_df


# ============================================================
# Render
# ============================================================
def render_examenes_departamentales(spreadsheet_url: str, vista: str | None = None, carrera: str | None = None):
    if not vista:
        vista = "Dirección General"

    st.info("Examen departamental: **Piloto**. Resultados con fines de diagnóstico y mejora continua.")

    # Carga
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            base, resp = _load_from_gsheets_by_url(spreadsheet_url)
            base, resp, df, by_alumno = _prepare(base, resp)
    except Exception as e:
        st.error("No se pudieron cargar/procesar las hojas (BASE_CONSOLIDADA / RESPUESTAS_LARGAS).")
        st.exception(e)
        return

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

    # Selector de versión
    versiones = sorted([v for v in base["Version"].dropna().unique().tolist() if v and str(v).lower() != "nan"])
    sel_version = st.selectbox("Aplicación / Versión", ["Todas"] + versiones, index=0)

    # Filtrar versión
    base_v = base.copy()
    df_v = df.copy()
    ba_v = by_alumno.copy()
    if sel_version != "Todas":
        base_v = base_v[base_v["Version"] == sel_version].copy()
        df_v = df_v[df_v["Version"] == sel_version].copy()
        ba_v = ba_v[ba_v["Version"] == sel_version].copy()

    # Diagnóstico mínimo (sin ensuciar UI):
    # Si no normaliza bien, veríamos demasiados NA en Clave_norm/Resp_norm.
    pct_match = float(df_v["Match_base"].mean()) if len(df_v) else 0.0
    if pct_match < 0.8:
        st.warning(
            "Aviso técnico: hay muchas respuestas que no están encontrando su reactivo en la base "
            "(match bajo). Esto puede afectar promedios. Revisa consistencia de Carrera/Version/ID_reactivo."
        )

    # ========================================================
    # Dirección General: selector EN PANTALLA (no sidebar)
    # ========================================================
    if vista == "Dirección General":
        modo = st.radio(
            "Vista",
            ["Institución (Resumen)", "Por carrera (Detalle)"],
            horizontal=True,
            index=0,
        )
        st.divider()

        prom_inst = float(pd.to_numeric(ba_v["Promedio_0_10"], errors="coerce").mean()) if not ba_v.empty else 0.0
        n_resp = int(ba_v["AlumnoID"].nunique()) if not ba_v.empty else 0

        if modo == "Institución (Resumen)":
            c1, c2, c3 = st.columns([1.2, 1.0, 1.0])
            c1.metric("Promedio general institucional (0–10)", f"{prom_inst:.2f}")
            c2.metric("Alumnos que respondieron", f"{n_resp:,}")
            c3.metric("Carreras con datos", f"{ba_v['Carrera'].nunique() if not ba_v.empty else 0:,}")

            st.divider()

            resumen = (
                ba_v.groupby("Carrera", as_index=False)
                .agg(Promedio=("Promedio_0_10", "mean"), Alumnos=("AlumnoID", "nunique"))
                .sort_values("Promedio", ascending=False)
            )

            st.markdown("### Promedio por carrera (0–10)")
            st.dataframe(resumen, use_container_width=True, hide_index=True)

            ch = _bar_h(resumen, "Carrera", "Promedio", "Promedio (0–10)")
            if ch is not None:
                st.altair_chart(ch, use_container_width=True)

            return

        # Por carrera (detalle) — selector dentro de pantalla
        carreras = sorted([c for c in ba_v["Carrera"].dropna().unique().tolist() if c and str(c).lower() != "nan"])
        if not carreras:
            st.warning("No hay carreras con datos para la versión seleccionada.")
            return

        sel_carrera = st.selectbox("Selecciona la carrera", carreras, index=0)

        prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, ba_v, sel_carrera, sel_version)

        c1, c2 = st.columns([1.2, 1.0])
        c1.metric("Promedio general (0–10)", f"{float(prom_c):.2f}" if pd.notna(prom_c) else "—")
        c2.metric("Alumnos que respondieron", f"{n_al:,}")

        st.divider()
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

        return

    # ========================================================
    # Director de carrera
    # ========================================================
    carrera_fija = (carrera or "").strip()
    if not carrera_fija:
        st.warning("No recibí la carrera desde app.py. Pasa el parámetro carrera en esta vista.")
        return

    prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, ba_v, carrera_fija, sel_version)

    c1, c2 = st.columns([1.2, 1.0])
    c1.metric("Promedio general (0–10)", f"{float(prom_c):.2f}" if pd.notna(prom_c) else "—")
    c2.metric("Alumnos que respondieron", f"{n_al:,}")

    st.divider()
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
