# bajas_retencion.py
import streamlit as st
import pandas as pd
import gspread
import json
import re
import altair as alt
from google.oauth2.service_account import Credentials

# ========= AJUSTA SOLO ESTO =========
BAJAS_SHEET_URL = "https://docs.google.com/spreadsheets/d/11-QVSp2zvRtsy3RA82N9j7g8zNzJGDKJqAIH9sabiUU/edit?gid=1444259240#gid=1444259240"
BAJAS_TAB_NAME = "BAJAS"
# ====================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ---------------- Helpers base ----------------
def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url or "")
    if not m:
        raise ValueError("No pude extraer el ID del Google Sheet desde la URL.")
    return m.group(1)

def _load_creds_dict() -> dict:
    raw = st.secrets["gcp_service_account_json"]
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)

@st.cache_resource(show_spinner=False)
def _get_gspread_client():
    creds_dict = _load_creds_dict()
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

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

def _norm_tab_key(s: str) -> str:
    s = str(s or "")
    s = s.replace("\u00A0", " ")
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()

def _open_sheet():
    if not BAJAS_SHEET_URL or "PEGA_AQUI" in BAJAS_SHEET_URL:
        raise ValueError("Falta configurar BAJAS_SHEET_URL en bajas_retencion.py")
    gc = _get_gspread_client()
    sh = gc.open_by_key(_extract_sheet_id(BAJAS_SHEET_URL))
    return sh

def _resolve_ws(sh, desired_title: str):
    try:
        return sh.worksheet(desired_title)
    except Exception:
        tabs = sh.worksheets()
        desired_key = _norm_tab_key(desired_title)
        for ws in tabs:
            if _norm_tab_key(ws.title) == desired_key:
                return ws
        titles = [ws.title for ws in tabs]
        raise gspread.exceptions.WorksheetNotFound(
            f"No encontré la pestaña '{desired_title}'. Pestañas disponibles: {titles}"
        )

def _load_from_ws(ws) -> pd.DataFrame:
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    headers = _dedupe_headers(values[0])
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    df.columns = [str(c).strip().upper() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].astype(str)
    return df

@st.cache_data(ttl=300, show_spinner=False)
def _load_bajas_df() -> pd.DataFrame:
    sh = _open_sheet()
    ws = _resolve_ws(sh, BAJAS_TAB_NAME)
    return _load_from_ws(ws)

# ---------------- Normalización de motivos ----------------
def _clean_upper(x: str) -> str:
    s = (x or "").strip()
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.upper()

def _split_motivo(x: str):
    s = (x or "").strip()
    if not s:
        return ("", "")
    parts = s.split("-", 1)
    cat = _clean_upper(parts[0])
    det = parts[1].strip() if len(parts) > 1 else ""
    return (cat, det)

def _std_categoria(cat_raw: str) -> str:
    c = _clean_upper(cat_raw)

    rules = [
        (r"ECON", "ECONÓMICO"),
        (r"CAMBIO\s*DE\s*CARRERA|CAMBIO", "CAMBIO DE CARRERA"),
        (r"PERSONAL", "MOTIVOS PERSONALES"),
        (r"SALUD|ENFERM", "SALUD"),
        (r"ADMIN|COBRAN|BAJA\s*ADMIN", "BAJA ADMINISTRATIVA"),
        (r"ADMISI|ENTREV|EXAMEN\s*ADM", "ADMISIÓN / INGRESO"),
        (r"OTROS", "OTROS"),
    ]
    for pat, lab in rules:
        if re.search(pat, c):
            return lab
    return c if c else "SIN ESPECIFICAR"

def _to_int_safe(x):
    try:
        return int(float(str(x).strip()))
    except Exception:
        return pd.NA

def _parse_fecha(x: str):
    s = str(x or "").strip()
    if not s:
        return pd.NaT
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s)

    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.notna(dt):
        return dt

    meses = {
        "ene": "jan", "feb": "feb", "mar": "mar", "abr": "apr", "may": "may", "jun": "jun",
        "jul": "jul", "ago": "aug", "sep": "sep", "oct": "oct", "nov": "nov", "dic": "dec"
    }
    m = re.search(r"(\d{1,2})-([a-zA-Z]{3})-(\d{2,4})", s.lower())
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        mon2 = meses.get(mon, mon)
        s2 = f"{d}-{mon2}-{y}"
        return pd.to_datetime(s2, errors="coerce", dayfirst=True)

    return pd.NaT

