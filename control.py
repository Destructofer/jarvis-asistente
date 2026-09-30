"""Control del equipo "como si fueran tus manos": cambiar de ventana, teclas, escribir, dar
clic en botones por su texto, leer lo que hay en la ventana y rutinas para demos.

clic_en usa el mismo truco que teams.py: el árbol de accesibilidad de Windows (UI Automation),
que es lo que usa un lector de pantalla. Chrome, Edge, las apps de Office y casi cualquier
programa lo exponen, así que "dale clic a Iniciar sesión" funciona en tu software web sin
coordenadas fijas: si el botón se mueve de lugar, Jarvis lo sigue encontrando por su nombre.
"""
import os
import re
import time
from difflib import SequenceMatcher

import psutil
import pyautogui
import pyperclip

import apps
import memoria
import skills
from apps import fonetica, puntaje
from skills import Callado, Fallo, _norm, skill

pyautogui.FAILSAFE = False  # sin esto, si el mouse queda en una esquina pyautogui lanza error
pyautogui.PAUSE = 0.03

# Quién ejecuta los pasos de una rutina y quién habla: los pone genesis.py al arrancar para que
# las rutinas pasen por las mismas confirmaciones que cualquier orden normal.
ejecutor = None   # fn(nombre_skill, args) -> str
hablar = None     # fn(texto)

CLICABLES = ("Button", "Hyperlink", "MenuItem", "TabItem", "ListItem", "TreeItem",
             "CheckBox", "RadioButton", "ComboBox", "SplitButton", "Edit")
LEGIBLES = CLICABLES + ("Text", "Header", "HeaderItem", "DataItem")
UMBRAL_CLIC = 0.5

# Clics que hacen algo difícil de deshacer o que otras personas ven (enviar, entregar,
# publicar, unirse a una llamada): siempre se confirman. Mismo criterio que teams.py, para que
# Teams abierto en el navegador no se salte la protección que sí tiene la app.
PELIGROSOS = [fonetica(x) for x in (
    "eliminar", "borrar", "quitar", "pagar", "comprar", "confirmar compra", "desinstalar",
    "formatear", "salir de la cuenta", "cerrar sesion", "delete", "remove", "pay", "buy",
    "entregar", "deshacer entrega", "enviar", "reenviar", "send", "submit", "publicar",
    "publish", "transferir", "transfer", "unirse", "reunirse", "llamar",
    "sign out", "signout", "log out", "logout", "abandonar", "salir del equipo")]

# Clases de ventana que no son "una app" (escritorio, barra de tareas...)
CLASES_SISTEMA = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                  "NotifyIconOverflowWindow", "Windows.UI.Core.CoreWindow"}

TECLAS = {
    "enter": "enter", "entrar": "enter", "intro": "enter", "aceptar": "enter", "return": "enter",
    "escape": "esc", "esc": "esc", "escapar": "esc",
    "tab": "tab", "tabulador": "tab", "tabulacion": "tab",
    "espacio": "space", "barra espaciadora": "space", "space": "space",
    "borrar": "backspace", "retroceso": "backspace", "backspace": "backspace",
    "suprimir": "delete", "supr": "delete", "delete": "delete",
    "arriba": "up", "flecha arriba": "up", "flecha hacia arriba": "up",
    "abajo": "down", "flecha abajo": "down", "flecha hacia abajo": "down",
    "izquierda": "left", "flecha izquierda": "left", "flecha a la izquierda": "left",
    "derecha": "right", "flecha derecha": "right", "flecha a la derecha": "right",
    "inicio": "home", "home": "home", "fin": "end", "end": "end",
    "re pag": "pageup", "repag": "pageup", "pagina arriba": "pageup", "page up": "pageup",
    "av pag": "pagedown", "avpag": "pagedown", "pagina abajo": "pagedown", "page down": "pagedown",
    "control": "ctrl", "ctrl": "ctrl", "contrl": "ctrl",
    "mayusculas": "shift", "mayus": "shift", "shift": "shift",
    "alt": "alt", "alternativa": "alt", "alt gr": "altgr",
    "windows": "win", "win": "win", "tecla windows": "win",
    "imprimir pantalla": "printscreen", "impr pant": "printscreen",
}
for _i in range(1, 13):
    TECLAS[f"f{_i}"] = f"f{_i}"
    TECLAS[f"efe {_i}"] = f"f{_i}"


