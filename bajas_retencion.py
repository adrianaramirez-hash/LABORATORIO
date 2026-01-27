# bajas_retencion.py
import streamlit as st
import pandas as pd
import gspread
import json
import re
import altair as alt
from collections.abc import Mapping
from google.oauth2.service_account import Credentials

# ============================================================
# Config (desde Secrets)
# ============================================================
# Secrets esperados:
#   BAJAS_URL = "https://docs.google.com/spreadsheets/d/....../edit"
#   BAJAS_SHEET_NAME = "BAJAS"  (opcional, default "BAJAS")
BAJAS_SHEET_URL = (st.secrets.get("BAJAS_URL", "") or "").strip()
BAJAS_TAB_NAME = (st.secrets.get("BAJAS_SHEET_NAME", "BAJAS") or "BAJAS").strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Unidades compactadas (si llegan desde DC)
UNIDADES_COMPACTADAS = {"EDN", "ECDG", "EJEC"}

# ============================================================
# Helpers base (conexión + carga)
# ============================================================
def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9-_]+)", url or "")
    if not m:
        raise ValueError("No pude extraer el ID del Google Sheet desde la URL.")
    return m.group(1)

def _load_creds_dict() -> dict:
    raw = st.secrets["gcp_service_account_json"]
    if isinstance(raw, Mapping):
        return dict(raw)
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
    if not BAJAS_SHEET_URL:
        raise ValueError("Falta configurar BAJAS_URL en Secrets.")
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
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df

@st.cache_data(ttl=300, show_spinner=False)
def _load_bajas_df() -> pd.DataFrame:
    sh = _open_sheet()
    ws = _resolve_ws(sh, BAJAS_TAB_NAME)
    return _load_from_ws(ws)

# ============================================================
# Normalización estilo "Servicio_norm" (tu patrón)
# ============================================================
def normalizar_texto(valor) -> str:
    """strip + lower + colapsa espacios; elimina NBSP."""
    if pd.isna(valor):
        return ""
    s = str(valor).replace("\u00A0", " ").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

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

# ============================================================
# Base normalizada y funciones consumibles por otros módulos
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def get_bajas_base_df() -> pd.DataFrame:
    """
    Regresa BAJAS ya normalizada:
      - CICLO int (si existe)
      - AREA_norm (para comparar robusto)
      - MOTIVO_CATEGORIA_STD (homologada)
      - FECHA_BAJA_DT y MES (si existe fecha)
    """
    df0 = _load_bajas_df()
    if df0 is None or df0.empty:
        return pd.DataFrame()

    df = df0.copy()

    # Columnas esperadas (defensivo)
    col_ciclo = "CICLO" if "CICLO" in df.columns else None
    col_area = "AREA" if "AREA" in df.columns else None
    col_fecha = "FECHA_BAJA" if "FECHA_BAJA" in df.columns else None
    col_motivo = "MOTIVO_BAJA" if "MOTIVO_BAJA" in df.columns else None

    if col_area:
        df["AREA_norm"] = df[col_area].apply(normalizar_texto)
        df[col_area] = df[col_area].astype(str).str.strip()
    else:
        df["AREA_norm"] = ""

    if col_motivo:
        df["MOTIVO_CATEGORIA_RAW"], df["MOTIVO_DETALLE"] = zip(*df[col_motivo].fillna("").astype(str).apply(_split_motivo))
        df["MOTIVO_CATEGORIA_STD"] = df["MOTIVO_CATEGORIA_RAW"].apply(_std_categoria)
    else:
        df["MOTIVO_CATEGORIA_RAW"] = ""
        df["MOTIVO_DETALLE"] = ""
        df["MOTIVO_CATEGORIA_STD"] = "SIN ESPECIFICAR"

    if col_ciclo:
        df[col_ciclo] = df[col_ciclo].apply(_to_int_safe)

    if col_fecha:
        df["FECHA_BAJA_DT"] = df[col_fecha].apply(_parse_fecha)
        df["MES"] = df["FECHA_BAJA_DT"].dt.to_period("M").astype(str)
    else:
        df["FECHA_BAJA_DT"] = pd.NaT
        df["MES"] = ""

    return df

def resumen_bajas_por_filtros(ciclo: int | None = None, area: str | None = None) -> dict:
    """
    Resumen compacto para integrarlo en otros módulos (ej. reprobación).
    Usa normalización tipo Servicio_norm.
    """
    df = get_bajas_base_df()
    if df is None or df.empty:
        return {"ok": True, "n": 0, "nota": "Sin datos de BAJAS.", "top_motivos": pd.DataFrame()}

    x = df.copy()

    # Filtro ciclo
    if ciclo is not None and "CICLO" in x.columns:
        x = x[pd.to_numeric(x["CICLO"], errors="coerce") == ciclo]

    nota = []

    # Filtro area (robusto)
    area_txt = (area or "").strip()
    if area_txt:
        # Si llega una unidad compactada, no se puede mapear sin catálogo
        if area_txt.strip().upper() in UNIDADES_COMPACTADAS:
            nota.append(f"AREA='{area_txt}' es unidad compactada (EDN/ECDG/EJEC); se muestra agregado sin filtrar por AREA.")
        else:
            area_norm = normalizar_texto(area_txt)
            if area_norm:
                x = x[x["AREA_norm"] == area_norm]
                nota.append("Filtrado por AREA usando normalización (AREA_norm).")

    n = len(x)

    top = pd.DataFrame(columns=["Motivo (cat.)", "Bajas"])
    if n > 0 and "MOTIVO_CATEGORIA_STD" in x.columns:
        s = x["MOTIVO_CATEGORIA_STD"].astype(str).str.strip()
        s = s[s != ""]
        if not s.empty:
            top = s.value_counts().head(8).reset_index()
            top.columns = ["Motivo (cat.)", "Bajas"]

    return {"ok": True, "n": n, "nota": " | ".join(nota) if nota else "", "top_motivos": top}

