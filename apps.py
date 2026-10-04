"""Abrir y cerrar apps, con tolerancia a nombres mal pronunciados (estilo Alexa/Siri)."""
import json
import os
import re
import subprocess
import threading
import time
import winreg
from difflib import SequenceMatcher
from pathlib import Path

import psutil
from pywinauto import Desktop

import memoria
import skills
from skills import Fallo, _norm, skill

INDICE_PATH = Path(__file__).parent / "datos" / "apps.json"
UMBRAL_SEGURO = 0.6   # a partir de aquí se abre sin más comentario
UMBRAL_INTENTO = 0.55  # por debajo hay algo parecido, pero se avisa que es una suposición
SIN_VENTANA = 0x08000000  # que pythonw no muestre una consola al llamar a PowerShell

MENUS = [
    Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop",
    Path.home() / "Desktop",
]
IGNORAR = ("desinstalar", "uninstall", "leeme", "readme", "manual", "license", "licencia")
# "Actualización de...", parches y similares: aparecen en el registro pero no son programas
IGNORAR_REGISTRO = IGNORAR + ("update for", "actualizacion de", "security update",
                              "hotfix", "controlador", "driver", "runtime", "redistributable")
IGNORAR_STEAM = ("redistributable", "steamworks common", "proton", "steam linux runtime")

# Terminales y ajustes del sistema: Genesis los abre, pero solo tras confirmar (a diferencia
# del resto de apps, que abre directo). Se detecta por el destino real, no por el nombre
# dicho, para que no dependa de en qué idioma esté instalado Windows.
RIESGOSAS = ("powershell.exe", "cmd.exe", "windowsterminal.exe", "wt.exe", "ms-settings:")

_indice = {}  # nombre visible -> destino que entiende os.startfile
_lock = threading.Lock()

# Aproxima cómo suena una palabra en español: "wasap" ≈ "whatsapp", "espotifai" ≈ "spotify"
_REGLAS = [("ph", "f"), ("qu", "k"), ("ll", "y"), ("ce", "se"), ("ci", "si"), ("ge", "je"),
           ("gi", "ji"), ("z", "s"), ("c", "k"), ("v", "b"), ("w", "gu"), ("h", ""),
           ("y", "i"), ("x", "ks")]


def fonetica(texto):
    t = _norm(texto)
    for a, b in _REGLAS:
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def puntaje(consulta, nombre):
    """Parecido entre 0 y 1 entre lo que dijo el usuario y el nombre de una app."""
    q, n = fonetica(consulta), fonetica(nombre)
    qs, ns = q.replace(" ", ""), n.replace(" ", "")
    if len(qs) < 2 or not ns:
        return 0.0
    if qs == ns:
        return 1.0
    p = SequenceMatcher(None, qs, ns).ratio()
    # Dos textos de longitud muy distinta pueden compartir letras sueltas y dar un ratio
    # engañosamente alto (p. ej. una frase larga sin sentido "parecida" a una app corta);
    # se castiga para no confundir eso con un nombre mal pronunciado.
    corta, larga = sorted((len(qs), len(ns)))
    if larga and corta / larga < 0.5:
        p *= corta / larga * 2
    if len(qs) >= 3 and len(ns) >= 3 and (qs in ns or ns in qs):  # "opera" -> "opera gx"
        p = max(p, 0.85 if ns.startswith(qs) or qs.startswith(ns) else 0.78)
    tq, tn = q.split(), n.split()
    if tq and all(any(a == b or (len(a) >= 3 and b.startswith(a)) for b in tn) for a in tq):
        p = max(p, 0.9)  # "word" -> "microsoft word"
    return p


# ---------- Bibliotecas de juegos (no siempre tienen acceso directo en el menú Inicio) ----------
def _ruta_steam():
    for hive, subkey, valor in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, subkey) as k:
                return Path(winreg.QueryValueEx(k, valor)[0])
        except OSError:
            continue
    return Path("C:/Program Files (x86)/Steam")


