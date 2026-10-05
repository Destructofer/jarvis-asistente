"""Jarvis usa las páginas y las apps como tú: ve qué hay en pantalla y en qué orden, elige "el
primero", "la segunda playlist" o "el último resultado", controla el video y salta anuncios.

    "Jarvis, abre YouTube y pon la primera canción o playlist que veas"
    "reproduce el segundo video" · "abre el primer resultado" · "pon el mix de trap que sale ahí"
    "¿qué videos hay?" -> los dice en orden, y luego "pon el tercero"
    "pausa" · "siguiente canción" · "adelanta 30 segundos" · "pantalla completa" · "subtítulos"
    "salta el anuncio" (y los anuncios que se pueden omitir se omiten solos)
    "busca lofi en esta página" · "escribe hola en el chat"

Cómo: el árbol de accesibilidad de Windows (UI Automation), el mismo de clic_en. En Chrome,
Edge y Opera cada enlace trae su URL real, así que se sabe con certeza si es un video, una
playlist, un mix, un short, un canal o un resultado de búsqueda (sin adivinar por el texto ni
por la imagen), y su posición en pantalla da el orden en que tú lo ves. Los botones del
reproductor de YouTube se reconocen por su atajo de teclado entre paréntesis ("Pausa (k)",
"Siguiente (SHIFT+n)"), que es igual en cualquier idioma, y se pulsan sin necesitar el foco.
Fuera de YouTube (Spotify, otros reproductores) se usan las teclas multimedia de Windows.
"""
import ctypes
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyautogui

import apps
import control
import memoria
from skills import Callado, Fallo, _norm, skill

BASE = Path(__file__).parent
ESTADO = BASE / "datos" / "interaccion.json"

# Qué clases de cosas acepta cada pedido ("la primera playlist" acepta también un mix)
ACEPTA = {
    "video": {"video"},
    "cancion": {"cancion", "video"},
    "playlist": {"playlist", "mix", "album"},
    "mix": {"mix"},
    "short": {"short"},
    "album": {"album", "playlist"},
    "artista": {"artista", "canal"},
    "canal": {"canal", "artista"},
    "podcast": {"podcast"},
    "resultado": {"resultado"},
    "enlace": {"enlace", "navegacion", "resultado", "video", "playlist", "mix", "short", "cancion",
               "album", "artista", "canal", "podcast"},
    "boton": {"boton"},
    "elemento": {"elemento"},
    "cualquiera": {"video", "playlist", "mix", "cancion", "album", "artista", "podcast", "resultado"},
}
# Si no hay nada de lo pedido, lo que sí sirve como "lo primero que veas"
RESPALDO = {"cualquiera": {"enlace", "elemento"},
            "resultado": {"video", "playlist", "mix", "cancion", "album", "artista", "podcast", "enlace"}}
SINONIMOS = {"videos": "video", "canciones": "cancion", "tema": "cancion", "temas": "cancion",
             "musica": "cancion", "rola": "cancion", "lista": "playlist", "listas": "playlist",
             "playlists": "playlist", "lista de reproduccion": "playlist", "mixes": "mix",
             "radio": "mix", "shorts": "short", "albumes": "album", "disco": "album",
             "artistas": "artista", "canales": "canal", "episodio": "podcast",
             "resultados": "resultado", "link": "enlace", "liga": "enlace", "enlaces": "enlace",
             "botones": "boton", "opcion": "elemento", "item": "elemento", "algo": "cualquiera",
             "lo que sea": "cualquiera", "cualquier cosa": "cualquiera", "": "cualquiera"}
MEDIOS = {"video", "cancion", "playlist", "mix", "short", "album", "podcast"}
BUSCADORES = ("google.", "bing.com", "duckduckgo.com", "search.yahoo.", "search.brave.com",
              "ecosia.org", "yandex.")
DURACION = re.compile(r"\s+\d+\s+(?:horas?|minutos?|segundos?|hours?|minutes?|seconds?)\b.*$", re.I)


# ---------- Leer la pantalla ----------
class Cosa:
    """Algo que se ve en la ventana: un enlace, un botón, una fila..."""

    def __init__(self, nodo, nombre, uia, rect, url=""):
        self.nodo, self.nombre, self.uia, self.rect, self.url = nodo, nombre, uia, rect, url
        self.clase = ""
        self.visible = True

    def __repr__(self):
        return f"<{self.clase} {self.nombre[:40]!r}>"


def limpio(nombre):
    """El título sin la duración que YouTube le pega ('... 3 minutos y 7 segundos')."""
    return DURACION.sub("", " ".join(str(nombre).split()))[:90].strip()


def _sitio(host):
    partes = host.lower().split(":")[0].split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else host


def clasificar(url, nombre="", uia="Hyperlink", host_pagina=""):
    """Qué es un enlace según su URL: video, playlist, mix, short, canal, canción, álbum,
    artista, podcast, resultado (de un buscador), navegacion (menús del mismo sitio)..."""
    if url:
        u = urlparse(url)
        h, p = u.netloc.lower(), u.path
        q = parse_qs(u.query)
        if h.endswith("youtube.com") or h == "youtu.be":
            if p.startswith("/shorts/"):
                return "short"
            if p.startswith("/playlist"):
                return "playlist"
            if p == "/watch" or h == "youtu.be":
                lista, v = q.get("list", [""])[0], q.get("v", [""])[0]
                if re.match(r"mix\b", _norm(nombre)):
                    return "mix"
                if not lista:
                    return "video"
                if lista.startswith("RD"):
                    # Un video que YouTube ofrece "como radio" sigue siendo ese video
                    return "video" if (lista[2:] == v or "start_radio" in q) else "mix"
                return "playlist"
            if p.startswith(("/@", "/channel/", "/c/", "/user/")):
                return "canal"
            return "navegacion"
        if h.endswith("spotify.com"):
            for parte, clase in (("/track/", "cancion"), ("/album/", "album"), ("/playlist/", "playlist"),
                                 ("/artist/", "artista"), ("/episode/", "podcast"), ("/show/", "podcast")):
                if parte in p:
                    return clase
            return "navegacion"
        if host_pagina and h and _sitio(h) != _sitio(host_pagina):
            if any(b in host_pagina for b in BUSCADORES):
                return "resultado"
            return "enlace"
        if host_pagina and any(b in host_pagina for b in BUSCADORES):
            return "navegacion"   # menús del propio buscador (Imágenes, Noticias, página 2...)
        return "enlace"
    if uia == "Button":
        return "boton"
    if uia == "Hyperlink":
        return "enlace"
    return "elemento"


