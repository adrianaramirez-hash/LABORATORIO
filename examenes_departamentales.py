# examenes_departamentales.py
import re
import pandas as pd
import streamlit as st
import altair as alt
import gspread

SHEET_BASE = "BASE_CONSOLIDADA"
SHEET_RESP = "RESPUESTAS_LARGAS"


# ============================================================
# Helpers
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
    candidates = [
        "Fecha", "fecha",
        "Marca temporal", "Marca Temporal",
        "Timestamp", "timestamp",
        "Aplicación", "Aplicacion",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _infer_year_from_version(version_value: str) -> int | None:
    if not version_value:
        return None
    m = re.search(r"(20\d{2})", str(version_value))
    return int(m.group(1)) if m else None


def _nice_pct(x: float) -> str:
    try:
        return f"{x:.1%}"
    except Exception:
        return "—"


def _bar_h(df: pd.DataFrame, cat: str, val: str, title: str):
    if df is None or df.empty:
        return None
    # Limitamos altura para que no se “infinitezca” si hay muchas filas
    height = min(900, max(280, len(df) * 26))
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(f"{cat}:N", sort="-x", title=None),
            x=alt.X(f"{val}:Q", title=title),
            tooltip=[alt.Tooltip(cat, title=cat), alt.Tooltip(val, title=title, format=".1%")],
        )
        .properties(height=height)
    )


# ============================================================
# Core cálculos (sin alumnos individuales)
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

    # Evitar duplicados por llave
    base = base.drop_duplicates(subset=["Carrera", "Version", "ID_reactivo"], keep="first")

    # AlumnoID (no mostramos detalle; solo sirve para agregar)
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

    # Aciertos y puntos
    df["Acierto"] = (df["Respuesta_alumno"] == df["Clave"]).astype("Int64")
    df["Puntos_obtenidos"] = df["Acierto"].fillna(0).astype(float) * df["Puntos"].fillna(0).astype(float)

    # Puntos posibles por carrera+version (para score por alumno)
    puntos_posibles_cv = (
        base.groupby(["Carrera", "Version"], as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles_examen"})
    )

    df = df.merge(puntos_posibles_cv, on=["Carrera", "Version"], how="left")

    return base, resp, df


def _promedio_institucional(df: pd.DataFrame) -> tuple[float, int]:
    """
    Promedio institucional = promedio de scores de alumnos (cada alumno normalizado por puntos posibles de su examen).
    Regresa (promedio, n_alumnos)
    """
    if df.empty:
        return 0.0, 0

    # total por alumno + carrera+version
    by_alumno = (
        df.groupby(["AlumnoID", "Carrera", "Version"], as_index=False)
        .agg(Puntos_obtenidos=("Puntos_obtenidos", "sum"),
             Puntos_posibles_examen=("Puntos_posibles_examen", "first"))
    )
    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"]) if r["Puntos_posibles_examen"] else 0.0,
        axis=1,
    )
    return float(by_alumno["Score"].mean()) if len(by_alumno) else 0.0, int(len(by_alumno))


