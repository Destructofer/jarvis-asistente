"""Mantenimiento AUTOMÁTICO del equipo: Jarvis lo hace solo y en silencio (sin ventanas ni
avisos), cada pocos minutos:

- Borra temporales VIEJOS (más de 2 días sin tocarse) de tu usuario y de Windows, su propia
  caché de voz cuando crece de más, y los volcados y reportes de fallos viejos de Windows.
- Memoria: con la RAM alta libera la suya (los modelos que viven en la RAM) y RECORTA la
  memoria de los procesos que llevan rato sin hacer nada y no están al frente (las pestañas
  de fondo del navegador, por ejemplo). Es seguro: si los vuelves a usar, Windows les
  regresa su memoria.
- Procesador: a un programa de FONDO que se come el CPU por más de una vuelta le baja la
  prioridad (no te alenta lo que tienes al frente) y se la regresa cuando se calma.

Lo que NUNCA hace solo: cerrar programas (perderías lo no guardado; config.json →
mantenimiento_cerrar_inactivos permite nombrar los que sí puede cerrar cuando no tienen
ventana y llevan rato sin usarse), vaciar la papelera (son tus archivos: "vacía la papelera"
lo pide confirmando), ni tocar Windows, el antivirus, la música, las llamadas o a Jarvis.

Lo que hizo queda en datos/mantenimiento.json: "¿qué has hecho para que la compu vaya
rápido?" lo cuenta.
"""
import ctypes
import json
import os
import threading
import time
from pathlib import Path

import psutil

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
RAM_RECORTAR_PCT = 80          # desde aquí se recorta la memoria de lo que no se usa
RECORTAR_MIN_MB = 150          # solo procesos que ocupan al menos esto
CPU_FONDO_PCT = 25             # un programa de fondo con más CPU que esto (promedio) se baja de prioridad
CPU_CALMADO_PCT = 5            # ... y se le regresa la prioridad cuando baja de esto
LIMPIEZA_CADA_SEG = 6 * 3600   # temporales y cachés: cada 6 h
CACHE_VOZ_MAX_MB = 300         # la caché de voz de Jarvis se recorta a 200 MB si pasa de esto
VIEJOS_DIAS = 7                # volcados y reportes de fallos: con más días se borran
VOZ_CACHE = Path(__file__).parent / "datos" / "voz_cache"
LOCAL = Path(os.environ.get("LOCALAPPDATA", ""))
CARPETAS_VIEJOS = [LOCAL / "CrashDumps", LOCAL / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                   LOCAL / "Microsoft" / "Windows" / "WER" / "ReportQueue"]
# Lo que nunca se recorta ni se le baja la prioridad: música, llamadas, grabación, Jarvis y
# sus modelos (además de lo del sistema: apps.bloqueado y NO_OFRECER)
NO_TOCAR = {"spotify", "vlc", "wmplayer", "music.ui", "itunes", "discord", "zoom", "teams", "ms-teams",
            "obs64", "obs", "audiodg", "python", "pythonw", "ollama", "ollama app", "llama-server",
            "whatsapp", "whatsapp.root", "code", "explorer", "sihost", "ctfmon", "textinputhost",
            "searchhost", "startmenuexperiencehost", "shellexperiencehost", "lockapp", "csrss",
            "winlogon", "services", "lsass", "svchost", "smss", "wininit", "fontdrvhost", "taskmgr"}
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


# ---------- Más limpieza segura ----------
def _recortar_cache_voz(maximo_mb=CACHE_VOZ_MAX_MB, objetivo_mb=200):
    """La caché de frases de Jarvis: si pasa del máximo, se borran las más viejas."""
    if not VOZ_CACHE.is_dir():
        return 0
    archivos = sorted((f for f in VOZ_CACHE.rglob("*") if f.is_file()), key=lambda f: f.stat().st_atime)
    total = sum(f.stat().st_size for f in archivos)
    if total <= maximo_mb * 1e6:
        return 0
    liberado = 0
    for f in archivos:
        if total - liberado <= objetivo_mb * 1e6:
            break
        try:
            tam = f.stat().st_size
            f.unlink()
            liberado += tam
        except OSError:
            pass
    return liberado


