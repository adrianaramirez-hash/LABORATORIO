# catalogos.py
# ============================================================
# CATÁLOGOS MAESTROS – Dirección Académica UDL
# (Enfoque inicial: CAT_CARRERAS)
# ============================================================

from __future__ import annotations

import re
import unicodedata
from typing import Optional

import pandas as pd

# ============================================================
# Google Sheet – Catálogo de Carreras
# ============================================================
CAT_CARRERAS_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1CK7nphUH9YS2JqSWRhrgamYoQdgJCsn5tERA-WnwXes/edit"
    "?gid=2108194656#gid=2108194656"
)
CAT_CARRERAS_SHEET_NAME = "CAT_CARRERAS"

# ============================================================
# Normalización de texto
# ============================================================


def normalizar_texto(texto: str) -> str:
    """
    Normaliza un texto para matching robusto:
    - Upper
    - Quita acentos/diacríticos
    - Quita caracteres raros
    - Colapsa espacios
    """
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""

    s = str(texto).strip().upper()
    if not s:
        return ""

    # quitar diacríticos
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))

    # reemplazos típicos
    s = s.replace("\u00A0", " ")  # NBSP
    s = s.replace("\u200B", "")   # zero-width

    # dejar letras/números/espacios y algunos separadores
    s = re.sub(r"[^A-Z0-9\s:/\-\(\)\.]", " ", s)

    # colapsar espacios
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _split_variantes(cell: str) -> list[str]:
    """
    Convierte una celda de 'variantes' en lista.
    Acepta separadores: salto de línea, coma, punto y coma, pipe.
    """
    if cell is None or (isinstance(cell, float) and pd.isna(cell)):
        return []

    txt = str(cell).strip()
    if not txt:
        return []

    parts = re.split(r"[\n,;\|]+", txt)
    out = [p.strip() for p in parts if p and p.strip()]
    return out


# ============================================================
# Carga desde Google Sheets (gspread)
# ============================================================


def cargar_cat_carreras_desde_gsheets(gc) -> pd.DataFrame:
    """
    Lee la hoja CAT_CARRERAS desde Google Sheets usando un cliente gspread ya autenticado.

    Espera columnas (nombres flexibles):
      - carrera_id
      - nombre_oficial
      - variantes

    Devuelve DF normalizado con:
      - carrera_id
      - nombre_oficial
      - variantes              (string original)
      - variante_norm          (una fila por variante + nombre_oficial + carrera_id)
    """
    sh = gc.open_by_url(CAT_CARRERAS_SHEET_URL)
    try:
        ws = sh.worksheet(CAT_CARRERAS_SHEET_NAME)
    except Exception:
        ws = sh.sheet1

    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=["carrera_id", "nombre_oficial", "variantes", "variante_norm"])

    header = [str(c).strip() for c in values[0]]
    rows = values[1:]

    df = pd.DataFrame(rows, columns=header).replace("", pd.NA)

    # Normaliza nombres de columnas
    df.columns = [normalizar_texto(c).replace(" ", "_") for c in df.columns]

    # Mapea columnas flexibles
    col_id = None
    for c in ["CARRERA_ID", "ID", "CLAVE", "CODIGO", "CODIGO_CARRERA"]:
        if c in df.columns:
            col_id = c
            break

    col_nombre = None
    for c in ["NOMBRE_OFICIAL", "NOMBRE", "CARRERA", "PROGRAMA", "PROGRAMA_ACADEMICO"]:
        if c in df.columns:
            col_nombre = c
            break

    col_vars = None
    for c in ["VARIANTES", "VARIACIONES", "ALIAS", "SINONIMOS", "SINÓNIMOS"]:
        if c in df.columns:
            col_vars = c
            break

    # Si no existen, crea columnas vacías (no truena, pero será visible en DF)
    if col_id is None:
        df["CARRERA_ID"] = pd.NA
        col_id = "CARRERA_ID"
    if col_nombre is None:
        df["NOMBRE_OFICIAL"] = pd.NA
        col_nombre = "NOMBRE_OFICIAL"
    if col_vars is None:
        df["VARIANTES"] = pd.NA
        col_vars = "VARIANTES"

    out = pd.DataFrame(
        {
            "carrera_id": df[col_id].astype(str).str.strip(),
            "nombre_oficial": df[col_nombre].astype(str).str.strip(),
            "variantes": df[col_vars].astype(str).str.strip(),
        }
    )

    # Limpieza: quitar 'nan'
    out["carrera_id"] = out["carrera_id"].replace({"nan": "", "None": ""})
    out["nombre_oficial"] = out["nombre_oficial"].replace({"nan": "", "None": ""})
    out["variantes"] = out["variantes"].replace({"nan": "", "None": ""})

    # Filas válidas: al menos carrera_id y nombre_oficial
    out = out[(out["carrera_id"].str.strip() != "") & (out["nombre_oficial"].str.strip() != "")]
    out = out.reset_index(drop=True)

    # Construcción de variante_norm: incluye
    # - carrera_id
    # - nombre_oficial
    # - cada variante
    # - además el propio carrera_id como variante (útil: ACT, ADM, etc.)
    records = []
    for _, r in out.iterrows():
        cid = str(r["carrera_id"]).strip()
        nom = str(r["nombre_oficial"]).strip()
        vars_list = _split_variantes(r["variantes"])

        # always include nombre_oficial y carrera_id como variantes “reconocibles”
        base_variants = [nom, cid]
        all_variants = base_variants + vars_list

        for v in all_variants:
            vn = normalizar_texto(v)
            if vn:
                records.append(
                    {
                        "carrera_id": cid,
                        "nombre_oficial": nom,
                        "variantes": r["variantes"],
                        "variante_norm": vn,
                        "variante_raw": str(v).strip(),
                    }
                )

    dfv = pd.DataFrame(records)
    if dfv.empty:
        # fallback mínimo
        out["variante_norm"] = out["nombre_oficial"].apply(normalizar_texto)
        return out[["carrera_id", "nombre_oficial", "variantes", "variante_norm"]]

    # Deduplicar por carrera_id + variante_norm
    dfv = dfv.drop_duplicates(subset=["carrera_id", "variante_norm"]).reset_index(drop=True)

    # Orden útil
    dfv = dfv.sort_values(["carrera_id", "variante_norm"]).reset_index(drop=True)

    return dfv


