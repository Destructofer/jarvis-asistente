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

CLAVES = {"GROQ_API_KEY", "ELEVENLABS_API_KEY", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
          "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "DEMO_USUARIO", "DEMO_CLAVE"}


def _claves_de_config(cfg):
    nombres = set()
    for bloque in (cfg.get("nube"), (cfg.get("vision") or {}).get("nube"), cfg.get("stt"),
                   *(cfg.get("nubes_extra") or [])):
        if isinstance(bloque, dict) and bloque.get("clave_env"):
            nombres.add(bloque["clave_env"])
    login = ((cfg.get("demo") or {}).get("login") or {})
    nombres.update(v for k, v in login.items() if k.endswith("_env") and v)
    return nombres


def cargar_claves_de_windows(cfg=None):
    """Las claves guardadas con setx viven en el registro de Windows, pero un programa solo las
    ve si se abrió DESPUÉS del setx: la terminal de VS Code abierta antes no las tiene, y Jarvis
    arrancaba sin la nube (modelo local lento y sin la voz de ElevenLabs) sin decir por qué.
    Se leen directo del registro (lo mismo que haría una terminal nueva). Devuelve los NOMBRES
    cargados; los valores nunca se imprimen."""
    try:
        import winreg
    except ImportError:
        return []
    nombres = CLAVES | _claves_de_config(cfg or {})
    cargadas = []
    for raiz, ruta in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE,
                        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            clave = winreg.OpenKey(raiz, ruta)
        except OSError:
            continue
        with clave:
            for nombre in sorted(nombres):
                if os.environ.get(nombre):
                    continue
                try:
                    valor, tipo = winreg.QueryValueEx(clave, nombre)
                except OSError:
                    continue
                if valor:
                    os.environ[nombre] = (os.path.expandvars(valor) if tipo == winreg.REG_EXPAND_SZ
                                          else str(valor))
                    cargadas.append(nombre)
    return cargadas


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
