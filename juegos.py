"""Jarvis juega: mandos exactos a cualquier juego de PC (control virtual de Xbox o teclado).

Fase 1 (este módulo): la base para que Jarvis de verdad apriete botones en un juego.
- Control virtual de Xbox 360 (vgamepad + driver ViGEmBus): el juego lo ve como un control más.
  Si tienes otro control conectado, el juego puede tomar el virtual como jugador 2; desconecta
  el tuyo o elige "teclado" en el perfil.
- Teclado con códigos de escaneo (SendInput + KEYEVENTF_SCANCODE): lo que leen los juegos (las
  teclas virtuales normales muchos juegos las ignoran).
- Combos por voz: "Jarvis, atrás adelante dos" o "haz el gancho" (un combo guardado) se ejecutan
  al instante, con el ritmo de cuadros del juego (60 por segundo), sin pasar por el modelo.
  Notación de peleas: U/D/F/B (arriba, abajo, adelante, atrás), 1-4 (golpes), BL (bloqueo),
  THROW, FLIP, AMP; "+" = al mismo tiempo ("D+BL"); "espera" = una pausa. Adelante/atrás se
  voltean solos según tu lado de la pantalla (lado_jugador).

Nunca en juegos en línea competitivos (Call of Duty, Destiny, Valorant, Fortnite...): ahí un bot
es trampa contra personas reales y los anti-trampas (Ricochet, BattlEye, Vanguard, EAC) lo
detectan y banean la cuenta. EN_LINEA lista los procesos que se rechazan.

Todo lo que se ejecuta pasa por genesis.ejecutar_herramienta, así que queda en la bitácora
(bitacora.py): "¿estás jugando por mí?" se contesta con la verdad.
"""
import ctypes
import json
import re
import threading
import time
import unicodedata
from pathlib import Path

from skills import Callado, Fallo, skill

BASE = Path(__file__).resolve().parent
ARCHIVO = BASE / "datos" / "juegos.json"      # combos guardados, lado y ajustes por juego
CUADRO = 1 / 60                               # los juegos de pelea van a 60 cuadros por segundo

# ---------- Perfiles ----------
# botones: notación -> botón del control de Xbox; teclado: notación -> tecla (si control = teclado)
PERFILES = {
    "mortal_kombat": {
        "nombre": "Mortal Kombat",
        "procesos": ("mk10.exe", "mk11.exe", "mk12.exe", "mk1.exe", "mortalkombat"),
        "control": "pad",
        "botones": {"1": "X", "2": "Y", "3": "A", "4": "B", "BL": "RT", "THROW": "LB", "FLIP": "LT", "AMP": "RB"},
        "teclado": {"U": "W", "D": "S", "L": "A", "R": "D", "1": "J", "2": "I", "3": "K", "4": "L",
                    "BL": "SPACE", "THROW": "U", "FLIP": "O", "AMP": "H"},
        "cuadros": 3,          # cuadros que se mantiene cada paso
        "hueco": 2,            # cuadros entre paso y paso
    },
    "generico": {
        "nombre": "el juego",
        "procesos": (),
        "control": "teclado",
        "botones": {"1": "X", "2": "Y", "3": "A", "4": "B", "BL": "RT", "THROW": "LB", "FLIP": "LT", "AMP": "RB"},
        "teclado": {"U": "W", "D": "S", "L": "A", "R": "D", "1": "J", "2": "I", "3": "K", "4": "L",
                    "BL": "SPACE", "THROW": "U", "FLIP": "O", "AMP": "H"},
        "cuadros": 3,
        "hueco": 2,
    },
}

# Juegos en línea competitivos: Jarvis no les manda nada (trampa + baneo)
EN_LINEA = re.compile(
    r"^(cod|codhq|modernwarfare|blackops|bo6|mw3|warzone|destiny2|valorant|fortniteclient|r5apex|cs2|csgo|"
    r"overwatch|rainbowsix|r6|leagueoflegends|league of legends|pubg|tslgame|escapefromtarkov|dota2|"
    r"rocketleague|eafc|fifa|thefinals|marvelrivals|deadbydaylight|bf2042|battlefield)", re.I)