def _borrar_viejos(carpetas=None, dias=VIEJOS_DIAS):
    """Volcados de memoria y reportes de fallos de Windows con más de 'dias': nadie los usa."""
    limite = time.time() - dias * 86400
    liberado = 0
    for base in carpetas or CARPETAS_VIEJOS:
        if not base.is_dir():
            continue
        for raiz, dirs, archivos in os.walk(base, topdown=False, followlinks=False):
            for a in archivos:
                f = Path(raiz) / a
                try:
                    if _es_enlace(f):
                        continue
                    st = f.stat()
                    if st.st_mtime < limite:
                        f.unlink()
                        liberado += st.st_size
                except OSError:
                    pass
            for d in dirs:
                try:
                    if not _es_enlace(Path(raiz) / d):
                        (Path(raiz) / d).rmdir()
                except OSError:
                    pass
    return liberado


# ---------- Procesos ----------
_vistos = {}       # pid -> psutil.Process (para medir su CPU entre una vuelta y otra)
_bajados = {}      # pid -> (nombre, prioridad original): los que se bajaron de prioridad
_ultima_vuelta = {"t": 0.0}


def _pid_al_frente():
    try:
        import win32gui
        import win32process
        return win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[1]
    except Exception:
        return None


def _intocable(nombre):
    base = Path(nombre or "").stem.lower()
    return not nombre or bloqueado(nombre) or base in NO_OFRECER or base in NO_TOCAR


def _protegidos():
    """Jarvis y sus hijos, y la app que tienes al frente con toda su familia de procesos."""
    pids = set()
    for raiz in (os.getpid(), _pid_al_frente()):
        if not raiz:
            continue
        try:
            p = psutil.Process(raiz)
            pids.add(p.pid)
            pids.update(h.pid for h in p.children(recursive=True))
            if raiz != os.getpid():   # el navegador al frente: todos sus procesos hermanos también
                nombre = p.name()
                pids.update(q.pid for q in psutil.process_iter(["name"]) if q.info["name"] == nombre)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def medir_procesos():
    """[(proceso, nombre, cpu % promedio desde la vuelta anterior, MB)] de lo que se puede tocar."""
    protegidos = _protegidos()
    salida, vivos = [], set()
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            nombre = p.info["name"] or ""
            if p.pid in protegidos or _intocable(nombre):
                continue
            vivos.add(p.pid)
            viejo = _vistos.get(p.pid)
            if viejo is None:
                _vistos[p.pid] = p
                p.cpu_percent(None)   # primera medición: el promedio sale en la próxima vuelta
                continue
            cpu = viejo.cpu_percent(None) / (psutil.cpu_count() or 1)
            salida.append((viejo, nombre, cpu, p.info["memory_info"].rss / 1e6))
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    for pid in list(_vistos):
        if pid not in vivos:
            _vistos.pop(pid, None)
            _bajados.pop(pid, None)
    return salida


def _recortar(proceso):
    """EmptyWorkingSet: Windows saca de la RAM las páginas que el proceso no está usando."""
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x0400 | 0x0100, False, proceso.pid)   # QUERY_INFORMATION | SET_QUOTA
    if not h:
        return False
    try:
        return bool(ctypes.windll.psapi.EmptyWorkingSet(h))
    finally:
        k.CloseHandle(h)


def regular_procesos(ram_pct=None, medidos=None):
    """Recorta la memoria de lo inactivo (con la RAM alta) y regula la prioridad de lo que se
    come el CPU de fondo. Devuelve las frases de lo que hizo."""
    ram_pct = psutil.virtual_memory().percent if ram_pct is None else ram_pct
    medidos = medir_procesos() if medidos is None else medidos
    hecho = []
    if ram_pct >= RAM_RECORTAR_PCT:
        libre_antes = psutil.virtual_memory().available
        recortados = {}
        for proc, nombre, cpu, mb in medidos:
            if cpu < 1 and mb >= RECORTAR_MIN_MB:
                try:
                    if _recortar(proc):
                        recortados[nombre] = recortados.get(nombre, 0) + 1
                except Exception:
                    pass
        if recortados:
            time.sleep(1.5)
            # lo que de verdad quedó libre (la suma de lo que soltó cada proceso exagera: parte
            # se queda en la memoria comprimida o de reserva de Windows)
            libres = max(0, psutil.virtual_memory().available - libre_antes) / 1e6
            if libres >= 50:
                apps = ", ".join(Path(n).stem for n in list(recortados)[:4])
                hecho.append(f"Liberé {libres / 1000:.1f} GB de RAM de lo que no estabas usando ({apps}).")
    for proc, nombre, cpu, mb in medidos:
        try:
            if proc.pid not in _bajados and cpu >= CPU_FONDO_PCT:
                original = proc.nice()
                if original in (psutil.NORMAL_PRIORITY_CLASS, psutil.ABOVE_NORMAL_PRIORITY_CLASS):
                    proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    _bajados[proc.pid] = (nombre, original)
                    hecho.append(f"Bajé la prioridad de {Path(nombre).stem} (usaba {cpu:.0f}% del procesador en segundo plano).")
            elif proc.pid in _bajados and cpu < CPU_CALMADO_PCT:
                proc.nice(_bajados.pop(proc.pid)[1])   # ya se calmó: como estaba
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            _bajados.pop(proc.pid, None)
    return hecho


