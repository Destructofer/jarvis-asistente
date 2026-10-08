"""WhatsApp al cien: escribir y llamar a tus contactos (whatsapp.py solo lee la lista de chats).

"Jarvis, dile a Ana que llego tarde a la junta" -> Jarvis abre el chat de Ana, lee lo último de la
conversación para entender el contexto, redacta un mensaje profesional en TU voz ("Hola Ana,
te aviso que llegaré unos minutos tarde a la junta. Una disculpa."), te lo muestra y SOLO si lo
confirmas, lo envía. "Cámbialo a algo más corto" lo vuelve a redactar. "Mándale tal cual: ..."
lo envía sin retocar. "Llama a mi mamá" / "videollamada con Luis": confirma y marca.

Seguridad: nada se envía ni se marca sin tu confirmación (texto exacto y destinatario). Antes
de escribir, se verifica que la caja de texto sea la del chat correcto ("Type a message to
<nombre>"); si no coincide, no se escribe nada. Desde el teléfono no se puede (pide
confirmación). Abrir un chat lo marca como leído (palomitas azules): solo se abre cuando pides
escribirle, llamarle o leerlo.

Cómo lo hace (WhatsApp para Windows 2.26, por accesibilidad): el buscador de la cabecera (sin
nombre si ya tiene texto; se reconoce por NO ser la caja del mensaje) -> la tabla "Search
results." con secciones (Chats, Contacts, Messages) -> clic en la fila. La app no expone el
TEXTO de los mensajes por accesibilidad (sí quién lo mandó: los tuyos traen "Delivered/Read"),
así que el contexto se lee de una captura de la conversación con el modelo de visión LOCAL
(no sale de tu PC).
"""
import json
import re
import time
import unicodedata
from pathlib import Path

from skills import Fallo, skill

confirmar = None      # lo pone genesis.py: fn(pregunta) -> bool (ventana Sí/No + voz)
hablar = None         # lo pone genesis.py: fn(texto)
_cfg = {"cfg": {}}
_borrador = {"chat": None, "texto": None, "instruccion": None, "tono": None, "contexto": ""}

SECCIONES_CHAT = {"chats", "contacts", "groups", "contactos", "grupos", "chats y grupos"}
SECCIONES_OTRAS = {"messages", "mensajes", "channels", "canales"}
CAJA_MENSAJE = re.compile(r"^(type a message to|escribe un mensaje (?:a|para))\s+(.*)$", re.I)
PROPIO = re.compile(r"\((you|tú|tu)\)", re.I)
BOTON_LLAMADA = {False: ("Voice call", "Llamada de voz"), True: ("Video call", "Videollamada")}


def _norm(t):
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ+ ]", " ", t).split()


def parecido(buscado, nombre):
    """0-1: qué tanto se parece el nombre de un chat a lo que pediste ('mi mamá' ~ 'Mamá ❤️')."""
    b = [w for w in _norm(buscado) if w not in ("mi", "a", "al", "la", "el", "con", "de")]
    n = _norm(nombre)
    if not b or not n:
        return 0.0
    if " ".join(b) == " ".join(n):
        return 1.0
    aciertos = sum(1 for w in b if any(x == w or (len(w) >= 4 and x.startswith(w)) for x in n))
    return aciertos / len(b) * (0.95 if aciertos == len(b) else 0.8)


# ---------- Nombres que suenan igual ----------
# Whisper escribe los nombres como suenan: "Yun Cook" llegó como "jumcook" y la búsqueda de
# WhatsApp (letra por letra) no lo encontraba. Se comparan por SONIDO en español.
_VACIAS = {"mi", "a", "al", "la", "el", "con", "de", "del", "los", "las"}
_FONEMAS = [
    (r"ll", "y"), (r"ch", "x"), (r"sh", "x"), (r"ph", "f"), (r"qu", "k"), (r"g(?=[ei])", "y"),
    (r"c(?=[ei])", "s"), (r"z", "s"), (r"c", "k"), (r"q", "k"), (r"h", ""), (r"j", "y"),
    (r"v", "b"), (r"w", "u"), (r"oo", "u"), (r"ee", "i"), (r"ñ", "n"), (r"x(?=[aeiou])", "x"),
    (r"m(?![aeiou])", "n"), (r"y(?![aeiou])", "i"),
]