# ---------- Notación ----------
PALABRAS = [
    (r"\babajo adelante\b|\babajo-adelante\b", "D+F"), (r"\babajo atras\b|\babajo-atras\b", "D+B"),
    (r"\batras\b|\bback\b", "B"), (r"\badelante\b|\bfrente\b|\bforward\b", "F"),
    (r"\babajo\b|\bdown\b", "D"), (r"\barriba\b|\bsalta\b|\bup\b", "U"),
    (r"\buno\b", "1"), (r"\bdos\b", "2"), (r"\btres\b", "3"), (r"\bcuatro\b", "4"),
    (r"\bbloqueo\b|\bbloquea\b|\bbloquear\b|\bblock\b", "BL"), (r"\bagarre\b|\bagarra\b|\bthrow\b", "THROW"),
    (r"\bamplifica\w*\b|\bamplify\b", "AMP"), (r"\bvoltea\w*\b|\bflip\b", "FLIP"),
    (r"\bespera\b|\bpausa\b|\bwait\b", "ESPERA"), (r"\bmas\b|\bjunto con\b|\bal mismo tiempo que\b", "+"),
    (r"\by\b", " "),   # "atrás, adelante y dos" es una secuencia, no "al mismo tiempo"
]
FICHAS = {"U", "D", "F", "B", "1", "2", "3", "4", "BL", "THROW", "FLIP", "AMP", "ESPERA"}
PAD = {"A", "B", "X", "Y", "LB", "RB", "LT", "RT", "START", "BACK", "LS", "RS"}


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def interpretar(secuencia):
    """'atrás adelante dos', 'B F 2', 'D+BL, 4' -> [['B'], ['F'], ['2']] (pasos; cada paso son
    las fichas que se aprietan juntas). ValueError si algo no se entiende."""
    t = " " + _norm(secuencia) + " "
    t = t.replace(",", " ").replace(">", " ").replace("~", " ").replace(";", " ")
    for patron, ficha in PALABRAS:
        t = re.sub(patron, f" {ficha} ", t)
    t = re.sub(r"\s*\+\s*", "+", t.strip())
    pasos = []
    for grupo in t.upper().split():
        fichas = [f for f in grupo.split("+") if f]
        for f in fichas:
            if f not in FICHAS and not f.startswith("PAD_") and not f.startswith("TECLA_"):
                raise ValueError(f"no entiendo '{f.lower()}'")
        if fichas:
            pasos.append(fichas)
    if not pasos:
        raise ValueError("la secuencia está vacía")
    return pasos


def es_notacion(texto):
    """¿Todo lo que dijo es una secuencia de botones? ('atrás adelante dos' sí; 'abre youtube' no)."""
    try:
        pasos = interpretar(re.sub(r"^\s*(jarvis\W*)?(haz|tira|mete|echa|lanza|aplica)?\s*", "", _norm(texto)))
    except ValueError:
        return False
    return len(pasos) >= 2 or any(f not in ("U", "D", "F", "B", "ESPERA") for p in pasos for f in p)


def resolver(fichas, perfil, lado):
    """Fichas de un paso -> ('pad', [botones, dpad]) o ('teclado', [teclas]). Adelante/atrás según
    el lado: a la izquierda miras a la derecha (F = derecha)."""
    derecha_es_f = lado != "derecha"
    salida = []
    for f in fichas:
        if f in ("F", "B"):
            f = "R" if (f == "F") == derecha_es_f else "L"
        salida.append(f)
    if perfil["control"] == "pad":
        r = []
        for f in salida:
            if f in ("U", "D", "L", "R"):
                r.append("DPAD_" + f)
            elif f.startswith("PAD_"):
                r.append(f[4:])
            elif f in perfil["botones"]:
                r.append(perfil["botones"][f])
            elif f != "ESPERA":
                raise ValueError(f"el perfil no tiene '{f}'")
        return r
    r = []
    for f in salida:
        if f.startswith("TECLA_"):
            r.append(f[6:])
        elif f in perfil["teclado"]:
            r.append(perfil["teclado"][f])
        elif f != "ESPERA":
            raise ValueError(f"el perfil no tiene '{f}' en el teclado")
    return r


# ---------- Control virtual de Xbox ----------
_pad = {"obj": None, "lock": threading.Lock()}


def _gamepad():
    """El control virtual (se crea una vez: cada creación suena como 'control conectado')."""
    if _pad["obj"] is None:
        import vgamepad
        _pad["obj"] = vgamepad.VX360Gamepad()
        time.sleep(0.3)   # que Windows y el juego lo detecten
    return _pad["obj"]


def _pad_poner(botones, abajo):
    import vgamepad as vg
    p = _gamepad()
    B = vg.XUSB_BUTTON
    nombres = {"A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B, "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
               "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
               "START": B.XUSB_GAMEPAD_START, "BACK": B.XUSB_GAMEPAD_BACK, "LS": B.XUSB_GAMEPAD_LEFT_THUMB,
               "RS": B.XUSB_GAMEPAD_RIGHT_THUMB, "DPAD_U": B.XUSB_GAMEPAD_DPAD_UP,
               "DPAD_D": B.XUSB_GAMEPAD_DPAD_DOWN, "DPAD_L": B.XUSB_GAMEPAD_DPAD_LEFT,
               "DPAD_R": B.XUSB_GAMEPAD_DPAD_RIGHT}
    for b in botones:
        if b == "LT":
            p.left_trigger(255 if abajo else 0)
        elif b == "RT":
            p.right_trigger(255 if abajo else 0)
        elif abajo:
            p.press_button(nombres[b])
        else:
            p.release_button(nombres[b])
    p.update()