def _rect(nodo):
    r = nodo.element_info.rectangle
    return r.left, r.top, r.right, r.bottom


def _valor(nodo):
    try:
        return nodo.iface_value.CurrentValue or ""
    except Exception:
        return ""


def _pagina(raiz):
    """(url, rect) de la página web dentro de la ventana del navegador; ("", None) si no hay."""
    try:
        docs = raiz.descendants(control_type="Document")
    except Exception:
        return "", None
    for d in docs:
        try:
            r = _rect(d)
        except Exception:
            continue
        if r[2] - r[0] > 200 and r[3] - r[1] > 150:
            return _valor(d), r
    return "", None


def _rect_ventana(hwnd):
    try:
        import win32gui
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return 0, 0, 10 ** 5, 10 ** 5


def _se_ve(r, vista):
    """El centro del elemento cae dentro de la parte visible de la página."""
    if r[2] - r[0] < 2 or r[3] - r[1] < 2:
        return False
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return vista[0] <= cx <= vista[2] and vista[1] <= cy <= vista[3]


def _unir(cosas):
    """La miniatura y el título de un mismo video son dos enlaces a la misma URL: se juntan
    en uno, en la posición de la miniatura y con el nombre más completo."""
    por_url, res = {}, []
    for c in cosas:
        if not c.url:
            res.append(c)
            continue
        clave = c.url.split("&pp=")[0]
        otra = por_url.get(clave)
        if otra is None:
            por_url[clave] = c
            res.append(c)
            continue
        if len(limpio(c.nombre)) > len(limpio(otra.nombre)):
            otra.nombre = c.nombre
        if (c.rect[1], c.rect[0]) < (otra.rect[1], otra.rect[0]) and c.visible:
            otra.rect, otra.nodo = c.rect, c.nodo
        otra.visible = otra.visible or c.visible
    return res


def ordenar(cosas):
    """En el orden en que se leen: por renglones (de arriba abajo) y luego de izquierda a
    derecha. Las miniaturas de un mismo renglón empiezan casi a la misma altura."""
    return sorted(cosas, key=lambda c: (round(c.rect[1] / 45), c.rect[0]))


def leer(hwnd, tipos_uia=("Hyperlink",)):
    """[Cosa] de la ventana, ya clasificadas, unidas y en orden de lectura."""
    raiz = control._raiz_uia(hwnd)
    url_pagina, vista = _pagina(raiz)
    dentro_de_pagina = vista is not None
    vista = vista or _rect_ventana(hwnd)
    host = urlparse(url_pagina).netloc.lower()
    cosas = []
    for tipo in tipos_uia:
        try:
            nodos = raiz.descendants(control_type=tipo)
        except Exception:
            continue
        for n in nodos:
            try:
                nombre = (n.element_info.name or "").strip()
                r = _rect(n)
            except Exception:
                continue
            url = _valor(n) if tipo == "Hyperlink" else ""
            if not nombre and not url:
                continue
            # Botones de la ventana del navegador (atrás, pestañas, favoritos): no son la página
            if dentro_de_pagina and tipo != "Hyperlink" and not _se_ve(r, vista):
                continue
            c = Cosa(n, nombre, tipo, r, url)
            c.clase = clasificar(url, nombre, tipo, host)
            c.visible = _se_ve(r, vista)
            cosas.append(c)
    return ordenar(_unir(cosas))


def clases_pedidas(tipo):
    """'canción o playlist' -> {'cancion', 'video', 'playlist', 'mix', 'album'}."""
    clases = set()
    for parte in re.split(r"\s*(?:,|/|\bo\b|\by\b|\bu\b)\s*", _norm(tipo or "")):
        parte = SINONIMOS.get(parte.strip(), parte.strip())
        if parte in ACEPTA:
            clases |= ACEPTA[parte]
    return clases or set(ACEPTA["cualquiera"])


def _respaldo(tipo):
    clases = set()
    for parte in re.split(r"\s*(?:,|/|\bo\b|\by\b|\bu\b)\s*", _norm(tipo or "")):
        clases |= RESPALDO.get(SINONIMOS.get(parte.strip(), parte.strip()), set())
    return clases


def _tipos_uia(clases):
    tipos = ["Hyperlink"]
    if "boton" in clases:
        tipos.append("Button")
    if "elemento" in clases:
        tipos += ["ListItem", "DataItem", "TreeItem"]
    return tuple(tipos)


def _coincide(contiene, nombre):
    if not contiene:
        return True
    nombre = limpio(nombre)  # sin "3 minutos y 7 segundos": "el segundo" no es "segundos"
    a, b = _norm(contiene), _norm(nombre)
    return bool(re.search(rf"{re.escape(a)}", b)) or control._parecido(contiene, nombre) >= 0.6