def _resumen_por_carrera(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tabla: carrera, promedio (promedio de alumnos), alumnos_respondieron
    """
    if df.empty:
        return pd.DataFrame(columns=["Carrera", "Promedio_general", "Alumnos"])

    by_alumno = (
        df.groupby(["AlumnoID", "Carrera", "Version"], as_index=False)
        .agg(Puntos_obtenidos=("Puntos_obtenidos", "sum"),
             Puntos_posibles_examen=("Puntos_posibles_examen", "first"))
    )
    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"]) if r["Puntos_posibles_examen"] else 0.0,
        axis=1,
    )

    out = (
        by_alumno.groupby("Carrera", as_index=False)
        .agg(Promedio_general=("Score", "mean"),
             Alumnos=("AlumnoID", "nunique"))
        .sort_values("Promedio_general", ascending=False)
    )
    return out


def _detalle_carrera(df: pd.DataFrame, base: pd.DataFrame, carrera: str, version: str | None):
    """
    Devuelve: promedio_carrera, alumnos, area_df, materia_df
    (sin listar alumnos)
    """
    base_f = base[base["Carrera"] == carrera].copy()
    df_f = df[df["Carrera"] == carrera].copy()

    if version and version != "Todas":
        base_f = base_f[base_f["Version"] == version].copy()
        df_f = df_f[df_f["Version"] == version].copy()

    if base_f.empty or df_f.empty:
        return 0.0, 0, pd.DataFrame(), pd.DataFrame()

    # Promedio general carrera (promedio de alumnos)
    by_alumno = (
        df_f.groupby(["AlumnoID", "Version"], as_index=False)
        .agg(Puntos_obtenidos=("Puntos_obtenidos", "sum"),
             Puntos_posibles_examen=("Puntos_posibles_examen", "first"))
    )
    by_alumno["Score"] = by_alumno.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles_examen"]) if r["Puntos_posibles_examen"] else 0.0,
        axis=1,
    )
    prom_carrera = float(by_alumno["Score"].mean()) if len(by_alumno) else 0.0
    n_alumnos = int(by_alumno["AlumnoID"].nunique())

    # Área: score por alumno-área (normalizado por puntos posibles de área)
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
    area_df = (
        area_al.groupby("Area", as_index=False)["Score_area"]
        .mean()
        .rename(columns={"Score_area": "Promedio_area"})
        .sort_values("Promedio_area", ascending=False)
    )

    # Materia: score por alumno-materia
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
    materia_df = (
        mat_al.groupby("Materia", as_index=False)["Score_materia"]
        .mean()
        .rename(columns={"Score_materia": "Promedio_materia"})
        .sort_values("Promedio_materia", ascending=False)
    )

    return prom_carrera, n_alumnos, area_df, materia_df


# ============================================================
# Render principal
# ============================================================
def render_examenes_departamentales(spreadsheet_url: str, vista: str | None = None, carrera: str | None = None):
    if not vista:
        vista = "Dirección General"

    # Mensaje piloto (siempre arriba)
    st.info("Examen departamental: **Piloto**. Los resultados se presentan con fines de diagnóstico y mejora continua.")

    # Carga
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            base, resp = _load_from_gsheets_by_url(spreadsheet_url)
            base, resp, df = _prepare(base, resp)
    except Exception as e:
        st.error("No se pudieron cargar/procesar las hojas requeridas (BASE_CONSOLIDADA / RESPUESTAS_LARGAS).")
        st.exception(e)
        return

    # Año de aplicación
    date_col = _pick_date_col(resp)
    year_aplicacion = None
    if date_col:
        # Intento de parseo flexible
        dt = pd.to_datetime(resp[date_col], errors="coerce", dayfirst=True)
        if dt.notna().any():
            year_aplicacion = int(dt.dropna().dt.year.mode().iloc[0])
    if year_aplicacion is None:
        # fallback: intentar inferir de Version
        sample_version = None
        if "Version" in base.columns and base["Version"].notna().any():
            sample_version = str(base["Version"].dropna().iloc[0])
        year_aplicacion = _infer_year_from_version(sample_version) if sample_version else None

    st.caption(f"Aplicación: **{year_aplicacion if year_aplicacion else '—'}**")

    # Version selector (para proyección multianual)
    versiones = sorted([v for v in base["Version"].dropna().unique().tolist() if v and str(v).lower() != "nan"])
    sel_version = st.selectbox("Aplicación / Versión", ["Todas"] + versiones, index=0)

    # Filtrar por versión
    base_v = base.copy()
    df_v = df.copy()
    if sel_version != "Todas":
        base_v = base_v[base_v["Version"] == sel_version].copy()
        df_v = df_v[df_v["Version"] == sel_version].copy()

    # =========================
    # Vista Dirección General
    # =========================
    if vista == "Dirección General":
        prom_inst, n_alumnos_inst = _promedio_institucional(df_v)
        resumen = _resumen_por_carrera(df_v)

        c1, c2, c3 = st.columns([1.2, 1.0, 1.0])
        c1.metric("Promedio general institucional", _nice_pct(prom_inst))
        c2.metric("Alumnos que respondieron", f"{n_alumnos_inst:,}")
        c3.metric("Carreras con datos", f"{len(resumen):,}")

        st.divider()
        st.markdown("### Promedio general por carrera")

        # Tabla (sin alumnos individuales; solo conteo)
        show = resumen.copy()
        show["Promedio_general"] = show["Promedio_general"].astype(float)
        st.dataframe(show, use_container_width=True, hide_index=True)

        ch = _bar_h(show, "Carrera", "Promedio_general", "Promedio general")
        if ch is not None:
            st.altair_chart(ch, use_container_width=True)

        st.divider()
        st.markdown("### Ver detalle por carrera (igual a Director de carrera)")

        carrera_opts = resumen["Carrera"].dropna().astype(str).tolist()
        sel_carrera_detalle = st.selectbox("Selecciona una carrera para ver detalle", ["(Selecciona)"] + carrera_opts, index=0)

        if sel_carrera_detalle != "(Selecciona)":
            prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, sel_carrera_detalle, sel_version)

            c1, c2 = st.columns([1.2, 1.0])
            c1.metric("Promedio general (carrera)", _nice_pct(prom_c))
            c2.metric("Alumnos que respondieron", f"{n_al:,}")

            st.divider()
            st.markdown("#### Promedio por área")
            st.dataframe(area_df, use_container_width=True, hide_index=True)
            ch_a = _bar_h(area_df, "Area", "Promedio_area", "Promedio por área")
            if ch_a is not None:
                st.altair_chart(ch_a, use_container_width=True)

            st.divider()
            st.markdown("#### Promedio por materia")
            st.dataframe(materia_df, use_container_width=True, hide_index=True)
            ch_m = _bar_h(materia_df, "Materia", "Promedio_materia", "Promedio por materia")
            if ch_m is not None:
                st.altair_chart(ch_m, use_container_width=True)

        return

    # =========================
    # Vista Director de carrera
    # =========================
    # Director: NO menciona alumnos individualmente, solo conteo de respuestas
    carrera_fija = (carrera or "").strip()
    st.caption(f"Carrera (vista Director): **{carrera_fija if carrera_fija else '—'}**")
    if not carrera_fija:
        st.warning("No recibí la carrera desde app.py. Pasa el parámetro carrera en esta vista.")
        return

    prom_c, n_al, area_df, materia_df = _detalle_carrera(df_v, base_v, carrera_fija, sel_version)

    c1, c2 = st.columns([1.2, 1.0])
    c1.metric("Promedio general (carrera)", _nice_pct(prom_c))
    c2.metric("Alumnos que respondieron", f"{n_al:,}")

    st.divider()
    st.markdown("### Promedio por área")
    st.dataframe(area_df, use_container_width=True, hide_index=True)
    ch_a = _bar_h(area_df, "Area", "Promedio_area", "Promedio por área")
    if ch_a is not None:
        st.altair_chart(ch_a, use_container_width=True)

    st.divider()
    st.markdown("### Promedio por materia")
    st.dataframe(materia_df, use_container_width=True, hide_index=True)
    ch_m = _bar_h(materia_df, "Materia", "Promedio_materia", "Promedio por materia")
    if ch_m is not None:
        st.altair_chart(ch_m, use_container_width=True)
