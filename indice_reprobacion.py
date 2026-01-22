# indice_reprobacion.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread

from catalogos import mapear_carrera_id

# =========================================
# Config
# =========================================
SHEET_NAME_DEFAULT = "REPROBACION"  # puedes cambiarlo o dejarlo vacío para usar la primera pestaña


# =========================================
# Helpers
# =========================================
def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().upper() for c in out.columns]
    return out


def _pick_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return None


def _to_num(s):
    return pd.to_numeric(s, errors="coerce")


@st.cache_data(show_spinner=False, ttl=300)
def _load_reprobacion_from_gsheets(url: str, sheet_name: str | None = None) -> pd.DataFrame:
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    if sheet_name:
        try:
            ws = sh.worksheet(sheet_name)
        except Exception:
            ws = sh.sheet1
    else:
        ws = sh.sheet1

    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
    df = _norm_cols(df)

    # Normalizar CALIF FINAL (a veces viene con espacios)
    cal_col = _pick_col(df, ["CALIF FINAL", "CALIF_FINAL", "CALIFICACION FINAL", "CALIFICACIÓN FINAL"])
    if cal_col and cal_col != "CALIF_FINAL":
        df = df.rename(columns={cal_col: "CALIF_FINAL"})

    # Normalizar MATERIA
    mat_col = _pick_col(df, ["MATERIA", "ASIGNATURA"])
    if mat_col and mat_col != "MATERIA":
        df = df.rename(columns={mat_col: "MATERIA"})

    # Normalizar AREA (carrera/servicio)
    area_col = _pick_col(df, ["AREA", "CARRERA", "SERVICIO"])
    if area_col and area_col != "AREA":
        df = df.rename(columns={area_col: "AREA"})

    # Normalizar MATRICULA
    mcol = _pick_col(df, ["MATRICULA", "MATRÍCULA"])
    if mcol and mcol != "MATRICULA":
        df = df.rename(columns={mcol: "MATRICULA"})

    # Normalizar CICLO
    ccol = _pick_col(df, ["CICLO", "CICLO_ESCOLAR"])
    if ccol and ccol != "CICLO":
        df = df.rename(columns={ccol: "CICLO"})

    return df