# ---------- Ventanas ----------
def _win32():
    import win32con
    import win32gui
    import win32process
    return win32gui, win32con, win32process


def _pid_de(hwnd):
    _g, _c, wp = _win32()
    try:
        return wp.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return None


def _es_propia(hwnd):
    return _pid_de(hwnd) == os.getpid()


def _es_app(hwnd):
    g, _c, _p = _win32()
    try:
        return (g.IsWindowVisible(hwnd) and g.GetWindowText(hwnd).strip()
                and g.GetClassName(hwnd) not in CLASES_SISTEMA and not _es_propia(hwnd))
    except Exception:
        return False


def ventana_activa():
    """hwnd de la ventana con la que el usuario está trabajando. Si lo que está al frente es
    una ventanita de Jarvis (confirmar, escribir orden), se toma la siguiente debajo."""
    g, c, _p = _win32()
    h = g.GetForegroundWindow()
    vistas = 0
    while h and vistas < 60:
        if _es_app(h):
            return h
        h = g.GetWindow(h, c.GW_HWNDNEXT)
        vistas += 1
    return None


def _titulo(hwnd):
    g, _c, _p = _win32()
    try:
        return g.GetWindowText(hwnd)
    except Exception:
        return ""


def _proceso(hwnd):
    try:
        return psutil.Process(_pid_de(hwnd)).name()
    except Exception:
        return ""


def _todas_las_ventanas():
    g, _c, _p = _win32()
    res = []

    def cada(h, _):
        if _es_app(h):
            res.append(h)
        return True
    g.EnumWindows(cada, None)
    return res


def buscar_ventana(nombre):
    """(hwnd, título, parecido) de la ventana abierta que más se parece a 'nombre', comparando
    contra el título ("Mi sistema - Google Chrome") y contra el programa ("chrome")."""
    mejor = (None, "", 0.0)
    for h in _todas_las_ventanas():
        titulo = _titulo(h)
        proc = os.path.splitext(_proceso(h))[0]
        p = max(puntaje(nombre, titulo), puntaje(nombre, proc) if proc else 0.0)
        if p > mejor[2]:
            mejor = (h, titulo, p)
    return mejor


def traer_al_frente(hwnd):
    g, c, _p = _win32()
    try:
        if g.IsIconic(hwnd):
            g.ShowWindow(hwnd, c.SW_RESTORE)
        try:
            g.SetForegroundWindow(hwnd)
        except Exception:
            # Windows bloquea SetForegroundWindow si el proceso no tiene el foco; pulsar Alt
            # "desbloquea" el permiso (es el truco estándar que usa pywinauto también).
            pyautogui.press("alt")
            g.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        return g.GetForegroundWindow() == hwnd
    except Exception:
        try:
            from pywinauto import Desktop
            Desktop(backend="uia").window(handle=hwnd).set_focus()
            return True
        except Exception:
            return False


@skill("enfocar_ventana",
       "Cambia a una ventana o app que YA está abierta y la trae al frente: 'cambia a Chrome', "
       "'ve a Visual Studio Code', 'regresa a PowerPoint', 'muéstrame WhatsApp'. Si la app no "
       "está abierta, usa abrir_app.",
       {"nombre": {"type": "string", "description": "Nombre aproximado de la app o del título de la ventana"}})