def _bibliotecas_steam(base):
    """Steam guarda sus discos adicionales en steamapps/libraryfolders.vdf (no es JSON, es el
    formato propio de Valve); basta con sacar las rutas con una expresión regular."""
    rutas = [base]
    vdf = base / "steamapps" / "libraryfolders.vdf"
    try:
        texto = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return rutas
    for m in re.finditer(r'"path"\s+"([^"]+)"', texto):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p not in rutas:
            rutas.append(p)
    return rutas


def _juegos_steam():
    """Muchos juegos de Steam no tienen acceso directo en el menú Inicio ni aparecen en
    Get-StartApps; sus datos reales viven en un appmanifest_<id>.acf por juego."""
    juegos = {}
    for biblioteca in _bibliotecas_steam(_ruta_steam()):
        carpeta = biblioteca / "steamapps"
        if not carpeta.is_dir():
            continue
        for acf in carpeta.glob("appmanifest_*.acf"):
            m_id = re.search(r"appmanifest_(\d+)\.acf", acf.name)
            try:
                texto = acf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m_nombre = re.search(r'"name"\s+"([^"]+)"', texto)
            if m_id and m_nombre and not any(x in m_nombre.group(1).lower() for x in IGNORAR_STEAM):
                juegos[m_nombre.group(1)] = f"steam://rungameid/{m_id.group(1)}"
    return juegos


def _juegos_epic():
    carpeta = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Epic/EpicGamesLauncher/Data/Manifests"
    juegos = {}
    for item in carpeta.glob("*.item") if carpeta.is_dir() else ():
        try:
            datos = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        nombre, appname = datos.get("DisplayName"), datos.get("AppName")
        if nombre and appname:
            juegos[nombre] = f"com.epicgames.launcher://apps/{appname}?action=launch&silent=true"
    return juegos


# ---------- Todo lo que Windows tiene registrado como "programa instalado" ----------
def _programas_registrados():
    """La lista que usa "Aplicaciones instaladas" de Windows: es la fuente más completa que
    hay (más que el menú Inicio), porque cualquier instalador decente se registra ahí aunque
    no cree ningún acceso directo. Se arma con lo que trae DisplayIcon si de verdad apunta a
    un .exe; si no, y la carpeta de instalación tiene un único .exe, se usa ese (si hay varios
    no se adivina, para no abrir el programa equivocado)."""
    claves = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    programas = {}
    for hive, ruta in claves:
        try:
            raiz = winreg.OpenKey(hive, ruta)
        except OSError:
            continue
        with raiz:
            for i in range(winreg.QueryInfoKey(raiz)[0]):
                try:
                    with winreg.OpenKey(raiz, winreg.EnumKey(raiz, i)) as k:
                        def leer(nombre, _k=k):
                            try:
                                return winreg.QueryValueEx(_k, nombre)[0]
                            except OSError:
                                return None

                        nombre = leer("DisplayName")
                        if (not nombre or leer("SystemComponent") == 1
                                or any(x in nombre.lower() for x in IGNORAR_REGISTRO)):
                            continue
                        icono = (leer("DisplayIcon") or "").split(",")[0].strip('"')
                        destino = None
                        if icono.lower().endswith(".exe") and Path(icono).is_file():
                            destino = icono
                        else:
                            instalacion = leer("InstallLocation")
                            if instalacion and Path(instalacion).is_dir():
                                exes = [e for e in Path(instalacion).glob("*.exe")
                                       if "unins" not in e.stem.lower()]
                                if len(exes) == 1:
                                    destino = str(exes[0])
                        if destino:
                            programas.setdefault(nombre, destino)
                except OSError:
                    continue
    return programas


