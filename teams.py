"""Microsoft Teams: navegación y control general leyendo su árbol de accesibilidad.

No existe una API para "haz clic aquí" en Teams normal, así que Genesis lee la misma
información que usaría un lector de pantalla (UI Automation) y actúa sobre los controles
por su nombre visible, con el mismo buscador de parecidos que usa abrir_app. La nueva Teams
(MSTeams) renderiza todo dentro de un WebView2/Chromium: ese contenido tarda unos segundos en
aparecer en el árbol de accesibilidad la primera vez que algo lo consulta; _documento() espera
a que aparezca en vez de fallar de una.

Enviar un mensaje toca a otra persona real, así que nunca se manda sin que el usuario oiga el
texto exacto y confirme (igual que memoria.olvidar), y no pasa por el atajo "terminal" de
genesis.py: siempre hay una vuelta al modelo después de cada acción para que decida el
siguiente paso con lo que ve en pantalla.
"""
import os
import time
from difflib import SequenceMatcher

from pywinauto import Desktop
from pywinauto.keyboard import send_keys

import memoria
from apps import fonetica
from skills import skill

APP_ID = "shell:AppsFolder\\MSTeams_8wekyb3d8bbwe!MSTeams"
CLICABLES = ("Button", "TabItem", "ListItem", "MenuItem", "TreeItem", "Hyperlink")
SECCIONES = {
    "actividad": "Actividad", "calendario": "Calendario", "equipos": "Equipos",
    "clases": "Equipos", "equipo": "Equipos", "tareas": "Tareas", "chat": "Chat",
    "chats": "Chat", "mensajes": "Chat", "llamadas": "Llamadas", "onedrive": "OneDrive",
    "archivos": "OneDrive", "copilot": "Copilot",
}
UMBRAL_CLICK = 0.45
# Botones que hacen algo difícil de deshacer o que otros ven (salir de un equipo, borrar,
# llamar, entregar): se pulsan solo tras confirmar, aunque el modelo lo haya decidido tras
# leer un texto de la pantalla (que pudo escribir cualquiera).
_PELIGROSOS = [fonetica(x) for x in (
    "salir", "abandonar", "eliminar", "borrar", "quitar", "llamar", "reunirse", "unirse",
    "entregar", "desentregar", "deshacer entrega", "archivar", "bloquear", "leave", "delete",
    "remove", "turn in")]  # sin "call"/"join": "kal" y "join" aparecen dentro de "Calendario" y otros

_ventana = None


# ---------- Ventana y árbol de accesibilidad ----------
def _ventana_teams():
    global _ventana
    if _ventana is not None:
        try:
            _ventana.element_info  # ¿sigue viva la referencia?
        except Exception:
            _ventana = None
    for intento in range(2):
        if _ventana is None:
            for w in Desktop(backend="uia").windows():
                try:
                    if w.element_info.class_name == "TeamsWebView":
                        _ventana = w
                        break
                except Exception:
                    continue
        if _ventana is not None:
            return _ventana
        try:
            os.startfile(APP_ID)
        except OSError:
            return None
        time.sleep(6)
    return None


def _documento(ventana, intentos=6, espera=2.5):
    """Espera a que el contenido web tenga accesibilidad activa (ver docstring del módulo)."""
    for _ in range(intentos):
        docs = ventana.descendants(control_type="Document")
        if docs:
            return docs[0]
        time.sleep(espera)
    return None


def _preparar():
    ventana = _ventana_teams()
    if ventana is None:
        return None, None, "No pude abrir Microsoft Teams."
    try:
        ventana.set_focus()
    except Exception:
        pass
    doc = _documento(ventana)
    if doc is None:
        return None, None, "Teams está abierto pero no terminó de cargar; intenta de nuevo en unos segundos."
    return ventana, doc, None


def _click(nodo):
    try:
        nodo.invoke()
    except Exception:
        nodo.click_input()


# ---------- Buscar controles por nombre aproximado ----------
def _candidatos(raiz, tipos, maxprof=12, limite=2000):
    pila, vistos = [(raiz, 0)], 0
    while pila and vistos < limite:
        nodo, prof = pila.pop()
        vistos += 1
        try:
            ei = nodo.element_info
        except Exception:
            continue
        if ei.control_type in tipos and ei.name:
            yield nodo, ei.name
        if prof < maxprof:
            try:
                hijos = nodo.children()
            except Exception:
                hijos = []
            for h in hijos:
                pila.append((h, prof + 1))


