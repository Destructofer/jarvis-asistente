"""Detecta lo que suele hacer más lento el equipo (caché, papelera, RAM, disco) y lo arregla
solo cuando el usuario lo pide o lo confirma en el panel emergente (panel.py); nunca actúa
sin avisar antes."""
import ctypes
import json
import os
import threading
import time
from pathlib import Path

import psutil

import panel
from apps import bloqueado
from skills import skill

_CFG = {}
_notificar = None  # genesis.py instala aquí cómo avisar (voz) cuando hay algo que mostrar
_ocupado = None    # genesis.py: fn() -> True si estás exponiendo (no se revisa ni se avisa)
_hilo = None

TEMP_DIRS = [Path(os.environ.get("TEMP", "")), Path("C:/Windows/Temp")]
AVISADOS_PATH = Path(__file__).parent / "datos" / "mantenimiento.json"

UMBRAL_TEMP_GB = 2.0
TEMP_DIAS_MINIMO = 2   # solo se borran temporales con más de estos días sin tocarse
UMBRAL_PAPELERA_GB = 1.0
UMBRAL_DISCO_LIBRE_GB = 15.0
UMBRAL_RAM_PCT = 88
COOLDOWN_AVISO_SEG = 3 * 3600      # no repetir el mismo aviso antes de 3 horas
COOLDOWN_DISCO_SEG = 24 * 3600     # el disco lleno no se arregla solo en 3 h: una vez al día basta
# Procesos que aunque usen mucha RAM no se ofrecen para cerrar: son de Windows, del antivirus
# o del propio Genesis (dwm.exe dibuja la pantalla; cerrarlo deja todo en negro).
NO_OFRECER = {"dwm", "memory compression", "msmpeng", "mssense", "searchindexer",
              "msedgewebview2", "system", "registry"}


def _cargar_avisados():
    """Los avisos ya dados se guardan en disco: si solo vivieran en memoria, cada reinicio de
    Genesis volvería a repetir el mismo aviso aunque hubiera sonado hace un rato."""
    try:
        return json.loads(AVISADOS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _guardar_avisados():
    try:
        AVISADOS_PATH.parent.mkdir(parents=True, exist_ok=True)
        AVISADOS_PATH.write_text(json.dumps(_avisados), encoding="utf-8")
    except OSError:
        pass


_avisados = _cargar_avisados()  # clave del hallazgo -> último momento en que se avisó


# ---------- Medir ----------
class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64),
               ("i64NumItems", ctypes.c_int64)]


def tamano_papelera_gb():
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
    try:
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return info.i64Size / 1e9
    except OSError:
        pass
    return 0.0


def _tamano_carpeta(ruta):
    total = 0
    for raiz, _dirs, archivos in os.walk(ruta):
        for a in archivos:
            try:
                total += (Path(raiz) / a).stat().st_size
            except OSError:
                continue
    return total


def tamano_temporales_gb():
    return sum(_tamano_carpeta(d) for d in TEMP_DIRS if d.is_dir()) / 1e9


def _top_procesos_ram(n=3):
    """Apps que más RAM usan, sumando todos sus procesos (el navegador abre uno por pestaña,
    así que uno solo nunca refleja lo que de verdad ocupa). Deja fuera lo del sistema y lo de
    Genesis, que no tiene sentido ofrecer cerrar."""
    por_app = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            nombre = p.info["name"] or ""
            base = Path(nombre).stem.lower()
            if not nombre or bloqueado(nombre) or base in NO_OFRECER:
                continue
            por_app[nombre] = por_app.get(nombre, 0) + p.info["memory_info"].rss / 1e6
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    return sorted(por_app.items(), key=lambda x: -x[1])[:n]


def estado_equipo():
    ram = psutil.virtual_memory()
    disco = psutil.disk_usage(str(Path.home().anchor))
    return {
        "cpu": psutil.cpu_percent(interval=0.6),
        "ram_pct": ram.percent,
        "ram_gb_usada": (ram.total - ram.available) / 1e9,
        "ram_gb_total": ram.total / 1e9,
        "disco_libre_gb": disco.free / 1e9,
        "disco_total_gb": disco.total / 1e9,
        "temp_gb": tamano_temporales_gb(),
        "papelera_gb": tamano_papelera_gb(),
        "top_ram": _top_procesos_ram(),
    }


