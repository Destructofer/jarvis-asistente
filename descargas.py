"""Vigila la carpeta de Descargas para que el Vault Boy del HUD lo muestre.

Mientras haya un archivo bajándose, el HUD muestra el costal (vaultboy/descargando.gif); al
terminar, el pulgar arriba (completado.gif). Se reconoce una descarga en curso por el archivo
temporal que crea cada navegador o programa mientras baja. Una descarga muy rápida (que no
alcanzó a verse en curso) se detecta porque aparece un archivo nuevo en la carpeta.

Solo mira nombres de archivo en la carpeta (no los abre ni lee): es barato y no toca nada.
"""
import os
import threading
import time
from pathlib import Path

import hud

# Archivo temporal mientras se descarga: Chrome/Edge/Brave, Opera, Firefox, Safari/otros,
# qBittorrent, uTorrent/BitTorrent, Internet Download Manager y genéricos
PARCIALES = (".crdownload", ".opdownload", ".part", ".partial", ".download", ".!qb", ".!ut",
             ".idm", ".downloading")
INTERVALO = 1.5


def _carpeta():
    try:
        from win32com.shell import shell, shellcon
        return Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Downloads))
    except Exception:
        return Path.home() / "Downloads"


def _nombres(carpeta):
    try:
        with os.scandir(carpeta) as it:
            return {e.name for e in it if e.is_file(follow_symlinks=False)}
    except OSError:
        return set()


def _vigilar(carpeta):
    previos = _nombres(carpeta)
    en_curso = False
    while True:
        time.sleep(INTERVALO)
        actuales = _nombres(carpeta)
        parciales = {n for n in actuales if n.lower().endswith(PARCIALES)}
        if parciales and not en_curso:
            print(f"[Descarga en curso: {sorted(parciales)[0]}]")
        if bool(parciales) != en_curso:
            en_curso = bool(parciales)
            hud.descarga(en_curso)  # al pasar a False, el HUD muestra el pulgar arriba
        elif not en_curso:
            # archivo nuevo sin haber visto su temporal: una descarga que tardó menos que INTERVALO
            nuevos = {n for n in actuales - previos if not n.lower().endswith(PARCIALES)
                      and not n.startswith(("~$", "."))}
            if nuevos:
                print(f"[Descarga terminada: {sorted(nuevos)[0]}]")
                hud.descarga_terminada()
        previos = actuales


def iniciar(cfg):
    if not (cfg.get("hud", {}) or {}).get("vigilar_descargas", True):
        return
    carpeta = _carpeta()
    if carpeta.exists():
        threading.Thread(target=_vigilar, args=(carpeta,), daemon=True, name="descargas").start()
