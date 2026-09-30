"""Leer y guardar config.json sin riesgo de dejarlo roto.

Antes cada módulo reescribía config.json directamente: si Jarvis se cerraba justo a la mitad
(un cierre forzado, un apagón), el archivo quedaba cortado y Jarvis ya no arrancaba. Ahora se
escribe a un archivo temporal y se reemplaza de golpe (os.replace es atómico en Windows).
"""
import json
import os
import threading
from pathlib import Path

RUTA = Path(__file__).parent / "config.json"
_lock = threading.Lock()


def cargar(ruta=RUTA):
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def guardar(datos, ruta=RUTA):
    ruta = Path(ruta)
    tmp = ruta.with_suffix(".json.tmp")
    with _lock:
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, ruta)


def guardar_campos(cambios, ruta=RUTA):
    """Cambia solo algunas claves (lee lo último del disco para no pisar ediciones hechas a
    mano mientras Jarvis corría)."""
    with _lock:
        datos = cargar(ruta)
        datos.update(cambios)
        tmp = Path(ruta).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, ruta)
    return datos
