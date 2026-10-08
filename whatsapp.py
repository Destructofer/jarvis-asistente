"""WhatsApp: qué chats tienes sin responder (últimas 24 h), con el nombre del chat y el último
mensaje. "¿Tengo mensajes de WhatsApp?" y en el resumen de la mañana (ciclo.py).

Lee la LISTA DE CHATS de la app de escritorio de WhatsApp por accesibilidad (UI Automation),
como un lector de pantalla: nombre, hora, último mensaje y cuántos sin leer. NUNCA abre un
chat (eso los marcaría como leídos y le saldrían las palomitas azules a quien te escribió) ni
envía nada. Si la app está en la bandeja, se muestra minimizada (sin quitarte el foco) solo
mientras lee y luego vuelve a como estaba.

Requisito: la app de WhatsApp para Windows con la sesión iniciada (vinculada con el celular).
Escribir, llamar y leer un chat: whatsapp_chat.py (siempre por orden tuya y con confirmación).
"""
import re
import time


from skills import Fallo, skill

NO_LEIDOS = re.compile(r"(\d+)\s+(?:mensajes?\s+no\s+le[ií]dos?|unread\s+messages?)", re.I)
DE_MI = re.compile(r"^\s*(?:t[uú]|you)\s*:", re.I)
ESTADO_ENVIADO = re.compile(r"^\s*(?:le[ií]do|entregado|enviado|read|delivered|sent|pendiente)\s*$", re.I)
HORA = re.compile(r"^\s*(\d{1,2}:\d{2}(?:\s*[ap]\.?\s*m\.?)?|ayer|yesterday|hoy|today)\s*$", re.I)
LISTA_NOMBRES = ("lista de chats", "chat list")
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


# Cómo viene una fila en la app de escritorio (WhatsApp para Windows, 2.26, en inglés o
# español): el nombre de la fila junta TODO:
#   "<chat> <hora> [N unread messages] <...> [Muted chat] [~Remitente :] <mensaje> [avisos]"
# y una fila hija trae "[N unread messages] <último mensaje>". La app no expone si el último
# mensaje fue tuyo (ni "Tú:" ni palomitas): lo confiable son los NO LEÍDOS.
HORA_FILA = re.compile(
    r"\s(\d{1,2}:\d{2}\s?(?:[ap]\.?\s?m\.?)?|yesterday|ayer|hoy|today|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|lunes|martes|mi[eé]rcoles|"
    r"jueves|viernes|s[aá]bado|domingo)(?=\s|$)", re.I)
SILENCIADO = re.compile(r"\b(muted chat|chat silenciado|silenciado)\b", re.I)
AVISOS = re.compile(
    r"new messages will disappear from this chat.*?except when kept\.?|los mensajes nuevos "
    r"desaparecer[aá]n.*?(?:guarden|conserven)\.?|muted chat|chat silenciado|starred chat|"
    r"chat destacado|pinned chat|chat fijado|group members have changed\.?(?: click to view)?", re.I)
# En los grupos el mensaje va con quién lo mandó: "~ Nombre : texto" (no lo tienes guardado) o
# "Nombre: texto" (sí lo tienes); en un chat de una persona no lleva nombre
REMITENTE = re.compile(r"(?:^|~)\s*([^:~]{0,40}?)\s*:\s+")


def skills_norm(t):
    import unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    return " ".join("".join(c for c in t if unicodedata.category(c) != "Mn").split())


def para_voz(texto, maximo=80):
    """Sin emojis ni símbolos raros (se leerían en voz alta), corto."""
    t = AVISOS.sub(" ", texto or "")
    t = re.sub(r"[^\w\s,.;:¿?¡!$%&@'()/-]", " ", t)
    t = " ".join(t.replace("\xa0", " ").split())
    if len(t) > maximo:
        t = t[:maximo].rsplit(" ", 1)[0] + "…"
    return t


