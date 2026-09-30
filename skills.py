import ctypes
import datetime
import os
import subprocess
import unicodedata
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

import psutil
import pyautogui

_CFG = {}
_SKILLS = {}
SIN_VENTANA = 0x08000000  # subprocess sin consola: con pythonw evita ventanas parpadeando

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Apps base. Las tuyas se añaden en config.json → "apps"
APPS_BASE = {
    "calculadora": "calc.exe",
        "bloc de notas": "notepad.exe",
    "blog de notas": "notepad.exe",
    "block de notas": "notepad.exe",
    "notepad": "notepad.exe",
    "explorador": "explorer.exe",
    "administrador de tareas": "taskmgr.exe",
    "edge": "msedge",
    "spotify": "spotify:",
    # Terminales: abrir_app (apps.py) pide confirmación antes de abrir cualquiera de estas
    "powershell": "powershell.exe",
    "windows powershell": "powershell.exe",
    "simbolo del sistema": "cmd.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    # Ajustes de Windows: también piden confirmación (mismo motivo)
    "configuracion": "ms-settings:",
    "ajustes": "ms-settings:",
    "almacenamiento": "ms-settings:storagesense",
    "pantalla": "ms-settings:display",
    "sonido": "ms-settings:sound",
    "bluetooth": "ms-settings:bluetooth",
    "estado de la red": "ms-settings:network-status",
    "actualizaciones de windows": "ms-settings:windowsupdate",
    "bateria": "ms-settings:batterysaver",
    "aplicaciones instaladas": "ms-settings:appsfeatures",
    "cuentas": "ms-settings:yourinfo",
    "impresoras": "ms-settings:printers",
}

CARPETAS = {
    "descargas": "Downloads", "documentos": "Documents", "escritorio": "Desktop",
    "imagenes": "Pictures", "musica": "Music", "videos": "Videos",
}

AFIRMATIVAS = {"si", "confirmo", "confirmado", "adelante", "claro", "dale",
               "ok", "okey", "correcto", "afirmativo", "hazlo", "procede"}
NEGATIVAS = {"no", "cancela", "cancelar", "detente", "alto", "nunca"}


# ---------- Infraestructura ----------
def _norm(texto):
    texto = unicodedata.normalize("NFD", str(texto).lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if c.isalnum() else " " for c in texto).split())


def configurar(cfg):
    global _CFG
    _CFG = cfg


class Callado(str):
    """Resultado que NO se dice en voz alta (solo se muestra en el HUD y en el registro).

    Para acciones que se repiten mucho frente al público, como pasar de diapositiva: que
    Jarvis diga "Diapositiva 4 de 12" cada vez rompería la exposición. Si la acción falla, la
    skill devuelve un str normal y el error sí se dice."""


def skill(nombre, descripcion, parametros=None, requeridos=None,
          riesgo="seguro", pregunta="", terminal=True, externo=False, sensible=False):
    """Registra una función como skill que el modelo puede llamar.

    terminal=True (la mayoría) significa que el resultado ya es una frase final para el
    usuario: genesis.py se ahorra una segunda vuelta al modelo y lo dice directo, lo que
    corta bastante la latencia. Ponlo en False solo cuando el resultado es una lista o dato
    en bruto que conviene que el modelo interprete o sobre el que pueda decidir un siguiente
    paso (p. ej. buscar_archivo, listar_avisos, consultar_memoria).

    externo=True: el resultado trae texto escrito por otras personas (mensajes y documentos
    de Teams). Ese texto puede contener instrucciones ("cierra todo", "vacía la papelera")
    que no vienen del usuario; genesis.py lo marca como datos y, tras leerlo, pide
    confirmación para cualquier skill sensible=True que el modelo intente en ese turno.
    """
    parametros = parametros or {}

    def deco(fn):
        _SKILLS[nombre] = {
            "fn": fn,
            "riesgo": riesgo,
            "pregunta": pregunta,
            "terminal": terminal,
            "externo": externo,
            "sensible": sensible,
            "params": list(parametros),
            "schema": {
                "type": "function",
                "function": {
                    "name": nombre,
                    "description": descripcion,
                    "parameters": {
                        "type": "object",
                        "properties": parametros,
                        "required": list(parametros) if requeridos is None else requeridos,
                    },
                },
            },
        }
        return fn

    return deco