def escoger(cosas, clases, posicion=1, contiene=""):
    """La cosa número 'posicion' (1 = primera, -1 = última) de esas clases. Primero entre lo
    que se ve; si pides más de las que se ven, cuenta también las de más abajo."""
    lista = [c for c in cosas if c.clase in clases and _coincide(contiene, c.nombre)]
    visibles = [c for c in lista if c.visible]
    for grupo in (visibles, lista):
        if not grupo:
            continue
        i = posicion - 1 if posicion > 0 else posicion
        if -len(grupo) <= i < len(grupo):
            return grupo[i]
    return None


def elegir(hwnd, tipo="cualquiera", posicion=1, contiene="", espera=8.0):
    """Espera (la página puede estar cargando) y devuelve (Cosa o None, cuántas hay)."""
    clases, respaldo = clases_pedidas(tipo), _respaldo(tipo)
    inicio = time.time()
    while True:
        cosas = leer(hwnd, _tipos_uia(clases | respaldo))
        c = escoger(cosas, clases, posicion, contiene)
        if c is not None:
            return c, sum(x.clase in clases for x in cosas)
        # Sin lo pedido: si la página ya cargó y hay algo parecido, eso (sin esperar de más)
        espero = time.time() - inicio
        if respaldo and (espero > min(2.5, espera) or espero > espera):
            c = escoger(cosas, respaldo, posicion, contiene)
            if c is not None or espero > espera:
                return c, sum(x.clase in clases for x in cosas)
        elif espero > espera:
            return None, sum(x.clase in clases for x in cosas)
        time.sleep(0.6)


def activar(cosa, hwnd):
    """Abre/pulsa la cosa. Por accesibilidad (no mueve tu ratón ni necesita el foco); si la
    página no lo acepta, con un clic real."""
    try:
        cosa.nodo.invoke()
        return True
    except Exception:
        pass
    try:
        control.traer_al_frente(hwnd)
        cosa.nodo.click_input()
        return True
    except Exception:
        return False


# ---------- Ventanas ----------
def titulo_corto(hwnd):
    """'(1351) Lofi Girl - YouTube and 5 more pages - Personal - Microsoft Edge' -> 'Lofi Girl'."""
    t = re.sub(r"^\(\d+\)\s*", "", control._titulo(hwnd))
    t = re.split(r"\s+(?:and \d+ more pages?|y \d+ páginas? más)\b", t)[0]
    partes = [x for x in t.split(" - ") if x.strip()]
    while len(partes) > 1 and re.search(r"youtube|google|edge|chrome|opera|brave|firefox|personal",
                                         partes[-1], re.I):
        partes = partes[:-1]
    return (" - ".join(partes) or t)[:60]


def ventanas_youtube():
    """Ventanas con YouTube en la pestaña visible; la del frente primero."""
    import win32gui
    frente = win32gui.GetForegroundWindow()
    res = [h for h in control._todas_las_ventanas() if "youtube" in control._titulo(h).lower()]
    return sorted(res, key=lambda h: h != frente)


def _ventana(ventana="", medios=False):
    if (ventana or "").strip():
        h, _t, p = control.buscar_ventana(ventana)
        return h if h is not None and p >= apps.UMBRAL_INTENTO else None
    h = control.ventana_activa()
    if medios and (h is None or "youtube" not in control._titulo(h).lower()):
        yt = ventanas_youtube()
        if yt and (h is None or not _es_navegador(h)):
            return yt[0]
    return h


def _es_navegador(hwnd):
    return control._proceso(hwnd).lower() in ("chrome.exe", "msedge.exe", "opera.exe", "brave.exe",
                                              "firefox.exe", "vivaldi.exe", "opera_gx.exe")


def esperar_youtube(desde, limite=10.0):
    """La ventana de YouTube que se acaba de abrir (la pestaña nueva queda al frente)."""
    fin = time.time() + limite
    while time.time() < fin:
        h = control.ventana_activa()
        if h and "youtube" in control._titulo(h).lower() and time.time() - desde > 0.8:
            return h
        time.sleep(0.3)
    yt = ventanas_youtube()
    return yt[0] if yt else None


# ---------- Reproductor de YouTube ----------
# El atajo entre paréntesis identifica cada botón en cualquier idioma
ATAJO = {"play": r"\(k\)", "siguiente": r"\(shift\s*\+\s*n\)", "anterior": r"\(shift\s*\+\s*p\)",
         "mute": r"\(m\)", "pantalla": r"\(f\)", "subtitulos": r"\(c\)|subt[ií]tulos|subtitles|captions",
         "cine": r"\(t\)|teclas t\b|theater"}
EN_PAUSA = re.compile(r"^\s*(reproducir|play|reanudar|replay|volver a reproducir)", re.I)
SONANDO = re.compile(r"^\s*(pausa|pause)", re.I)
OMITIR = re.compile(r"^\s*(omitir|saltar|skip|pular|ignorer)(\s+(el\s+|los\s+)?(anuncios?|ads?|an[uú]ncios?|publicidad))?\s*[›>]*\s*$", re.I)
NOMBRES_OMITIR = ("Omitir", "Omitir anuncio", "Omitir anuncios", "Saltar", "Saltar anuncio",
                  "Saltar anuncios", "Skip", "Skip ad", "Skip ads", "Pular", "Pular anúncio")


def _botones(hwnd):
    try:
        return control._raiz_uia(hwnd).descendants(control_type="Button")
    except Exception:
        return []