def interpretar_fila(nombre_fila, mensaje_hijo=""):
    """Una fila de la lista de chats -> {nombre, hora, mensaje, remitente, no_leidos,
    silenciado, de_mi} o None si no parece un chat. Separado para probarlo con filas inventadas."""
    nombre_fila = (nombre_fila or "").replace("\xa0", " ")
    m = HORA_FILA.search(nombre_fila)
    if not m:
        return None
    nombre = para_voz(nombre_fila[:m.start()], 50) or "un chat"
    resto = nombre_fila[m.end():]
    u = NO_LEIDOS.search(resto[:40])
    no_leidos = int(u.group(1)) if u else 0
    mensaje = (mensaje_hijo or "").replace("\xa0", " ")
    mensaje = NO_LEIDOS.sub("", mensaje, count=1).strip()
    mensaje = AVISOS.sub(" ", mensaje).strip()
    de_mi = bool(DE_MI.match(mensaje))
    mensaje = DE_MI.sub("", mensaje).strip()
    remitente, grupo = "", False
    r = REMITENTE.search(mensaje)
    if r and (r.group(0).lstrip().startswith("~")
              or (len(r.group(1).split()) <= 4 and not re.search(r"\d", r.group(1)))):
        grupo = True
        remitente = para_voz(r.group(1), 30)   # puede quedar vacío si su nombre es solo un emoji
        mensaje = mensaje[r.end():]
    if skills_norm(para_voz(mensaje, 200)).startswith(skills_norm(nombre)[:20]) and len(nombre) > 3:
        mensaje = ""   # la fila de una comunidad repite su nombre: no es un mensaje
    return {"nombre": nombre, "hora": m.group(1), "mensaje": para_voz(mensaje), "remitente": remitente,
            "grupo": grupo, "no_leidos": no_leidos, "silenciado": bool(SILENCIADO.search(nombre_fila)),
            "de_mi": de_mi}


def reciente(hora, ahora=None):
    """¿La hora de la lista ('10:32 AM', 'Yesterday') cae en las últimas 24 h?"""
    h = (hora or "").strip().lower()
    if h in ("ayer", "yesterday"):
        return True   # ayer: puede ser un poco más de 24 h, pero mejor avisar que dejarlo sin responder
    return bool(re.match(r"\d{1,2}:\d{2}", h)) or h in ("hoy", "today")


def sin_responder(chats, ahora=None):
    """Los chats con mensajes sin leer de las últimas 24 h que no silenciaste: primero las
    personas y después los grupos (tienen remitente), cada uno en el orden de la lista (el más
    reciente primero)."""
    res = [c for c in chats if reciente(c["hora"], ahora) and c["no_leidos"] > 0
           and not c.get("silenciado") and not c.get("de_mi")]
    return sorted(res, key=lambda c: bool(c.get("grupo")))


def silenciados_con_mensajes(chats, ahora=None):
    return [c for c in chats if c.get("silenciado") and c["no_leidos"] > 0 and reciente(c["hora"], ahora)]


def _buscador():
    """fn(hwnd, tipo, nombres) -> el primer elemento de ese tipo con alguno de esos nombres
    (sin importar mayúsculas), con UNA consulta FindFirst de UI Automation."""
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    iuia = IUIA()
    u, dll = iuia.iuia, iuia.UIA_dll

    def buscar(hwnd, tipo, nombres):
        cond = None
        for n in nombres:
            c = u.CreatePropertyConditionEx(dll.UIA_NamePropertyId, n, 1)
            cond = c if cond is None else u.CreateOrCondition(cond, c)
        cond = u.CreateAndCondition(
            u.CreatePropertyCondition(dll.UIA_ControlTypePropertyId,
                                      getattr(dll, f"UIA_{tipo}ControlTypeId")), cond)
        try:
            el = u.ElementFromHandle(hwnd).FindFirst(iuia.tree_scope["descendants"], cond)
        except Exception:
            return None
        return UIAWrapper(UIAElementInfo(el)) if el else None
    return buscar


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
        if volcar:
            return [(e.element_info.control_type, (e.element_info.name or "")[:120])
                    for e in raiz.descendants()[:600]]
        buscar = _buscador()
        fin = time.time() + espera
        lista = sin_sesion = None
        while time.time() < fin:
            # Una sola consulta a Windows por cada cosa: con un chat abierto la ventana tiene
            # ~29 000 elementos y recorrerlos todos tardaba más que la espera
            lista = buscar(h, "DataGrid", LISTA_NOMBRES)
            if lista is not None:
                break
            sin_sesion = buscar(h, "Button", ("log in", "iniciar sesión", "iniciar sesion"))
            if sin_sesion is not None:
                break
            time.sleep(0.7)
        if lista is None:
            if sin_sesion is not None:
                raise PermissionError("WhatsApp no tiene la sesión iniciada")
            raise RuntimeError("no encontré la lista de chats")
        filas = [f for f in lista.children()]
        if len(filas) <= 1:  # a veces la lista está un nivel más adentro
            filas = [g for f in filas for g in f.children()] or filas
        chats = []
        for f in filas:
            try:
                nombre_fila = f.element_info.name or ""
                hijo = next((d.element_info.name for d in f.descendants(control_type="DataItem")
                             if d.element_info.name and d.element_info.name != nombre_fila), "")
            except Exception:
                continue
            c = interpretar_fila(nombre_fila, hijo)
            if c is not None:
                chats.append(c)
        return chats
    finally:
        if not visible:
            win32gui.ShowWindow(h, win32con.SW_HIDE)   # de vuelta a la bandeja, como estaba