# ============================================================
# Render principal (tu módulo Bajas / Retención)
# ============================================================
def render_bajas_retencion(vista: str, carrera: str | None):
    st.subheader("Bajas / Retención")
    st.caption("Vista analítica (solo lectura).")

    try:
        df = get_bajas_base_df()
    except Exception as e:
        st.error("No pude cargar la información de Bajas. Revisa Secrets (BAJAS_URL) y permisos.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La pestaña BAJAS está vacía o no tiene datos.")
        return

    col_area = "AREA" if "AREA" in df.columns else None
    col_ciclo = "CICLO" if "CICLO" in df.columns else None

    # ============================
    # Filtros arriba (sin grupo)
    # ============================
    st.markdown("### Filtros")

    areas = sorted([a for a in df[col_area].dropna().astype(str).unique().tolist() if str(a).strip()]) if col_area else []
    ciclos = sorted([c for c in df[col_ciclo].dropna().unique().tolist() if pd.notna(c)]) if col_ciclo else []
    cats = sorted(df["MOTIVO_CATEGORIA_STD"].dropna().unique().tolist())

    carrera_norm = None
    if vista == "Director de carrera" and carrera and col_area:
        carrera_norm = normalizar_texto(carrera)

    cA, cC, cM = st.columns([2.4, 1.2, 2.0])

    if carrera_norm:
        with cA:
            st.markdown(f"**Área:** {carrera} (vista Director de carrera)")
        area_sel = "(director)"
    else:
        with cA:
            area_sel = st.selectbox("Área", options=["(Todas)"] + areas, index=0) if col_area else "(Todas)"

    with cC:
        ciclo_sel = st.selectbox("Ciclo", options=["(Todos)"] + ciclos, index=0) if col_ciclo else "(Todos)"

    with cM:
        cat_sel = st.selectbox("Motivo (categoría)", options=["(Todos)"] + cats, index=0)

    # ============================
    # Aplicar filtros (robustos)
    # ============================
    f = df.copy()

    if carrera_norm:
        # filtro robusto como tu ejemplo Servicio_norm
        f = f[f["AREA_norm"] == carrera_norm]
    elif col_area and area_sel != "(Todas)":
        f = f[f[col_area].astype(str).str.strip() == str(area_sel).strip()]

    if col_ciclo and ciclo_sel != "(Todos)":
        f = f[pd.to_numeric(f[col_ciclo], errors="coerce") == ciclo_sel]

    if cat_sel != "(Todos)":
        f = f[f["MOTIVO_CATEGORIA_STD"] == cat_sel]

    st.divider()

    # ============================
    # KPIs
    # ============================
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Bajas (filtrado)", f"{len(f):,}")
    k2.metric("Motivos únicos", f"{f['MOTIVO_CATEGORIA_STD'].nunique():,}")
    k3.metric("Áreas presentes", f"{f[col_area].nunique():,}" if col_area else "—")
    k4.metric("Ciclos presentes", f"{f[col_ciclo].nunique():,}" if col_ciclo else "—")

    # ============================
    # Tendencia
    # ============================
    st.markdown("### Tendencia")
    if f["FECHA_BAJA_DT"].notna().any():
        ts = (
            f.dropna(subset=["FECHA_BAJA_DT"])
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
    elif col_ciclo and f[col_ciclo].notna().any():
        ts = (
            f.dropna(subset=[col_ciclo])
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

    # ============================
    # Motivos (categoría)
    # ============================
    st.markdown("### Motivos de baja (categoría homologada)")
    vc = f["MOTIVO_CATEGORIA_STD"].value_counts().reset_index()
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

    # ============================
    # Resumen ciclo–área
    # ============================
    if col_ciclo and col_area:
        st.markdown("### Resumen por ciclo y área")
        resumen = (
            f.groupby([col_ciclo, col_area], dropna=False)
            .size()
            .reset_index(name="bajas")
            .sort_values("bajas", ascending=False)
        )
        st.dataframe(resumen, use_container_width=True)

    # ============================
    # Depuración “OTROS”
    # ============================
    st.markdown("### Depuración: detalles más repetidos (solo OTROS)")
    otros = f[f["MOTIVO_CATEGORIA_STD"] == "OTROS"].copy()
    if not otros.empty:
        det = otros["MOTIVO_DETALLE"].astype(str).str.strip()
        det = det[det != ""]
        if not det.empty:
            st.dataframe(det.value_counts().head(25).rename("conteo"), use_container_width=True)
        else:
            st.caption("OTROS no tiene detalles capturados.")
    else:
        st.caption("No hay registros OTROS en el filtro actual.")

    # ============================
    # Tabla de casos
    # ============================
    st.markdown("### Casos (detalle)")
    cols = [c for c in [
        "CICLO", "CICLO INGRESO", "TIPO", "NIVEL", "AREA", "ALUMNO",
        "FECHA_BAJA", "MOTIVO_CATEGORIA_RAW", "MOTIVO_CATEGORIA_STD", "MOTIVO_DETALLE"
    ] if c in f.columns]
    st.dataframe(f[cols] if cols else f, use_container_width=True, height=520)

    with st.expander("🔧 Reglas actuales de homologación (MOTIVO_CATEGORIA_STD)"):
        st.write("Se toma el texto antes del '-' como categoría RAW y se mapea a una categoría homologada.")
        st.write("Si detectas que algo quedó mal agrupado, dime el texto exacto y ajusto `_std_categoria()`.")