def restaurar_prioridades():
    """Al cerrar Jarvis: todos los procesos vuelven a su prioridad original."""
    for pid, (_, original) in list(_bajados.items()):
        try:
            psutil.Process(pid).nice(original)
        except Exception:
            pass
        _bajados.pop(pid, None)


def _pids_con_ventana():
    """Los procesos que tienen alguna ventana visible (ahí podría haber algo sin guardar)."""
    import win32gui
    import win32process
    pids = set()

    def cada(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                pids.add(win32process.GetWindowThreadProcessId(h)[1])
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cada, None)
    return pids


def _cerrar_inactivos(medidos):
    """Solo los que TÚ pusiste en config.json → mantenimiento_cerrar_inactivos, y solo si no
    tienen ventana ni usan el procesador (no hay nada que guardar)."""
    permitidos = {Path(n).stem.lower() for n in (_CFG.get("mantenimiento_cerrar_inactivos") or [])}
    if not permitidos:
        return []
    hecho = []
    con_ventana = _pids_con_ventana()
    for nombre in {n for _, n, cpu, _ in medidos if Path(n).stem.lower() in permitidos}:
        familia = [(p, c) for p, n, c, _ in medidos if n == nombre]
        if any(c >= 1 for _, c in familia) or any(p.pid in con_ventana for p, _ in familia):
            continue
        for p, _ in familia:
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        hecho.append(f"Cerré {Path(nombre).stem}, que estaba abierto sin usarse.")
    return hecho


# ---------- El mantenimiento automático ----------
def _historial():
    d = _cargar_avisados()
    return d.get("historial", []), d


def _anotar(acciones):
    if not acciones:
        return
    historial, d = _historial()
    historial.append({"ts": time.time(), "acciones": acciones})
    d["historial"] = historial[-60:]
    try:
        AVISADOS_PATH.parent.mkdir(parents=True, exist_ok=True)
        AVISADOS_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    for a in acciones:
        print(f"[Mantenimiento: {a}]")


def limpiar_ahora():
    """Temporales viejos, la caché de voz y los reportes de fallos viejos. Frases de lo hecho."""
    hecho = []
    texto = _limpiar_temporales_real()
    try:
        gb = float(texto.split("Liberé ")[1].split(" GB")[0])
    except (IndexError, ValueError):
        gb = 0
    if gb >= 0.01:
        hecho.append(f"Borré {gb:.2f} GB de archivos temporales viejos.")
    voz = _recortar_cache_voz()
    if voz >= 1e6:
        hecho.append(f"Recorté {voz / 1e6:.0f} MB de mi caché de voz.")
    viejos = _borrar_viejos()
    if viejos >= 1e6:
        hecho.append(f"Borré {viejos / 1e6:.0f} MB de reportes de fallos viejos de Windows.")
    _avisados["limpieza"] = time.time()
    return hecho


def una_vuelta(forzar_limpieza=False):
    """Lo que hace cada vuelta: limpieza (cada 6 h, o si los temporales crecieron), memoria y
    procesador. Devuelve las frases de lo que hizo (también quedan anotadas)."""
    hecho = []
    ahora = time.time()
    if forzar_limpieza or ahora - _avisados.get("limpieza", 0) >= LIMPIEZA_CADA_SEG \
            or tamano_temporales_gb() >= UMBRAL_TEMP_GB:
        hecho += limpiar_ahora()
    ram = psutil.virtual_memory().percent
    if ram >= RAM_RECORTAR_PCT:
        try:   # lo primero: lo que Jarvis mismo tiene en la RAM (los modelos en CPU)
            import escuchar
            escuchar.liberar_memoria()
        except Exception:
            pass
    medidos = medir_procesos()
    if _CFG.get("mantenimiento_procesos", True):
        hecho += regular_procesos(ram, medidos)
        hecho += _cerrar_inactivos(medidos)
    _anotar(hecho)
    _guardar_avisados()
    return hecho