def enfocar_ventana(nombre):
    h, titulo, p = buscar_ventana(nombre)
    if h is None or p < apps.UMBRAL_INTENTO:
        return Fallo(f"No tengo ninguna ventana abierta parecida a '{nombre}'. Si quieres, la abro.")
    if not traer_al_frente(h):
        return Fallo(f"Encontré '{titulo}' pero Windows no me dejó traerla al frente.")
    return f"Listo, en {titulo}."


# ---------- Teclado ----------
def interpretar_teclas(texto):
    """'control más c' -> ['ctrl', 'c']; 'flecha derecha' -> ['right']; 'f5' -> ['f5'].
    Devuelve None si alguna parte no es una tecla conocida."""
    t = _norm(str(texto).replace("+", " mas "))  # _norm quitaría el "+"
    t = re.sub(r"\b(la|el|tecla|teclas|boton|presiona|pulsa|oprime|aprieta|dale)\b", " ", t)
    t = re.sub(r"\s+(mas|y|con)\s+", "+", " " + " ".join(t.split()) + " ").strip()
    partes = [p.strip() for p in t.split("+") if p.strip()]

    def una(p):
        if p in TECLAS:
            return TECLAS[p]
        if len(p) == 1 and p.isalnum():
            return p
        if p.replace(" ", "") in TECLAS:
            return TECLAS[p.replace(" ", "")]
        if p in pyautogui.KEYBOARD_KEYS:
            return p
        return None

    teclas = []
    for p in partes:
        k = una(p)
        if k is None and " " in p:  # "alt tab" dicho sin "más": cada palabra es una tecla
            sueltas = [una(x) for x in p.split()]
            if all(sueltas):
                teclas.extend(sueltas)
                continue
        if k is None:
            return None
        teclas.append(k)
    return teclas or None


@skill("presionar_teclas",
       "Presiona una tecla o combinación en la ventana activa: enter, escape, tab, espacio, "
       "flechas, retroceso, f5, control+c, alt+tab, windows+d, etc. Para escribir palabras usa "
       "escribir_texto.",
       {"teclas": {"type": "string", "description": "Tecla o combinación, p. ej. 'enter', 'ctrl+s', 'flecha derecha'"},
        "veces": {"type": "integer", "description": "Cuántas veces pulsarla (1 por defecto)"}},
       requeridos=["teclas"], sensible=True)
def presionar_teclas(teclas, veces=1):
    lista = interpretar_teclas(teclas)
    if not lista:
        return Fallo(f"No reconozco la tecla '{teclas}'.")
    veces = max(1, min(20, int(veces or 1)))
    for _ in range(veces):
        if len(lista) == 1:
            pyautogui.press(lista[0])
        else:
            pyautogui.hotkey(*lista)
        time.sleep(0.05)
    nombre = "+".join(lista)
    return Callado(f"Presioné {nombre}" + (f" {veces} veces." if veces > 1 else "."))


@skill("desplazar",
       "Baja o sube la página o documento de la ventana que está al frente (scroll): 'baja la "
       "página', 'sube', 've al final', 'regresa arriba'. No pide confirmación: solo mueve la vista.",
       {"direccion": {"type": "string", "enum": ["abajo", "arriba", "inicio", "final"],
                      "description": "Hacia dónde mover la vista"},
        "cantidad": {"type": "integer", "description": "Cuántas pantallas mover (1 por defecto)"}},
       requeridos=["direccion"])
def desplazar(direccion, cantidad=1):
    h = ventana_activa()
    if h:
        traer_al_frente(h)
    tecla = {"abajo": "pagedown", "arriba": "pageup", "inicio": "home", "final": "end"}.get(direccion)
    if not tecla:
        return f"No sé desplazar hacia '{direccion}'."
    for _ in range(1 if tecla in ("home", "end") else max(1, min(10, int(cantidad or 1)))):
        pyautogui.press(tecla)
        time.sleep(0.15)
    return Callado(f"Desplazado hacia {direccion}.")


