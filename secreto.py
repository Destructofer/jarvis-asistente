"""Guarda tokens (Spotify, Microsoft) cifrados con DPAPI de Windows: solo tu usuario de
Windows en este equipo puede descifrarlos. Antes quedaban en texto plano en datos/, y
cualquiera con acceso al archivo (o una copia del disco) podía usar tu cuenta.

Si encuentra un archivo viejo sin cifrar, lo lee y lo reescribe cifrado en ese momento, así
que no hace falta volver a conectar nada."""
import json
from pathlib import Path

import win32crypt

_PREFIJO = b"DPAPI1:"


def guardar_json(ruta, datos):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    cifrado = win32crypt.CryptProtectData(json.dumps(datos).encode("utf-8"), "Genesis",
                                          None, None, None, 0)
    ruta.write_bytes(_PREFIJO + cifrado)


def leer_json(ruta):
    """Datos guardados, o None si no existe o no se puede leer."""
    ruta = Path(ruta)
    try:
        crudo = ruta.read_bytes()
    except OSError:
        return None
    try:
        if crudo.startswith(_PREFIJO):
            _desc, plano = win32crypt.CryptUnprotectData(crudo[len(_PREFIJO):], None, None, None, 0)
            return json.loads(plano.decode("utf-8"))
        datos = json.loads(crudo.decode("utf-8"))  # archivo viejo sin cifrar: se cifra ya
        guardar_json(ruta, datos)
        return datos
    except Exception:
        return None