def _bar_top_materias(df_mat: pd.DataFrame, top_n: int = 20):
    if df_mat.empty:
        return None

    d = df_mat.sort_values("REPROBADOS_UNICOS", ascending=False).head(top_n).copy()
    d["MATERIA_WR"] = d["MATERIA"].astype(str)

    chart = (
        alt.Chart(d)
        .mark_bar()
        .encode(
            y=alt.Y("MATERIA_WR:N", sort="-x", title=None),
            x=alt.X("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
            tooltip=[
                alt.Tooltip("MATERIA_WR:N", title="Materia"),
                alt.Tooltip("REPROBADOS_REGISTROS:Q", title="Registros"),
                alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos"),
                alt.Tooltip("PROM_CALIF:Q", title="Prom. calif", format=".2f"),
            ],
        )
        .properties(height=max(360, min(1000, 22 * len(d))))
    )
    return chart


def _get_catalogo_carreras_df() -> pd.DataFrame:
    """
    Toma el catálogo maestro desde session_state.
    NOTA: app.py debe setear st.session_state["df_cat_carreras"] = df_cat_carreras
    """
    df_cat = st.session_state.get("df_cat_carreras")
    if df_cat is None or getattr(df_cat, "empty", True):
        return pd.DataFrame()
    return df_cat


def _compute_carrera_id_para_registro(area_text: str, df_cat: pd.DataFrame):
    if not area_text or df_cat.empty:
        return None
    try:
        return mapear_carrera_id(area_text, df_cat)
    except Exception:
        return None


# =========================================
# Render principal
# =========================================
def render_indice_reprobacion(vista: str | None = None, carrera: str | None = None):
    st.subheader("Índice de reprobación (base de reprobados)")

    st.info(
        "Esta base contiene registros de reprobación. Aquí verás **conteos y tendencias de reprobados**. "
        "El **índice (%) real** requiere el total de evaluados/inscritos por materia y ciclo."
    )

    if not vista:
        vista = "Dirección General"

    # URL desde secrets
    url = st.secrets.get("IR_URL", "").strip()
    if not url:
        st.error("Falta configurar `IR_URL` en Secrets (URL del Google Sheet de reprobación).")
        return

    sheet_name = st.secrets.get("IR_SHEET_NAME", SHEET_NAME_DEFAULT).strip() or None

    # Carga
    try:
        with st.spinner("Cargando datos de reprobación (Google Sheets)…"):
            df = _load_reprobacion_from_gsheets(url, sheet_name=sheet_name)
    except Exception as e:
        st.error("No se pudo cargar el Google Sheet de reprobación.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja está vacía o no trae datos.")
        return

    # Validación de columnas mínimas
    required = ["CICLO", "AREA", "MATERIA"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Faltan columnas requeridas: {', '.join(missing)}")
        st.caption(f"Columnas detectadas: {', '.join(df.columns)}")
        return

    # CALIF_FINAL es deseable pero no obligatoria
    if "CALIF_FINAL" in df.columns:
        df["CALIF_FINAL"] = _to_num(df["CALIF_FINAL"])

    # Limpieza ligera
    df["CICLO"] = df["CICLO"].astype(str).str.strip()
    df["AREA"] = df["AREA"].astype(str).str.strip()
    df["MATERIA"] = df["MATERIA"].astype(str).str.strip()
    if "MATRICULA" in df.columns:
        df["MATRICULA"] = df["MATRICULA"].astype(str).str.strip()

    # =========================================
    # Catálogo maestro y carrera_id
    # =========================================
    df_cat = _get_catalogo_carreras_df()

    # Creamos una columna calculada CARRERA_ID para cada registro (solo si hay catálogo)
    if not df_cat.empty:
        # Ojo: esto puede tardar si hay muchos registros, pero en reprobación suele ser manejable.
        # Si creciera, lo optimizamos con cache/memoización.
        df["CARRERA_ID"] = df["AREA"].apply(lambda x: _compute_carrera_id_para_registro(x, df_cat))
    else:
        df["CARRERA_ID"] = None

    # ---------------------------
    # Filtros (replicando patrón vista/carrera)
    # ---------------------------
    f = df.copy()

    # DG: filtros libres. DC: AREA fijo.
    if vista == "Dirección General":
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.8, 1.2])

        # ESCUELA y NIVEL si existen
        with c1:
            if "ESCUELA" in f.columns:
                esc_opts = ["(Todas)"] + sorted(f["ESCUELA"].dropna().astype(str).str.strip().unique().tolist())
                esc_sel = st.selectbox("Escuela", esc_opts, index=0)
            else:
                esc_sel = "(Todas)"

        with c2:
            if "NIVEL" in f.columns:
                niv_opts = ["(Todos)"] + sorted(f["NIVEL"].dropna().astype(str).str.strip().unique().tolist())
                niv_sel = st.selectbox("Nivel", niv_opts, index=0)
            else:
                niv_sel = "(Todos)"

        with c3:
            # Si hay catálogo, mostramos nombres oficiales para seleccionar
            if not df_cat.empty:
                nombres_oficiales = sorted(df_cat["nombre_oficial"].dropna().astype(str).unique().tolist())
                area_opts = ["(Todas)"] + nombres_oficiales
                area_sel = st.selectbox("Carrera/Servicio", area_opts, index=0)
            else:
                area_opts = ["(Todas)"] + sorted(f["AREA"].dropna().astype(str).str.strip().unique().tolist())
                area_sel = st.selectbox("Carrera/Servicio", area_opts, index=0)

        with c4:
            ciclo_opts = ["(Todos)"] + sorted(f["CICLO"].dropna().astype(str).str.strip().unique().tolist())
            ciclo_sel = st.selectbox("Ciclo", ciclo_opts, index=0)

        if "ESCUELA" in f.columns and esc_sel != "(Todas)":
            f = f[f["ESCUELA"].astype(str).str.strip() == esc_sel]
        if "NIVEL" in f.columns and niv_sel != "(Todos)":
            f = f[f["NIVEL"].astype(str).str.strip() == niv_sel]

        # Filtro por carrera (vía carrera_id si hay catálogo)
        if area_sel != "(Todas)":
            if not df_cat.empty:
                carrera_id_sel = mapear_carrera_id(area_sel, df_cat)
                if carrera_id_sel is not None:
                    f = f[f["CARRERA_ID"] == carrera_id_sel]
                else:
                    # si por alguna razón no mapea, cae a texto
                    f = f[f["AREA"].astype(str).str.strip() == area_sel]
            else:
                f = f[f["AREA"].astype(str).str.strip() == area_sel]

        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"].astype(str).str.strip() == ciclo_sel]

    else:
        # Director de carrera
        carrera_fix = (carrera or "").strip()
        st.text_input("Carrera (fija por vista)", value=carrera_fix, disabled=True)

        ciclo_opts = ["(Todos)"] + sorted(f["CICLO"].dropna().astype(str).str.strip().unique().tolist())
        ciclo_sel = st.selectbox("Ciclo", ciclo_opts, index=0)

        if not df_cat.empty:
            carrera_id_fix = mapear_carrera_id(carrera_fix, df_cat)
            if carrera_id_fix is not None:
                f = f[f["CARRERA_ID"] == carrera_id_fix]
            else:
                # fallback por texto si no mapea
                f = f[f["AREA"].astype(str).str.strip() == carrera_fix]
        else:
            f = f[f["AREA"].astype(str).str.strip() == carrera_fix]

        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"].astype(str).str.strip() == ciclo_sel]

    st.caption(f"Registros filtrados: **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    st.divider()

    # ---------------------------
    # KPIs
    # ---------------------------
    reprob_reg = len(f)
    alumnos_unicos = f["MATRICULA"].nunique() if "MATRICULA" in f.columns else pd.NA
    prom_calif = float(_to_num(f["CALIF_FINAL"]).mean()) if "CALIF_FINAL" in f.columns else pd.NA

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Reprobaciones (registros)", f"{reprob_reg:,}")
    c2.metric("Alumnos reprobados (únicos)", f"{alumnos_unicos:,}" if pd.notna(alumnos_unicos) else "—")
    c3.metric("Promedio calificación (reprobados)", f"{prom_calif:.2f}" if pd.notna(prom_calif) else "—")

    # Materia top
    top_materia = (
        f.groupby("MATERIA")["MATRICULA"].nunique().sort_values(ascending=False).index[0]
        if "MATRICULA" in f.columns and f["MATERIA"].notna().any()
        else (f["MATERIA"].value_counts().index[0] if f["MATERIA"].notna().any() else "—")
    )
    c4.metric("Materia con más reprobados", str(top_materia))

    st.divider()

    # ---------------------------
    # Tabla por materia
    # ---------------------------
    g = f.groupby("MATERIA", dropna=False)

    df_mat = pd.DataFrame({
        "MATERIA": g.size().index.astype(str),
        "REPROBADOS_REGISTROS": g.size().values,
    })

    if "MATRICULA" in f.columns:
        df_mat["REPROBADOS_UNICOS"] = g["MATRICULA"].nunique().values
    else:
        df_mat["REPROBADOS_UNICOS"] = df_mat["REPROBADOS_REGISTROS"]

    if "CALIF_FINAL" in f.columns:
        df_mat["PROM_CALIF"] = g["CALIF_FINAL"].apply(lambda s: _to_num(s).mean()).values
        df_mat["MIN_CALIF"] = g["CALIF_FINAL"].apply(lambda s: _to_num(s).min()).values
        df_mat["MAX_CALIF"] = g["CALIF_FINAL"].apply(lambda s: _to_num(s).max()).values
    else:
        df_mat["PROM_CALIF"] = pd.NA
        df_mat["MIN_CALIF"] = pd.NA
        df_mat["MAX_CALIF"] = pd.NA

    df_mat = df_mat.sort_values("REPROBADOS_UNICOS", ascending=False).reset_index(drop=True)

    st.markdown("### Reprobación por materia (ciclo seleccionado)")
    st.dataframe(df_mat, use_container_width=True)

    # ---------------------------
    # Gráfica comparativa (Top N)
    # ---------------------------
    st.markdown("### Comparativo de materias (Top)")
    top_n = st.slider("Top N materias", min_value=10, max_value=60, value=20, step=5)
    chart = _bar_top_materias(df_mat, top_n=top_n)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)

    st.divider()

    # ---------------------------
    # Histórico por materia (drill-down)
    # ---------------------------
    st.markdown("### Histórico por materia (reprobados por ciclo)")

    materia_sel = st.selectbox("Materia", ["(Selecciona)"] + df_mat["MATERIA"].tolist(), index=0)
    if materia_sel != "(Selecciona)":
        # Para histórico, usamos el df completo, pero respetando filtros de carrera (vía carrera_id si aplica)
        fh = df[df["MATERIA"].astype(str).str.strip() == str(materia_sel).strip()].copy()

        if vista != "Dirección General":
            if not df_cat.empty:
                carrera_id_fix = mapear_carrera_id((carrera or "").strip(), df_cat)
                if carrera_id_fix is not None:
                    fh = fh[fh["CARRERA_ID"] == carrera_id_fix]
                else:
                    fh = fh[fh["AREA"].astype(str).str.strip() == (carrera or "").strip()]
            else:
                fh = fh[fh["AREA"].astype(str).str.strip() == (carrera or "").strip()]
        else:
            # En DG usamos f para mantener consistencia con filtros actuales
            fh = f[f["MATERIA"].astype(str).str.strip() == str(materia_sel).strip()].copy()

        if fh.empty:
            st.info("No hay datos históricos para esa materia con los filtros actuales.")
            return

        if "MATRICULA" in fh.columns:
            hist = fh.groupby("CICLO")["MATRICULA"].nunique().reset_index(name="REPROBADOS_UNICOS")
        else:
            hist = fh.groupby("CICLO").size().reset_index(name="REPROBADOS_UNICOS")

        # Orden por ciclo (numérico si se puede)
        hist["CICLO_NUM"] = pd.to_numeric(hist["CICLO"], errors="coerce")
        hist = hist.sort_values(["CICLO_NUM", "CICLO"]).drop(columns=["CICLO_NUM"])

        line = (
            alt.Chart(hist)
            .mark_line(point=True)
            .encode(
                x=alt.X("CICLO:N", title="Ciclo", sort=None),
                y=alt.Y("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
                tooltip=[
                    alt.Tooltip("CICLO:N", title="Ciclo"),
                    alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos"),
                ],
            )
            .properties(height=360)
        )
        st.altair_chart(line, use_container_width=True)
        st.dataframe(hist, use_container_width=True)