def _resumen(e):
    return (f"CPU al {e['cpu']:.0f}%, RAM al {e['ram_pct']:.0f}% "
            f"({e['ram_gb_usada']:.1f} de {e['ram_gb_total']:.1f} GB), "
            f"disco libre {e['disco_libre_gb']:.0f} de {e['disco_total_gb']:.0f} GB, "
            f"{e['temp_gb']:.1f} GB en temporales y {e['papelera_gb']:.1f} GB en la papelera.")


def analizar():
    """(estado, hallazgos, resumen). Cada hallazgo trae su acción lista para el panel."""
    e = estado_equipo()
    hallazgos = []
    if e["temp_gb"] >= UMBRAL_TEMP_GB:
        hallazgos.append({"clave": "temp",
                          "texto": f"{e['temp_gb']:.1f} GB en archivos temporales",
                          "etiqueta": f"Limpiar temporales ({e['temp_gb']:.1f} GB)",
                          "accion": _limpiar_temporales_real})
    if e["papelera_gb"] >= UMBRAL_PAPELERA_GB:
        hallazgos.append({"clave": "papelera",
                          "texto": f"{e['papelera_gb']:.1f} GB en la papelera de reciclaje",
                          "etiqueta": f"Vaciar papelera ({e['papelera_gb']:.1f} GB)",
                          "accion": _vaciar_papelera_real})
    if e["disco_libre_gb"] < UMBRAL_DISCO_LIBRE_GB:
        hallazgos.append({"clave": "disco",
                          "texto": f"Solo quedan {e['disco_libre_gb']:.1f} GB libres en el disco",
                          "etiqueta": "Ver qué ocupa el disco (Almacenamiento de Windows)",
                          "accion": _abrir_almacenamiento})
    if e["ram_pct"] >= UMBRAL_RAM_PCT and e["top_ram"]:
        nombre, mb = e["top_ram"][0]
        hallazgos.append({"clave": "ram",
                          "texto": f"RAM al {e['ram_pct']:.0f}%; {nombre} está usando {mb:.0f} MB",
                          "etiqueta": f"Cerrar {nombre} ({mb:.0f} MB)",
                          "accion": lambda n=nombre: _cerrar_proceso_real(n)})
    return e, hallazgos, _resumen(e)


# ---------- Acciones reales ----------
def _es_enlace(ruta):
    """Symlinks y "junctions" de Windows: se borra el enlace, nunca lo que hay del otro lado
    (un junction dentro de Temp podría apuntar a Documentos)."""
    try:
        return ruta.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(ruta))
    except OSError:
        return True


def _limpiar_temporales_real(dias_minimo=None):
    """Borra temporales VIEJOS. Antes se borraba todo lo que no estuviera bloqueado, incluidos
    archivos que programas abiertos aún usan (p. ej. un documento abierto desde un .zip vive
    en Temp): ahora solo lo que lleva días sin tocarse."""
    dias = TEMP_DIAS_MINIMO if dias_minimo is None else dias_minimo
    limite = time.time() - dias * 86400
    liberado, fallos, recientes = 0, 0, 0
    for base in TEMP_DIRS:
        if not base.is_dir():
            continue
        for raiz, dirs, archivos in os.walk(base, topdown=False, followlinks=False):
            raiz = Path(raiz)
            for a in archivos:
                p = raiz / a
                try:
                    if _es_enlace(p):
                        continue
                    st = p.stat()
                    if max(st.st_mtime, st.st_atime) > limite:
                        recientes += 1
                        continue
                    p.unlink()
                    liberado += st.st_size
                except OSError:
                    fallos += 1
            for d in dirs:
                sub = raiz / d
                try:
                    if _es_enlace(sub):
                        continue
                    sub.rmdir()  # solo cae si ya quedó vacía
                except OSError:
                    pass
    texto = f"Liberé {liberado / 1e9:.2f} GB de temporales."
    if fallos or recientes:
        texto += f" (Dejé {fallos + recientes} archivos en uso o de los últimos {dias} días)."
    return texto


def _vaciar_papelera_real():
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 0x1, 0x2, 0x4
    try:
        ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
        return "Papelera de reciclaje vaciada."
    except OSError as e:
        return f"No pude vaciar la papelera: {type(e).__name__}."


def _cerrar_proceso_real(nombre):
    """Cierra la app por sus ventanas (como con la X, respeta "¿guardar cambios?") en vez de
    matar sus procesos, que perdería lo no guardado."""
    if bloqueado(nombre) or Path(nombre).stem.lower() in NO_OFRECER:
        return f"'{nombre}' es parte del sistema; no lo cierro."
    import apps
    return apps.cerrar_app(Path(nombre).stem)


def _abrir_almacenamiento():
    os.startfile("ms-settings:storagesense")
    return "Abrí Almacenamiento de Windows: ahí ves qué ocupa más y puedes liberarlo."


