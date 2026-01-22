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