def existe(nombre):
    return nombre in _SKILLS


def activa(nombre):
    return _CFG.get("skills", {}).get(nombre, True)


def riesgo(nombre):
    return _SKILLS[nombre]["riesgo"]


def pregunta(nombre):
    return _SKILLS[nombre]["pregunta"] or f"¿Confirmas que ejecute {nombre}?"


def es_terminal(nombre):
    return _SKILLS.get(nombre, {}).get("terminal", True)


def es_externo(nombre):
    return _SKILLS.get(nombre, {}).get("externo", False)


def es_sensible(nombre):
    return _SKILLS.get(nombre, {}).get("sensible", False)


def schemas(nombres=None):
    """Descripciones de herramientas para el modelo. nombres=None: todas; si no, solo esas
    (genesis.py manda solo las que tienen que ver con la orden: ver elegir_herramientas)."""
    return [s["schema"] for n, s in _SKILLS.items()
            if activa(n) and (nombres is None or n in nombres)]


def ejecutar(nombre, args):
    s = _SKILLS[nombre]
    args = {k: v for k, v in (args or {}).items() if k in s["params"]}
    try:
        r = s["fn"](**args)
        return r if isinstance(r, str) else str(r)  # conserva Callado
    except Exception as e:
        return f"Error al ejecutar {nombre}: {e}"


def es_afirmativo(texto):
    palabras = set(_norm(texto).split())
    if palabras & NEGATIVAS:
        return False
    return bool(palabras & AFIRMATIVAS)


# ---------- Skills seguras ----------
@skill("hora_fecha", "Devuelve la fecha y la hora actuales del equipo.")
def hora_fecha():
    a = datetime.datetime.now()
    return (f"Hoy es {DIAS[a.weekday()]} {a.day} de {MESES[a.month - 1]} "
            f"de {a.year}, son las {a:%H:%M}.")


@skill("info_sistema", "Informa del uso de CPU, memoria RAM y batería del equipo.")
def info_sistema():
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    txt = f"CPU al {cpu:.0f}%, RAM al {ram:.0f}%."
    bat = psutil.sensors_battery()
    if bat:
        estado = "cargando" if bat.power_plugged else "sin cargador"
        txt += f" Batería al {bat.percent:.0f}% ({estado})."
    return txt


# abrir_app vive en apps.py (índice de apps instaladas + nombres aproximados)
@skill("abrir_carpeta",
       "Abre una carpeta del usuario: descargas, documentos, escritorio, imágenes, música o videos.",
       {"nombre": {"type": "string", "description": "Nombre de la carpeta"}})
def abrir_carpeta(nombre):
    clave = _norm(nombre)
    for k, v in CARPETAS.items():
        if k in clave:
            os.startfile(str(Path.home() / v))
            return f"Abriendo {k}."
    return f"No conozco esa carpeta. Disponibles: {', '.join(CARPETAS)}."


@skill("volumen",
       "Controla el volumen: subir, bajar, silenciar (alterna el silencio) o establecer un nivel exacto.",
       {"accion": {"type": "string",
                   "enum": ["subir", "bajar", "silenciar", "establecer"],
                   "description": "Acción a realizar"},
        "porcentaje": {"type": "integer",
                       "description": "De 0 a 100. En subir/bajar es cuánto cambiar (10 por defecto); "
                                      "en establecer es el nivel final"}},
       requeridos=["accion"])
