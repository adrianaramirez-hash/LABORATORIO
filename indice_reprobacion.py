# indice_reprobacion.py
import pandas as pd
import streamlit as st
import altair as alt
import gspread

import bajas_retencion  # ✅ nuevo (para traer resumen de bajas)

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


def _ciclo_to_int(x) -> int | None:
    """Convierte '201' -> 201; '(Todos)' -> None; si no se puede -> None."""
    if x is None:
        return None
    s = str(x).strip()
    if not s or s == "(Todos)":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _user_can_see_bajas() -> bool:
    """Muestra el bloque solo si el usuario tiene ALL o el módulo bajas_retencion."""
    if bool(st.session_state.get("user_allow_all", False)):
        return True
    mods = st.session_state.get("user_modulos", set())
    try:
        return "bajas_retencion" in set(mods)
    except Exception:
        return False


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


def _bar_top_carreras(df_area: pd.DataFrame, top_n: int = 20):
    if df_area.empty:
        return None
    d = df_area.sort_values("REPROBADOS_UNICOS", ascending=False).head(top_n).copy()
    d["AREA_WR"] = d["AREA"].astype(str)

    chart = (
        alt.Chart(d)
        .mark_bar()
        .encode(
            y=alt.Y("AREA_WR:N", sort="-x", title=None),
            x=alt.X("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
            tooltip=[
                alt.Tooltip("AREA_WR:N", title="Carrera/Servicio"),
                alt.Tooltip("REPROBADOS_REGISTROS:Q", title="Registros"),
                alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos"),
                alt.Tooltip("PROM_CALIF:Q", title="Prom. calif", format=".2f"),
            ],
        )
        .properties(height=max(360, min(1000, 22 * len(d))))
    )
    return chart


def _bar_top_materias_area(df_mat_area: pd.DataFrame, top_n: int = 20):
    if df_mat_area.empty:
        return None
    d = df_mat_area.sort_values("REPROBADOS_UNICOS", ascending=False).head(top_n).copy()
    d["MAT_LABEL"] = d["MATERIA"].astype(str) + "  —  " + d["AREA"].astype(str)

    chart = (
        alt.Chart(d)
        .mark_bar()
        .encode(
            y=alt.Y("MAT_LABEL:N", sort="-x", title=None),
            x=alt.X("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
            tooltip=[
                alt.Tooltip("AREA:N", title="Carrera/Servicio"),
                alt.Tooltip("MATERIA:N", title="Materia"),
                alt.Tooltip("REPROBADOS_REGISTROS:Q", title="Registros"),
                alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos"),
                alt.Tooltip("PROM_CALIF:Q", title="Prom. calif", format=".2f"),
            ],
        )
        .properties(height=max(360, min(1200, 20 * len(d))))
    )
    return chart


# =========================================
# Render principal
# =========================================
def render_indice_reprobacion(vista: str | None = None, carrera: str | None = None):
    st.subheader("Índice de reprobación (base de reprobados)")

    st.info(
        "Esta base contiene registros de reprobación. "
        "Aquí verás **conteos y tendencias de reprobados**."
    )

    if not vista:
        vista = "Dirección General"

    url = st.secrets.get("IR_URL", "").strip()
    if not url:
        st.error("Falta configurar `IR_URL` en Secrets (URL del Google Sheet de reprobación).")
        return

    sheet_name = st.secrets.get("IR_SHEET_NAME", SHEET_NAME_DEFAULT).strip() or None

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

    required = ["CICLO", "AREA", "MATERIA"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Faltan columnas requeridas: {', '.join(missing)}")
        st.caption(f"Columnas detectadas: {', '.join(df.columns)}")
        return

    if "CALIF_FINAL" in df.columns:
        df["CALIF_FINAL"] = _to_num(df["CALIF_FINAL"])

    df["CICLO"] = df["CICLO"].astype(str).str.strip()
    df["AREA"] = df["AREA"].astype(str).str.strip()
    df["MATERIA"] = df["MATERIA"].astype(str).str.strip()
    if "MATRICULA" in df.columns:
        df["MATRICULA"] = df["MATRICULA"].astype(str).str.strip()

    # ---------------------------
    # Filtros
    # ---------------------------
    f = df.copy()

    ciclo_sel = "(Todos)"
    area_sel = "(Todas)"
    carrera_fix = (carrera or "").strip()

    if vista == "Dirección General":
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.8, 1.2])

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
            area_opts = ["(Todas)"] + sorted(f["AREA"].dropna().astype(str).str.strip().unique().tolist())
            area_sel = st.selectbox("Carrera/Servicio", area_opts, index=0)

        with c4:
            ciclo_opts = ["(Todos)"] + sorted(f["CICLO"].dropna().astype(str).str.strip().unique().tolist())
            ciclo_sel = st.selectbox("Ciclo", ciclo_opts, index=0)

        if "ESCUELA" in f.columns and esc_sel != "(Todas)":
            f = f[f["ESCUELA"].astype(str).str.strip() == esc_sel]
        if "NIVEL" in f.columns and niv_sel != "(Todos)":
            f = f[f["NIVEL"].astype(str).str.strip() == niv_sel]
        if area_sel != "(Todas)":
            f = f[f["AREA"].astype(str).str.strip() == area_sel]
        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"].astype(str).str.strip() == ciclo_sel]

    else:
        st.text_input("Carrera (fija por vista)", value=carrera_fix, disabled=True)

        ciclo_opts = ["(Todos)"] + sorted(f["CICLO"].dropna().astype(str).str.strip().unique().tolist())
        ciclo_sel = st.selectbox("Ciclo", ciclo_opts, index=0)

        f = f[f["AREA"].astype(str).str.strip() == carrera_fix]
        if ciclo_sel != "(Todos)":
            f = f[f["CICLO"].astype(str).str.strip() == ciclo_sel]

    st.caption(f"Registros filtrados: **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # ===========================
    # ✅ NUEVO: Resumen de Bajas (contexto)
    # ===========================
    if _user_can_see_bajas():
        ciclo_int = _ciclo_to_int(ciclo_sel)  # None si (Todos) o no numérico

        if vista == "Dirección General":
            area_ctx = None if area_sel == "(Todas)" else area_sel
        else:
            area_ctx = carrera_fix or None

        with st.expander("📌 Resumen de bajas (contexto)", expanded=True):
            try:
                res = bajas_retencion.resumen_bajas_por_filtros(ciclo=ciclo_int, area=area_ctx)
                st.metric("Bajas (mismo ciclo/área)", f"{res.get('total', 0):,}")

                top = res.get("top_motivos")
                if top is not None and not top.empty:
                    st.markdown("**Top motivos (categoría)**")
                    st.dataframe(top, use_container_width=True)
                else:
                    st.caption("Sin datos de motivos para este filtro.")
            except Exception as e:
                st.error("No pude calcular el resumen de bajas. Revisa BAJAS_SHEET_URL / permisos / pestaña.")
                st.exception(e)

    st.divider()

    # ---------------------------
    # KPIs
    # ---------------------------
    reprob_reg = len(f)
    alumnos_unicos = f["MATRICULA"].nunique() if "MATRICULA" in f.columns else pd.NA
    prom_calif = float(_to_num(f["CALIF_FINAL"]).mean()) if "CALIF_FINAL" in f.columns else pd.NA

    c1, c2, c3 = st.columns(3)
    c1.metric("Reprobaciones (registros)", f"{reprob_reg:,}")
    c2.metric("Alumnos reprobados (únicos)", f"{alumnos_unicos:,}" if pd.notna(alumnos_unicos) else "—")
    c3.metric("Promedio calificación (reprobados)", f"{prom_calif:.2f}" if pd.notna(prom_calif) else "—")

    st.divider()

    # =========================================================
    # 1) COMPARATIVO ENTRE CARRERAS (AREA) — mayor a menor
    # =========================================================
    st.markdown("## Comparativo por carrera/servicio (AREA)")

    gA = f.groupby("AREA", dropna=False)

    df_area = pd.DataFrame({
        "AREA": gA.size().index.astype(str),
        "REPROBADOS_REGISTROS": gA.size().values,
    })

    if "MATRICULA" in f.columns:
        df_area["REPROBADOS_UNICOS"] = gA["MATRICULA"].nunique().values
    else:
        df_area["REPROBADOS_UNICOS"] = df_area["REPROBADOS_REGISTROS"]

    if "CALIF_FINAL" in f.columns:
        df_area["PROM_CALIF"] = gA["CALIF_FINAL"].apply(lambda s: _to_num(s).mean()).values
    else:
        df_area["PROM_CALIF"] = pd.NA

    df_area = df_area.sort_values("REPROBADOS_UNICOS", ascending=False).reset_index(drop=True)

    top_area = df_area["AREA"].iloc[0] if not df_area.empty else "—"
    top_area_val = df_area["REPROBADOS_UNICOS"].iloc[0] if not df_area.empty else pd.NA

    cA1, cA2 = st.columns([2, 1])
    cA1.metric("Carrera/Servicio con mayor reprobación", str(top_area))
    cA2.metric("Reprobados (únicos)", f"{int(top_area_val):,}" if pd.notna(top_area_val) else "—")

    st.dataframe(df_area, use_container_width=True)

    st.markdown("### Top carreras/servicios (comparativo)")
    top_n_area = st.slider("Top N carreras", min_value=5, max_value=50, value=20, step=5)
    chart_area = _bar_top_carreras(df_area, top_n=top_n_area)
    if chart_area is not None:
        st.altair_chart(chart_area, use_container_width=True)

    st.divider()

    # =========================================================
    # 2) MATERIAS DE LA CARRERA CON MAYOR REPROBACIÓN
    # =========================================================
    st.markdown("## Materias con mayor reprobación dentro de una carrera")

    area_sel2 = st.selectbox(
        "Selecciona carrera/servicio (por default: la de mayor reprobación)",
        options=df_area["AREA"].tolist() if not df_area.empty else [],
        index=0 if not df_area.empty else 0,
    )

    ff = f[f["AREA"].astype(str).str.strip() == str(area_sel2).strip()].copy()
    if ff.empty:
        st.info("No hay registros para esa carrera con los filtros actuales.")
        return

    gM = ff.groupby(["AREA", "MATERIA"], dropna=False)

    df_mat_area = pd.DataFrame({
        "AREA": [i[0] for i in gM.size().index],
        "MATERIA": [i[1] for i in gM.size().index],
        "REPROBADOS_REGISTROS": gM.size().values,
    })

    if "MATRICULA" in ff.columns:
        df_mat_area["REPROBADOS_UNICOS"] = gM["MATRICULA"].nunique().values
    else:
        df_mat_area["REPROBADOS_UNICOS"] = df_mat_area["REPROBADOS_REGISTROS"]

    if "CALIF_FINAL" in ff.columns:
        df_mat_area["PROM_CALIF"] = gM["CALIF_FINAL"].apply(lambda s: _to_num(s).mean()).values
        df_mat_area["MIN_CALIF"] = gM["CALIF_FINAL"].apply(lambda s: _to_num(s).min()).values
        df_mat_area["MAX_CALIF"] = gM["CALIF_FINAL"].apply(lambda s: _to_num(s).max()).values
    else:
        df_mat_area["PROM_CALIF"] = pd.NA
        df_mat_area["MIN_CALIF"] = pd.NA
        df_mat_area["MAX_CALIF"] = pd.NA

    df_mat_area = df_mat_area.sort_values("REPROBADOS_UNICOS", ascending=False).reset_index(drop=True)

    st.markdown("### Reprobación por materia (siempre mostrando AREA)")
    st.dataframe(df_mat_area, use_container_width=True)

    st.markdown("### Comparativo de materias (Top) — dentro de la carrera seleccionada")
    top_n_mat = st.slider("Top N materias", min_value=10, max_value=60, value=20, step=5)
    chart_mat = _bar_top_materias_area(df_mat_area, top_n=top_n_mat)
    if chart_mat is not None:
        st.altair_chart(chart_mat, use_container_width=True)

    st.divider()

    # =========================================================
    # 3) HISTÓRICO por materia
    # =========================================================
    st.markdown("## Histórico por materia (reprobados por ciclo) — dentro de la carrera")

    materia_sel = st.selectbox(
        "Materia (dentro de la carrera seleccionada)",
        ["(Selecciona)"] + df_mat_area["MATERIA"].astype(str).tolist(),
        index=0,
    )

    if materia_sel != "(Selecciona)":
        fh = f[
            (f["AREA"].astype(str).str.strip() == str(area_sel2).strip())
            & (f["MATERIA"].astype(str).str.strip() == str(materia_sel).strip())
        ].copy()

        if fh.empty:
            st.info("No hay datos históricos para esa materia con los filtros actuales.")
            return

        if "MATRICULA" in fh.columns:
            hist = fh.groupby("CICLO")["MATRICULA"].nunique().reset_index(name="REPROBADOS_UNICOS")
        else:
            hist = fh.groupby("CICLO").size().reset_index(name="REPROBADOS_UNICOS")

        hist["CICLO_NUM"] = pd.to_numeric(hist["CICLO"], errors="coerce")
        hist = hist.sort_values(["CICLO_NUM", "CICLO"]).drop(columns=["CICLO_NUM"])

        line = (
            alt.Chart(hist)
            .mark_line(point=True)
            .encode(
                x=alt.X("CICLO:N", title="Ciclo", sort=None),
                y=alt.Y("REPROBADOS_UNICOS:Q", title="Alumnos reprobados (únicos)"),
                tooltip=[alt.Tooltip("CICLO:N", title="Ciclo"), alt.Tooltip("REPROBADOS_UNICOS:Q", title="Únicos")],
            )
            .properties(height=360)
        )
        st.altair_chart(line, use_container_width=True)
        st.dataframe(hist, use_container_width=True)