def resumen_whatsapp(maximo=6):
    """Texto para decir ('' si WhatsApp no está disponible)."""
    try:
        chats = leer_chats()
    except PermissionError:
        return "Tu WhatsApp de escritorio no tiene la sesión iniciada; vincúlalo con tu celular y ya te digo tus mensajes."
    except Exception as e:
        print(f"[WhatsApp: {type(e).__name__}: {str(e)[:80]}]")
        return ""
    return redactar(chats, maximo)


def _unir(frases):
    """'a. b. c.' sin dobles signos ('¿ya llegaste?.')."""
    return " ".join(f if f.rstrip()[-1:] in ".?!…" else f + "." for f in frases)


MUCHOS = 50   # tantos sin leer en un chat: es un grupo (una persona rara vez te manda tantos)


def es_grupo(c):
    return bool(c.get("grupo")) or c["no_leidos"] >= MUCHOS


def redactar(chats, maximo=6):
    """El resumen hablado: las PERSONAS con detalle (nombre, cuántos y el último mensaje) y los
    grupos en una sola frase con su total (los que se llaman igual, juntos): con comunidades de
    ventas de cientos de mensajes, leerlos uno por uno era puro ruido."""
    pendientes = sin_responder(chats)
    silenciados = silenciados_con_mensajes(chats)
    personas = [c for c in pendientes if not es_grupo(c)]
    grupos = {}
    for c in pendientes:
        if es_grupo(c):
            grupos[c["nombre"]] = grupos.get(c["nombre"], 0) + c["no_leidos"]
    if not personas and not grupos:
        texto = "En WhatsApp no tienes nada pendiente de responder"
        return texto + (f", fuera de {len(silenciados)} grupo{'s' if len(silenciados) != 1 else ''} "
                        "que tienes silenciado." if silenciados else ".")
    frases = []
    if personas:
        n = len(personas)
        detalle = []
        for c in personas[:maximo]:
            cuantos = f"{c['no_leidos']} mensaje{'s' if c['no_leidos'] != 1 else ''}"
            detalle.append(f"{c['nombre']}, {cuantos}{': ' + c['mensaje'] if c['mensaje'] else ''}")
        resto = n - len(detalle)
        frases.append(f"En WhatsApp te escribieron {n} persona{'s' if n != 1 else ''} y no has "
                      "respondido: " + _unir(detalle) + (f" Y {resto} más." if resto > 0 else ""))
    else:
        frases.append("En WhatsApp no tienes mensajes de personas sin responder.")
    if grupos:
        lista = sorted(grupos.items(), key=lambda kv: -kv[1])
        dichos = [f"{nombre} con {total}" for nombre, total in lista[:4]]
        mas = len(lista) - len(dichos)
        frases.append("En grupos tienes mensajes sin leer: " + ", ".join(dichos)
                      + (f" y {mas} más" if mas > 0 else "") + ".")
    if silenciados:
        frases.append(f"Además {len(silenciados)} grupo{'s' if len(silenciados) != 1 else ''} "
                      f"silenciado{'s' if len(silenciados) != 1 else ''} con mensajes.")
    return " ".join(frases)


@skill("whatsapp_pendientes",
       "Dice qué chats de WhatsApp tiene el usuario sin responder en las últimas 24 horas, con el "
       "nombre del chat y su último mensaje (sin abrirlos ni marcarlos como leídos): '¿tengo "
       "mensajes de WhatsApp?', '¿quién me escribió?', '¿qué me falta contestar?'.",
       requeridos=[], terminal=False, externo=True)
def whatsapp_pendientes():
    texto = resumen_whatsapp(maximo=12)
    return texto or Fallo("No pude leer WhatsApp (¿está abierta la app de escritorio?).")