def _mejor_control(raiz, texto, tipos=CLICABLES):
    """Ojo con los falsos positivos: un texto de icono largo puede "contener" la palabra
    pedida (p. ej. 'Imagen de perfil de Tareas.' al buscar 'tareas') sin ser lo que se
    quiere. Empezar-por pesa mucho más que solo-contener, y contener se escala por cuánto
    del nombre real es la búsqueda, para no confundir una coincidencia parcial larga con
    una buena."""
    objetivo = fonetica(texto)
    mejor, mejor_nombre, mejor_p = None, None, 0.0
    for nodo, nombre in _candidatos(raiz, tipos):
        ln = fonetica(nombre)
        if not objetivo or not ln:
            continue
        p = SequenceMatcher(None, objetivo, ln).ratio()
        if ln.startswith(objetivo):  # típico de "Tareas (Ctrl+4)" al pedir "tareas"
            p = max(p, 0.9)
        elif objetivo in ln or ln in objetivo:
            corta, larga = sorted((len(objetivo), len(ln)))
            p = max(p, 0.9 * corta / larga)
        if p > mejor_p:
            mejor, mejor_nombre, mejor_p = nodo, nombre, p
    return mejor, mejor_nombre, mejor_p


# ---------- Skills de navegación y lectura ----------
@skill("teams_abrir",
       "Abre Microsoft Teams (si no está abierto) y va a una sección: actividad, calendario, "
       "equipos (o clases), tareas, chat, llamadas u onedrive. Sin sección solo abre y enfoca "
       "Teams. Para entrar a un canal, clase o chat concretos usa teams_click.",
       {"seccion": {"type": "string",
                    "description": "actividad, calendario, equipos, tareas, chat, llamadas u onedrive"}},
       requeridos=[], terminal=False)
def teams_abrir(seccion=""):
    _, doc, error = _preparar()
    if error:
        return error
    if not seccion.strip():
        return "Teams está abierto."
    clave = SECCIONES.get(fonetica(seccion).replace(" ", ""), seccion)
    nodo, nombre, p = _mejor_control(doc, clave, ("Button",))
    if nodo is None or p < 0.5:
        return f"No encontré la sección '{seccion}' en Teams."
    _click(nodo)
    time.sleep(0.8)
    return f"Abriendo {nombre} en Teams."


@skill("teams_click",
       "Hace clic en algo visible en Microsoft Teams por su texto o nombre aproximado: un canal, "
       "una clase, una pestaña ('Tareas', 'Anuncios', 'Trabajo en clase'), un chat, una tarea de la "
       "lista, un botón. Úsala para navegar paso a paso dentro de Teams; después conviene leer la "
       "pantalla con teams_leer_pantalla. No la uses para enviar mensajes.",
       {"texto": {"type": "string", "description": "Texto o nombre aproximado del elemento"}},
       requeridos=["texto"], terminal=False)
def teams_click(texto):
    _, doc, error = _preparar()
    if error:
        return error
    nodo, nombre, p = _mejor_control(doc, texto)
    if nodo is None or p < UMBRAL_CLICK:
        return f"No encontré nada parecido a '{texto}' en lo que se ve ahora de Teams."
    if any(x in fonetica(nombre) for x in _PELIGROSOS):
        if memoria.pedir_confirmacion is None or not memoria.pedir_confirmacion(
                f"Voy a pulsar '{nombre}' en Teams. ¿Confirmas?"):
            return f"No pulsé '{nombre}'."
    _click(nodo)
    time.sleep(0.8)
    return f"Hice clic en '{nombre}'."


@skill("teams_leer_pantalla",
       "Lee lo que se ve ahora mismo en Microsoft Teams: canales, clases, tareas, chats, mensajes, "
       "botones y texto visible. Úsala después de navegar (teams_abrir o teams_click) para saber "
       "qué hay antes de decidir el siguiente paso, o para contestar qué contiene la pantalla "
       "actual (qué tareas hay, qué dice una tarea o un mensaje, quién escribió, etc.).",
       terminal=False, externo=True)
def teams_leer_pantalla():
    _, doc, error = _preparar()
    if error:
        return error
    vistos, lineas = set(), []
    for _n, nombre in _candidatos(doc, CLICABLES + ("Text", "Edit")):
        t = nombre.strip()
        if t and t not in vistos and len(t) > 1:
            vistos.add(t)
            lineas.append(t)
        if len(lineas) >= 150:
            break
    if not lineas:
        return "No veo contenido legible en la pantalla actual de Teams."
    texto = "En pantalla: " + " | ".join(lineas)
    return texto[:4000]


