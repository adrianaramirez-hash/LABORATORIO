# evaluacion_docente.py
import pandas as pd
import streamlit as st
import gspread
import textwrap
import re
import altair as alt

# ============================================================
# Config
# ============================================================
SHEET_BASE = "BASE"  # pestaña en tu Google Sheet de Evaluación Docente


# ============================================================
# Helpers (alineados a tu estilo previo)
# ============================================================
def _to_float(x):
    try:
        return float(str(x).replace("%", "").strip())
    except Exception:
        return pd.NA


def _to_int(x):
    try:
        return int(float(str(x).strip()))
    except Exception:
        return pd.NA


def _wrap_text(s: str, width: int = 40, max_lines: int = 2) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip()
    if not s:
        return ""
    lines = textwrap.wrap(s, width=width)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    kept[-1] = (kept[-1][:-1] + "…") if len(kept[-1]) >= 1 else "…"
    return "\n".join(kept)


def _norm_text(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_key(s: str) -> str:
    # clave robusta para matching (ignoramos puntuación, dobles espacios, etc.)
    s = _norm_text(s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _pick_date_col(df: pd.DataFrame) -> str | None:
    for c in ["Marca temporal", "Marca Temporal", "Fecha", "fecha", "timestamp", "Timestamp"]:
        if c in df.columns:
            return c
    return None


def _safe_percent(n, d):
    try:
        if pd.isna(n) or pd.isna(d) or float(d) == 0:
            return pd.NA
        return (float(n) / float(d)) * 100.0
    except Exception:
        return pd.NA


def _cycle_sort_key(c: str):
    """
    Ordena ciclos tipo 25-1, 25-2, 26-1...
    Si no matchea, manda al final.
    """
    s = str(c).strip()
    m = re.match(r"^(\d{2,4})\s*-\s*(\d{1,2})$", s)
    if not m:
        return (9999, 99, s)
    y = int(m.group(1))
    if y < 100:
        y = 2000 + y
    p = int(m.group(2))
    return (y, p, s)


# ============================================================
# Google Sheets loader
# ============================================================
@st.cache_data(show_spinner=False, ttl=300)
def _load_sheet_as_df(url: str, sheet_name: str) -> pd.DataFrame:
    sa = dict(st.secrets["gcp_service_account_json"])
    gc = gspread.service_account_from_dict(sa)
    sh = gc.open_by_url(url)

    def norm(x: str) -> str:
        return str(x).strip().lower().replace(" ", "").replace("_", "")

    titles = [ws.title for ws in sh.worksheets()]
    titles_norm = {norm(t): t for t in titles}

    resolved = titles_norm.get(norm(sheet_name))
    if not resolved:
        raise ValueError(
            f"No encontré la pestaña '{sheet_name}'. "
            f"Pestañas disponibles: {', '.join(titles)}"
        )

    ws = sh.worksheet(resolved)
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers).replace("", pd.NA)
    return df


@st.cache_data(show_spinner=False, ttl=300)
def _load_observaciones_df_from_secret() -> tuple[pd.DataFrame, str | None]:
    """
    Carga Observación de clases desde secret OC_SHEET_URL.
    Si OC_SHEET_NAME existe, la usa; si no, intenta nombres comunes.
    """
    url = st.secrets.get("OC_SHEET_URL", "").strip()
    if not url:
        return pd.DataFrame(), None

    sheet_name = st.secrets.get("OC_SHEET_NAME", "").strip()
    if not sheet_name:
        candidates = ["FORM", "RESPUESTAS", "DATA", "PROCESADO", "Observacion", "OBSERVACION"]
    else:
        candidates = [sheet_name]

    for sn in candidates:
        try:
            df = _load_sheet_as_df(url, sn)
            if not df.empty:
                fecha_col = _pick_date_col(df)
                if fecha_col:
                    df[fecha_col] = pd.to_datetime(df[fecha_col], errors="coerce", dayfirst=True)
                return df, fecha_col
        except Exception:
            continue

    return pd.DataFrame(), None


# ============================================================
# Heurísticas columnas Observación
# ============================================================
def _pick_prof_col_oc(df: pd.DataFrame) -> str | None:
    candidates = [
        "Docente", "docente",
        "Profesor", "profesor",
        "Nombre del docente", "Nombre del Docente",
        "Docente observado", "Docente Observado",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    for c in df.columns:
        lc = str(c).lower()
        if "doc" in lc or "prof" in lc:
            return c
    return None


def _pick_carrera_col_oc(df: pd.DataFrame) -> str | None:
    candidates = [
        "Carrera", "carrera",
        "Servicio", "servicio",
        "Programa", "programa",
        "Carrera/Servicio", "Carrera / Servicio",
    ]
    for c in candidates:
        if c in df.columns:
            return c
    for c in df.columns:
        lc = str(c).lower()
        if "carrera" in lc or "servicio" in lc or "programa" in lc:
            return c
    return None


def _pick_link_col_oc(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        lc = str(c).lower()
        if "link" in lc or "liga" in lc or "evidencia" in lc or "drive" in lc:
            return c
    return None


def _pick_result_col_oc(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        lc = str(c).lower()
        if any(k in lc for k in ["resultado", "calificacion", "calificación", "puntaje", "score", "cumplimiento"]):
            return c
    return None


# ============================================================
# Cálculos
# ============================================================
def _promedio_ponderado(dfx: pd.DataFrame) -> float | pd.NA:
    w = pd.to_numeric(dfx["total"], errors="coerce")
    y = pd.to_numeric(dfx["promedio"], errors="coerce")
    denom = w.sum(skipna=True)
    if pd.notna(denom) and float(denom) > 0:
        return (y * w).sum(skipna=True) / denom
    return y.mean()


def _make_line_chart(df_line: pd.DataFrame, x: str, y: str, title: str):
    if df_line.empty:
        st.info("No hay datos suficientes para graficar con los filtros actuales.")
        return
    chart = (
        alt.Chart(df_line)
        .mark_line(point=True)
        .encode(
            x=alt.X(f"{x}:O", sort=df_line[x].tolist(), title="Ciclo"),
            y=alt.Y(f"{y}:Q", title="Promedio"),
            tooltip=[x, y],
        )
        .properties(height=300, title=title)
    )
    st.altair_chart(chart, use_container_width=True)


# ============================================================
# Render principal
# ============================================================
def render_evaluacion_docente(
    vista: str | None = None,
    carrera: str | None = None,
    ed_url: str | None = None,
):
    st.subheader("Evaluación docente")

    if not vista:
        vista = "Dirección General"

    if not ed_url:
        ed_url = st.secrets.get("EDOCENTE_URL", "").strip()

    if not ed_url:
        st.error("Falta configurar la URL de Evaluación Docente (EDOCENTE_URL en Secrets) o pásala como parámetro.")
        return

    # ---------------------------
    # Carga ED
    # ---------------------------
    try:
        with st.spinner("Cargando Evaluación Docente (Google Sheets)…"):
            df = _load_sheet_as_df(ed_url, SHEET_BASE)
    except Exception as e:
        st.error("No se pudo cargar la pestaña BASE de Evaluación Docente.")
        st.exception(e)
        return

    if df.empty:
        st.warning("La hoja BASE está vacía.")
        return

    required = {"profesor", "grupo", "materia", "carrera", "aplicaron", "total", "promedio", "ciclo"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Faltan columnas en BASE: {', '.join(missing)}")
        return

    # Tipos + normalizados
    df = df.copy()
    df["aplicaron"] = df["aplicaron"].apply(_to_int)
    df["total"] = df["total"].apply(_to_int)
    df["promedio"] = df["promedio"].apply(_to_float)
    df["participacion_pct"] = [_safe_percent(n, d) for n, d in zip(df["aplicaron"], df["total"])]

    df["_prof_key"] = df["profesor"].apply(_norm_key)
    df["_car_key"] = df["carrera"].apply(_norm_key)
    df["_ciclo_key"] = df["ciclo"].apply(_cycle_sort_key)

    # ciclos ordenados
    ciclos = df["ciclo"].dropna().astype(str).str.strip().unique().tolist()
    ciclos = sorted(ciclos, key=_cycle_sort_key)
    if not ciclos:
        st.warning("No encontré valores de ciclo.")
        return

    # ---------------------------
    # Controles
    # ---------------------------
    c1, c2, c3 = st.columns([1.0, 1.4, 1.0])
    with c1:
        ciclo_sel = st.selectbox("Ciclo", ciclos, index=max(0, len(ciclos) - 1))

    # Carrera (DG vs DC)
    if vista == "Dirección General":
        with c2:
            # opciones desde el ciclo seleccionado
            base_ciclo = df[df["ciclo"].astype(str).str.strip() == str(ciclo_sel).strip()].copy()
            carreras = sorted(base_ciclo["carrera"].dropna().astype(str).str.strip().unique().tolist())
            carrera_opts = ["(Todas)"] + carreras
            carrera_sel = st.selectbox("Carrera", carrera_opts, index=0)
            carrera_key_sel = "" if carrera_sel == "(Todas)" else _norm_key(carrera_sel)
    else:
        carrera_sel = (carrera or "").strip()
        carrera_key_sel = _norm_key(carrera_sel)
        with c2:
            st.text_input("Carrera (fija por vista)", value=carrera_sel, disabled=True)

    with c3:
        umbral = st.number_input("Umbral de alerta (≤)", min_value=0, max_value=100, value=79, step=1)

    st.divider()

    # ---------------------------
    # Filtro principal (para tablas del ciclo seleccionado)
    # ---------------------------
    f = df[df["ciclo"].astype(str).str.strip() == str(ciclo_sel).strip()].copy()

    # FIX DC: filtrar por clave normalizada
    if vista == "Dirección General":
        if carrera_key_sel:
            f = f[f["_car_key"] == carrera_key_sel]
    else:
        if not carrera_key_sel:
            st.warning("Vista Director requiere carrera fija para filtrar.")
            return
        f = f[f["_car_key"] == carrera_key_sel]

    # Diagnóstico breve (útil para cuando “no se ve nada”)
    if vista != "Dirección General" and len(f) == 0:
        st.error("No hay registros para tu carrera con el filtro actual.")
        st.caption(
            "Causa típica: el nombre de carrera en ACCESOS no coincide con el de la base. "
            "Solución: ajustar SERVICIO_ASIGNADO o estandarizar el nombre en la columna 'carrera'."
        )
        st.caption(f"Carrera (DC): '{carrera_sel}' | clave usada: '{carrera_key_sel}'")
        # Mostrar carreras disponibles en la base (primeras 25) para comparar rápido
        uniques = sorted(df["carrera"].dropna().astype(str).str.strip().unique().tolist())
        st.caption("Ejemplos de carreras en la base (para comparar):")
        st.dataframe(pd.DataFrame({"carrera": uniques[:25]}), use_container_width=True)
        return

    st.caption(f"Registros filtrados (ciclo): **{len(f)}**")
    if len(f) == 0:
        st.warning("No hay registros con los filtros seleccionados.")
        return

    # KPIs del ciclo
    prom_global = pd.to_numeric(f["promedio"], errors="coerce").mean()
    part_global = _safe_percent(
        pd.to_numeric(f["aplicaron"], errors="coerce").sum(),
        pd.to_numeric(f["total"], errors="coerce").sum(),
    )
    grupos = len(f)

    focos_ciclo = f[pd.to_numeric(f["promedio"], errors="coerce") <= float(umbral)]
    pct_focos = _safe_percent(len(focos_ciclo), len(f))  # % de casos alerta sobre filas

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Promedio (ciclo)", f"{prom_global:.2f}" if pd.notna(prom_global) else "—")
    k2.metric("Participación", f"{part_global:.1f}%" if pd.notna(part_global) else "—")
    k3.metric("Grupos evaluados", f"{grupos}")
    k4.metric("Casos en alerta", f"{len(focos_ciclo)} ({pct_focos:.1f}%)" if pd.notna(pct_focos) else f"{len(focos_ciclo)}")

    st.divider()

    # ---------------------------
    # Cargar Observaciones (para vínculo)
    # ---------------------------
    oc_df, oc_fecha_col = _load_observaciones_df_from_secret()
    oc_has_data = not oc_df.empty

    if oc_has_data:
        oc_prof_col = _pick_prof_col_oc(oc_df)
        oc_car_col = _pick_carrera_col_oc(oc_df)
        oc_link_col = _pick_link_col_oc(oc_df)
        oc_res_col = _pick_result_col_oc(oc_df)

        if oc_prof_col:
            oc_df["_prof_key"] = oc_df[oc_prof_col].apply(_norm_key)
        else:
            oc_df["_prof_key"] = ""

        if oc_car_col:
            oc_df["_car_key"] = oc_df[oc_car_col].apply(_norm_key)
        else:
            oc_df["_car_key"] = ""
    else:
        oc_prof_col = oc_car_col = oc_link_col = oc_res_col = None

    # ---------------------------
    # Tabs
    # ---------------------------
    tabs = ["Resumen", "Tendencia", "Focos rojos", "Top docentes", "Vinculación con Observación"]
    tab1, tabT, tab2, tabTop, tab3 = st.tabs(tabs)

    # ===========================
    # TAB 1: Resumen (tablas)
    # ===========================
    with tab1:
        if vista == "Dirección General" and (carrera_key_sel == ""):
            st.markdown("### Promedio por carrera — tabla (ciclo)")
            rows = []
            for car, dfx in f.groupby("carrera"):
                car = str(car).strip()
                if not car:
                    continue
                prom = pd.to_numeric(dfx["promedio"], errors="coerce").mean()
                part = _safe_percent(
                    pd.to_numeric(dfx["aplicaron"], errors="coerce").sum(),
                    pd.to_numeric(dfx["total"], errors="coerce").sum(),
                )
                alert_cnt = (pd.to_numeric(dfx["promedio"], errors="coerce") <= float(umbral)).sum()
                rows.append(
                    {
                        "Carrera": car,
                        "Promedio": float(prom) if pd.notna(prom) else pd.NA,
                        "Participación %": float(part) if pd.notna(part) else pd.NA,
                        "Grupos": int(len(dfx)),
                        "Casos en alerta": int(alert_cnt),
                    }
                )
            out = pd.DataFrame(rows).sort_values("Promedio", ascending=False, na_position="last")
            st.dataframe(out.reset_index(drop=True), use_container_width=True)
        else:
            st.markdown("### Promedio por docente — tabla (ciclo)")
            rows = []
            for prof, dfx in f.groupby("profesor"):
                prom_w = _promedio_ponderado(dfx)
                part = _safe_percent(
                    pd.to_numeric(dfx["aplicaron"], errors="coerce").sum(),
                    pd.to_numeric(dfx["total"], errors="coerce").sum(),
                )
                alerts = (pd.to_numeric(dfx["promedio"], errors="coerce") <= float(umbral)).sum()
                rows.append(
                    {
                        "Profesor": prof,
                        "Promedio (ponderado)": float(prom_w) if pd.notna(prom_w) else pd.NA,
                        "Participación %": float(part) if pd.notna(part) else pd.NA,
                        "Grupos": int(len(dfx)),
                        "Casos en alerta": int(alerts),
                    }
                )
            out = pd.DataFrame(rows).sort_values("Promedio (ponderado)", ascending=False, na_position="last")
            out["Profesor"] = out["Profesor"].apply(lambda x: _wrap_text(x, width=45, max_lines=2))
            st.dataframe(out.reset_index(drop=True), use_container_width=True)

    # ===========================
    # TAB Tendencia (línea por ciclos)
    # ===========================
    with tabT:
        st.markdown("### Tendencia por ciclos — promedio")
        # Base para tendencia: se filtra por carrera si aplica (DG con carrera elegida o DC)
        trend_base = df.copy()

        if vista == "Dirección General":
            if carrera_key_sel:
                trend_base = trend_base[trend_base["_car_key"] == carrera_key_sel]
                title = f"Tendencia de promedio — {carrera_sel}"
            else:
                title = "Tendencia de promedio — Institución"
        else:
            trend_base = trend_base[trend_base["_car_key"] == carrera_key_sel]
            title = f"Tendencia de promedio — {carrera_sel}"

        # Agrupar por ciclo
        rows = []
        for cyc, dfx in trend_base.groupby("ciclo"):
            prom = pd.to_numeric(dfx["promedio"], errors="coerce").mean()
            rows.append({"ciclo": str(cyc).strip(), "promedio": float(prom) if pd.notna(prom) else pd.NA})

        df_line = pd.DataFrame(rows)
        if df_line.empty:
            st.info("No hay datos suficientes para tendencia.")
        else:
            df_line = df_line.dropna(subset=["ciclo"]).copy()
            df_line["sort_key"] = df_line["ciclo"].apply(_cycle_sort_key)
            df_line = df_line.sort_values("sort_key")
            df_line = df_line.drop(columns=["sort_key"])

            _make_line_chart(df_line, x="ciclo", y="promedio", title=title)
            st.dataframe(df_line.reset_index(drop=True), use_container_width=True)

    # ===========================
    # TAB 2: Focos rojos (detalle + vínculo Sí/No)
    # ===========================
    with tab2:
        st.markdown("### Casos con promedio ≤ umbral — detalle (ciclo)")
        focos = f[pd.to_numeric(f["promedio"], errors="coerce") <= float(umbral)].copy()

        if focos.empty:
            st.info("No hay focos rojos con el umbral seleccionado.")
        else:
            focos["Participación %"] = focos["participacion_pct"]
            focos["Observación encontrada"] = ""

            if oc_has_data and oc_prof_col:
                oc_sub = oc_df.copy()

                # acotar por carrera si podemos (DG carrera específica / DC siempre)
                if oc_car_col and (carrera_key_sel or vista != "Dirección General"):
                    oc_sub = oc_sub[oc_sub["_car_key"] == (carrera_key_sel if carrera_key_sel else oc_sub["_car_key"])]

                oc_keys = set(oc_sub["_prof_key"].dropna().tolist())
                focos["Observación encontrada"] = focos["_prof_key"].apply(lambda k: "Sí" if k in oc_keys else "No")
            elif not oc_has_data:
                st.caption("Nota: No hay datos de Observación de clases configurados (OC_SHEET_URL).")
            else:
                st.caption("Nota: No pude detectar la columna de docente en Observación; se omite el vínculo.")

            show_cols = ["profesor", "materia", "grupo", "promedio", "Participación %", "ciclo", "Observación encontrada"]
            out = focos[show_cols].copy()
            out["profesor"] = out["profesor"].apply(lambda x: _wrap_text(x, width=35, max_lines=2))
            out["materia"] = out["materia"].apply(lambda x: _wrap_text(x, width=45, max_lines=2))
            st.dataframe(out.reset_index(drop=True), use_container_width=True)

    # ===========================
    # TAB Top docentes
    # ===========================
    with tabTop:
        st.markdown("### Top docentes — ranking (ciclo)")
        st.caption("Criterio: promedio ponderado por total de alumnos. Filtros aplican según tu vista/carrera.")

        # Reglas mínimas para ranking (evitar “tops” con 1 grupo si no quieres)
        cmin1, cmin2 = st.columns([1, 1])
        with cmin1:
            min_grupos = st.number_input("Mínimo de grupos", min_value=1, max_value=20, value=1, step=1)
        with cmin2:
            min_part = st.number_input("Participación mínima %", min_value=0, max_value=100, value=0, step=5)

        rows = []
        for prof, dfx in f.groupby("profesor"):
            grupos_prof = len(dfx)
            part_prof = _safe_percent(
                pd.to_numeric(dfx["aplicaron"], errors="coerce").sum(),
                pd.to_numeric(dfx["total"], errors="coerce").sum(),
            )
            if grupos_prof < int(min_grupos):
                continue
            if pd.notna(part_prof) and float(part_prof) < float(min_part):
                continue

            prom_w = _promedio_ponderado(dfx)
            rows.append(
                {
                    "Profesor": prof,
                    "Promedio (ponderado)": float(prom_w) if pd.notna(prom_w) else pd.NA,
                    "Participación %": float(part_prof) if pd.notna(part_prof) else pd.NA,
                    "Grupos": int(grupos_prof),
                }
            )

        out = pd.DataFrame(rows)
        if out.empty:
            st.info("No hay docentes que cumplan los criterios mínimos con los filtros actuales.")
        else:
            out = out.sort_values("Promedio (ponderado)", ascending=False, na_position="last")
            out["Profesor"] = out["Profesor"].apply(lambda x: _wrap_text(x, width=50, max_lines=2))
            st.dataframe(out.reset_index(drop=True), use_container_width=True)

    # ===========================
    # TAB 3: Vinculación con Observación (panel por profesor)
    # ===========================
    with tab3:
        st.markdown("### Vinculación (Evaluación Docente → Observación de clases)")
        if not oc_has_data:
            st.warning("No pude cargar Observación de clases. Configura **OC_SHEET_URL** (y opcionalmente OC_SHEET_NAME) en Secrets.")
            return
        if not oc_prof_col:
            st.warning("Cargué Observación, pero no pude detectar la columna del docente. Renombra una columna a 'Docente' o 'Profesor' (o similar).")
            return

        profs = sorted(f["profesor"].dropna().astype(str).str.strip().unique().tolist())
        if not profs:
            st.info("No hay profesores para mostrar.")
            return

        prof_sel = st.selectbox("Profesor", profs, index=0)

        ed_prof = f[f["profesor"].astype(str).str.strip() == str(prof_sel).strip()].copy()
        st.caption(f"Registros en Evaluación Docente para este profesor (ciclo): {len(ed_prof)}")

        ed_show = ed_prof[["materia", "grupo", "promedio", "participacion_pct"]].copy()
        ed_show["materia"] = ed_show["materia"].apply(lambda x: _wrap_text(x, width=55, max_lines=2))
        ed_show.rename(columns={"participacion_pct": "Participación %"}, inplace=True)
        st.dataframe(ed_show.reset_index(drop=True), use_container_width=True)

        # buscar observaciones por profesor y carrera (si existe)
        oc_sub = oc_df[oc_df["_prof_key"] == _norm_key(prof_sel)].copy()

        if oc_car_col and carrera_key_sel:
            oc_sub = oc_sub[oc_sub["_car_key"] == carrera_key_sel]

        if oc_sub.empty:
            st.info("No se encontraron observaciones vinculadas para este profesor con los filtros actuales.")
            return

        st.markdown("#### Observaciones encontradas — tabla")

        cols_to_show = []
        if oc_fecha_col and oc_fecha_col in oc_sub.columns:
            cols_to_show.append(oc_fecha_col)
        cols_to_show.append(oc_prof_col)
        if oc_car_col and oc_car_col in oc_sub.columns:
            cols_to_show.append(oc_car_col)
        if oc_res_col and oc_res_col in oc_sub.columns:
            cols_to_show.append(oc_res_col)
        if oc_link_col and oc_link_col in oc_sub.columns:
            cols_to_show.append(oc_link_col)

        if not cols_to_show:
            cols_to_show = oc_sub.columns.tolist()[:12]

        out = oc_sub[cols_to_show].copy()

        if oc_fecha_col and oc_fecha_col in out.columns:
            out[oc_fecha_col] = pd.to_datetime(out[oc_fecha_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")

        out[oc_prof_col] = out[oc_prof_col].apply(lambda x: _wrap_text(x, width=40, max_lines=2))
        if oc_car_col and oc_car_col in out.columns:
            out[oc_car_col] = out[oc_car_col].apply(lambda x: _wrap_text(x, width=35, max_lines=2))
        if oc_link_col and oc_link_col in out.columns:
            out[oc_link_col] = out[oc_link_col].astype(str).apply(lambda x: _wrap_text(x, width=55, max_lines=2))

        st.dataframe(out.reset_index(drop=True), use_container_width=True)

        st.caption(
            "Vinculación por coincidencia de **Profesor** y (si existe) **Carrera**. "
            "Si más adelante confirmamos Materia/Grupo en Observación, lo hacemos más preciso."
        )