# ---------- Teclado (códigos de escaneo) ----------
U32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_EXTENDIDAS = {"UP", "DOWN", "LEFT", "RIGHT", "RCTRL", "RALT", "INSERT", "DELETE", "HOME", "END", "PGUP", "PGDN"}
_VK = {"SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09, "LSHIFT": 0xA0, "LCTRL": 0xA2, "LALT": 0xA4,
       "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27, "BACKSPACE": 0x08}


class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _UI(ctypes.Union):
    _fields_ = [("ki", _KI), ("mi", _MI)]


class _IN(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _UI)]


def _scan(tecla):
    """Código de escaneo de una tecla ('J', 'SPACE', 'UP'...) y si es extendida."""
    vk = _VK.get(tecla) or (ord(tecla) if len(tecla) == 1 else None)
    if vk is None:
        raise ValueError(f"no conozco la tecla '{tecla}'")
    return U32.MapVirtualKeyW(vk, 0), tecla in _EXTENDIDAS


def eventos_teclado(teclas, abajo):
    """[(scan, flags)] para apretar o soltar varias teclas (KEYEVENTF_SCANCODE = 0x8)."""
    r = []
    for t in teclas:
        sc, ext = _scan(t)
        r.append((sc, 0x0008 | (0x0001 if ext else 0) | (0 if abajo else 0x0002)))
    return r


def _teclado_poner(teclas, abajo):
    ev = eventos_teclado(teclas, abajo)
    arr = (_IN * len(ev))()
    for i, (sc, flags) in enumerate(ev):
        arr[i].type = 1
        arr[i].u.ki = _KI(0, sc, flags, 0, 0)
    U32.SendInput(len(ev), arr, ctypes.sizeof(_IN))


# ---------- Ejecutar ----------
def _esperar(segundos):
    """Espera precisa (time.sleep en Windows puede pasarse ~15 ms; un combo lo nota)."""
    fin = time.perf_counter() + segundos
    if segundos > 0.004:
        time.sleep(segundos - 0.003)
    while time.perf_counter() < fin:
        pass


def ejecutar(pasos, perfil, lado="izquierda", poner=None):
    """Aprieta cada paso el tiempo del perfil y lo suelta. poner(entradas, abajo) se puede
    cambiar en las pruebas. Devuelve la lista de lo que se apretó."""
    if poner is None:
        poner = _pad_poner if perfil["control"] == "pad" else _teclado_poner
    sostener, hueco = perfil.get("cuadros", 3) * CUADRO, perfil.get("hueco", 2) * CUADRO
    hecho = []
    try:
        import ctypes as _c
        _c.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass
    try:
        for fichas in pasos:
            if fichas == ["ESPERA"]:
                _esperar(0.2)
                continue
            entradas = resolver(fichas, perfil, lado)
            poner(entradas, True)
            _esperar(sostener)
            poner(entradas, False)
            _esperar(hueco)
            hecho.append(entradas)
    finally:
        try:
            import ctypes as _c
            _c.windll.winmm.timeEndPeriod(1)
        except Exception:
            pass
    return hecho


# ---------- Qué juego está al frente ----------
def _juego_al_frente():
    """(proceso, título) de la ventana al frente."""
    try:
        import control
        h = control.ventana_activa()
        return (control._proceso(h) or "").lower(), control._titulo(h) if h else ""
    except Exception:
        return "", ""


def perfil_de(proceso):
    proceso = (proceso or "").lower()
    for clave, p in PERFILES.items():
        if any(proceso.startswith(x) for x in p["procesos"]):
            return clave
    return "generico"


def bloqueado(proceso):
    return bool(EN_LINEA.match((proceso or "").lower()))


# ---------- Guardado (combos y lado por juego) ----------
_lock = threading.Lock()


def _leer():
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar(datos):
    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def _perfil_completo(clave):
    p = dict(PERFILES[clave])
    ajustes = _leer().get(clave, {})
    for k in ("control", "botones", "teclado", "cuadros", "hueco"):
        if k in ajustes:
            p[k] = {**p[k], **ajustes[k]} if isinstance(p[k], dict) else ajustes[k]
    return p


def combos(clave):
    return _leer().get(clave, {}).get("combos", {})