# ============================================================
# Mapeo: texto libre -> carrera_id
# ============================================================


def mapear_carrera_id(texto: str, df_cat: pd.DataFrame) -> Optional[str]:
    """
    Devuelve el carrera_id a partir de un texto (ej. "FINANZAS MV", "Licenciatura Ejecutiva: Derecho", "DER").
    Usa df_cat tal como sale de cargar_cat_carreras_desde_gsheets() (con columna 'variante_norm').

    Reglas:
      1) Match exacto por variante_norm
      2) Fallback: match exacto por nombre_oficial normalizado
      3) Fallback: contiene-palabra (solo si produce 1 único resultado)
    """
    if df_cat is None or getattr(df_cat, "empty", True):
        return None

    if not texto:
        return None

    t = normalizar_texto(texto)
    if not t:
        return None

    cols = set(df_cat.columns)
    if "variante_norm" not in cols:
        # intentamos crearla si viene el DF base sin explotar
        tmp = df_cat.copy()
        if "nombre_oficial" in tmp.columns:
            tmp["variante_norm"] = tmp["nombre_oficial"].apply(normalizar_texto)
            df_cat = tmp
        else:
            return None

    # 1) exact match
    hit = df_cat[df_cat["variante_norm"] == t]
    if not hit.empty:
        return str(hit.iloc[0]["carrera_id"]).strip()

    # 2) por nombre_oficial normalizado
    if "nombre_oficial" in df_cat.columns:
        nom_norm = df_cat["nombre_oficial"].astype(str).apply(normalizar_texto)
        hit2 = df_cat[nom_norm == t]
        if not hit2.empty:
            return str(hit2.iloc[0]["carrera_id"]).strip()

    # 3) contains (controlado)
    #    Si el texto incluye una variante o una variante incluye el texto.
    #    Solo aceptamos si el resultado es único.
    c1 = df_cat[df_cat["variante_norm"].str.contains(re.escape(t), na=False)]
    c2 = df_cat[df_cat["variante_norm"].apply(lambda x: t in str(x) if x else False)]
    cand = pd.concat([c1, c2], ignore_index=True)
    if cand.empty:
        return None

    cand = cand.drop_duplicates(subset=["carrera_id"])
    if len(cand) == 1:
        return str(cand.iloc[0]["carrera_id"]).strip()

    return None


# ============================================================
# Compatibilidad: alias para módulos antiguos
# ============================================================

def resolver_carrera(texto: str, df_cat: pd.DataFrame) -> Optional[str]:
    """
    Alias retrocompatible.
    Devuelve carrera_id a partir de texto libre usando el catálogo (df_cat).
    """
    return mapear_carrera_id(texto, df_cat)


def resolver_nombre_oficial(texto: str, df_cat: pd.DataFrame) -> Optional[str]:
    """
    Devuelve el nombre_oficial de la carrera (si se puede resolver) a partir de texto libre.
    Útil cuando un módulo necesita mostrar el nombre canónico.
    """
    cid = mapear_carrera_id(texto, df_cat)
    if not cid or df_cat is None or getattr(df_cat, "empty", True):
        return None

    if "carrera_id" not in df_cat.columns or "nombre_oficial" not in df_cat.columns:
        return None

    hit = df_cat[df_cat["carrera_id"].astype(str).str.strip() == str(cid).strip()]
    if hit.empty:
        return None

    return str(hit.iloc[0]["nombre_oficial"]).strip()