def volumen(accion, porcentaje=10):
    p = max(0, min(100, int(porcentaje)))
    pasos = round(p / 2)  # cada pulsación mueve ~2 %
    if accion == "subir":
        pyautogui.press("volumeup", presses=max(1, pasos), interval=0.01)
        return f"Volumen subido un {p}%."
    if accion == "bajar":
        pyautogui.press("volumedown", presses=max(1, pasos), interval=0.01)
        return f"Volumen bajado un {p}%."
    if accion == "silenciar":
        pyautogui.press("volumemute")
        return "Silencio alternado."
    if accion == "establecer":
        pyautogui.press("volumedown", presses=50, interval=0.005)
        if pasos:
            pyautogui.press("volumeup", presses=pasos, interval=0.01)
        return f"Volumen en {p}%."
    return "Acción de volumen no válida."


@skill("captura_pantalla",
       "Toma una captura de pantalla (pantallazo, screenshot) y la guarda en la carpeta Imágenes. "
       "Úsala cuando el usuario pida capturar, fotografiar o guardar lo que se ve en pantalla.")
def captura_pantalla():
    from PIL import ImageGrab
    

    carpeta = Path.home() / "Pictures" / "Capturas Jarvis"
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"captura_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
    ImageGrab.grab(all_screens=True).save(ruta)
    return f"Captura guardada en {ruta}"


@skill("buscar_web", "Busca algo en internet abriendo el navegador con los resultados.",
       {"consulta": {"type": "string", "description": "Texto a buscar"}})
def buscar_web(consulta):
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(consulta)}")
    return f"Buscando '{consulta}' en el navegador."


@skill("abrir_web", "Abre un sitio web en el navegador a partir de su dirección.",
       {"url": {"type": "string", "description": "Dirección del sitio, por ejemplo youtube.com"}},
       sensible=True)
def abrir_web(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Abriendo {url}."


@skill("bloquear_pantalla", "Bloquea la sesión de Windows.")
def bloquear_pantalla():
    ctypes.windll.user32.LockWorkStation()
    return "Pantalla bloqueada."


@skill("cancelar_apagado", "Cancela un apagado o reinicio que esté programado.")
def cancelar_apagado():
    r = subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=SIN_VENTANA)
    return "Apagado cancelado." if r.returncode == 0 else "No había ningún apagado programado."


# ---------- Skills que piden confirmación ----------
@skill("apagar_equipo", "Apaga el equipo en 30 segundos.",
       riesgo="confirmar", pregunta="¿Seguro que quieres apagar el equipo?")
def apagar_equipo():
    subprocess.run(["shutdown", "/s", "/t", "30"], creationflags=SIN_VENTANA)
    return "El equipo se apagará en 30 segundos. Se puede cancelar con cancelar_apagado."


@skill("reiniciar_equipo", "Reinicia el equipo en 30 segundos.",
       riesgo="confirmar", pregunta="¿Seguro que quieres reiniciar el equipo?")
def reiniciar_equipo():
    subprocess.run(["shutdown", "/r", "/t", "30"], creationflags=SIN_VENTANA)
    return "El equipo se reiniciará en 30 segundos. Se puede cancelar con cancelar_apagado."


@skill("wifi",
       "Activa o desactiva el adaptador Wi-Fi del equipo.",
       {"accion": {"type": "string", "enum": ["activar", "desactivar"],
                   "description": "Encender o apagar el Wi-Fi"}},
       riesgo="confirmar", pregunta="¿Seguro que quieres cambiar el estado del Wi-Fi?")
def wifi(accion):
    nombre = _CFG.get("wifi_interfaz", "Wi-Fi")
    estado = "enable" if accion == "activar" else "disable"
    r = subprocess.run(["netsh", "interface", "set", "interface", nombre, estado],
                       capture_output=True, text=True, creationflags=SIN_VENTANA)
    if r.returncode != 0:
        return (f"No pude cambiar el Wi-Fi. Comprueba que la interfaz se llame '{nombre}' "
                "y que Jarvis se ejecute como administrador.")
    return f"Wi-Fi {'activado' if accion == 'activar' else 'desactivado'}."


if __name__ == "__main__":
    print(ejecutar("hora_fecha", {}))
    print(ejecutar("info_sistema", {}))
    print(ejecutar("captura_pantalla", {}))

    