def boton(hwnd, clave, botones=None):
    """(nodo, nombre) del botón del reproductor, o (None, "")."""
    patron = re.compile(ATAJO[clave], re.I)
    for b in botones if botones is not None else _botones(hwnd):
        try:
            n = b.element_info.name or ""
        except Exception:
            continue
        if patron.search(n):
            return b, n
    return None, ""


def boton_omitir(hwnd, botones=None):
    for b in botones if botones is not None else _botones(hwnd):
        try:
            n = b.element_info.name or ""
            if OMITIR.match(n) and _rect(b)[2] > _rect(b)[0]:
                return b, n
        except Exception:
            continue
    return None, ""


def _pulsar(nodo):
    try:
        nodo.invoke()
        return True
    except Exception:
        try:
            nodo.click_input()
            return True
        except Exception:
            return False


def asegurar_que_suene(hwnd, espera=7.0):
    """Tras abrir un video: si el navegador no lo dejó arrancar solo (reproducción automática
    bloqueada), le da play. En otro hilo para contestar al instante."""
    def revisar():
        time.sleep(2.5)
        fin = time.time() + espera
        while time.time() < fin:
            b, n = boton(hwnd, "play")
            if b is not None:
                if EN_PAUSA.match(n):
                    _pulsar(b)
                return
            # Controles escondidos: los de Windows, solo si es ESTE video el que está en pausa
            pagina = _norm(control._titulo(hwnd))
            for _app, titulo, _art, estado in lo_que_suena():
                if titulo and _norm(titulo)[:25] in pagina:
                    if estado == PAUSADO_SMTC:
                        medios("reanudar")
                    return
            time.sleep(0.7)
    threading.Thread(target=revisar, daemon=True, name="asegurar-play").start()


def reproducir_en(hwnd, tipo="cualquiera", posicion=1, contiene="", espera=10.0):
    """Elige y abre algo en la ventana; devuelve la frase para el usuario (o Fallo)."""
    cosa, total = elegir(hwnd, tipo, posicion, contiene, espera)
    if cosa is None:
        que = tipo if tipo and tipo != "cualquiera" else "algo que abrir"
        if total:
            return Fallo(f"Solo veo {total} de tipo {que}; no hay un número {posicion}.")
        return Fallo(f"No veo {que} en {titulo_corto(hwnd)}. "
                     "Si quieres, te digo lo que hay en pantalla.")
    nombre = limpio(cosa.nombre) or cosa.url
    if control.es_peligroso(nombre):
        if memoria.pedir_confirmacion is None or not memoria.pedir_confirmacion(
                f"Voy a pulsar '{nombre}'. ¿Confirmas?"):
            return Fallo(f"No pulsé '{nombre}'.")
    if not activar(cosa, hwnd):
        return Fallo(f"Encontré '{nombre}' pero la página no me dejó abrirlo.")
    if cosa.clase in MEDIOS and "youtube" in cosa.url:
        asegurar_que_suene(hwnd)
    etiqueta = {"mix": "el mix", "playlist": "la playlist", "album": "el álbum",
                "short": "el short"}.get(cosa.clase, "")
    if cosa.clase in MEDIOS:
        return f"Reproduciendo {etiqueta + ' ' if etiqueta else ''}{nombre}."
    return f"Abrí {nombre}."


# ---------- Skills ----------
@skill("elegir_en_pantalla",
       "Abre, reproduce o selecciona algo que SE VE en la ventana, por su orden o su tipo: 'pon "
       "la primera canción que veas', 'reproduce el segundo video', 'abre la primera playlist', "
       "'el último resultado', 'pon el mix de trap que sale ahí'. Distingue con certeza videos, "
       "playlists, mixes, shorts, canales y resultados de búsqueda. Sirve en YouTube, Google, "
       "Spotify web y casi cualquier página o app. Úsala también después de abrir una página "
       "cuando pidan 'lo primero que veas'.",
       {"tipo": {"type": "string",
                 "description": "Qué buscar: video, cancion, playlist, mix, short, album, artista, "
                                "canal, podcast, resultado, enlace, boton, elemento o cualquiera. "
                                "Varios con 'o': 'cancion o playlist'."},
        "posicion": {"type": "integer", "description": "1 = el primero (por defecto), 2 = el segundo... -1 = el último"},
        "contiene": {"type": "string", "description": "Opcional: palabras de su título ('el de Bad Bunny')"},
        "ventana": {"type": "string", "description": "Opcional: app o ventana; por defecto la del frente"}},
       requeridos=[])  # sin "sensible": lo peligroso (comprar, borrar, enviar...) se confirma adentro
def elegir_en_pantalla(tipo="cualquiera", posicion=1, contiene="", ventana=""):
    try:
        posicion = int(posicion or 1)
    except (TypeError, ValueError):
        posicion = 1
    medios = bool(clases_pedidas(tipo) & MEDIOS)
    h = _ventana(ventana, medios=medios)
    if h is None:
        return Fallo(f"No encontré la ventana '{ventana}'." if ventana else "No hay ninguna ventana al frente.")
    return reproducir_en(h, tipo, posicion, contiene, espera=6.0)


@skill("listar_en_pantalla",
       "Dice en orden lo que hay para abrir en la ventana (videos, playlists, resultados, "
       "botones...), numerado: '¿qué videos hay?', '¿qué playlists me salen?', 'léeme los "
       "resultados'. Después se puede pedir 'pon el tercero' con elegir_en_pantalla.",
       {"tipo": {"type": "string", "description": "Qué listar (como en elegir_en_pantalla); por defecto cualquiera"},
        "ventana": {"type": "string", "description": "Opcional: app o ventana"}},
       requeridos=[], terminal=False, externo=True)
