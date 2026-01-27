# bajas_retencion.py
import streamlit as st
import pandas as pd
import gspread
import json
import re
import altair as alt
from google.oauth2.service_account import Credentials

# ========= AJUSTA SOLO ESTO =========
BAJAS_SHEET_URL = "PEGA_AQUI_LA_URL_DEL_SHEET_DE_BAJAS"
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
    """
    Espera: CATEGORIA - detalle
    Si no hay '-', la categoría será el texto y el detalle vacío.
    """
    s = (x or "").strip()
    if not s:
        return ("", "")
    parts = s.split("-", 1)
    cat = _clean_upper(parts[0])
    det = parts[1].strip() if len(parts) > 1 else ""
    return (cat, det)

def _std_categoria(cat_raw: str) -> str:
    """
    Homologación mínima (puedes ajustar después).
    La idea: reducir variabilidad de escritura para dashboard.
    """
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
    """
    Soporta formatos tipo: 25-jul-19, 3-dic-19, 2019-07-25, etc.
    """
    s = str(x or "").strip()
    if not s:
        return pd.NaT
    s = s.replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s)

    # Intento 1: parse libre
    dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if pd.notna(dt):
        return dt

    # Intento 2: normalizar meses en español (abreviados comunes)
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

    # 1) Carga
    try:
        df = _load_bajas_df()
    except Exception as e:
        st.error("No pude cargar la información de Bajas. Revisa URL, permisos o nombre de pestaña.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La pestaña está vacía o no tiene datos.")
        return

    # 2) Columnas esperadas (defensivo)
    col_ciclo = "CICLO" if "CICLO" in df.columns else None
    col_area = "AREA" if "AREA" in df.columns else None
    col_grupo = "GRUPO" if "GRUPO" in df.columns else None
    col_fecha = "FECHA_BAJA" if "FECHA_BAJA" in df.columns else None
    col_motivo = "MOTIVO_BAJA" if "MOTIVO_BAJA" in df.columns else None

    if not col_motivo:
        st.error("No encontré la columna MOTIVO_BAJA en esta pestaña.")
        st.write("Columnas detectadas:", list(df.columns))
        return

    # 3) Normalización base
    if col_area:
        df[col_area] = df[col_area].apply(_clean_upper)

    df["MOTIVO_CATEGORIA_RAW"], df["MOTIVO_DETALLE"] = zip(*df[col_motivo].apply(_split_motivo))
    df["MOTIVO_CATEGORIA_STD"] = df["MOTIVO_CATEGORIA_RAW"].apply(_std_categoria)

    if col_ciclo:
        df[col_ciclo] = df[col_ciclo].apply(_to_int_safe)

    if col_fecha:
        df["FECHA_BAJA_DT"] = df[col_fecha].apply(_parse_fecha)
        df["ANIO"] = df["FECHA_BAJA_DT"].dt.year
        df["MES"] = df["FECHA_BAJA_DT"].dt.to_period("M").astype(str)
    else:
        df["FECHA_BAJA_DT"] = pd.NaT
        df["ANIO"] = pd.NA
        df["MES"] = ""

    # 4) Filtros (sidebar)
    st.sidebar.markdown("## Filtros — Bajas")

    # Filtro por área: DG elige; DC se fuerza por carrera (si viene)
    if col_area:
        areas = sorted([a for a in df[col_area].dropna().unique().tolist() if str(a).strip()])
    else:
        areas = []

    if carrera and col_area:
        df = df[df[col_area] == str(carrera).upper()].copy()
        st.sidebar.info(f"Área fija (DC): {str(carrera).upper()}")
        area_sel = str(carrera).upper()
    else:
        area_sel = None
        if col_area and areas:
            area_sel = st.sidebar.selectbox("Área", options=["(Todas)"] + areas, index=0)
            if area_sel != "(Todas)":
                df = df[df[col_area] == area_sel].copy()

    # Ciclo
    if col_ciclo:
        ciclos = sorted([c for c in df[col_ciclo].dropna().unique().tolist() if pd.notna(c)])
        ciclo_sel = st.sidebar.selectbox("Ciclo", options=["(Todos)"] + ciclos, index=0)
        if ciclo_sel != "(Todos)":
            df = df[df[col_ciclo] == ciclo_sel].copy()
    else:
        ciclo_sel = None

    # Grupo
    if col_grupo and not df.empty:
        grupos = sorted([g for g in df[col_grupo].dropna().unique().tolist() if str(g).strip()])
        grupo_sel = st.sidebar.selectbox("Grupo", options=["(Todos)"] + grupos, index=0)
        if grupo_sel != "(Todos)":
            df = df[df[col_grupo] == grupo_sel].copy()
    else:
        grupo_sel = None

    # Categoría motivo
    cats = sorted(df["MOTIVO_CATEGORIA_STD"].dropna().unique().tolist())
    cat_sel = st.sidebar.selectbox("Motivo (categoría)", options=["(Todos)"] + cats, index=0)
    if cat_sel != "(Todos)":
        df = df[df["MOTIVO_CATEGORIA_STD"] == cat_sel].copy()

    # 5) KPIs
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Bajas (filtrado)", f"{len(df):,}")
    with c2:
        st.metric("Motivos únicos", f"{df['MOTIVO_CATEGORIA_STD'].nunique():,}")
    with c3:
        st.metric("Grupos con bajas", f"{df[col_grupo].nunique():,}" if col_grupo else "—")
    with c4:
        st.metric("Áreas presentes", f"{df[col_area].nunique():,}" if col_area else "—")

    # 6) Tendencia (por mes si hay fecha; si no, por ciclo)
    st.markdown("### Tendencia")
    if df["FECHA_BAJA_DT"].notna().any():
        ts = (
            df.dropna(subset=["FECHA_BAJA_DT"])
              .groupby("MES")
              .size()
              .reset_index(name="bajas")
              .sort_values("MES")
        )
        chart = (
            alt.Chart(ts)
            .mark_line(point=True)
            .encode(x="MES:N", y="bajas:Q", tooltip=["MES:N", "bajas:Q"])
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
        chart = (
            alt.Chart(ts)
            .mark_line(point=True)
            .encode(x=f"{col_ciclo}:O", y="bajas:Q", tooltip=[f"{col_ciclo}:O", "bajas:Q"])
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No hay FECHA_BAJA o CICLO usable para tendencia.")

    # 7) Pareto de motivos (categoría)
    st.markdown("### Pareto de motivos (categoría homologada)")
    vc = df["MOTIVO_CATEGORIA_STD"].value_counts().reset_index()
    vc.columns = ["motivo", "bajas"]
    if not vc.empty:
        vc["%"] = (vc["bajas"] / vc["bajas"].sum()) * 100
        vc["%_acum"] = vc["%"].cumsum()

        st.dataframe(vc, use_container_width=True)

        pareto = alt.Chart(vc).mark_bar().encode(
            x=alt.X("motivo:N", sort="-y", title="Motivo"),
            y=alt.Y("bajas:Q", title="Bajas"),
            tooltip=["motivo:N", "bajas:Q", alt.Tooltip("%:Q", format=".1f"), alt.Tooltip("%_acum:Q", format=".1f")]
        )
        st.altair_chart(pareto, use_container_width=True)

    # 8) Top grupos y resumen por área/ciclo
    if col_grupo:
        st.markdown("### Top grupos con más bajas")
        top_g = df[col_grupo].value_counts().head(20).rename("bajas").reset_index()
        top_g.columns = ["grupo", "bajas"]
        st.dataframe(top_g, use_container_width=True)

    if col_ciclo and col_area:
        st.markdown("### Resumen por ciclo y área")
        resumen = (
            df.groupby([col_ciclo, col_area], dropna=False)
              .size()
              .reset_index(name="bajas")
              .sort_values("bajas", ascending=False)
        )
        st.dataframe(resumen, use_container_width=True)

    # 9) “OTROS” -> detalles más repetidos (para depurar categoría)
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

    # 10) Tabla de casos (detalle)
    st.markdown("### Casos (detalle)")
    cols = [c for c in [
        "CICLO", "CICLO INGRESO", "TIPO", "NIVEL", "AREA", "GRUPO", "ALUMNO",
        "FECHA_BAJA", "MOTIVO_CATEGORIA_RAW", "MOTIVO_CATEGORIA_STD", "MOTIVO_DETALLE"
    ] if c in df.columns]
    st.dataframe(df[cols] if cols else df, use_container_width=True, height=520)

    # 11) Catálogo de homologación visible (para ajustar)
    with st.expander("🔧 Cómo se está homologando MOTIVO_CATEGORIA (referencia)"):
        st.write("Se toma el texto antes del '-' como categoría RAW y se mapea a una categoría STD.")
        st.write("Si ves categorías mal agrupadas, me dices cuáles y ajusto las reglas en `_std_categoria()`.")
