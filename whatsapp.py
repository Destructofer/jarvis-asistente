"""WhatsApp: qué chats tienes sin responder (últimas 24 h), con el nombre del chat y el último
mensaje. "¿Tengo mensajes de WhatsApp?" y en el resumen de la mañana (ciclo.py).

Lee la LISTA DE CHATS de la app de escritorio de WhatsApp por accesibilidad (UI Automation),
como un lector de pantalla: nombre, hora, último mensaje y cuántos sin leer. NUNCA abre un
chat (eso los marcaría como leídos y le saldrían las palomitas azules a quien te escribió) ni
envía nada. Si la app está en la bandeja, se muestra minimizada (sin quitarte el foco) solo
mientras lee y luego vuelve a como estaba.

Requisito: la app de WhatsApp para Windows con la sesión iniciada (vinculada con el celular).
"""
import re
import time
from datetime import datetime

from skills import Fallo, skill

NO_LEIDOS = re.compile(r"(\d+)\s+(?:mensajes?\s+no\s+le[ií]dos?|unread\s+messages?)", re.I)
DE_MI = re.compile(r"^\s*(?:t[uú]|you)\s*:", re.I)
ESTADO_ENVIADO = re.compile(r"^\s*(?:le[ií]do|entregado|enviado|read|delivered|sent|pendiente)\s*$", re.I)
HORA = re.compile(r"^\s*(\d{1,2}:\d{2}(?:\s*[ap]\.?\s*m\.?)?|ayer|yesterday|hoy|today)\s*$", re.I)
LISTA_NOMBRES = ("lista de chats", "chat list", "chats")
SIN_SESION = re.compile(r"welcome to whatsapp|te damos la bienvenida|log in|iniciar sesi[oó]n|"
                        r"vincula|link (?:a|this) device", re.I)


def _ventana():
    """(hwnd, estaba_visible) de la ventana principal de WhatsApp, o (None, False)."""
    import psutil
    import win32gui
    import win32process
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("whatsapp")}
    encontrada = []

    def cada(h, _):
        try:
            if (win32process.GetWindowThreadProcessId(h)[1] in pids and win32gui.GetWindowText(h) == "WhatsApp"
                    and win32gui.GetClassName(h).startswith("WinUIDesktop")):
                encontrada.append(h)
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cada, None)
    if not encontrada:
        return None, False
    return encontrada[0], bool(win32gui.IsWindowVisible(encontrada[0]))


def _textos(elemento):
    """Los textos de un elemento y sus hijos, en orden (sin repetidos seguidos)."""
    res = []
    try:
        nombre = (elemento.element_info.name or "").strip()
        if nombre:
            res.append(nombre)
        for d in elemento.descendants():
            n = (d.element_info.name or "").strip()
            if n and (not res or res[-1] != n):
                res.append(n)
    except Exception:
        pass
    return res


def interpretar_fila(textos, ahora=None):
    """Un chat de la lista -> {nombre, hora, mensaje, no_leidos, de_mi} (o None si no parece chat).
    Separado para probarlo con filas inventadas."""
    textos = [t for t in textos if t]
    if len(textos) < 2:
        return None
    no_leidos = 0
    de_mi = False
    hora = ""
    resto = []
    for t in textos:
        m = NO_LEIDOS.search(t)
        if m:
            no_leidos = max(no_leidos, int(m.group(1)))
            continue
        if ESTADO_ENVIADO.match(t):
            de_mi = True
            continue
        if not hora and HORA.match(t):
            hora = t.strip()
            continue
        resto.append(t)
    if not resto:
        return None
    nombre = resto[0]
    # El nombre de la fila a veces es la concatenación de todo: se queda lo primero corto
    if len(nombre) > 60 and len(resto) > 1:
        nombre = resto[1]
    mensajes = [t for t in resto[1:] if t != nombre]
    mensaje = max(mensajes, key=len) if mensajes else ""
    if DE_MI.match(mensaje):
        de_mi = True
        mensaje = DE_MI.sub("", mensaje).strip()
    return {"nombre": nombre[:50], "hora": hora, "mensaje": mensaje[:160], "no_leidos": no_leidos,
            "de_mi": de_mi}