@skill("teams_desplazar",
       "Baja o sube en la lista o página actual de Teams para ver más contenido (más canales, "
       "más archivos, más mensajes). Úsala cuando teams_leer_pantalla se quede corto y sospeches "
       "que hay más de lo que se ve.",
       {"direccion": {"type": "string", "enum": ["abajo", "arriba"], "description": "Hacia dónde desplazar"}},
       requeridos=["direccion"], terminal=False)
def teams_desplazar(direccion):
    ventana, _doc, error = _preparar()
    if error:
        return error
    try:
        ventana.set_focus()
        send_keys("{PGDN}" if direccion == "abajo" else "{PGUP}")
        time.sleep(0.5)
    except Exception as e:
        return f"No pude desplazar: {type(e).__name__}."
    return f"Desplazado hacia {direccion}."


# ---------- Enviar un mensaje (siempre con confirmación con el texto exacto) ----------
def _campo_mensaje(ventana, doc):
    """Busca el cuadro de escribir: suele ser el control editable más abajo de la ventana."""
    y_ventana = ventana.rectangle().bottom
    candidatos = []
    for tipo in ("Edit", "Document"):
        for nodo, _nombre in _candidatos(doc, (tipo,)):
            if nodo is doc:
                continue
            try:
                r = nodo.rectangle()
            except Exception:
                continue
            if r.height() and r.height() < 300 and r.bottom > y_ventana - 250:
                candidatos.append((r.top, nodo))
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: -c[0])  # el que esté más abajo
    return candidatos[0][1]


def _escribir(texto):
    especiales = set("+^%~(){}[]")
    send_keys("".join("{" + c + "}" if c in especiales else c for c in texto),
              with_spaces=True, pause=0.01)


def _destino(ventana):
    """A quién va el mensaje, sacado del título ("Chat | Juan Pérez | Microsoft Teams")."""
    partes = [p.strip() for p in (ventana.window_text() or "").split("|")]
    partes = [p for p in partes if p and p != "Microsoft Teams"]
    return partes[-1] if partes else "el chat abierto"


def _foco_en(ventana, campo):
    """True solo si Teams está al frente y el cursor quedó dentro del cuadro de escribir.
    send_keys escribe donde esté el foco: sin esta comprobación, un clic que no atinó
    mandaría el texto a otro sitio (u otra app)."""
    try:
        import win32gui
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        if win32gui.GetForegroundWindow() != ventana.handle:
            return False
        r = UIAElementInfo(IUIA().iuia.GetFocusedElement()).rectangle
        c = campo.rectangle()
        return (c.left - 3 <= r.left and r.right <= c.right + 3
                and c.top - 3 <= r.top and r.bottom <= c.bottom + 3)
    except Exception:
        return False


@skill("teams_enviar_mensaje",
       "Escribe y envía un mensaje en el chat o canal que ya está abierto en Microsoft Teams "
       "(ábrelo primero con teams_click). Pide confirmación diciendo a quién y el texto exacto "
       "antes de enviarlo, porque lo va a leer otra persona.",
       {"texto": {"type": "string", "description": "El mensaje a enviar, tal cual lo dijo el usuario"}},
       requeridos=["texto"])
def teams_enviar_mensaje(texto):
    if memoria.pedir_confirmacion is None:
        return "No puedo pedir confirmación ahora, así que no envié nada."
    ventana, doc, error = _preparar()
    if error:
        return error
    destino = _destino(ventana)
    if not memoria.pedir_confirmacion(f"Voy a enviar a {destino}: {texto}. ¿Confirmas?"):
        return "El usuario canceló. No se envió nada."

    ventana, doc, error = _preparar()  # la confirmación pudo tardar: se vuelve a leer la pantalla
    if error:
        return error
    if _destino(ventana) != destino:
        return "La conversación abierta cambió mientras confirmabas; no envié nada."
    campo = _campo_mensaje(ventana, doc)
    if campo is None:
        return "No encontré el cuadro de escribir; asegúrate de tener abierto el chat correcto."
    try:
        ventana.set_focus()
        campo.click_input()
        time.sleep(0.4)
        if not _foco_en(ventana, campo):
            return "No logré poner el cursor en el cuadro de escribir; no envié nada para no escribir en otro lado."
        _escribir(texto)
        time.sleep(0.2)
        send_keys("{ENTER}")
    except Exception as e:
        return f"No pude enviar el mensaje: {type(e).__name__}."
    return f"Mensaje enviado a {destino}."


if __name__ == "__main__":
    v, d, err = _preparar()
    print("error:" if err else "ok:", err or v.window_text())
    if d is not None:
        print(teams_leer_pantalla()[:500])
