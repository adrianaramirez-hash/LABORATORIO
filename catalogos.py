# ============================================================
# Google Sheet – Catálogo de Carreras
# ============================================================

CAT_CARRERAS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1CK7nphUH9YS2JqSWRhrgamYoQdgJCsn5tERA-WnwXes/edit?gid=2108194656#gid=2108194656"
CAT_CARRERAS_SHEET_NAME = "CAT_CARRERAS"
# ============================================================
# CATÁLOGOS MAESTROS
# Dirección Académica UDL
# ============================================================

import pandas as pd
import unicodedata
import re


# ============================================================
# Normalización de texto
# ============================================================

def normalizar_texto(texto: str) -> str:
    """
    Convierte texto a mayúsculas, sin acentos ni caracteres especiales.
    """
    if pd.isna(texto):
        return ""

    texto = str(texto).upper()
    texto = unicodedata.normalize("NFKD", texto)
    texto = texto.encode("A