# ---------- Índice de apps instaladas ----------
def _escanear():
    apps = {}
    for base in MENUS:
        if base.exists():
            for p in base.rglob("*.lnk"):
                if not any(x in _norm(p.stem) for x in IGNORAR):
                    apps.setdefault(p.stem, str(p))
    try:  # apps de la Tienda (WhatsApp, Spotify, Netflix...) y clásicas
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=40,
            stdin=subprocess.DEVNULL, creationflags=SIN_VENTANA)
        datos = json.loads(r.stdout or "[]")
        for a in [datos] if isinstance(datos, dict) else datos:
            nombre, appid = a.get("Name"), a.get("AppID")
            if nombre and appid and not any(x in _norm(nombre) for x in IGNORAR):
                # Juegos de Steam/Epic vienen como URL y algunas apps como ruta: se abren tal cual
                directo = re.match(r"^[a-z][\w+.-]*://", appid, re.I) or os.path.isabs(appid)
                apps.setdefault(nombre, appid if directo else "shell:AppsFolder\\" + appid)
    except Exception as e:
        print(f"[No pude leer las apps de la Tienda: {type(e).__name__}]")
    try:
        for nombre, destino in {**_juegos_epic(), **_juegos_steam()}.items():
            apps.setdefault(nombre, destino)
    except Exception as e:
        print(f"[No pude leer las bibliotecas de juegos: {type(e).__name__}]")
    try:  # último y más ruidoso: solo rellena lo que ningún acceso directo ya cubría
        for nombre, destino in _programas_registrados().items():
            apps.setdefault(nombre, destino)
    except Exception as e:
        print(f"[No pude leer los programas instalados del registro: {type(e).__name__}]")
    return apps


def reindexar():
    global _indice
    apps = _escanear()
    if apps:
        with _lock:
            _indice = apps
        INDICE_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDICE_PATH.write_text(json.dumps(apps, ensure_ascii=False), encoding="utf-8")
    return len(apps)