def listar_en_pantalla(tipo="cualquiera", ventana="", maximo=15, para_voz=False):
    clases = clases_pedidas(tipo)
    h = _ventana(ventana, medios=bool(clases & MEDIOS))
    if h is None:
        return Fallo("No hay ninguna ventana al frente.")
    fin = time.time() + 4  # la página puede estar terminando de cargar
    while True:
        cosas = [c for c in leer(h, _tipos_uia(clases)) if c.clase in clases]
        if cosas or time.time() > fin:
            break
        time.sleep(0.6)
    visibles = [c for c in cosas if c.visible] or cosas
    if not visibles:
        return f"No veo nada de tipo {tipo} en {titulo_corto(h)}."
    if para_voz:  # respuesta directa (atajo): corta y sin etiquetas raras al oído
        lineas = [f"{i}, {limpio(c.nombre)}" for i, c in enumerate(visibles[:maximo], 1) if limpio(c.nombre)]
        return "Veo: " + "; ".join(lineas) + ". ¿Cuál pongo?"
    lineas = [f"{i}. [{c.clase}] {limpio(c.nombre) or c.url}" for i, c in enumerate(visibles[:maximo], 1)]
    mas = f" (y {len(cosas) - len(visibles)} más abajo)" if len(cosas) > len(visibles) else ""
    return f"En {titulo_corto(h)} veo{mas}:\n" + "\n".join(lineas)


ACCIONES = ["pausar", "reanudar", "alternar", "siguiente", "anterior", "adelantar", "retroceder",
            "reiniciar", "pantalla_completa", "salir_pantalla_completa", "silenciar",
            "activar_sonido", "subtitulos", "modo_cine", "mas_rapido", "mas_lento",
            "velocidad_normal", "saltar_anuncio"]


def _algo_suena():
    """True/False si se sabe si la PC está sonando; None si no hay medición."""
    try:
        import escuchar
        if not escuchar.SALIDA:
            return None
        return escuchar.pc_sonando(time.time() - 5, time.time()) > 0.2
    except Exception:
        return None


# ---------- Controles multimedia de Windows ----------
# El mismo panel que sale al subir el volumen: Edge, Chrome y Opera registran ahí el video de
# YouTube (con su título) y Spotify su canción. Pausar, siguiente, anterior y mover la posición
# funcionan sin el foco ni el ratón, aunque YouTube haya escondido sus controles (cuando se
# esconden, sus botones desaparecen también del árbol de accesibilidad).
SONANDO_SMTC, PAUSADO_SMTC = 4, 5
APPS = {"msedge": "Edge", "chrome": "Chrome", "opera": "Opera", "brave": "Brave",
        "firefox": "Firefox", "vivaldi": "Vivaldi", "spotify": "Spotify"}


def _en_hilo(corrutina, limite=6.0):
    """Corre una corrutina de WinRT en su propio hilo y bucle (Jarvis no es asíncrono)."""
    import asyncio
    res = {}

    def hilo():
        try:
            res["v"] = asyncio.run(corrutina())
        except Exception as e:
            res["e"] = e
    t = threading.Thread(target=hilo, daemon=True, name="smtc")
    t.start()
    t.join(limite)
    if "e" in res:
        raise res["e"]
    return res.get("v")


def _nombre_app(app_id):
    a = (app_id or "").lower()
    return next((v for k, v in APPS.items() if k in a), app_id or "una app")


async def _sesiones():
    from winrt.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as Gestor
    gestor = await Gestor.request_async()
    actual = gestor.get_current_session()
    actual = actual.source_app_user_model_id if actual else ""
    res = []
    for s in gestor.get_sessions():
        try:
            p = await s.try_get_media_properties_async()
            titulo, artista = p.title or "", p.artist or ""
        except Exception:
            titulo = artista = ""
        info = s.get_playback_info()
        res.append({"s": s, "app": s.source_app_user_model_id, "titulo": titulo,
                    "artista": artista, "estado": int(info.playback_status),
                    "controles": info.controls, "actual": s.source_app_user_model_id == actual})
    return res


def _elegir_sesion(sesiones, accion):
    sonando = [x for x in sesiones if x["estado"] == SONANDO_SMTC]
    pausadas = [x for x in sesiones if x["estado"] == PAUSADO_SMTC]
    if accion == "reanudar":
        grupo = pausadas or sesiones
    elif accion in ("pausar", "siguiente", "anterior", "adelantar", "retroceder", "reiniciar"):
        grupo = sonando or pausadas or sesiones
    else:
        grupo = sesiones
    grupo = sorted(grupo, key=lambda x: not x["actual"])  # la que Windows considera actual
    return grupo[0] if grupo else None


def _posicion(s):
    """Segundos reproducidos ahora (la línea de tiempo se actualiza a saltos)."""
    import datetime
    tl = s.get_timeline_properties()
    pos = tl.position.total_seconds()
    if int(s.get_playback_info().playback_status) == SONANDO_SMTC:
        try:
            pos += (datetime.datetime.now(datetime.timezone.utc) - tl.last_updated_time).total_seconds()
        except Exception:
            pass
    return max(0.0, min(pos, tl.end_time.total_seconds() or pos)), tl.end_time.total_seconds()