# ---------------- Render ----------------
def render_bajas_retencion(vista: str, carrera: str | None):
    st.subheader("Bajas / Retención")
    st.caption("Vista analítica (solo lectura).")

    # 1) Carga
    try:
        df0 = _load_bajas_df()
    except Exception as e:
        st.error("No pude cargar la información de Bajas. Revisa URL, permisos o nombre de pestaña.")
        st.exception(e)
        return

    if df0.empty:
        st.warning("La pestaña está vacía o no tiene datos.")
        return

    # 2) Columnas esperadas (defensivo)
    col_ciclo = "CICLO" if "CICLO" in df0.columns else None
    col_area = "AREA" if "AREA" in df0.columns else None
    col_fecha = "FECHA_BAJA" if "FECHA_BAJA" in df0.columns else None
    col_motivo = "MOTIVO_BAJA" if "MOTIVO_BAJA" in df0.columns else None

    if not col_motivo:
        st.error("No encontré la columna MOTIVO_BAJA en esta pestaña.")
        st.write("Columnas detectadas:", list(df0.columns))
        return

    # 3) Normalización base
    df = df0.copy()

    if col_area:
        df[col_area] = df[col_area].apply(_clean_upper)

    df["MOTIVO_CATEGORIA_RAW"], df["MOTIVO_DETALLE"] = zip(*df[col_motivo].apply(_split_motivo))
    df["MOTIVO_CATEGORIA_STD"] = df["MOTIVO_CATEGORIA_RAW"].apply(_std_categoria)

    if col_ciclo:
        df[col_ciclo] = df[col_ciclo].apply(_to_int_safe)

    if col_fecha:
        df["FECHA_BAJA_DT"] = df[col_fecha].apply(_parse_fecha)
        df["MES"] = df["FECHA_BAJA_DT"].dt.to_period("M").astype(str)
    else:
        df["FECHA_BAJA_DT"] = pd.NaT
        df["MES"] = ""

    # 4) Filtros ARRIBA (sin grupo)
    st.markdown("### Filtros")

    areas = []
    if col_area:
        areas = sorted([a for a in df[col_area].dropna().unique().tolist() if str(a).strip()])

    ciclos = []
    if col_ciclo:
        ciclos = sorted([c for c in df[col_ciclo].dropna().unique().tolist() if pd.notna(c)])

    cats = sorted(df["MOTIVO_CATEGORIA_STD"].dropna().unique().tolist())

    if carrera and col_area:
        cA, cC, cM = st.columns([2.4, 1.2, 2.0])
        with cA:
            st.text_input("Área", value=str(carrera).upper(), disabled=True)
            area_sel = str(carrera).upper()
        with cC:
            ciclo_sel = st.selectbox("Ciclo", options=["(Todos)"] + ciclos, index=0)
        with cM:
            cat_sel = st.selectbox("Motivo (categoría)", options=["(Todos)"] + cats, index=0)
    else:
        cA, cC, cM = st.columns([2.4, 1.2, 2.0])
        with cA:
            area_sel = st.selectbox("Área", options=["(Todas)"] + areas, index=0) if col_area else "(Todas)"
        with cC:
            ciclo_sel = st.selectbox("Ciclo", options=["(Todos)"] + ciclos, index=0) if col_ciclo else "(Todos)"
        with cM:
            cat_sel = st.selectbox("Motivo (categoría)", options=["(Todos)"] + cats, index=0)

    # Aplicar filtros
    if col_area and carrera:
        df = df[df[col_area] == str(carrera).upper()].copy()
    elif col_area and area_sel != "(Todas)":
        df = df[df[col_area] == area_sel].copy()

    if col_ciclo and ciclo_sel != "(Todos)":
        df = df[df[col_ciclo] == ciclo_sel].copy()

    if cat_sel != "(Todos)":
        df = df[df["MOTIVO_CATEGORIA_STD"] == cat_sel].copy()

    st.divider()

    # 5) KPIs
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Bajas (filtrado)", f"{len(df):,}")
    with k2:
        st.metric("Motivos únicos", f"{df['MOTIVO_CATEGORIA_STD'].nunique():,}")
    with k3:
        st.metric("Áreas presentes", f"{df[col_area].nunique():,}" if col_area else "—")
    with k4:
        st.metric("Ciclos presentes", f"{df[col_ciclo].nunique():,}" if col_ciclo else "—")

    # 6) Tendencia
    st.markdown("### Tendencia")
    if df["FECHA_BAJA_DT"].notna().any():
        ts = (
            df.dropna(subset=["FECHA_BAJA_DT"])
              .groupby("MES")
              .size()
              .reset_index(name="bajas")
              .sort_values("MES")
        )
        chart = alt.Chart(ts).mark_line(point=True).encode(
            x=alt.X("MES:N", title="Mes"),
            y=alt.Y("bajas:Q", title="Bajas"),
            tooltip=["MES:N", "bajas:Q"],
        )
        st.altair_chart(chart, use_container_width=True)
    elif col_ciclo and df[col_ciclo].notna().any():
        ts = (
            df.dropna(subset=[col_ciclo])
              .groupby(col_ciclo)
              .size()
              .reset_index(name="bajas")
              .sort_values(col_ciclo)
        )
        chart = alt.Chart(ts).mark_line(point=True).encode(
            x=alt.X(f"{col_ciclo}:O", title="Ciclo"),
            y=alt.Y("bajas:Q", title="Bajas"),
            tooltip=[alt.Tooltip(f"{col_ciclo}:O", title="Ciclo"), "bajas:Q"],
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No hay FECHA_BAJA o CICLO usable para tendencia.")

    # 7) Motivos (categoría)
    st.markdown("### Motivos de baja (categoría homologada)")
    vc = df["MOTIVO_CATEGORIA_STD"].value_counts().reset_index()
    vc.columns = ["motivo", "bajas"]
    if vc.empty:
        st.info("No hay datos para mostrar con el filtro actual.")
        return

    vc["%"] = (vc["bajas"] / vc["bajas"].sum()) * 100
    vc["%_acum"] = vc["%"].cumsum()

    st.dataframe(vc, use_container_width=True)

    pareto = alt.Chart(vc).mark_bar().encode(
        x=alt.X("motivo:N", sort="-y", title="Motivo"),
        y=alt.Y("bajas:Q", title="Bajas"),
        tooltip=[
            alt.Tooltip("motivo:N", title="Motivo"),
            alt.Tooltip("bajas:Q", title="Bajas"),
            alt.Tooltip("%:Q", title="%", format=".1f"),
            alt.Tooltip("%_acum:Q", title="% acumulado", format=".1f"),
        ],
    )
    st.altair_chart(pareto, use_container_width=True)

    # 8) Resumen ciclo–área
    if col_ciclo and col_area:
        st.markdown("### Resumen por ciclo y área")
        resumen = (
            df.groupby([col_ciclo, col_area], dropna=False)
              .size()
              .reset_index(name="bajas")
              .sort_values("bajas", ascending=False)
        )
        st.dataframe(resumen, use_container_width=True)

    # 9) Depuración “OTROS”
    st.markdown("### Depuración: detalles más repetidos (solo OTROS)")
    otros = df[df["MOTIVO_CATEGORIA_STD"] == "OTROS"].copy()
    if not otros.empty:
        det = otros["MOTIVO_DETALLE"].astype(str).str.strip()
        det = det[det != ""]
        if not det.empty:
            st.dataframe(det.value_counts().head(25).rename("conteo"), use_container_width=True)
        else:
            st.caption("OTROS no tiene detalles capturados.")
    else:
        st.caption("No hay registros OTROS en el filtro actual.")

    # 10) Tabla de casos
    st.markdown("### Casos (detalle)")
    cols = [c for c in [
        "CICLO", "CICLO INGRESO", "TIPO", "NIVEL", "AREA", "ALUMNO",
        "FECHA_BAJA", "MOTIVO_CATEGORIA_RAW", "MOTIVO_CATEGORIA_STD", "MOTIVO_DETALLE"
    ] if c in df.columns]
    st.dataframe(df[cols] if cols else df, use_container_width=True, height=520)

    # 11) Reglas de homologación (para ajustes finos)
    with st.expander("🔧 Reglas actuales de homologación (MOTIVO_CATEGORIA_STD)"):
        st.write("Se toma el texto antes del '-' como categoría RAW y se mapea a una categoría homologada.")
        st.write("Si detectas que algo quedó mal agrupado, me dices el texto exacto y ajusto `_std_categoria()`.")

# =========================================================
# ✅ Helpers para integración con otros módulos (reprobación, etc.)
# =========================================================
def get_bajas_base_df() -> pd.DataFrame:
    """
    Devuelve el DF de bajas ya normalizado, listo para reutilizar:
      - AREA en upper
      - CICLO a int si aplica
      - MOTIVO_CATEGORIA_STD
    """
    df0 = _load_bajas_df()
    if df0 is None or df0.empty:
        return pd.DataFrame()

    df = df0.copy()

    # AREA
    if "AREA" in df.columns:
        df["AREA"] = df["AREA"].apply(_clean_upper)

    # CICLO
    if "CICLO" in df.columns:
        df["CICLO"] = df["CICLO"].apply(_to_int_safe)

    # MOTIVO
    if "MOTIVO_BAJA" in df.columns:
        df["MOTIVO_CATEGORIA_RAW"], df["MOTIVO_DETALLE"] = zip(*df["MOTIVO_BAJA"].apply(_split_motivo))
        df["MOTIVO_CATEGORIA_STD"] = df["MOTIVO_CATEGORIA_RAW"].apply(_std_categoria)
    else:
        df["MOTIVO_CATEGORIA_STD"] = "SIN ESPECIFICAR"

    return df


def resumen_bajas_por_filtros(ciclo: int | None, area: str | None) -> dict:
    """
    Retorna métricas rápidas (para embedding en otros módulos):
      - total: int
      - top_motivos: DataFrame (motivo, bajas) top 3
    """
    df = get_bajas_base_df()
    if df.empty:
        return {"total": 0, "top_motivos": pd.DataFrame(columns=["motivo", "bajas"])}

    if ciclo is not None and "CICLO" in df.columns:
        df = df[df["CICLO"] == ciclo].copy()

    if area and "AREA" in df.columns:
        df = df[df["AREA"] == str(area).upper()].copy()

    total = int(len(df))

    top = (
        df["MOTIVO_CATEGORIA_STD"]
        .value_counts()
        .head(3)
        .reset_index()
    )
    top.columns = ["motivo", "bajas"]

    return {"total": total, "top_motivos": top}