@skill("escribir_texto",
       "Escribe un texto donde está el cursor en la ventana activa (un buscador, un formulario, "
       "un documento). Pon enter=true para pulsar Enter al terminar (por ejemplo para buscar).",
       {"texto": {"type": "string", "description": "Texto exacto a escribir"},
        "enter": {"type": "boolean", "description": "true para pulsar Enter al final"}},
       requeridos=["texto"], sensible=True)
def escribir_texto(texto, enter=False):
    # Se pega desde el portapapeles: pyautogui.write no sabe escribir acentos ni la ñ.
    try:
        anterior = pyperclip.paste()
    except Exception:
        anterior = None
    pyperclip.copy(str(texto))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if enter:
        pyautogui.press("enter")
    if anterior is not None:
        time.sleep(0.2)
        try:
            pyperclip.copy(anterior)
        except Exception:
            pass
    return Callado("Escrito." if not enter else "Escrito y enviado.")


@skill("cerrar_ventana_activa",
       "Cierra la ventana que está al frente, como darle a la X ('cierra esta ventana'). Si la "
       "app pregunta si guardar, se queda preguntando.",
       sensible=True)
def cerrar_ventana_activa():
    g, c, _p = _win32()
    h = ventana_activa()
    if not h:
        return "No hay ninguna ventana al frente que pueda cerrar."
    titulo, proc = _titulo(h), _proceso(h)
    if apps.bloqueado(proc) and g.GetClassName(h) != "CabinetWClass":
        return f"'{titulo}' es parte del sistema; no la cierro."
    g.PostMessage(h, c.WM_CLOSE, 0, 0)
    return f"Cerré {titulo}."


# ---------- UI Automation: clic y lectura ----------
def _raiz_uia(hwnd):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=hwnd).wrapper_object()


def _elementos(raiz, tipos):
    """(nodo, nombre, tipo) de los controles con nombre. Usa descendants() por tipo, que es
    una sola búsqueda de UI Automation por tipo (mucho más rápido que recorrer hijo por hijo)."""
    vistos = []
    for tipo in tipos:
        try:
            nodos = raiz.descendants(control_type=tipo)
        except Exception:
            continue
        for n in nodos:
            try:
                nombre = (n.element_info.name or "").strip()
            except Exception:
                continue
            if nombre:
                vistos.append((n, nombre, tipo))
    return vistos


def _parecido(buscado, nombre):
    objetivo, ln = fonetica(buscado), fonetica(nombre)
    if not objetivo or not ln:
        return 0.0
    if objetivo == ln:
        return 1.0
    p = SequenceMatcher(None, objetivo, ln).ratio()
    if ln.startswith(objetivo):
        p = max(p, 0.9)
    elif objetivo in ln or ln in objetivo:
        corta, larga = sorted((len(objetivo), len(ln)))
        p = max(p, 0.9 * corta / larga)
    return p


def es_peligroso(nombre):
    """True si pulsar un control con este texto hace algo difícil de deshacer o que otros ven.

    Se compara por palabras, no por subcadenas: "send" no debe saltar con "Descendente" ni
    "llamar" con "Llamadas". Los términos largos aceptan terminaciones ("Enviarlo",
    "Eliminarlos"); los cortos ("pay", "send") tienen que ser la palabra exacta."""
    palabras = fonetica(nombre).split()
    for termino in PELIGROSOS:
        partes = termino.split()
        for i in range(len(palabras) - len(partes) + 1):
            if all(w == p or (len(p) >= 5 and w.startswith(p))
                   for w, p in zip(palabras[i:i + len(partes)], partes)):
                return True
    return False


def mejor_elemento(elementos, texto):
    mejor = (None, "", "", 0.0)
    for n, nombre, tipo in elementos:
        p = _parecido(texto, nombre)
        if tipo == "Edit":
            p *= 0.95  # ante un empate, preferir el botón al campo de texto del mismo nombre
        if p > mejor[3]:
            mejor = (n, nombre, tipo, p)
    return mejor