def _vigilar():
    intervalo = _CFG.get("mantenimiento_intervalo_min", 10) * 60
    time.sleep(120)  # deja que Jarvis termine de arrancar antes de la primera revisión
    medir_procesos()  # primera medición del CPU (el promedio sale en la siguiente vuelta)
    while True:
        time.sleep(intervalo)
        try:
            # En plena exposición NO: que nada cambie de prioridad ni se mueva frente al jurado
            if _CFG.get("mantenimiento_activo", True) and not _exponiendo():
                una_vuelta()
        except Exception as e:
            print(f"[Error en mantenimiento: {type(e).__name__}: {str(e)[:120]}]")


def _exponiendo():
    try:
        return bool(_ocupado and _ocupado())
    except Exception:
        return False


def iniciar(cfg, notificar=None, ocupado=None):
    """Se puede llamar varias veces (iniciar_genesis.pyw reinicia main() si algo falla): el
    hilo de vigilancia es uno solo."""
    global _CFG, _notificar, _ocupado, _hilo
    _CFG = cfg
    _notificar = notificar
    _ocupado = ocupado
    if cfg.get("mantenimiento_activo", True) and (_hilo is None or not _hilo.is_alive()):
        import atexit
        atexit.register(restaurar_prioridades)
        _hilo = threading.Thread(target=_vigilar, daemon=True, name="mantenimiento")
        _hilo.start()


# ---------- Skills ----------
@skill("revisar_equipo",
       "Revisa el equipo (CPU, RAM, disco, temporales, papelera) y le da mantenimiento AHORA "
       "mismo: borra temporales viejos y cachés, libera la memoria de lo que no se usa y regula "
       "lo que se come el procesador de fondo. Úsala cuando pregunten por qué va lenta la "
       "computadora o pidan acelerarla, limpiarla u optimizarla.", terminal=True)
def revisar_equipo():
    hecho = una_vuelta(forzar_limpieza=True)
    e = estado_equipo()
    texto = " ".join(hecho) if hecho else "No encontré nada que limpiar ni procesos que regular."
    texto += f" Ahora: {_resumen(e)}"
    if e["disco_libre_gb"] < UMBRAL_DISCO_LIBRE_GB:
        texto += (" Te quedan pocos gigas libres; lo que más ocupa lo ves en Configuración, "
                  "Almacenamiento.")
    if e["papelera_gb"] >= UMBRAL_PAPELERA_GB:
        texto += f" Tienes {e['papelera_gb']:.1f} GB en la papelera; si quieres, dime que la vacíe."
    if e["ram_pct"] >= UMBRAL_RAM_PCT and e["top_ram"]:
        nombre, mb = e["top_ram"][0]
        texto += f" {Path(nombre).stem} usa {mb / 1000:.1f} GB de RAM; cerrarle pestañas o ventanas ayudaría."
    return texto


@skill("mantenimiento_reciente",
       "Cuenta lo que Jarvis hizo solo para que la computadora vaya rápido (temporales borrados, "
       "memoria liberada, procesos regulados): '¿qué has limpiado?', '¿qué hiciste para que la "
       "compu vaya rápido?'.", terminal=True)
def mantenimiento_reciente(horas=24):
    historial, _ = _historial()
    limite = time.time() - float(horas or 24) * 3600
    recientes = [a for h in historial if h["ts"] >= limite for a in h["acciones"]]
    if not recientes:
        return "En las últimas 24 horas no hizo falta limpiar ni regular nada."
    return f"En las últimas 24 horas hice {len(recientes)} cosas. Lo más reciente: " + " ".join(recientes[-4:])


# Por voz piden confirmación (una orden mal oída o un texto leído de Teams no deben poder
# borrar nada).
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
    print(_resumen(estado_equipo()))
    print("Lo que haría ahora:", una_vuelta() or "nada")