def iniciar():
    """Carga el índice guardado al instante y lo refresca en segundo plano."""
    global _indice
    try:
        _indice = json.loads(INDICE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _indice = {}
    threading.Thread(target=reindexar, daemon=True, name="indice-apps").start()


def _candidatos():
    """(nombre, destino, prioridad): tu config manda sobre las apps base y estas sobre el índice."""
    with _lock:
        c = [(n, d, 0) for n, d in _indice.items()]
    c += [(n, d, 1) for n, d in skills.APPS_BASE.items()]
    c += [(n, d, 2) for n, d in skills._CFG.get("apps", {}).items()]
    return c


def buscar_app(consulta):
    """Mejor app que se parece a la consulta, o None si no hay ninguna razonable.

    Siempre que hay algo mínimamente parecido lo abre directo (no pide que se repita el
    nombre); solo si el parecido es muy bajo devuelve None."""
    puntuados = sorted(((puntaje(consulta, n), pr, n, d) for n, d, pr in _candidatos()),
                       reverse=True)
    if not puntuados or puntuados[0][0] < UMBRAL_INTENTO:
        return None
    p, _, n, d = puntuados[0]
    return n, d, p


def vocabulario():
    """Pista para Whisper: nombres que debería reconocer bien al oír tus órdenes."""
    nombres = ([skills._CFG.get("name", "Jarvis"), "YouTube", "Spotify", "WhatsApp", "PowerPoint",
                "Chrome", "Visual Studio Code", "Teams", "diapositiva"]
               + list(skills._CFG.get("apps", {})) + list(skills._CFG.get("vocabulario_extra", [])))
    return ", ".join(dict.fromkeys(nombres)) + "."


# ---------- Cerrar apps y ventanas ----------
# Lo que nunca se cierra por pedido de voz: procesos del propio Genesis o del sistema. Un
# nombre mal pronunciado que "se parezca" a uno de estos no debe poder tumbar Windows.
BLOQUEADAS = {"genesis", "jarvis", "python", "pythonw", "explorer", "svchost", "system", "registry",
             "idle", "csrss", "smss", "wininit", "winlogon", "services", "lsass", "dwm",
             "fontdrvhost", "searchhost", "shellexperiencehost", "textinputhost",
             "runtimebroker", "sihost", "conhost", "taskhostw", "ctfmon", "audiodg"}


def _ventanas_visibles():
    """título -> elemento de ventana, solo lo que de verdad ves (para cerrar como con la X,
    con oportunidad de que la app pregunte si guardar)."""
    resultado = {}
    try:
        for w in Desktop(backend="uia").windows():
            try:
                titulo = w.window_text()
                if titulo and titulo.strip() and w.is_visible():
                    resultado.setdefault(titulo, w)
            except Exception:
                continue
    except Exception:
        pass
    return resultado


def bloqueado(nombre_proceso):
    """True si el proceso ("explorer.exe", "pythonw.exe"...) no se debe cerrar nunca. Se compara
    sin la extensión: _norm("explorer.exe") da "explorer exe", que no coincide con la lista."""
    return _norm(Path(nombre_proceso or "").stem) in BLOQUEADAS


def _nombre_pid(pid):
    try:
        return psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _procesos_cerrables():
    """nombre (sin .exe) -> [pids], de lo que corre y no está en BLOQUEADAS. Se guardan TODOS
    los procesos de cada app: el navegador, Teams o Spotify abren varios, y quedarse con el
    primero que aparecía podía cerrar un proceso hijo cualquiera en vez de la app."""
    resultado = {}
    for p in psutil.process_iter(["pid", "name"]):
        try:
            nombre = p.info["name"] or ""
            base = Path(nombre).stem
            if base and not bloqueado(nombre):
                resultado.setdefault(base, []).append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return resultado


def _ventanas_de(pids):
    """hwnds visibles de nivel superior que pertenecen a esos procesos."""
    try:
        import win32gui
        import win32process
    except ImportError:
        return []
    pids, hwnds = set(pids), []

    def cada(h, _):
        try:
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip() \
                    and win32process.GetWindowThreadProcessId(h)[1] in pids:
                hwnds.append(h)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(cada, None)
    except Exception:
        pass
    return hwnds


def _cerrar_con_la_x(pids, espera=4.0):
    """Pide a las ventanas de esos procesos que se cierren (como darle a la X: la app puede
    preguntar si guardar). True si ya no queda ninguna ventana suya."""
    try:
        import win32con
        import win32gui
    except ImportError:
        return False
    hwnds = _ventanas_de(pids)
    if not hwnds:
        return False
    for h in hwnds:
        try:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    fin = time.time() + espera
    while time.time() < fin:
        time.sleep(0.3)
        if not _ventanas_de(pids):
            return True
    return False


def _terminar_pids(pids):
    """Termina los procesos a la fuerza. OJO: en Windows psutil.terminate() ES un cierre
    forzado (TerminateProcess), no un "por favor ciérrate": lo no guardado se pierde. Por eso
    solo se llama tras confirmar."""
    ok = False
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            if bloqueado(proc.name()):
                continue
            proc.kill()
            ok = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return ok


def _terminar_pid(pid, forzar):
    """Compatibilidad: cierre forzado de un solo proceso (solo si forzar=True)."""
    return _terminar_pids([pid]) if forzar else False


@skill("cerrar_app",
       "Cierra una aplicación o ventana abierta, por su nombre aproximado (spotify, discord, "
       "el navegador, un juego...). Primero intenta cerrarla con normalidad, como si le dieras "
       "a la X (así puede preguntar si guardar cambios); si sigue abierta y el usuario insiste "
       "o pide forzarla, la termina ya.",
       {"nombre": {"type": "string", "description": "Nombre aproximado de la app o ventana a cerrar"},
        "forzar": {"type": "boolean", "description": "true para terminarla ya, sin esperar a que cierre sola"}},
       requeridos=["nombre"], sensible=True)
def cerrar_app(nombre, forzar=False):
    if forzar:  # terminar a la fuerza puede perder trabajo sin guardar: siempre se confirma
        if memoria.pedir_confirmacion is None:
            return Fallo("Forzar el cierre necesita confirmación y no puedo pedirla ahora.")
        if not memoria.pedir_confirmacion(f"¿Fuerzo el cierre de {nombre}? Se puede perder lo que no esté guardado."):
            return Fallo("El usuario canceló. No cerré nada.")

    ventanas = _ventanas_visibles()
    candidatas = sorted(((puntaje(nombre, t), t, w) for t, w in ventanas.items()), reverse=True)
    if candidatas and candidatas[0][0] >= UMBRAL_INTENTO:
        _, titulo, w = candidatas[0]
        try:
            pid = w.process_id()
            clase = w.element_info.class_name
        except Exception:
            pid, clase = None, ""
        if pid and bloqueado(_nombre_pid(pid)):
            # Una carpeta del Explorador sí se puede cerrar (como con la X); el resto de
            # ventanas del sistema (barra de tareas, escritorio) o de Genesis, nunca.
            if clase != "CabinetWClass" or forzar:
                return Fallo(f"'{titulo}' es parte del sistema o del asistente; no la cierro.")
        try:
            if not forzar:
                w.close()
        except Exception:
            pass
        # Se comprueba que se fue la VENTANA, no el proceso: apps como el navegador
        # siguen corriendo con otras ventanas abiertas y eso no es un fallo.
        for _ in range(10):
            time.sleep(0.3)
            if titulo not in _ventanas_visibles():
                return f"Cerré {titulo}."
        if forzar and pid and _terminar_pids([pid]):
            return f"Forcé el cierre de {titulo}."
        return Fallo(f"'{titulo}' no se cerró todavía; puede estar preguntando si guardar "
                     "cambios, o dime que la fuerce.")

    procesos = _procesos_cerrables()
    candidatos = sorted(((puntaje(nombre, n), n, pids) for n, pids in procesos.items()),
                        key=lambda c: c[0], reverse=True)
    if candidatos and candidatos[0][0] >= UMBRAL_INTENTO:
        _, n, pids = candidatos[0]
        # Primero como con la X, aunque no se haya encontrado por el título de la ventana
        if not forzar and _cerrar_con_la_x(pids):
            return f"Cerré {n}."
        if not forzar:
            # Sin ventanas que cerrar (o no quisieron cerrarse): matar el proceso pierde lo
            # no guardado, así que se pregunta antes, igual que cuando se pide forzar.
            if memoria.pedir_confirmacion is None or not memoria.pedir_confirmacion(
                    f"{n} no se cierra por las buenas. ¿Lo cierro a la fuerza? Se perdería lo no guardado."):
                return Fallo(f"No cerré {n}.")
        if _terminar_pids(pids):
            return f"Cerré {n}."
        return Fallo(f"No pude cerrar '{n}'.")
    return Fallo(f"No encontré ninguna app o ventana abierta parecida a '{nombre}'.")


def _destino_real(destino):
    """Para un acceso directo (.lnk), el programa al que apunta; si no, el mismo destino. Así
    un acceso directo a PowerShell del menú Inicio también pide confirmación."""
    if not destino.lower().endswith(".lnk"):
        return destino
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        return win32com.client.Dispatch("WScript.Shell").CreateShortcut(destino).TargetPath or destino
    except Exception:
        return destino


# ---------- Skill ----------
@skill("abrir_app",
       "Abre cualquier aplicación o programa instalado en el equipo por su nombre, aunque el "
       "nombre sea aproximado (whatsapp, spotify, word, excel, opera, discord, calculadora...). "
       "Pásale el nombre tal como lo dijo el usuario.",
       {"nombre": {"type": "string", "description": "Nombre de la aplicación"}})
def abrir_app(nombre):
    mejor = buscar_app(nombre)
    if mejor is None and not _indice:
        reindexar()
        mejor = buscar_app(nombre)
    if mejor is None:
        return Fallo(f"No encontré ninguna app parecida a '{nombre}'.")
    n, destino, p = mejor

    real = _destino_real(os.path.expandvars(destino)).lower()
    if any(d.endswith(r) or d.startswith(r) for d in (destino.lower(), real) for r in RIESGOSAS):
        if memoria.pedir_confirmacion is None:
            return Fallo("Esa abre una terminal o los ajustes del sistema y no puedo pedir confirmación ahora, así que no la abrí.")
        if not memoria.pedir_confirmacion(f"¿Confirmas que abra {n}?"):
            return Fallo("El usuario canceló. No abrí nada.")

    os.startfile(os.path.expandvars(destino))
    return f"Abriendo {n}." if p >= UMBRAL_SEGURO else f"Abriendo lo más parecido a '{nombre}': {n}."