def fonetica(texto):
    """Clave de cómo SUENA un nombre: 'Yun Cook' y 'jumcook' -> 'yunkuk'."""
    t = "".join(w for w in _norm(texto) if w not in _VACIAS)
    t = re.sub(r"[^a-zñ]", "", t)
    for patron, reemplazo in _FONEMAS:
        t = re.sub(patron, reemplazo, t)
    return re.sub(r"(.)\1+", r"\1", t)


def similitud(buscado, nombre):
    """0-1, combinando palabras ('mi mamá' ~ 'Mamá') y sonido ('jumcook' ~ 'Yun Cook'). Contra
    cada tramo de 1 a 3 palabras del nombre: 'Ana' ~ 'Ana López Ruiz' cuenta como 1.0."""
    from difflib import SequenceMatcher
    mejor = parecido(buscado, nombre)
    kb = fonetica(buscado)
    palabras = _norm(nombre)
    if len(kb) < 2 or not palabras:
        return mejor
    for i in range(len(palabras)):
        for j in range(i + 1, min(len(palabras), i + 3) + 1):
            kn = fonetica(" ".join(palabras[i:j]))
            if len(kn) >= 2:
                r = SequenceMatcher(None, kb, kn).ratio() - (0.02 * i)   # empezar por el nombre pesa más
                mejor = max(mejor, r)
    return round(mejor, 3)


AGENDA = Path(__file__).resolve().parent / "datos" / "whatsapp_contactos.json"


def _agenda():
    try:
        return json.loads(AGENDA.read_text(encoding="utf-8")).get("nombres", [])
    except (OSError, ValueError):
        return []