def medios(accion, segundos=0):
    """Hace la acción con los controles multimedia de Windows. Devuelve la frase, o None si no
    se puede por esta vía (no hay sesión, el paquete winrt no está, la app no lo permite)."""
    async def hacer():
        sesiones = await _sesiones()
        x = _elegir_sesion(sesiones, accion)
        if x is None:
            return None
        s, c = x["s"], x["controles"]
        if accion == "pausar":
            if x["estado"] != SONANDO_SMTC:
                return "No hay nada sonando."
            return Callado("Pausado.") if await s.try_pause_async() else None
        if accion == "reanudar":
            if x["estado"] == SONANDO_SMTC:
                return "Ya se está reproduciendo."
            return Callado("Reproduciendo.") if await s.try_play_async() else None
        if accion == "alternar":
            return Callado("Listo.") if await s.try_toggle_play_pause_async() else None
        if accion == "siguiente":
            if not c.is_next_enabled:
                return None
            return Callado("Listo.") if await s.try_skip_next_async() else None
        if accion == "anterior":
            if not c.is_previous_enabled:
                return None
            return Callado("Listo.") if await s.try_skip_previous_async() else None
        if accion in ("adelantar", "retroceder", "reiniciar"):
            if not c.is_playback_position_enabled:
                return None
            pos, fin = _posicion(s)
            if accion == "reiniciar":
                nueva = 0.0
            else:
                delta = (abs(int(segundos or 0)) or 10) * (1 if accion == "adelantar" else -1)
                nueva = max(0.0, pos + delta)
                if fin and nueva > fin - 1:
                    nueva = max(0.0, fin - 1)
            if not await s.try_change_playback_position_async(int(nueva * 10_000_000)):
                return None
            if accion == "reiniciar":
                return Callado("Desde el principio.")
            n = abs(int(segundos or 0)) or 10
            return Callado(f"{'Adelanté' if accion == 'adelantar' else 'Regresé'} {n} segundos.")
        return None
    try:
        return _en_hilo(hacer)
    except Exception as e:
        print(f"[Controles multimedia de Windows: {type(e).__name__}: {e}]")
        return None


def lo_que_suena():
    """[(app, título, artista, estado)] de lo que hay en los controles multimedia."""
    async def hacer():
        return [(_nombre_app(x["app"]), x["titulo"], x["artista"], x["estado"])
                for x in sorted(await _sesiones(), key=lambda x: (x["estado"] != SONANDO_SMTC, not x["actual"]))]
    try:
        return _en_hilo(hacer) or []
    except Exception:
        return []


@skill("que_suena",
       "Dice qué canción o video se está reproduciendo ahora (YouTube, Spotify o cualquier "
       "reproductor): '¿qué canción es esta?', '¿cómo se llama lo que suena?', '¿qué estoy "
       "escuchando?'.",
       terminal=True)
def que_suena():
    lista = lo_que_suena()
    if not lista:
        return "Ahorita no veo nada reproduciéndose."
    app, titulo, artista, estado = lista[0]
    if not titulo:
        return f"Algo suena en {app}, pero no dice qué es."
    de = f" de {artista}" if artista and artista.lower() not in titulo.lower() else ""
    if estado == SONANDO_SMTC:
        return f"Está sonando {titulo}{de}, en {app}."
    return f"Lo último fue {titulo}{de}, en {app}; está en pausa."


def _teclas_multimedia(accion):
    """Respaldo sin los controles de Windows: las teclas multimedia del teclado."""
    suena = _algo_suena()
    if accion == "pausar":
        if suena is False:
            return "No hay nada sonando."
        pyautogui.press("playpause")
        return Callado("Pausado.")
    if accion == "reanudar":
        if suena:
            return "Ya se está reproduciendo."
        pyautogui.press("playpause")
        return Callado("Reproduciendo.")
    teclas = {"alternar": "playpause", "siguiente": "nexttrack", "anterior": "prevtrack",
              "silenciar": "volumemute", "activar_sonido": "volumemute"}
    if accion in teclas:
        pyautogui.press(teclas[accion])
        return Callado("Listo.")
    return Fallo("Eso solo lo sé hacer en un video de YouTube y no veo ninguno abierto.")


def _ventana_del_video():
    """La ventana de YouTube a controlar: la del frente; si no, la del video que suena."""
    yt = ventanas_youtube()
    if not yt:
        return None
    import win32gui
    if yt[0] == win32gui.GetForegroundWindow():
        return yt[0]
    sonando = [t for _a, t, _r, e in lo_que_suena() if e == SONANDO_SMTC and t]
    for h in yt:
        if any(_norm(t)[:25] in _norm(control._titulo(h)) for t in sonando):
            return h
    return yt[0]


def _pantalla_completa(hwnd):
    """True si el video está en pantalla completa (la página ocupa todo el monitor)."""
    try:
        _u, vista = _pagina(control._raiz_uia(hwnd))
        ancho, alto = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        return vista is not None and vista[1] <= 1 and vista[3] - vista[1] >= alto - 2 and vista[2] - vista[0] >= ancho - 2
    except Exception:
        return False


def _teclas_youtube(hwnd, *teclas):
    """Atajos de YouTube con la ventana al frente y el foco fuera del buscador (si no, las
    letras se escribirían en él)."""
    control.traer_al_frente(hwnd)
    try:
        from pywinauto.uia_defines import IUIA
        iuia = IUIA()
        foco = iuia.iuia.GetFocusedElement()
        if foco.CurrentControlType in (iuia.UIA_dll.UIA_EditControlTypeId,
                                       iuia.UIA_dll.UIA_ComboBoxControlTypeId):
            docs = control._raiz_uia(hwnd).descendants(control_type="Document")
            if docs:
                docs[0].set_focus()
    except Exception:
        pass
    time.sleep(0.1)
    for t in teclas:
        if len(t) == 1 and not t.isalnum():
            pyautogui.write(t)       # '<' y '>' según la distribución del teclado
        else:
            pyautogui.press(t)
        time.sleep(0.05)