def reciente(hora, ahora=None):
    """¿La hora de la lista ('10:32', 'ayer') cae en las últimas 24 h?"""
    ahora = ahora or datetime.now()
    h = (hora or "").strip().lower()
    if h in ("ayer", "yesterday"):
        return True   # ayer: puede ser más de 24 h, pero mejor avisar que dejarlo sin responder
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])?", h)
    return bool(m) or h in ("hoy", "today")


def sin_responder(chats, ahora=None):
    """Los chats donde el último mensaje es de la otra persona (o hay no leídos), de las últimas
    24 h: primero los que tienen más mensajes sin leer."""
    res = [c for c in chats if reciente(c["hora"], ahora) and (c["no_leidos"] > 0 or not c["de_mi"])]
    return sorted(res, key=lambda c: -c["no_leidos"])


def leer_chats(espera=10.0, volcar=False):
    """[{nombre, hora, mensaje, no_leidos, de_mi}] de la lista de chats."""
    import win32con
    import win32gui
    from pywinauto import Desktop
    h, visible = _ventana()
    if h is None:
        raise RuntimeError("WhatsApp no está abierto")
    if not visible:
        win32gui.ShowWindow(h, win32con.SW_SHOWMINNOACTIVE)   # sin quitarte el foco
    try:
        raiz = Desktop(backend="uia").window(handle=h).wrapper_object()
        fin = time.time() + espera
        while True:
            todos = raiz.descendants()
            textos = " ".join((e.element_info.name or "") for e in todos[:400])
            lista = next((e for e in todos if (e.element_info.name or "").strip().lower() in LISTA_NOMBRES
                          and e.element_info.control_type not in ("Text", "Button", "Edit")), None)
            if lista is not None or time.time() > fin:
                break
            if SIN_SESION.search(textos) and time.time() > fin - espera + 2:
                break  # pantalla de "inicia sesión": no hay chats que esperar
            time.sleep(0.7)
        if volcar:
            return [(e.element_info.control_type, (e.element_info.name or "")[:120]) for e in todos[:600]]
        if lista is None:
            if SIN_SESION.search(textos):
                raise PermissionError("WhatsApp no tiene la sesión iniciada")
            raise RuntimeError("no encontré la lista de chats")
        filas = [f for f in lista.children()]
        if len(filas) <= 1:  # a veces la lista está un nivel más adentro
            filas = [g for f in filas for g in f.children()] or filas
        chats = []
        for f in filas:
            c = interpretar_fila(_textos(f))
            if c is not None:
                chats.append(c)
        return chats
    finally:
        if not visible:
            win32gui.ShowWindow(h, win32con.SW_HIDE)   # de vuelta a la bandeja, como estaba


def resumen_whatsapp(maximo=6):
    """Texto para decir ('' si WhatsApp no está disponible)."""
    try:
        chats = sin_responder(leer_chats())
    except PermissionError:
        return "Tu WhatsApp de escritorio no tiene la sesión iniciada; vincúlalo con tu celular y ya te digo tus mensajes."
    except Exception as e:
        print(f"[WhatsApp: {type(e).__name__}: {str(e)[:80]}]")
        return ""
    if not chats:
        return "En WhatsApp no tienes nada pendiente de responder."
    partes = []
    for c in chats[:maximo]:
        cuantos = f"{c['no_leidos']} sin leer" if c["no_leidos"] else "sin responder"
        partes.append(f"{c['nombre']} ({cuantos}){': ' + c['mensaje'] if c['mensaje'] else ''}")
    resto = len(chats) - len(partes)
    n = len(chats)
    return (f"En WhatsApp tienes {n} chat{'s' if n != 1 else ''} sin responder: " + "; ".join(partes)
            + (f"; y {resto} más." if resto > 0 else "."))


@skill("whatsapp_pendientes",
       "Dice qué chats de WhatsApp tiene el usuario sin responder en las últimas 24 horas, con el "
       "nombre del chat y su último mensaje (sin abrirlos ni marcarlos como leídos): '¿tengo "
       "mensajes de WhatsApp?', '¿quién me escribió?', '¿qué me falta contestar?'.",
       requeridos=[], terminal=False, externo=True)
def whatsapp_pendientes():
    texto = resumen_whatsapp(maximo=12)
    return texto or Fallo("No pude leer WhatsApp (¿está abierta la app de escritorio?).")
