# examenes_departamentales.py
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
    """
    Si hay encabezados duplicados (ej. 'Orden' repetido), los vuelve únicos:
    Orden, Orden__2, Orden__3...
    """
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
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
    return df


@st.cache_data(show_spinner=False, ttl=300)
def _load_from_gsheets_by_url(url: str):
    # MISMA LÓGICA que encuesta_calidad.py
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    titles = [ws.title for ws in sh.worksheets()]
    if SHEET_BASE not in titles or SHEET_RESP not in titles:
        raise ValueError(
            f"No encontré pestañas requeridas: {SHEET_BASE} y/o {SHEET_RESP}. "
            f"Pestañas disponibles: {', '.join(titles)}"
        )

    base = _ws_to_df(sh, SHEET_BASE)
    resp = _ws_to_df(sh, SHEET_RESP)
    return base, resp


def _as_str(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(str).str.strip()


def _as_upper(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype(str).str.strip().str.upper()


def _bar(df: pd.DataFrame, cat: str, val: str, title_cat: str, title_val: str):
    if df is None or df.empty:
        return None
    return (
        alt.Chart(df)
        .mark_bar()
        .encode(
            y=alt.Y(f"{cat}:N", sort="-x", title=title_cat),
            x=alt.X(f"{val}:Q", title=title_val),
            tooltip=[cat, alt.Tooltip(val, format=".1%")],
        )
        .properties(height=max(260, len(df) * 28))
    )


# ============================================================
# Render principal (compatible con tu app.py actual)
# ============================================================
def render_examenes_departamentales(spreadsheet_url: str, vista: str | None = None, carrera: str | None = None):
    st.subheader("Exámenes departamentales")

    # ---------------------------
    # Carga
    # ---------------------------
    try:
        with st.spinner("Cargando datos (Google Sheets)…"):
            base, resp = _load_from_gsheets_by_url(spreadsheet_url)
    except Exception as e:
        st.error("No se pudieron cargar las hojas requeridas (BASE_CONSOLIDADA / RESPUESTAS_LARGAS).")
        st.exception(e)
        return

    if base.empty:
        st.warning("La hoja BASE_CONSOLIDADA está vacía.")
        return
    if resp.empty:
        st.warning("La hoja RESPUESTAS_LARGAS está vacía.")
        return

    # ---------------------------
    # Validación mínima de columnas
    # ---------------------------
    required_base = {"Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"}
    required_resp = {"Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo", "Respuesta_alumno"}

    if not required_base.issubset(set(base.columns)):
        st.error(f"BASE_CONSOLIDADA debe contener: {sorted(required_base)}")
        st.caption(f"Columnas detectadas: {list(base.columns)}")
        return

    if not required_resp.issubset(set(resp.columns)):
        st.error(f"RESPUESTAS_LARGAS debe contener: {sorted(required_resp)}")
        st.caption(f"Columnas detectadas: {list(resp.columns)}")
        return

    # ---------------------------
    # Normalización
    # ---------------------------
    base = base.copy()
    resp = resp.copy()

    for c in ["Carrera", "Version", "ID_reactivo", "Area", "Materia"]:
        base[c] = _as_str(base, c)
    for c in ["Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo"]:
        resp[c] = _as_str(resp, c)

    base["Clave"] = _as_upper(base, "Clave")
    resp["Respuesta_alumno"] = _as_upper(resp, "Respuesta_alumno")

    base["Puntos"] = pd.to_numeric(base["Puntos"], errors="coerce").fillna(1.0)

    # Evitar duplicados en base por llave (por si quedó repetido)
    base = base.drop_duplicates(subset=["Carrera", "Version", "ID_reactivo"], keep="first")

    # ---------------------------
    # Filtros (vista/carrera)
    # ---------------------------
    if not vista:
        vista = "Dirección General"

    carreras = sorted([c for c in base["Carrera"].dropna().unique().tolist() if c and c.lower() != "nan"])

    if vista == "Dirección General":
        sel_carrera = st.selectbox("Servicio/Carrera", ["Todos"] + carreras, index=0)
    else:
        # Director de carrera: fija carrera si viene desde app.py
        sel_carrera = (carrera or "").strip()
        st.text_input("Carrera (fija por vista)", value=sel_carrera, disabled=True)
        if not sel_carrera:
            st.warning("No recibí carrera en esta vista. Selecciona 'Dirección General' o pasa carrera desde app.py.")
            return

    if sel_carrera != "Todos":
        base_f = base[base["Carrera"] == sel_carrera].copy()
        resp_f = resp[resp["Carrera"] == sel_carrera].copy()
    else:
        base_f = base.copy()
        resp_f = resp.copy()

    versiones = sorted([v for v in base_f["Version"].dropna().unique().tolist() if v and v.lower() != "nan"])
    sel_version = st.selectbox("Versión", ["Todas"] + versiones, index=0)

    if sel_version != "Todas":
        base_f = base_f[base_f["Version"] == sel_version].copy()
        resp_f = resp_f[resp_f["Version"] == sel_version].copy()

    st.caption(f"Registros base: **{len(base_f)}** | Respuestas: **{len(resp_f)}**")

    if base_f.empty or resp_f.empty:
        st.warning("No hay datos para los filtros seleccionados.")
        return

    # ---------------------------
    # Merge
    # ---------------------------
    df = resp_f.merge(
        base_f[["Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"]],
        on=["Carrera", "Version", "ID_reactivo"],
        how="left",
    )

    # Diagnóstico de match
    total = len(df)
    sin_match = int(df["Clave"].isna().sum())
    if sin_match > 0:
        st.warning(f"Respuestas sin match con base: **{sin_match:,}** de **{total:,}**. (Revisar ID_reactivo/Carrera/Version)")

    # ---------------------------
    # Cálculo correcto de promedios (por alumno)
    # ---------------------------
    # ID de alumno: usa matrícula; si no hay, usa correo
    df["AlumnoID"] = df["Matricula"].where(df["Matricula"].notna() & (df["Matricula"] != "nan") & (df["Matricula"] != ""), df["Correo"])
    df["AlumnoID"] = df["AlumnoID"].astype(str).str.strip()

    df["Acierto"] = (df["Respuesta_alumno"] == df["Clave"]).astype("Int64")
    df["Puntos_obtenidos"] = df["Acierto"].fillna(0).astype(float) * df["Puntos"].fillna(0).astype(float)

    # Puntos posibles por examen (según filtros)
    puntos_posibles_total = float(base_f["Puntos"].sum()) if len(base_f) else 0.0

    # Score por alumno
    by_alumno = (
        df.groupby(["AlumnoID"], as_index=False)["Puntos_obtenidos"]
        .sum()
        .rename(columns={"Puntos_obtenidos": "Puntos_obtenidos"})
    )
    by_alumno["Score"] = by_alumno["Puntos_obtenidos"] / puntos_posibles_total if puntos_posibles_total else 0.0
    promedio_general = float(by_alumno["Score"].mean()) if len(by_alumno) else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Promedio general (promedio de alumnos)", f"{promedio_general:.1%}")
    c2.metric("Alumnos", f"{len(by_alumno):,}")
    c3.metric("Puntos posibles (examen)", f"{puntos_posibles_total:,.0f}")

    st.divider()

    # ---------------------------
    # Promedio por Área (promedio de alumnos por área)
    # ---------------------------
    st.markdown("### Promedio por área")

    area_pos = base_f.groupby("Area", as_index=False)["Puntos"].sum().rename(columns={"Puntos": "Puntos_posibles"})
    # puntos obtenidos por alumno-área
    area_alumno = df.groupby(["AlumnoID", "Area"], as_index=False)["Puntos_obtenidos"].sum()
    area_alumno = area_alumno.merge(area_pos, on="Area", how="left")
    area_alumno["Score_area"] = area_alumno["Puntos_obtenidos"] / area_alumno["Puntos_posibles"]

    area_res = area_alumno.groupby("Area", as_index=False)["Score_area"].mean().rename(columns={"Score_area": "Promedio_area"})
    area_res = area_res.sort_values("Promedio_area", ascending=False)

    st.dataframe(area_res, use_container_width=True, hide_index=True)

    ch = _bar(area_res, "Area", "Promedio_area", "Área", "Promedio")
    if ch is not None:
        st.altair_chart(ch, use_container_width=True)

    st.divider()

    # ---------------------------
    # Promedio por Materia (promedio de alumnos por materia)
    # ---------------------------
    st.markdown("### Promedio por materia")

    mat_pos = base_f.groupby("Materia", as_index=False)["Puntos"].sum().rename(columns={"Puntos": "Puntos_posibles"})
    mat_alumno = df.groupby(["AlumnoID", "Materia"], as_index=False)["Puntos_obtenidos"].sum()
    mat_alumno = mat_alumno.merge(mat_pos, on="Materia", how="left")
    mat_alumno["Score_materia"] = mat_alumno["Puntos_obtenidos"] / mat_alumno["Puntos_posibles"]

    mat_res = mat_alumno.groupby("Materia", as_index=False)["Score_materia"].mean().rename(columns={"Score_materia": "Promedio_materia"})
    mat_res = mat_res.sort_values("Promedio_materia", ascending=False)

    st.dataframe(mat_res, use_container_width=True, hide_index=True)

    ch2 = _bar(mat_res, "Materia", "Promedio_materia", "Materia", "Promedio")
    if ch2 is not None:
        st.altair_chart(ch2, use_container_width=True)

    # ---------------------------
    # Detalle opcional
    # ---------------------------
    with st.expander("Detalle por alumno"):
        show = by_alumno.copy()
        show = show.sort_values("Score", ascending=False)
        st.dataframe(show, use_container_width=True, hide_index=True)