def saltar_anuncio_ahora(hwnd, espera=12.0):
    """Pulsa "Omitir" (esperando a que se pueda, si el anuncio aún no lo permite)."""
    fin = time.time() + espera
    vio_anuncio = False
    while True:
        botones = _botones(hwnd)
        b, n = boton_omitir(hwnd, botones)
        if b is not None and _pulsar(b):
            _vigia["omitidos"] += 1
            return "Listo, me salté el anuncio."
        try:
            nombres = " ".join((x.element_info.name or "") for x in botones[:200]).lower()
        except Exception:
            nombres = ""
        vio_anuncio = vio_anuncio or "anuncio" in nombres or " ad " in f" {nombres} "
        if time.time() > fin:
            if vio_anuncio:
                return Fallo("Ese anuncio todavía no se puede omitir o no tiene botón para omitirlo.")
            return "No veo ningún anuncio ahora."
        time.sleep(0.8)


@skill("controlar_reproduccion",
       "Controla lo que se está reproduciendo: pausar, reanudar, siguiente, anterior, adelantar "
       "o retroceder N segundos, reiniciar, pantalla completa, silenciar, subtítulos, modo cine, "
       "velocidad y saltar el anuncio. Funciona con YouTube en el navegador, Spotify y casi "
       "cualquier reproductor.",
       {"accion": {"type": "string", "enum": ACCIONES, "description": "Qué hacer"},
        "segundos": {"type": "integer", "description": "Para adelantar/retroceder (10 por defecto)"}},
       requeridos=["accion"])
def controlar_reproduccion(accion, segundos=0):
    accion = (accion or "").strip().lower()
    if accion not in ACCIONES:
        return Fallo(f"No sé hacer '{accion}' con el video.")
    if accion == "saltar_anuncio":
        h = _ventana_del_video()
        return saltar_anuncio_ahora(h) if h else "No veo YouTube abierto."
    if accion in ("pausar", "reanudar", "alternar", "siguiente", "anterior", "adelantar",
                  "retroceder", "reiniciar"):
        r = medios(accion, segundos)
        if r is not None:
            return r
    h = _ventana_del_video()
    if h is None:
        return _teclas_multimedia(accion)
    botones = _botones(h)
    play, nombre_play = boton(h, "play", botones)
    if accion in ("pausar", "reanudar", "alternar"):
        if play is None:
            _teclas_youtube(h, "k")
            return Callado("Listo.")
        if accion == "pausar" and EN_PAUSA.match(nombre_play):
            return "Ya estaba en pausa."
        if accion == "reanudar" and SONANDO.match(nombre_play):
            return "Ya se está reproduciendo."
        _pulsar(play)
        return Callado("Pausado." if SONANDO.match(nombre_play) else "Reproduciendo.")
    if accion in ("siguiente", "anterior"):
        b, _n = boton(h, accion, botones)
        if b is not None:
            _pulsar(b)
        else:
            _teclas_youtube(h, "shift+n" if accion == "siguiente" else "shift+p")
        return Callado("Listo.")
    if accion in ("pantalla_completa", "salir_pantalla_completa"):
        completa = _pantalla_completa(h)
        if completa == (accion == "pantalla_completa"):
            return "Ya está así."
        _teclas_youtube(h, "f")
        return Callado("Listo.")
    if accion in ("silenciar", "activar_sonido"):
        b, n = boton(h, "mute", botones)
        if b is not None:
            silenciado = bool(re.match(r"^\s*(activar|unmute)", n, re.I))
            if silenciado == (accion == "silenciar"):
                return "Ya está así."
            _pulsar(b)
        else:
            _teclas_youtube(h, "m")
        return Callado("Listo.")
    if accion in ("subtitulos", "modo_cine"):
        b, n = boton(h, "subtitulos" if accion == "subtitulos" else "cine", botones)
        if b is not None and ("no disponible" in n.lower() or "unavailable" in n.lower()):
            return Fallo("Este video no tiene subtítulos.")
        if b is not None:
            _pulsar(b)
        else:
            _teclas_youtube(h, "c" if accion == "subtitulos" else "t")
        return Callado("Listo.")
    s = abs(int(segundos or 0)) or 10
    if accion in ("adelantar", "retroceder"):
        diez, resto = divmod(s, 10)
        tecla = "l" if accion == "adelantar" else "j"
        _teclas_youtube(h, *([tecla] * min(60, diez) + (["right" if accion == "adelantar" else "left"]
                                                         if resto >= 5 else [])))
        return Callado(f"{'Adelanté' if accion == 'adelantar' else 'Regresé'} {s} segundos.")
    if accion == "reiniciar":
        _teclas_youtube(h, "0")
        return Callado("Desde el principio.")
    if accion == "mas_rapido":
        _teclas_youtube(h, ">")
        return Callado("Más rápido.")
    if accion == "mas_lento":
        _teclas_youtube(h, "<")
        return Callado("Más lento.")
    if accion == "velocidad_normal":
        _teclas_youtube(h, *(["<"] * 8 + [">"] * 3))  # a la mínima (0.25x) y tres pasos: 1x
        return Callado("Velocidad normal.")
    return Fallo(f"No sé hacer '{accion}' con el video.")


@skill("escribir_en",
       "Escribe en un campo de la ventana sin tener que darle clic antes (por defecto el "
       "buscador) y opcionalmente lo envía: 'busca lofi en esta página', 'escribe hola en el "
       "chat', 'pon mi usuario en el campo Correo'.",
       {"texto": {"type": "string", "description": "Lo que hay que escribir"},
        "campo": {"type": "string", "description": "Opcional: nombre del campo; por defecto el buscador"},
        "enviar": {"type": "boolean", "description": "true para pulsar Enter al final (buscar/enviar)"},
        "ventana": {"type": "string", "description": "Opcional: app o ventana"}},
       requeridos=["texto"], sensible=True)