def _visible(nodo):
    try:
        r = nodo.rectangle()
        return r.width() > 0 and r.height() > 0 and not nodo.element_info.is_offscreen
    except Exception:
        return True


def _hacer_clic(nodo, tipo):
    if tipo == "Edit":
        nodo.click_input()  # poner el cursor en un campo: clic real
        return
    try:
        nodo.invoke()
    except Exception:
        try:
            nodo.select()
        except Exception:
            nodo.click_input()


@skill("clic_en",
       "Hace clic en un botón, enlace, pestaña, menú, casilla o campo visible, buscándolo por "
       "su texto: 'dale clic a Iniciar sesión', 'pica el botón Guardar', 'entra al campo "
       "Usuario'. Funciona en páginas web (Chrome, Edge) y en casi cualquier programa. Por "
       "defecto busca en la ventana que está al frente; 'ventana' elige otra que ya esté abierta.",
       {"texto": {"type": "string", "description": "Texto visible del botón o elemento"},
        "ventana": {"type": "string", "description": "Opcional: app o ventana donde buscar"}},
       requeridos=["texto"], sensible=True, terminal=False)
def clic_en(texto, ventana=""):
    if ventana.strip():
        h, titulo, p = buscar_ventana(ventana)
        if h is None or p < apps.UMBRAL_INTENTO:
            return Fallo(f"No encontré la ventana '{ventana}'.")
        traer_al_frente(h)
    else:
        h = ventana_activa()
        if not h:
            return Fallo("No hay ninguna ventana al frente.")
        titulo = _titulo(h)

    raiz = _raiz_uia(h)
    elementos = []
    for intento in range(3):
        elementos = [e for e in _elementos(raiz, CLICABLES) if _visible(e[0])]
        nodo, nombre, tipo, p = mejor_elemento(elementos, texto)
        if nodo is not None and p >= UMBRAL_CLIC:
            break
        # Chrome/Edge arman el árbol de accesibilidad la primera vez que alguien lo pide:
        # la primera consulta puede venir casi vacía. Se espera y se vuelve a leer.
        time.sleep(1.2)
    else:
        return Fallo(f"No encontré nada parecido a '{texto}' en {titulo}. "
                     f"Veo {len(elementos)} elementos; si quieres, te leo lo que hay en la ventana.")

    if es_peligroso(nombre):
        if memoria.pedir_confirmacion is None or not memoria.pedir_confirmacion(
                f"Voy a pulsar '{nombre}'. ¿Confirmas?"):
            return Fallo(f"No pulsé '{nombre}'.")
    traer_al_frente(h)
    _hacer_clic(nodo, tipo)
    time.sleep(0.5)
    return f"Hice clic en '{nombre}'" + (" (campo de texto, ya puedes escribir)." if tipo == "Edit" else ".")


@skill("leer_ventana",
       "Lee los botones, enlaces y textos visibles de la ventana que está al frente (o la "
       "indicada). Úsala antes de clic_en si no sabes cómo se llama un botón, o para decir qué "
       "hay en pantalla.",
       {"ventana": {"type": "string", "description": "Opcional: app o ventana a leer"}},
       requeridos=[], terminal=False, externo=True)
def leer_ventana(ventana=""):
    if ventana.strip():
        h, _t, p = buscar_ventana(ventana)
        if h is None or p < apps.UMBRAL_INTENTO:
            return Fallo(f"No encontré la ventana '{ventana}'.")
    else:
        h = ventana_activa()
        if not h:
            return Fallo("No hay ninguna ventana al frente.")
    raiz = _raiz_uia(h)
    vistos, lineas = set(), []
    for _n, nombre, tipo in _elementos(raiz, LEGIBLES):
        if nombre not in vistos and len(nombre) > 1:
            vistos.add(nombre)
            lineas.append(f"[{tipo}] {nombre}" if tipo in CLICABLES else nombre)
        if len(lineas) >= 150:
            break
    if not lineas:
        return f"No veo contenido legible en '{_titulo(h)}'."
    return (f"Ventana '{_titulo(h)}': " + " | ".join(lineas))[:2500]


