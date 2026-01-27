# bajas_retencion.py
import streamlit as st
import pandas as pd
import gspread
import json
import re
from google.oauth2.service_account import Credentials

# ========= AJUSTA ESTO =========
BAJAS_SHEET_URL = "https://docs.google.com/spreadsheets/d/11-QVSp2zvRtsy3RA82N9j7g8zNzJGDKJqAIH9sabiUU/edit?gid=1444259240#gid=1444259240"
BAJAS_TAB_NAME = "BAJAS"  # cambia si tu pestaña se llama distinto
# ===============================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

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

@st.cache_data(ttl=300, show_spinner=False)
def _load_bajas_df() -> pd.DataFrame:
    if "PEGA_AQUI" in BAJAS_SHEET_URL:
        return pd.DataFrame()

    gc = _get_gspread_client()
    sh = gc.open_by_key(_extract_sheet_id(BAJAS_SHEET_URL))
    ws = sh.worksheet(BAJAS_TAB_NAME)

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

def _split_motivo(x: str):
    s = (x or "").strip()
    if not s:
        return ("", "")
    parts = s.split("-", 1)
    cat = parts[0].strip().upper()
    det = parts[1].strip() if len(parts) > 1 else ""
    return (cat, det)

def _clean_upper(x: str) -> str:
    s = (x or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()

def render_bajas_retencion(vista: str, carrera: str | None):
    st.subheader("Bajas / Retención")
    st.caption("Modo prueba (solo lectura).")

    df = _load_bajas_df()
    if df.empty:
        st.warning("No hay datos cargados. Revisa BAJAS_SHEET_URL y BAJAS_TAB_NAME en `bajas_retencion.py`.")
        return

    # columnas esperadas
    col_area = "AREA" if "AREA" in df.columns else None
    col_motivo = "MOTIVO_BAJA" if "MOTIVO_BAJA" in df.columns else None
    col_ciclo = "CICLO" if "CICLO" in df.columns else None
    col_grupo = "GRUPO" if "GRUPO" in df.columns else None

    if not col_motivo:
        st.error("No encontré la columna MOTIVO_BAJA.")
        st.write("Columnas detectadas:", list(df.columns))
        return

    if col_area:
        df[col_area] = df[col_area].apply(_clean_upper)

    df["MOTIVO_CATEGORIA"], df["MOTIVO_DETALLE"] = zip(*df[col_motivo].apply(_split_motivo))
    df["MOTIVO_CATEGORIA"] = df["MOTIVO_CATEGORIA"].apply(_clean_upper)

    # filtro DC por carrera
    if carrera and col_area:
        df = df[df[col_area] == str(carrera).upper()].copy()

    st.metric("Bajas registradas (filtrado)", f"{len(df):,}")

    st.markdown("### Top motivos (categoría)")
    st.dataframe(df["MOTIVO_CATEGORIA"].value_counts().head(10).rename("conteo"), use_container_width=True)

    if col_ciclo and col_area:
        st.markdown("### Resumen por ciclo y área")
        resumen = (
            df.groupby([col_ciclo, col_area], dropna=False)
              .size()
              .reset_index(name="bajas")
              .sort_values("bajas", ascending=False)
        )
        st.dataframe(resumen, use_container_width=True)

    if col_grupo:
        st.markdown("### Top grupos con más bajas")
        topg = df[col_grupo].value_counts().head(15).rename("bajas")
        st.dataframe(topg, use_container_width=True)

    st.markdown("### Casos (detalle)")
    cols = [c for c in ["CICLO", "CICLO INGRESO", "TIPO", "NIVEL", "AREA", "GRUPO", "ALUMNO", "FECHA_BAJA", "MOTIVO_CATEGORIA", "MOTIVO_DETALLE"] if c in df.columns]
    st.dataframe(df[cols] if cols else df, use_container_width=True, height=420)