def combo_en_texto(texto, clave):
    """El nombre de un combo guardado que aparece en lo que dijiste ('haz el gancho'), o None."""
    t = _norm(texto)
    for nombre in sorted(combos(clave), key=len, reverse=True):
        if re.search(r"\b" + re.escape(_norm(nombre)) + r"\b", t):
            return nombre
    return None


def _lado(clave):
    return _leer().get(clave, {}).get("lado", "izquierda")


# ---------- Skills ----------
@skill("combo_juego",
       "Aprieta botones de verdad en el juego que está al frente (control virtual de Xbox o teclado): "
       "un combo guardado por su nombre o una secuencia en notación de peleas: U/D/F/B (arriba, abajo, "
       "adelante, atrás), 1 2 3 4 (golpes), BL (bloqueo), THROW, FLIP, AMP; '+' = juntos; 'espera' = "
       "pausa. Ej: 'B F 2', 'D+BL', 'atrás adelante dos'. Úsala con 'haz el fatality', 'tira el "
       "combo', 'atrás adelante dos'. Si no sabes la secuencia exacta de un fatality o especial, "
       "búscala primero con buscar_web; nunca la inventes.",
       {"secuencia": {"type": "string", "description": "Nombre de un combo guardado o la secuencia"},
        "lado": {"type": "string", "description": "izquierda o derecha: de qué lado de la pantalla está tu personaje (opcional)"}},
       requeridos=["secuencia"])
def combo_juego(secuencia, lado=""):
    proceso, titulo = _juego_al_frente()
    if bloqueado(proceso):
        return Fallo("Ese es un juego en línea competitivo: no le mando botones. Sería trampa contra "
                     "otras personas y su anti-trampas te puede banear la cuenta.")
    clave = perfil_de(proceso)
    perfil = _perfil_completo(clave)
    guardado = combos(clave).get(secuencia) or combos(clave).get(combo_en_texto(secuencia, clave) or "")
    try:
        pasos = interpretar(guardado or secuencia)
        hecho = ejecutar(pasos, perfil, (lado or _lado(clave)).lower())
    except ValueError as e:
        return Fallo(f"No pude hacer esa secuencia: {e}.")
    except Exception as e:   # sin driver del control virtual, por ejemplo
        return Fallo(f"No pude mandar los botones ({type(e).__name__}: {str(e)[:100]}).")
    print(f"[Juego: {perfil['nombre']} ({proceso or 'sin proceso'}) · {len(hecho)} pasos: {hecho}]")
    return Callado("Hecho.")


@skill("guardar_combo",
       "Guarda un combo con un nombre para el juego al frente, para pedirlo después por voz: "
       "'guarda el combo gancho: atrás adelante dos'.",
       {"nombre": {"type": "string", "description": "Cómo lo vas a llamar"},
        "secuencia": {"type": "string", "description": "La secuencia en notación (B F 2, D+BL...)"}},
       requeridos=["nombre", "secuencia"])
def guardar_combo(nombre, secuencia):
    try:
        interpretar(secuencia)
    except ValueError as e:
        return Fallo(f"Esa secuencia no la entiendo: {e}.")
    proceso, _ = _juego_al_frente()
    clave = perfil_de(proceso)
    with _lock:
        datos = _leer()
        datos.setdefault(clave, {}).setdefault("combos", {})[nombre.strip().lower()] = secuencia.strip()
        _guardar(datos)
    return f"Guardé el combo «{nombre}» para {PERFILES[clave]['nombre']}. Dime «haz el {nombre}» y lo tiro."


@skill("listar_combos", "Dice los combos guardados del juego al frente ('¿qué combos tienes?').", terminal=True)
def listar_combos():
    proceso, _ = _juego_al_frente()
    clave = perfil_de(proceso)
    c = combos(clave)
    if not c:
        return f"No tengo combos guardados para {PERFILES[clave]['nombre']}. Dime «guarda el combo nombre: secuencia»."
    return f"Para {PERFILES[clave]['nombre']} tengo: " + "; ".join(f"{n} ({s})" for n, s in c.items()) + "."


@skill("lado_jugador",
       "Dice de qué lado de la pantalla está tu personaje en el juego de peleas, para voltear "
       "adelante y atrás: 'estoy del lado derecho'.",
       {"lado": {"type": "string", "description": "izquierda o derecha"}}, requeridos=["lado"])
def lado_jugador(lado):
    lado = "derecha" if "der" in _norm(lado) else "izquierda"
    proceso, _ = _juego_al_frente()
    clave = perfil_de(proceso)
    with _lock:
        datos = _leer()
        datos.setdefault(clave, {})["lado"] = lado
        _guardar(datos)
    return Callado(f"Lado: {lado}.")