# ---------- Rutinas (demos que salen igual siempre) ----------
# Respaldo para skills que todavía devuelven un str normal al fallar (las que ya devuelven
# skills.Fallo no dependen de esto). Frases concretas: "No había ningún apagado programado"
# no es un fallo, y "Windows no me dejó traerla al frente" sí lo es.
FALLOS = ("no encontre", "no pude", "no puedo", "no hay ninguna", "no tengo", "no reconozco",
          "no conozco", "no logre", "no se cerro", "no pulse", "no abri", "no envie",
          "error", "el usuario cancel", "el usuario no lo permitio", "esa funcion esta desactivada",
          "la herramienta", "la presentacion no esta", "no me dejo", "no me ha llegado")


def fallo(resultado):
    """True si el resultado de una skill indica que la acción no se hizo."""
    if isinstance(resultado, Fallo):
        return True
    t = _norm(resultado)
    return t.startswith(FALLOS) or any(f" {x}" in f" {t}" for x in ("no me dejo", "no se cerro"))


def _rutinas():
    return skills._CFG.get("rutinas", {}) or {}


def _buscar_rutina(nombre):
    rutinas = _rutinas()
    if not rutinas:
        return None, None
    mejor = max(rutinas, key=lambda n: puntaje(nombre, n))
    return (mejor, rutinas[mejor]) if puntaje(nombre, mejor) >= 0.5 else (None, None)


@skill("rutina",
       "Ejecuta una rutina guardada en config.json → rutinas: una secuencia fija de pasos para "
       "una demo ('corre la demo de mi software', 'haz la rutina de inicio'). Úsala siempre que "
       "el usuario nombre una rutina; es más confiable que improvisar los pasos.",
       {"nombre": {"type": "string", "description": "Nombre aproximado de la rutina"}})
def rutina(nombre):
    clave, pasos = _buscar_rutina(nombre)
    if not clave:
        disponibles = ", ".join(_rutinas()) or "ninguna"
        return Fallo(f"No tengo una rutina parecida a '{nombre}'. Rutinas: {disponibles}.")
    if ejecutor is None:
        return Fallo("Las rutinas no están listas todavía.")
    hablo = False
    for i, paso in enumerate(pasos, 1):
        if skills.INTERRUPCION.is_set():
            return Callado(f"Rutina '{clave}' detenida en el paso {i}.")
        if not isinstance(paso, dict) or not paso:
            return Fallo(f"El paso {i} de '{clave}' está mal escrito en config.json.")
        # Un paso puede llevar "continuar_si_falla": true además de su acción
        seguir = bool(paso.get("continuar_si_falla", False))
        acciones = [(k, v) for k, v in paso.items() if k != "continuar_si_falla"]
        if len(acciones) != 1:
            return Fallo(f"El paso {i} de '{clave}' está mal escrito en config.json.")
        accion, valor = acciones[0]
        if accion == "esperar":
            skills.INTERRUPCION.wait(float(valor))  # espera, pero se corta si lo interrumpen
            continue
        if accion == "decir":
            if hablar:
                hablar(str(valor))
                hablo = True
            continue
        args = valor if isinstance(valor, dict) else {}
        resultado = ejecutor(accion, args)
        print(f"[Rutina {clave} · paso {i}] {accion} -> {str(resultado)[:120]}")
        if fallo(resultado) and not seguir:
            # Se dice en voz alta: sin nombres internos de herramientas
            return Fallo(f"La rutina '{clave}' se detuvo en el paso {i}: {resultado}")
    fin = f"Rutina '{clave}' completada."
    return Callado(fin) if hablo else fin


@skill("listar_rutinas", "Dice qué rutinas de demo hay guardadas.", terminal=False)
def listar_rutinas():
    r = _rutinas()
    return ("Rutinas: " + ", ".join(r)) if r else "No hay rutinas guardadas en config.json."