def escribir_en(texto, campo="", enviar=None, ventana=""):
    h = _ventana(ventana)
    if h is None:
        return Fallo("No hay ninguna ventana al frente.")
    raiz = control._raiz_uia(h)
    _url, vista = _pagina(raiz)
    campos = []
    for tipo in ("Edit", "ComboBox"):
        try:
            for n in raiz.descendants(control_type=tipo):
                try:
                    nombre, r = (n.element_info.name or "").strip(), _rect(n)
                except Exception:
                    continue
                if r[2] - r[0] < 4:
                    continue
                if vista is not None and not _se_ve(r, vista):
                    continue  # la barra de direcciones del navegador no es un campo de la página
                campos.append((n, nombre, r))
        except Exception:
            continue
    if not campos:
        return Fallo(f"No veo dónde escribir en {titulo_corto(h)}.")
    if campo:
        mejor = max(campos, key=lambda c: control._parecido(campo, c[1]))
        if control._parecido(campo, mejor[1]) < 0.5:
            nombres = ", ".join(c[1] for c in campos if c[1])[:200]
            return Fallo(f"No encontré el campo '{campo}'. Veo: {nombres or 'campos sin nombre'}.")
    else:
        busca = [c for c in campos if re.search(r"busca|search|pesquis|find|encontrar", _norm(c[1]))]
        mejor = (busca or sorted(campos, key=lambda c: (c[2][1], c[2][0])))[0]
    nodo = mejor[0]
    control.traer_al_frente(h)
    try:
        nodo.set_focus()
    except Exception:
        try:
            nodo.click_input()
        except Exception:
            return Fallo("No pude poner el cursor en ese campo.")
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "a")
    enviar = (not campo) if enviar is None else bool(enviar)
    control.escribir_texto(texto, enter=enviar)
    return Callado(f"Escribí '{texto}'" + (" y lo envié." if enviar else "."))


# ---------- Vigía de anuncios ----------
_vigia = {"hilo": None, "activo": None, "omitidos": 0}


def _leer_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return {}


def saltar_anuncios_activo():
    if _vigia["activo"] is None:
        _vigia["activo"] = bool(_leer_estado().get("saltar_anuncios", True))
    return _vigia["activo"]


class _Omitir:
    """Busca el botón "Omitir" con UNA consulta por nombre (FindFirst): ~10 veces más barato
    que leer todos los botones, así se puede revisar cada segundo sin gastar."""

    def __init__(self):
        from pywinauto.uia_defines import IUIA
        self.iuia = IUIA()
        u, dll = self.iuia.iuia, self.iuia.UIA_dll
        cond = None
        for nombre in NOMBRES_OMITIR:
            c = u.CreatePropertyConditionEx(dll.UIA_NamePropertyId, nombre, 1)  # 1 = sin mayúsculas
            cond = c if cond is None else u.CreateOrCondition(cond, c)
        tipo = u.CreatePropertyCondition(dll.UIA_ControlTypePropertyId, dll.UIA_ButtonControlTypeId)
        self.cond = u.CreateAndCondition(tipo, cond)

    def buscar(self, hwnd):
        try:
            raiz = self.iuia.iuia.ElementFromHandle(hwnd)
            el = raiz.FindFirst(self.iuia.tree_scope["descendants"], self.cond)
        except Exception:
            return None
        if not el:
            return None
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
        return UIAWrapper(UIAElementInfo(el))


def _bucle_vigia():
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COM en modo MTA para este hilo
    except Exception:
        pass
    omitir = None
    vuelta = 0
    while True:
        time.sleep(1.2)
        if not saltar_anuncios_activo():
            continue
        try:
            yt = ventanas_youtube()[:2]
            if not yt:
                time.sleep(1.5)
                continue
            omitir = omitir or _Omitir()
            vuelta += 1
            for h in yt:
                b = omitir.buscar(h)
                if b is None and vuelta % 5 == 0:
                    b, _n = boton_omitir(h)  # de vez en cuando, por si el botón se llama distinto
                if b is not None and _pulsar(b):
                    _vigia["omitidos"] += 1
                    print("[Anuncio de YouTube omitido]")
        except Exception as e:
            print(f"[Vigía de anuncios: {type(e).__name__}: {e}]")
            time.sleep(5)


def iniciar():
    """Arranca el vigía de anuncios (una sola vez)."""
    if _vigia["hilo"] is None:
        _vigia["hilo"] = threading.Thread(target=_bucle_vigia, daemon=True, name="vigia-anuncios")
        _vigia["hilo"].start()


@skill("saltar_anuncios",
       "Activa o desactiva que Jarvis omita solo los anuncios de YouTube en cuanto se puedan "
       "omitir ('sáltate los anuncios automáticamente', 'ya no quites los anuncios'). Para "
       "saltar el anuncio de ahora mismo usa controlar_reproduccion con saltar_anuncio.",
       {"activar": {"type": "boolean", "description": "true para activarlo, false para apagarlo"}},
       requeridos=[])
def saltar_anuncios(activar=True):
    if isinstance(activar, str):
        activar = _norm(activar) in ("true", "1", "si", "activar")
    _vigia["activo"] = bool(activar)
    datos = _leer_estado()
    datos["saltar_anuncios"] = bool(activar)
    try:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps(datos), encoding="utf-8")
    except OSError:
        pass
    iniciar()
    if activar:
        return "Listo: en cuanto un anuncio de YouTube se pueda omitir, lo omito."
    return "De acuerdo, ya no omito los anuncios."
