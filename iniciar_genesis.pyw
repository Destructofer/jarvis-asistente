"""Arranca el asistente (Jarvis) en segundo plano: sin terminal, con icono en la bandeja.

Se lanza con .venv\\Scripts\\pythonw.exe (doble clic, o automáticamente con Windows
si ejecutas: python autoinicio.py instalar). El registro queda en datos\\genesis.log.
"""
import ctypes
import os
import sys
import threading
import time
import traceback
from pathlib import Path

BASE = Path(__file__).parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))

# Una sola instancia: si ya hay un Genesis corriendo, este sale sin hacer nada
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_mutex = _kernel.CreateMutexW(None, False, "Genesis.Asistente.Instancia")
if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

# Con pythonw no hay consola: print() fallaría, así que todo va a un archivo de registro
LOG = BASE / "datos" / "genesis.log"
LOG.parent.mkdir(exist_ok=True)
if LOG.exists() and LOG.stat().st_size > 2_000_000:
    LOG.replace(LOG.with_suffix(".old"))
sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
print(f"\n===== Asistente inicia {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

import pystray  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import escuchar  # noqa: E402
import expositor  # noqa: E402
import genesis  # noqa: E402

try:
    import json  # noqa: E402
    NOMBRE = json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("name", "Jarvis")
except Exception:
    NOMBRE = "Jarvis"

AZUL, GRIS = (0, 160, 255, 255), (130, 130, 130, 255)


def _imagen(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=color)
    d.ellipse((22, 22, 42, 42), fill="white")
    return img


def _alternar_pausa(icono, _item):
    if escuchar.PAUSA.is_set():
        escuchar.PAUSA.clear()
    else:
        escuchar.PAUSA.set()
    icono.icon = _imagen(GRIS if escuchar.PAUSA.is_set() else AZUL)


def _escribir_orden(_icono, _item):
    # Despierta a Genesis sin la palabra de activación: sirve aunque el micrófono falle
    escuchar.DESPERTAR.set()


def _alternar_expositor(_icono, _item):
    expositor.cambiar_modo(not expositor.ACTIVO)


def _ver_registro(_icono, _item):
    os.startfile(LOG)


def _salir(icono, _item):
    icono.stop()
    os._exit(0)


icono = pystray.Icon(
    "genesis", _imagen(AZUL), NOMBRE,
    pystray.Menu(
        pystray.MenuItem("Escribir una orden", _escribir_orden, default=True),
        pystray.MenuItem("Modo expositor", _alternar_expositor,
                         checked=lambda _i: expositor.ACTIVO),
        pystray.MenuItem("Pausar escucha", _alternar_pausa,
                         checked=lambda _i: escuchar.PAUSA.is_set()),
        pystray.MenuItem("Ver registro", _ver_registro),
        pystray.MenuItem("Salir", _salir),
    ),
)
icono.run_detached()

# Si algo falla (micrófono desconectado, Ollama caído...), reintenta en vez de morir en silencio
while True:
    try:
        genesis.main(persistente=True)
    except Exception:
        traceback.print_exc()
    time.sleep(10)
