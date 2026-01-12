# examenes_departamentales.py
import json
from typing import Tuple
from collections.abc import Mapping

import altair as alt
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# -----------------------------
# Config
# -----------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TAB_BASE = "BASE_CONSOLIDADA"
TAB_RESP = "RESPUESTAS_LARGAS"

# Columnas esperadas (en BASE_CONSOLIDADA puede haber duplicados; usamos las primeras ocurrencias)
COLS_BASE_REQUIRED = ["Carrera", "Version", "Orden", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"]
COLS_RESP_REQUIRED = ["Carrera", "Version", "Matricula", "Grupo", "Correo", "Orden_forms", "ID_reactivo", "Respuesta_alumno"]


# -----------------------------
# Helpers
# -----------------------------
@st.cache_data(ttl=300)
def _get_gspread_client() -> gspread.Client:
    raw = st.secrets["gcp_service_account_json"]

    if isinstance(raw, Mapping):
        creds_dict = dict(raw)
    elif isinstance(raw, (str, bytes, bytearray)):
        creds_dict = json.loads(raw)
    else:
        try:
            creds_dict = dict(raw)
        except Exception as e:
            raise TypeError(
                f"Formato no soportado para gcp_service_account_json: {type(raw)}. "
                "Debe ser Mapping (dict/AttrDict) o JSON string."
            ) from e

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _dedupe_columns(cols: list[str]) -> list[str]:
    """Convierte encabezados duplicados a nombres únicos: Orden, Orden__2, Orden__3..."""
    seen = {}
    out = []
    for c in cols:
        name = str(c).strip()
        if name in seen:
            seen[name] += 1
            out.append(f"{name}__{seen[name]}")
        else:
            seen[name] = 1
            out.append(name)
    return out


@st.cache_data(ttl=300)
def _load_worksheet_df(spreadsheet_url: str, worksheet_name: str) -> pd.DataFrame:
    client = _get_gspread_client()
    sh = client.open_by_url(spreadsheet_url)
    ws = sh.worksheet(worksheet_name)

    values = ws.get_all_values()  # <-- tolera encabezados duplicados
    if not values or len(values) < 2:
        return pd.DataFrame()

    raw_cols = values[0]
    cols = _dedupe_columns(raw_cols)
    rows = values[1:]

    df = pd.DataFrame(rows, columns=cols)

    # Limpia columnas totalmente vacías
    df = df.dropna(axis=1, how="all")
    return df


def _ensure_columns(df: pd.DataFrame, required: list, df_name: str) -> Tuple[bool, list]:
    missing = [c for c in required if c not in df.columns]
    ok = len(missing) == 0
    if not ok:
        st.error(f"Faltan columnas en {df_name}: {missing}")
    return ok, missing


def _normalize_text_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip()


def _safe_upper(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.upper()


def _pct(x: float) -> str:
    try:
        return f"{x:.1%}"
    except Exception:
        return "—"


def _to_num(s: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(default)


def _first_col(df: pd.DataFrame, name: str) -> str:
    """Si existe name, úsalo; si existe name__2, name__3, etc., preferimos el primero (name)."""
    if name in df.columns:
        return name
    # fallback: por si el primero quedó como name__2 (raro)
    for c in df.columns:
        if c == name or c.startswith(name + "__"):
            return c
    return name


# -----------------------------
# Main render
# -----------------------------
def render_examenes_departamentales(spreadsheet_url: str) -> None:
    st.header("Exámenes departamentales")

    with st.spinner("Cargando datos..."):
        base = _load_worksheet_df(spreadsheet_url, TAB_BASE)
        resp = _load_worksheet_df(spreadsheet_url, TAB_RESP)

    if base.empty:
        st.error("BASE_CONSOLIDADA está vacía o no tiene datos.")
        st.stop()
    if resp.empty:
        st.error("RESPUESTAS_LARGAS está vacía o no tiene datos.")
        st.stop()

    # Asegura columnas requeridas (usando el primer 'Orden' válido si hay duplicados)
    # Nota: si tienes Orden y Orden__2, la validación ya pasa porque existe "Orden"
    ok_base, _ = _ensure_columns(base, COLS_BASE_REQUIRED, TAB_BASE)
    ok_resp, _ = _ensure_columns(resp, COLS_RESP_REQUIRED, TAB_RESP)
    if not (ok_base and ok_resp):
        st.stop()

    # Detecta columna Orden (por si la primera se renombró)
    col_orden = _first_col(base, "Orden")

    # Normalizaciones clave
    for c in ["Carrera", "Version", "ID_reactivo", "Area", "Materia"]:
        base[c] = _normalize_text_series(base[c])
    for c in ["Carrera", "Version", "ID_reactivo", "Matricula", "Grupo", "Correo"]:
        resp[c] = _normalize_text_series(resp[c])

    base["Clave"] = _safe_upper(base["Clave"])
    resp["Respuesta_alumno"] = _safe_upper(resp["Respuesta_alumno"])

    # Puntos numéricos
    base["Puntos"] = _to_num(base["Puntos"], default=1.0)

    # Selector principal (Todos / Carrera)
    carreras = sorted([c for c in base["Carrera"].dropna().unique() if c and c.lower() != "nan"])
    sel_carrera = st.selectbox("Servicio/Carrera", ["Todos"] + carreras, index=0)

    # Filtros
    if sel_carrera != "Todos":
        base_v = base[base["Carrera"] == sel_carrera].copy()
        resp_v = resp[resp["Carrera"] == sel_carrera].copy()
    else:
        base_v = base.copy()
        resp_v = resp.copy()

    versiones = sorted([v for v in base_v["Version"].dropna().unique() if v and v.lower() != "nan"])
    sel_version = st.selectbox("Versión", ["Todas"] + versiones, index=0)

    if sel_version != "Todas":
        base_v = base_v[base_v["Version"] == sel_version].copy()
        resp_v = resp_v[resp_v["Version"] == sel_version].copy()

    # Merge respuestas + base
    df = resp_v.merge(
        base_v[["Carrera", "Version", "ID_reactivo", "Area", "Materia", "Clave", "Puntos"]],
        on=["Carrera", "Version", "ID_reactivo"],
        how="left",
        validate="many_to_one",
    )

    # Diagnóstico de match
    total = len(df)
    sin_match = int(df["Clave"].isna().sum())
    pct_sin_match = (sin_match / total) if total else 0.0
    if pct_sin_match > 0:
        st.warning(
            f"Hay {sin_match:,} respuestas ({pct_sin_match:.1%}) que no encontraron match con la base. "
            f"Revisa consistencia de Carrera/Version/ID_reactivo."
        )

    # Acierto y puntos
    df["Acierto"] = (df["Respuesta_alumno"] == df["Clave"]).astype("Int64")
    df["Puntos_obtenidos"] = (df["Acierto"].fillna(0).astype(float) * df["Puntos"].fillna(0)).astype(float)

    # KPIs
    puntos_posibles = float(base_v["Puntos"].sum()) if len(base_v) else 0.0
    puntos_obtenidos = float(df["Puntos_obtenidos"].sum()) if len(df) else 0.0
    promedio_general = (puntos_obtenidos / puntos_posibles) if puntos_posibles else 0.0

    alumnos = df[["Matricula", "Correo"]].drop_duplicates()
    n_alumnos = int(len(alumnos))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Promedio general", _pct(promedio_general))
    c2.metric("Alumnos", f"{n_alumnos:,}")
    c3.metric("Respuestas", f"{total:,}")
    c4.metric("Reactivos en base", f"{len(base_v):,}")

    st.divider()

    # Promedio por Área
    st.subheader("Promedio por área")
    area_pos = (
        base_v.groupby("Area", dropna=False, as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles"})
    )
    area_obt = (
        df.groupby("Area", dropna=False, as_index=False)["Puntos_obtenidos"]
        .sum()
        .rename(columns={"Puntos_obtenidos": "Puntos_obtenidos"})
    )
    area = area_obt.merge(area_pos, on="Area", how="left")
    area["Promedio_area"] = area.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles"]) if r["Puntos_posibles"] else 0.0,
        axis=1,
    )
    area = area.sort_values("Promedio_area", ascending=False)

    st.dataframe(
        area[["Area", "Promedio_area", "Puntos_obtenidos", "Puntos_posibles"]],
        use_container_width=True,
        hide_index=True,
    )

    area_chart = (
        alt.Chart(area.dropna(subset=["Area"]))
        .mark_bar()
        .encode(
            y=alt.Y("Area:N", sort="-x", title="Área"),
            x=alt.X("Promedio_area:Q", title="Promedio"),
            tooltip=[
                alt.Tooltip("Area:N", title="Área"),
                alt.Tooltip("Promedio_area:Q", title="Promedio", format=".1%"),
            ],
        )
    )
    st.altair_chart(area_chart, use_container_width=True)

    st.divider()

    # Promedio por Materia
    st.subheader("Promedio por materia")
    mat_pos = (
        base_v.groupby("Materia", dropna=False, as_index=False)["Puntos"]
        .sum()
        .rename(columns={"Puntos": "Puntos_posibles"})
    )
    mat_obt = (
        df.groupby("Materia", dropna=False, as_index=False)["Puntos_obtenidos"]
        .sum()
        .rename(columns={"Puntos_obtenidos": "Puntos_obtenidos"})
    )
    mat = mat_obt.merge(mat_pos, on="Materia", how="left")
    mat["Promedio_materia"] = mat.apply(
        lambda r: (r["Puntos_obtenidos"] / r["Puntos_posibles"]) if r["Puntos_posibles"] else 0.0,
        axis=1,
    )
    mat = mat.sort_values("Promedio_materia", ascending=False)

    st.dataframe(
        mat[["Materia", "Promedio_materia", "Puntos_obtenidos", "Puntos_posibles"]],
        use_container_width=True,
        hide_index=True,
    )

    mat_chart = (
        alt.Chart(mat.dropna(subset=["Materia"]))
        .mark_bar()
        .encode(
            y=alt.Y("Materia:N", sort="-x", title="Materia"),
            x=alt.X("Promedio_materia:Q", title="Promedio"),
            tooltip=[
                alt.Tooltip("Materia:N", title="Materia"),
                alt.Tooltip("Promedio_materia:Q", title="Promedio", format=".1%"),
            ],
        )
    )
    st.altair_chart(mat_chart, use_container_width=True)

    st.divider()

    with st.expander("Detalle por alumno (promedio individual)"):
        puntos_pos = puntos_posibles if puntos_posibles else 0.0
        by_alumno = (
            df.groupby(["Matricula", "Correo", "Grupo"], dropna=False, as_index=False)["Puntos_obtenidos"]
            .sum()
            .rename(columns={"Puntos_obtenidos": "Puntos_obtenidos"})
        )
        by_alumno["Promedio"] = by_alumno["Puntos_obtenidos"] / puntos_pos if puntos_pos else 0.0
        by_alumno = by_alumno.sort_values("Promedio", ascending=False)

        st.dataframe(
            by_alumno[["Matricula", "Grupo", "Correo", "Promedio", "Puntos_obtenidos"]],
            use_container_width=True,
            hide_index=True,
        )
