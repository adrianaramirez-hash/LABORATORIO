# ======== SOLO SE MUESTRAN LAS PARTES MODIFICADAS =========
# TODO LO DEMÁS QUEDA IGUAL

def resolver_permiso_por_email(email: str, df_accesos: pd.DataFrame) -> dict:
    ...
    rol = str(fila.iloc[0]["ROL"]).strip().upper()

    if rol not in ["DG", "DC", "DF"]:
        return {
            "ok": False,
            "rol": None,
            "servicios": [],
            "modulos": set(),
            "mensaje": "ROL inválido en ACCESOS. Usa DG, DC o DF.",
        }

    if rol == "DC" and not servicios:
        return {
            "ok": False,
            "rol": None,
            "servicios": [],
            "modulos": set(),
            "mensaje": "Falta SERVICIO_ASIGNADO (ROL=DC).",
        }

    return {
        "ok": True,
        "rol": rol,
        "servicios": (servicios if rol == "DC" else []),
        "modulos": modulos,
        "mensaje": "OK",
    }


# ============================================================
# Contexto de usuario (DG vs DC vs DF)
# ============================================================
ROL = st.session_state["user_rol"]

if ROL == "DG":
    vista = "Dirección General"
    carrera = None

elif ROL == "DF":
    vista = "Dirección Finanzas"
    carrera = None

else:
    vista = "Director de carrera"
    SERVICIOS_DC = st.session_state.get("user_servicios") or []
    ...