def recordar_nombres(nombres):
    """Guarda (solo en tu PC) los nombres de chats y contactos que se ven en WhatsApp: así un
    nombre mal entendido se resuelve aunque la búsqueda de WhatsApp no lo encuentre."""
    nuevos = [n.strip() for n in nombres if n and 2 <= len(n.strip()) <= 60 and n.strip() != "un chat"]
    if not nuevos:
        return
    actuales = _agenda()
    juntos = list(dict.fromkeys(actuales + nuevos))
    if len(juntos) != len(actuales):
        try:
            AGENDA.parent.mkdir(parents=True, exist_ok=True)
            AGENDA.write_text(json.dumps({"nombres": juntos[-2000:]}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def mas_parecido(buscado, nombres, minimo=0.8, margen=0.06):
    """(nombre, puntaje) si uno se parece claramente más que los demás; si no, None."""
    puntuados = sorted(((similitud(buscado, n), n) for n in dict.fromkeys(nombres)), reverse=True)
    if not puntuados or puntuados[0][0] < minimo:
        return None
    if len(puntuados) > 1 and puntuados[1][0] >= puntuados[0][0] - margen and puntuados[1][0] >= minimo:
        return None   # dos casi iguales: que decida elegir() o pregunte
    return puntuados[0][1], puntuados[0][0]


def consultas_de_rescate(buscado, maximo=6):
    """Pedazos del nombre para buscar en WhatsApp cuando el nombre completo no aparece
    ('jumcook' -> 'cook', 'jumc', 'umco'...): WhatsApp solo encuentra letras exactas."""
    palabras = [w for w in _norm(buscado) if w not in _VACIAS]
    junto = "".join(palabras)
    salida = [w for w in palabras if len(w) >= 3 and w != junto]
    if len(junto) >= 4:
        cuatro = [junto[i:i + 4] for i in range(len(junto) - 3)]
        salida += [cuatro[-1], cuatro[0]] + cuatro[1:-1]
    if len(junto) >= 3:
        salida += [junto[-3:], junto[:3]]
    return list(dict.fromkeys(q for q in salida if q != " ".join(palabras)))[:maximo]


def nombre_de_fila(nombre_fila):
    """El nombre del chat en una fila de resultados ('Ana López 4:20 PM Hola' -> 'Ana López')."""
    import whatsapp
    n = (nombre_fila or "").replace("\xa0", " ")
    p = PROPIO.search(n)
    if p:
        return n[:p.start()].strip()
    c = whatsapp.interpretar_fila(n)
    if c and c["nombre"] != "un chat":
        return c["nombre"]
    return " ".join(n.split()[:4])   # contactos sin hora: "Nombre Apellido estado..."


def candidatos(filas):
    """De las filas de 'Search results.' (nombres), las que son chats o contactos (no los
    encabezados ni los mensajes encontrados): [(indice, nombre_chat, es_propio)]."""
    seccion, salida = "chats", []
    for i, n in enumerate(filas):
        t = (n or "").strip().lower()
        if t in SECCIONES_CHAT or t in SECCIONES_OTRAS:
            seccion = t
            continue
        if seccion in SECCIONES_OTRAS or not t:
            continue
        salida.append((i, nombre_de_fila(n), bool(PROPIO.search(n or ""))))
    return salida


def es_propio(buscado):
    return bool(re.fullmatch(r"\s*(yo|a mi mismo|a mi misma|mi chat|mi numero|conmigo)\s*", " ".join(_norm(buscado))))


def elegir(buscado, cands, minimo=0.72, margen=0.05):
    """('ok', candidato) | ('varios', [nombres]) | ('ninguno', sugerencia o None).
    Gana el que más se parece (por palabras o por sonido); si otro distinto queda casi igual,
    pregunta cuál."""
    if es_propio(buscado):
        propios = [c for c in cands if c[2]]
        return ("ok", propios[0]) if propios else ("ninguno", None)
    puntuados = sorted(((similitud(buscado, c[1]), c) for c in cands), key=lambda x: -x[0])
    if not puntuados or puntuados[0][0] < minimo:
        sugerencia = puntuados[0][1][1] if puntuados and puntuados[0][0] >= 0.5 else None
        return "ninguno", sugerencia
    mejor, cand = puntuados[0]
    rivales = list(dict.fromkeys(c[1] for p, c in puntuados if p >= max(minimo, mejor - margen)))
    if len(rivales) > 1:
        return "varios", rivales[:5]
    return "ok", cand


# ---------- La app ----------
def _ventana_uia():
    import whatsapp
    from pywinauto import Desktop
    h, _ = whatsapp._ventana()
    if not h:
        raise RuntimeError("WhatsApp no está abierto (ábrelo y vincúlalo con tu celular).")
    return h, Desktop(backend="uia").window(handle=h)


# Tu WhatsApp tiene miles de elementos (todo el historial cargado: ~3,000 botones). Recorrer el
# árbol completo tardaba 3-5 s por búsqueda; aquí cada cosa se pide de la forma más directa.
def _uia():
    from pywinauto.uia_defines import IUIA
    return IUIA()


def _envolver(el):
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo
    return UIAWrapper(UIAElementInfo(el)) if el else None


def _primero(w, tipo, nombre=None, auto_id=None):
    """El primer elemento de ese tipo (y nombre / id): FindFirst se detiene en cuanto lo halla."""
    iuia = _uia()
    u, dll = iuia.iuia, iuia.UIA_dll
    cond = u.CreatePropertyCondition(dll.UIA_ControlTypePropertyId, getattr(dll, f"UIA_{tipo}ControlTypeId"))
    if nombre:
        cond = u.CreateAndCondition(cond, u.CreatePropertyConditionEx(dll.UIA_NamePropertyId, nombre, 1))
    if auto_id:
        cond = u.CreateAndCondition(cond, u.CreatePropertyCondition(dll.UIA_AutomationIdPropertyId, auto_id))
    try:
        return _envolver(w.element_info.element.FindFirst(iuia.tree_scope["descendants"], cond))
    except Exception:
        return None


def _cajas(w):
    """(buscador, caja del mensaje o None), recorriendo todas las cajas (lento: último recurso)."""
    iuia = _uia()
    cond = iuia.iuia.CreatePropertyCondition(iuia.UIA_dll.UIA_ControlTypePropertyId, iuia.UIA_dll.UIA_EditControlTypeId)
    arr = w.element_info.element.FindAll(iuia.tree_scope["descendants"], cond)
    busq = msg = None
    for k in range(arr.Length):
        e = _envolver(arr.GetElement(k))
        if CAJA_MENSAJE.match(e.element_info.name or ""):
            msg = msg or e
        else:
            busq = busq or e
    return busq, msg


_mem = {"busq": None, "caja": None}   # elementos ya encontrados (buscarlos de nuevo tarda segundos)


def _vivo(e, patron=None):
    """¿El elemento recordado sigue en la ventana (y con un nombre que encaje)?"""
    if e is None:
        return False
    try:
        nombre = e.element_info.element.CurrentName or ""
        e.element_info.element.CurrentBoundingRectangle
    except Exception:
        return False
    return patron is None or bool(patron.match(nombre))


def _buscador_de_chats(w):
    """La caja de búsqueda: por su nombre (vacía) o por su id (con texto pierde el nombre)."""
    if _vivo(_mem["busq"]):
        return _mem["busq"]
    _mem["busq"] = _buscar_buscador(w)
    return _mem["busq"]


def _buscar_buscador(w):
    for nombre in ("Search or start a new chat", "Buscar o empezar un chat nuevo", "Buscar un chat o iniciar uno nuevo"):
        e = _primero(w, "Edit", nombre=nombre)
        if e is not None:
            return e
    return _primero(w, "Edit", auto_id="_r_e_") or _cajas(w)[0]


def _caja_mensaje(w):
    """La caja del mensaje: al abrir un chat, WhatsApp le pone el foco (se pide directo al
    sistema, instantáneo); si no, se busca."""
    try:
        iuia = _uia()
        caminante = iuia.iuia.ControlViewWalker
        e = iuia.iuia.GetFocusedElement()
        for _ in range(4):   # el foco queda en un hijo sin nombre de la caja: se sube
            if e is None:
                break
            if CAJA_MENSAJE.match(e.CurrentName or ""):
                _mem["caja"] = _envolver(e)
                return _mem["caja"]
            e = caminante.GetParentElement(e)
    except Exception:
        pass
    if _vivo(_mem["caja"], CAJA_MENSAJE):   # la misma caja se reutiliza al cambiar de chat
        return _mem["caja"]
    _mem["caja"] = _cajas(w)[1]
    return _mem["caja"]


def _filas(tabla):
    """[(nombre, elemento nativo)] de las filas de una tabla. La tabla de resultados cambia
    mientras se escribe la búsqueda: una fila que desaparece a media lectura se salta."""
    iuia = _uia()
    try:
        arr = tabla.element_info.element.FindAll(iuia.tree_scope["children"], iuia.iuia.CreateTrueCondition())
    except Exception:
        return []
    filas = []
    for k in range(arr.Length):
        try:
            e = arr.GetElement(k)
            filas.append((e.CurrentName or "", e))
        except Exception:
            pass
    return filas


def _valor(caja):
    try:
        return (caja.legacy_properties().get("Value") or "").strip()
    except Exception:
        return ""


def destinatario(caja):
    m = CAJA_MENSAJE.match((caja.element_info.name if caja else "") or "")
    return m.group(2).strip() if m else ""


def _al_frente(h):
    import realidad
    import win32con
    import win32gui
    if win32gui.IsIconic(h):
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
    realidad._dar_foco(h)
    time.sleep(0.4)


def _teclas(secuencia):
    from pywinauto.keyboard import send_keys
    send_keys(secuencia)


def _buscar(h, w, consulta):
    """Escribe la consulta en el buscador de WhatsApp y devuelve las filas de resultados
    [(nombre, elemento)] cuando terminan de cargar ([] si no hay ninguno)."""
    import realidad
    import whatsapp
    busq = _buscador_de_chats(w)
    if busq is None:
        raise RuntimeError("No encontré el buscador de WhatsApp.")
    busq.click_input()
    time.sleep(0.15)
    _teclas("^a{BACKSPACE}")
    realidad._escribir(consulta)
    buscar = whatsapp._buscador()
    inicio, anterior, vacias = time.time(), None, 0
    while time.time() - inicio < 6:
        time.sleep(0.3)
        tabla = buscar(h, "DataGrid", ["Search results.", "Resultados de la búsqueda."])
        if tabla is None:
            if time.time() - inicio > 3:   # sin tabla: no hubo resultados
                return []
            continue
        filas = _filas(tabla)
        nombres = [n for n, _ in filas]
        if not nombres:
            vacias += 1
            if vacias >= 3:
                return []
            continue
        if nombres == anterior:   # dos lecturas iguales: ya terminó de cargar
            recordar_nombres([c[1] for c in candidatos(nombres)])
            return filas
        anterior = nombres
    return []


def abrir_chat(buscado):
    """Abre el chat del nombre más parecido (también por sonido: 'jumcook' -> 'Yun Cook').
    Devuelve (estado, dato): ('ok', nombre_real) | ('varios', [nombres]) | ('ninguno',
    sugerencia o None). Al terminar, la caja del mensaje es la de ese chat (verificado)."""
    h, w = _ventana_uia()
    _al_frente(h)
    estado, dato, filas = resolver(h, w, buscado)
    if estado != "ok":
        _teclas("{ESC}")
        return estado, dato
    i, nombre, _ = dato
    _envolver(filas[i][1]).click_input()
    fin = time.time() + 5
    while time.time() < fin:
        time.sleep(0.3)
        caja = _caja_mensaje(w)
        if caja is not None and parecido(nombre, destinatario(caja)) >= 0.75:
            return "ok", destinatario(caja)
    raise RuntimeError(f"Abrí la búsqueda pero no pude confirmar que el chat abierto sea el de {nombre}.")


def resolver(h, w, buscado):
    """Encuentra la fila del chat sin abrirlo: (estado, dato, filas)."""
    propio = es_propio(buscado)
    consulta = "You" if propio else buscado
    if not propio:   # 1. los nombres que ya conoce: se busca con el nombre real
        conocido = mas_parecido(buscado, _agenda())
        if conocido:
            consulta = conocido[0]
    filas = _buscar(h, w, consulta)
    estado, dato = elegir(buscado, candidatos([n for n, _ in filas]))
    if estado == "ninguno" and not propio:
        # 2. WhatsApp busca letras exactas: se prueba con pedazos y se compara por sonido
        vistos = {}
        for q in consultas_de_rescate(buscado):
            for c in candidatos([n for n, _ in _buscar(h, w, q)]):
                vistos.setdefault(c[1], c)
            estado, dato = elegir(buscado, list(vistos.values()))
            if estado != "ninguno":
                break
        if estado == "ok":   # se vuelve a buscar con el nombre real para tener su fila
            nombre_real = dato[1]
            filas = _buscar(h, w, nombre_real)
            estado, dato = elegir(nombre_real, candidatos([n for n, _ in filas]))
    return estado, dato, filas


def enviar(nombre, texto):
    """Escribe y envía en el chat abierto, SOLO si la caja es la de 'nombre'. Verifica que se
    envió: la caja tenía el texto y quedó vacía."""
    import realidad
    h, w = _ventana_uia()
    _al_frente(h)
    caja = _caja_mensaje(w)
    if caja is None or parecido(nombre, destinatario(caja)) < 0.75:
        raise RuntimeError("El chat abierto ya no es el de " + nombre + ": no escribí nada.")
    caja.click_input()
    time.sleep(0.2)
    if _valor(caja):   # algo que quedó escrito de antes no se debe ir pegado a tu mensaje
        _teclas("^a{BACKSPACE}")
        time.sleep(0.2)
    lineas = (texto or "").strip().split("\n")
    for k, linea in enumerate(lineas):
        if linea:
            realidad._escribir(linea)
        if k < len(lineas) - 1:
            _teclas("+{ENTER}")
    # la lectura de la caja va retrasada: se espera a que muestre lo escrito
    inicio = " ".join((texto or "").split())[:15]
    fin = time.time() + 2.5
    while time.time() < fin and inicio not in " ".join(_valor(caja).split()):
        time.sleep(0.2)
    if inicio not in " ".join(_valor(caja).split()):
        _teclas("^a{BACKSPACE}")
        raise RuntimeError("No pude confirmar que se escribiera bien; lo borré y no envié nada.")
    if parecido(nombre, destinatario(caja)) < 0.75:   # por si algo cambió el chat mientras escribía
        _teclas("^a{BACKSPACE}")
        raise RuntimeError("El chat cambió mientras escribía; borré el texto y no envié nada.")
    _teclas("{ENTER}")
    fin = time.time() + 6
    while time.time() < fin:
        time.sleep(0.3)
        if inicio not in " ".join(_valor(caja).split()):   # la caja quedó vacía: se envió
            return True
    raise RuntimeError("Lo escribí pero no vi que se enviara; revisa WhatsApp.")


def llamar_chat_abierto(nombre, video=False):
    h, w = _ventana_uia()
    _al_frente(h)
    caja = _caja_mensaje(w)
    if caja is None or parecido(nombre, destinatario(caja)) < 0.75:
        raise RuntimeError("El chat abierto no es el de " + nombre + ": no marqué.")
    for etiqueta in BOTON_LLAMADA[video]:
        b = _primero(w, "Button", nombre=etiqueta)
        if b is not None:
            b.click_input()
            return True
    raise RuntimeError("No encontré el botón de " + ("videollamada" if video else "llamada") + ".")


def leer_conversacion(maximo=8):
    """Lo último del chat abierto, leído de una captura con el modelo de visión LOCAL. '' si no
    se puede (no es indispensable: sin contexto igual se redacta)."""
    try:
        from PIL import ImageGrab
        import vision
        h, w = _ventana_uia()
        caja = _caja_mensaje(w)
        if caja is None:
            return ""
        r_caja, r_ven = caja.rectangle(), w.rectangle()
        img = ImageGrab.grab(bbox=(r_caja.left - 60, r_ven.top + 60, r_ven.right, r_caja.top - 4), all_screens=True)
        cfg = dict(_cfg["cfg"])
        texto = vision.ver(cfg, img.convert("RGB"),
                           f"Es una conversación de WhatsApp. Transcribe los últimos {maximo} mensajes, en "
                           "orden, uno por línea: 'YO: ...' para los de la derecha (verdes, míos) y "
                           "'ELLOS: ...' para los de la izquierda. Solo el texto, sin horas.",
                           "Transcribes capturas de pantalla con exactitud. No inventas nada.",
                           max_tokens=400, lado=1280, fondo=True)
        return texto.strip()[:2000]
    except Exception as e:
        print(f"[WhatsApp: no pude leer la conversación ({type(e).__name__})]")
        return ""


# ---------- Redactar ----------
TONOS = {
    "profesional": "profesional, claro y cordial (como con un cliente, maestro o jefe), sin sonar acartonado",
    "amable": "cálido y cercano, como con un amigo o familiar, pero bien escrito",
    "formal": "formal y respetuoso, con saludo y cierre",
    "breve": "muy breve y directo, una o dos frases",
}


def redactar(chat, instruccion, contexto="", tono="profesional", nombre_usuario=""):
    """El mensaje listo para enviar, en la voz del usuario (no de Jarvis)."""
    import cerebro
    sistema = (
        f"Redactas mensajes de WhatsApp que {nombre_usuario or 'el usuario'} le enviará a «{chat}». "
        "Escribe EN PRIMERA PERSONA, como si él mismo lo escribiera (nunca como asistente, nunca "
        "menciones a Jarvis). Español de México, natural y bien escrito: ortografía y puntuación "
        f"correctas. Tono {TONOS.get(tono, TONOS['profesional'])}. Respeta EXACTAMENTE lo que quiere "
        "decir: no inventes datos, fechas, montos ni promesas que no pidió; si algo falta, no lo "
        "rellenes. Sin firma, sin comillas y como máximo un emoji solo si encaja. Responde SOLO "
        "con el mensaje.")
    usuario = f"Lo que quiere decir: {instruccion}"
    if contexto:
        usuario = f"Conversación reciente (para el contexto, no la repitas):\n{contexto}\n\n" + usuario
    for intento in range(2):
        r = cerebro.chat(_cfg["cfg"], [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                         [], 0.4, razonamiento="low")
        texto = re.sub(r"<think>.*?</think>", "", r.get("content") or "", flags=re.S).strip().strip('"«»')
        if texto:
            return texto
        usuario = f"Lo que quiere decir: {instruccion}"   # sin el contexto, por si eso lo confundió
    # el modelo no contestó: se proponen sus palabras tal cual (igual se le muestran para confirmar)
    texto = instruccion.strip()
    return texto[:1].upper() + texto[1:] + ("" if texto.endswith((".", "!", "?")) else ".")


def _nombre_usuario():
    try:
        import presencia
        return presencia._nombre_usuario() or ""
    except Exception:
        return ""


def _confirmar(pregunta):
    if confirmar is None:
        return False
    return bool(confirmar(pregunta))


def _proponer_y_enviar(chat, texto):
    if not _confirmar(f"Le mando a {chat}:\n\n{texto}\n\n¿Lo envío?"):
        return ("No lo envié. Si quieres, dime qué le cambio (más corto, más formal, agrega algo...) "
                "o pídeme mandarlo tal cual.")
    enviar(chat, texto)
    _borrador.update(chat=None, texto=None)
    return f"Listo, le mandé el mensaje a {chat}."


# ---------- Skills ----------
@skill("whatsapp_mensaje",
       "Le escribe un mensaje de WhatsApp a un contacto o grupo: abre su chat, lee lo último para "
       "entender el contexto, redacta un mensaje bien escrito (profesional por defecto) a partir de "
       "lo que quieres decir, te lo muestra y SOLO lo envía si lo confirmas. 'Dile a Ana que llego "
       "tarde', 'contéstale a mi jefe que sí puedo el lunes', 'mándale a Luis tal cual: ya voy'.",
       {"contacto": {"type": "string", "description": "A quién (nombre del contacto o grupo; 'yo' para tu propio chat)"},
        "que_decir": {"type": "string", "description": "Lo que quiere decir, con sus palabras"},
        "tono": {"type": "string", "description": "profesional (por defecto), amable, formal o breve"},
        "tal_cual": {"type": "boolean", "description": "true si pidió mandarlo exactamente así, sin retocar"}},
       requeridos=["contacto", "que_decir"], sensible=True)
def whatsapp_mensaje(contacto, que_decir, tono="profesional", tal_cual=False):
    if isinstance(tal_cual, str):
        tal_cual = tal_cual.strip().lower() in ("true", "si", "sí", "1")
    try:
        estado, dato = abrir_chat(contacto)
    except Exception as e:
        return Fallo(f"No pude abrir WhatsApp: {e}")
    if estado == "varios":
        return Fallo(f"Encontré varios chats parecidos a «{contacto}»: {', '.join(dato)}. ¿A cuál?")
    if estado == "ninguno":
        return Fallo(f"No encontré a «{contacto}» en tus chats ni contactos de WhatsApp."
                     + (f" ¿Quisiste decir {dato}?" if dato else ""))
    chat = dato
    contexto = "" if tal_cual else leer_conversacion()
    try:
        texto = que_decir.strip() if tal_cual else redactar(chat, que_decir, contexto, tono, _nombre_usuario())
    except Exception as e:
        return Fallo(f"No pude redactar el mensaje ({e}).")
    _borrador.update(chat=chat, texto=texto, instruccion=que_decir, tono=tono, contexto=contexto)
    try:
        return _proponer_y_enviar(chat, texto)
    except Exception as e:
        return Fallo(f"No se envió: {e}")


@skill("whatsapp_corregir",
       "Cambia el último mensaje de WhatsApp que Jarvis propuso y NO se envió, y lo vuelve a proponer: "
       "'hazlo más corto', 'más formal', 'agrégale que llevo los documentos', 'mándalo así'.",
       {"cambio": {"type": "string", "description": "Qué cambiarle, o 'así' para mandarlo como estaba"}},
       requeridos=["cambio"], sensible=True)
def whatsapp_corregir(cambio):
    if not _borrador.get("texto"):
        return Fallo("No tengo ningún mensaje pendiente de WhatsApp.")
    chat = _borrador["chat"]
    if re.fullmatch(r"\s*(asi|así|tal cual|como estaba|mandalo|mándalo|envialo|envíalo)[\s.!]*", cambio or "", re.I):
        texto = _borrador["texto"]
    else:
        try:
            texto = redactar(chat, f"{_borrador['instruccion']}\n\nYa le propuse esto:\n{_borrador['texto']}\n\n"
                                   f"Cámbialo así: {cambio}", _borrador["contexto"], _borrador["tono"],
                             _nombre_usuario())
        except Exception as e:
            return Fallo(f"No pude corregirlo ({e}).")
        _borrador["texto"] = texto
    try:
        estado, dato = abrir_chat(chat)
        if estado != "ok":
            return Fallo(f"Ya no encuentro el chat de {chat}.")
        return _proponer_y_enviar(dato, texto)
    except Exception as e:
        return Fallo(f"No se envió: {e}")


@skill("whatsapp_llamar",
       "Hace una llamada (o videollamada) de WhatsApp a un contacto, después de que confirmes: "
       "'llama a mi mamá por WhatsApp', 'videollamada con Luis'.",
       {"contacto": {"type": "string", "description": "A quién"},
        "video": {"type": "boolean", "description": "true para videollamada"}},
       requeridos=["contacto"], sensible=True)
def whatsapp_llamar(contacto, video=False):
    if isinstance(video, str):
        video = video.strip().lower() in ("true", "si", "sí", "1")
    try:
        estado, dato = abrir_chat(contacto)
    except Exception as e:
        return Fallo(f"No pude abrir WhatsApp: {e}")
    if estado == "varios":
        return Fallo(f"Encontré varios parecidos a «{contacto}»: {', '.join(dato)}. ¿A cuál?")
    if estado == "ninguno":
        return Fallo(f"No encontré a «{contacto}» en WhatsApp." + (f" ¿Quisiste decir {dato}?" if dato else ""))
    tipo = "una videollamada" if video else "una llamada"
    if not _confirmar(f"¿Le hago {tipo} de WhatsApp a {dato}?"):
        return "No marqué."
    try:
        llamar_chat_abierto(dato, video)
    except Exception as e:
        return Fallo(f"No pude marcar: {e}")
    return f"Marcando a {dato}" + (" por video." if video else ".")


@skill("whatsapp_leer_chat",
       "Lee lo último de un chat de WhatsApp (abre el chat: queda como leído) para resumirlo o "
       "contestarlo: '¿qué me dijo Ana?', 'lee el chat del grupo de la escuela'.",
       {"contacto": {"type": "string", "description": "El chat"}},
       requeridos=["contacto"], terminal=False)
def whatsapp_leer_chat(contacto):
    try:
        estado, dato = abrir_chat(contacto)
    except Exception as e:
        return Fallo(f"No pude abrir WhatsApp: {e}")
    if estado == "varios":
        return Fallo(f"Encontré varios chats parecidos a «{contacto}»: {', '.join(dato)}. ¿Cuál?")
    if estado != "ok":
        return Fallo(f"No encontré el chat de «{contacto}»." + (f" ¿Quisiste decir {dato}?" if dato else ""))
    texto = leer_conversacion(12)
    if not texto:
        return Fallo("Abrí el chat pero no pude leerlo.")
    return f"Chat de {dato} (YO = el usuario):\n{texto}"


def configurar(cfg):
    _cfg["cfg"] = cfg