# ---------- Vigilancia en segundo plano ----------
def _avisar_si_hace_falta(hallazgos, resumen):
    ahora = time.time()
    pendientes = [h for h in hallazgos
                 if ahora - _avisados.get(h["clave"], 0) >
                 (COOLDOWN_DISCO_SEG if h["clave"] == "disco" else COOLDOWN_AVISO_SEG)]
    if not pendientes:
        return
    for h in pendientes:
        _avisados[h["clave"]] = ahora
    _guardar_avisados()
    if _mostrar_panel(pendientes, resumen) and _notificar:
        _notificar("Revisé el equipo y encontré algo que podría estar ralentizándolo. "
                   "Te dejo las opciones en pantalla.")
    elif _notificar:
        _notificar("Revisé el equipo: " + resumen)


def _mostrar_panel(hallazgos, resumen):
    """Devuelve True si de verdad abrió el panel (solo tiene sentido si hay al menos una
    acción con botón; un hallazgo como "poco disco libre" es informativo y no abre nada)."""
    opciones = [{"etiqueta": h["etiqueta"], "accion": h["accion"]}
               for h in hallazgos if h["accion"]]
    if not opciones:
        return False
    panel.mostrar("Jarvis · Mantenimiento", resumen, opciones)
    return True


def _exponiendo():
    try:
        return bool(_ocupado and _ocupado())
    except Exception:
        return False


def _vigilar():
    intervalo = _CFG.get("mantenimiento_intervalo_min", 30) * 60
    time.sleep(120)  # deja que Jarvis termine de arrancar antes de la primera revisión
    while True:
        try:
            # En plena exposición NO: un panel encima de la presentación y un "revisé el
            # equipo" por las bocinas frente al jurado es lo último que se quiere.
            if _CFG.get("mantenimiento_activo", True) and not _exponiendo():
                _, hallazgos, resumen = analizar()
                _avisar_si_hace_falta(hallazgos, resumen)
        except Exception as e:
            print(f"[Error en mantenimiento: {type(e).__name__}: {str(e)[:120]}]")
        time.sleep(intervalo)


def iniciar(cfg, notificar, ocupado=None):
    """Se puede llamar varias veces (iniciar_genesis.pyw reinicia main() si algo falla): el
    hilo de vigilancia es uno solo. Antes cada reinicio sumaba otro y los avisos se repetían."""
    global _CFG, _notificar, _ocupado, _hilo
    _CFG = cfg
    _notificar = notificar
    _ocupado = ocupado
    if cfg.get("mantenimiento_activo", True) and (_hilo is None or not _hilo.is_alive()):
        _hilo = threading.Thread(target=_vigilar, daemon=True, name="mantenimiento")
        _hilo.start()


# ---------- Skills ----------
@skill("revisar_equipo",
       "Analiza el estado del equipo (CPU, RAM, disco, archivos temporales, papelera) y "
       "muestra un panel con las soluciones disponibles como botones. Úsala cuando el "
       "usuario pregunte por qué va lento, pida revisar o diagnosticar el equipo, o quiera "
       "liberar espacio.", terminal=False)
def revisar_equipo():
    _, hallazgos, resumen = analizar()
    if not hallazgos:
        return resumen + " No encontré nada que valga la pena arreglar ahora mismo."
    if _mostrar_panel(hallazgos, resumen):
        return resumen + " Te dejé las opciones para arreglarlo en un panel en pantalla."
    return resumen  # hallazgos solo informativos (p. ej. poco disco libre): no hay botón que mostrar


# Por voz piden confirmación (una orden mal oída o un texto leído de Teams no deben poder
# borrar nada); desde el panel no, porque pulsar el botón ya es la confirmación.
@skill("limpiar_temporales",
       "Borra los archivos temporales del sistema para liberar espacio y ayudar a que el "
       "equipo vaya más fluido.",
       riesgo="confirmar", pregunta="¿Borro los archivos temporales del equipo?")
def limpiar_temporales():
    return _limpiar_temporales_real()


@skill("vaciar_papelera", "Vacía la papelera de reciclaje de Windows (no se puede deshacer).",
       riesgo="confirmar",
       pregunta="¿Vacío la papelera de reciclaje? Lo que hay ahí se borra para siempre.")
def vaciar_papelera():
    return _vaciar_papelera_real()


if __name__ == "__main__":
    e, hallazgos, resumen = analizar()
    print(resumen)
    for h in hallazgos:
        print(" -", h["texto"])
