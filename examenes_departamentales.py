# examenes_departamentales.py
import re
import pandas as pd
import streamlit as st
import altair as alt
import gspread

SHEET_BASE = "BASE_CONSOLIDADA"
SHEET_RESP = "RESPUESTAS_LARGAS"


# ============================================================
# Helpers (mismo patrón que encuesta_calidad.py)
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


def _as_upper(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(str).str.strip().str.upper()


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


def _safe_mean(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce")
    return float(s.mean()) if s.notna().any() else 0.0


# ============================================================
# Core: preparación y promedios (NO porcentajes)
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

    base["Clave"] = _as_upper(base, "Clave")
    resp["Respuesta_alumno"] = _as_upper(resp, "Respuesta_alumno")

    base["Puntos"] = pd.to_numeric(base["Puntos"], errors="coerce").fillna(1.0)
    base = base.drop_duplicates(subset=["Carrera", "Version", "ID_reactivo"], keep="first")

    # AlumnoID (solo para agregar; NO se muestra)
    resp["AlumnoID"] = resp["Matricula"].where(
        resp["Matricula"].notna() & (resp["Matricula"] != "") & (resp["Matricula"].str.lower() != "nan"),
        resp["Correo"],
    ).astype(str).str.strip()

    # Merge
    df = resp.merge(
        base[["Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"]],
        on=["Carrera", "Version", "ID_reactivo"],
        how="left",
    )

    # Acierto / puntos
    df["Acierto"] = (df["Respuesta_alumno"] == df["Clave"]).astype("Int64")
    df["Puntos_obtenidos"] = df["Acierto"].fillna(0).astype(float) * df["Puntos"].fillna(0).astype(float)

    # Puntos posibles por examen (Carrera+Version)
    puntos_posibles_cv = (
        base.groupby(["Carrera", "Version"], as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_examen"})
    )
    df = df.merge(puntos_posibles_cv, on=["Carrera", "Version"], how="left")

    # Score por alumno = puntos_obtenidos / puntos_posibles
    by_alumno = (
        df.groupby(["AlumnoID", "Carrera", "Version"], as_index=False)
        .agg(Puntos_obtenidos=("Puntos_obtenidos", "sum"),
             Puntos_posibles_examen=("Puntos_posibles_examen", "first"))
    )
    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"]) if r["Puntos_posibles_examen"] else 0.0,
        axis=1,
    )

    # Promedio en escala 0–10 (por defecto)
    # Si tu examen se califica distinto, aquí se ajusta el factor.
    by_alumno["Promedio_0_10"] = by_alumno["Score"] * 10.0

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
        return 0.0, 0, pd.DataFrame(), pd.DataFrame()

    prom_carrera_0_10 = float(ba_f["Promedio_0_10"].mean())
    n_alumnos = int(ba_f["AlumnoID"].nunique())

    # Área: promedio por alumno-área -> promedio de alumnos
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
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_area"]) if r["Puntos_posibles_area"] else 0.0,
        axis=1,
    )
    area_al["Promedio_area_0_10"] = area_al["Score_area"] * 10.0

    area_df = (
        area_al.groupby("Area", as_index=False)["Promedio_area_0_10"]
        .mean()
        .rename(columns={"Promedio_area_0_10": "Promedio_area"})
        .sort_values("Promedio_area", ascending=False)
    )

    # Materia: promedio por alumno-materia -> promedio de alumnos
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
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_materia"]) if r["Puntos_posibles_materia"] else 0.0,
        axis=1,
    )
    mat_al["Promedio_materia_0_10"] = mat_al["Score_materia"] * 10.0

    materia_df = (
        mat_al.groupby("Materia", as_index=False)["Promedio_materia_0_10"]
        .mean()
        .rename(columns={"Promedio_materia_0_10": "Promedio_materia"})
        .sort_values("Promedio_materia", ascending=False)
    )

    return prom_carrera_0_10, n_alumnos, area_df, materia_df


# ============================================================
# UI: layout tipo "pestaña de costado" para Dirección General
# ============================================================
def render_examenes_departamentales(spreadsheet_url: str, vista: str | None = None, carrera: str | None = None):
    if not vista:
        vista = "Dirección General"

    # Mensaje piloto + aplicación
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

    # Año de aplicación (ideal: Fecha/Marca temporal en RESPUESTAS_LARGAS)
    date_col = _pick_date_col(resp)
    year_aplicacion = None
    if date_col:
        dt = pd.to_datetime(resp[date_col], errors="coerce", dayfirst=True)
        if dt.notna().any():
            year_aplicacion = int(dt.dropna().dt.year.mode().iloc[0])
    if year_aplicacion is None:
        # fallback: inferir de Version
        sample_version = None
        if base["Version"].notna().any():
            sample_version = str(base["Version"].dropna().iloc[0])
        year_aplicacion = _infer_year_from_version(sample_version) if sample_version else None

    st.caption(f"Aplicación: **{year_aplicacion if year_aplicacion else '—'}**")

    # Selector de versión (para histórico a 5 años)
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

    # =========================
    # Dirección General: UI con "pestañas de costado" (radio en sidebar)
    # =========================
    if vista == "Dirección General":
        st.sidebar.markdown("## Exámenes departamentales")
        dg_tab = st.sidebar.radio(
            "Vista",
            ["Institución (Resumen)", "Por carrera (Detalle)"],
            index=0,
        )

        # KPI institucional (promedio 0–10)
        prom_inst_0_10 = float(ba_v["Promedio_0_10"].mean()) if not ba_v.empty else 0.0
        n_alumnos_inst = int(ba_v["AlumnoID"].nunique()) if not ba_v.empty else 0

        if dg_tab == "Institución (Resumen)":
            c1, c2, c3 = st.columns([1.2, 1.0, 1.0])
            c1.metric("Promedio general institucional (0–10)", f"{prom_inst_0_10:.2f}")
            c2.metric("Alumnos que respondieron", f"{n_alumnos_inst:,}")
            c3.metric("Carreras con datos", f"{ba_v['Carrera'].nunique() if not ba_v.empty else 0:,}")

            st.divider()

            # Promedio por carrera (0–10) + alumnos
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

        # -------------------------
        # Detalle por carrera (lo mismo que ve director)
        # -------------------------
        carreras = sorted([c for c in ba_v["Carrera"].dropna().unique().tolist() if c and str(c).lower() != "nan"])
        if not carreras:
            st.warning("No hay carreras con datos para la versión seleccionada.")
            return

        sel_carrera = st.selectbox("Selecciona la carrera", carreras, index=0)

        prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, ba_v, sel_carrera, sel_version)

        c1, c2 = st.columns([1.2, 1.0])
        c1.metric("Promedio general (0–10)", f"{prom_c:.2f}")
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

    # =========================
    # Director de carrera: solo su carrera, sin “alumnos” salvo conteo
    # =========================
    carrera_fija = (carrera or "").strip()
    if not carrera_fija:
        st.warning("No recibí la carrera desde app.py. Pasa el parámetro carrera en esta vista.")
        return

    prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, ba_v, carrera_fija, sel_version)

    c1, c2 = st.columns([1.2, 1.0])
    c1.metric("Promedio general (0–10)", f"{prom_c:.2f}")
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
