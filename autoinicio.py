"""Arranque de Genesis con Windows (sin terminal) y vigilante que lo relanza si se cae.

    python autoinicio.py instalar   -> arranca solo al iniciar sesión + vigilante
    python autoinicio.py quitar     -> quita las dos cosas
    python autoinicio.py estado

El vigilante es una tarea programada que cada 5 minutos intenta lanzar Genesis. Si ya está
corriendo, la copia nueva detecta la instancia existente (mutex en iniciar_genesis.pyw) y
sale sin hacer nada; si el proceso murió de golpe (un cierre forzado que el reinicio interno
no alcanza a atrapar), así vuelve solo sin esperar al próximo inicio de sesión.
"""
import subprocess
import sys
import winreg
from pathlib import Path

CLAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE = "Jarvis"
TAREA = "JarvisVigilante"
BASE = Path(__file__).parent
PYTHONW = BASE / ".venv" / "Scripts" / "pythonw.exe"
LANZADOR = BASE / "iniciar_genesis.pyw"
COMANDO = f'"{PYTHONW}" "{LANZADOR}"'
SIN_VENTANA = 0x08000000


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=SIN_VENTANA)


def instalar():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, NOMBRE, 0, winreg.REG_SZ, COMANDO)
    r = _schtasks("/Create", "/F", "/TN", TAREA, "/SC", "MINUTE", "/MO", "5", "/TR", COMANDO)
    print("Jarvis arrancará con Windows.")
    print("Vigilante instalado." if r.returncode == 0 else f"No pude crear el vigilante: {r.stderr.strip()}")


def quitar():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, NOMBRE)
        print("Jarvis ya no arrancará con Windows.")
    except FileNotFoundError:
        print("El arranque con Windows no estaba instalado.")
    r = _schtasks("/Delete", "/F", "/TN", TAREA)
    print("Vigilante quitado." if r.returncode == 0 else "El vigilante no estaba instalado.")


def estado():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLAVE) as k:
            print("Arranque con Windows:", winreg.QueryValueEx(k, NOMBRE)[0])
    except FileNotFoundError:
        print("Arranque con Windows: no instalado.")
    r = _schtasks("/Query", "/TN", TAREA)
    print("Vigilante:", "instalado" if r.returncode == 0 else "no instalado")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "estado"
    {"instalar": instalar, "quitar": quitar, "estado": estado}.get(accion, estado)()
