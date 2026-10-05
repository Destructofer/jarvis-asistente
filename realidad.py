"""Modo realidad aumentada: el escritorio se vuelve la cámara de la laptop y encima flotan tus
ventanas abiertas EN VIVO, más YouTube, Spotify y Steam. Con las manos las mueves, las abres y
también las USAS: clic, scroll y arrastrar dentro de ellas, como en unas gafas de realidad mixta.

    "Jarvis, activa el modo realidad aumentada"   -> pantalla completa con la cámara
    "Jarvis, sal de la realidad aumentada"        -> vuelve al escritorio normal

Gestos (la cámara te ve como en un espejo; el anillo es tu cursor):
- Pellizcar = juntar pulgar e índice. Toque = pellizco corto y quieto.
- Toque en una ventana pequeña o en un icono del dock -> se abre GRANDE e interactiva.
- En la ventana interactiva:
    · pasar la mano por encima mueve el puntero real (aparecen los controles del video, menús...)
    · toque = clic
    · pellizcar y deslizar arriba/abajo = scroll (como en el celular)
    · pellizcar, quedarse quieto medio segundo y mover = arrastrar (barra del video, volumen)
    · barra de título: arrastrarla mueve la ventana; botones Reducir, Escritorio y X (cerrar)
- Dos manos pellizcando la barra de una ventana y separándolas = agrandar o achicar.
- Arrastrar una ventana a "Llevar al escritorio" (arriba) sale del modo y la deja al frente.
- Como un escritorio: cualquier ventana que se ve grande se usa directo (el clic la enfoca),
  dos toques = doble clic, pulgar con dedo medio = clic derecho, y lo que arrastras (archivos,
  carpetas, texto) se puede soltar en OTRA ventana. Botón Mosaico: hasta 4 ventanas usables a
  la vez; las reales se acomodan lado a lado y vuelven a como estaban al salir.
- Escribir: teclado virtual (aparece al tocar un campo de texto, o con el botón Teclado), el
  teclado físico o dictándole a Jarvis. Va a la ventana enfocada.
- Salir: botón Salir, decirlo, o mantener Esc un segundo.

Animación (todo con resortes, independiente de los fps):
- Agarrar = el panel se levanta (sombra). Arrastrar = se inclina en 3D hacia donde va, se
  balancea como colgado de la mano y su borde delantero brilla. Soltar en movimiento = inercia
  con rebote, sin salirse de la pantalla. Quieto, se dibuja directo (la deformación en
  perspectiva solo cuesta mientras se mueve).
- Iconos del dock elásticos y con rebote mientras abre su app; botones que se hunden; ondas en
  cada toque; anillo del cursor que se cierra al juntar los dedos; scroll con inercia.

Cómo está hecho:
- MediaPipe detecta 21 puntos por mano (~18 ms en CPU); OpenCV dibuja a 1280x720 (~10-15 ms).
  La interfaz se dibuja a 30 fps aunque la cámara entregue menos (poca luz).
- Las ventanas se ven con Windows Graphics Capture (la API de OBS/Teams): la GPU entrega la
  ventana aunque esté tapada, en cuanto cambia. La interactiva llega a ~60 fps; las demás a 4.
  Si una ventana no lo permite, se usa PrintWindow cada segundo.
- La capa de realidad aumentada es "transparente al clic" (WS_EX_TRANSPARENT): los clics y el
  scroll que generan tus manos (SendInput) caen en la ventana REAL, que se pone al frente justo
  debajo de la capa. El punto de la miniatura se traduce a la coordenada exacta de la ventana.
- Mientras está activo, este modo es el dueño de la webcam: camara.EXTERNO hace que
  mirar/escanear_entorno usen este mismo video.
"""
import atexit
import ctypes
import ctypes.wintypes
import json
import math
import os
import subprocess
import threading
import time
import traceback
import urllib.request
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

import apps
import camara
import control
import skills
from skills import skill

BASE = Path(__file__).parent
MODELO = BASE / "datos" / "hand_landmarker.task"
URL_MODELO = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
              "hand_landmarker/float16/latest/hand_landmarker.task")
TITULO = "Jarvis - Realidad aumentada"
W, H = 1280, 720
MARGEN = 0.09          # el borde de la cámara que no hace falta alcanzar con la mano
PINZA_ON, PINZA_OFF = 0.30, 0.42   # distancia pulgar-índice / tamaño de la mano (con histéresis)
TOQUE_SEG, TOQUE_PX = 0.45, 30     # un pellizco más corto y quieto que esto es un clic
ESPERA_ARRASTRE = 0.5              # quieto este tiempo con la pinza cerrada = empezar a arrastrar
MANTENER_SALIR = 0.8               # Salir hay que mantenerlo: un pellizco accidental cerraba el modo
SCROLL_POR_PX = 7                  # unidades de rueda por píxel de mano (120 = una "muesca")
MAX_VENTANAS = 8
PINZA_DER_ON, PINZA_DER_OFF = 0.28, 0.42  # pulgar con el dedo MEDIO = clic derecho
USABLE_PX = 360                    # una ventana cuya imagen mide al menos esto se usa directo
DOBLE_SEG, DOBLE_PX = 0.5, 28      # dos toques así de juntos = doble clic (mismo punto exacto)
MOSAICO_MAX = 4
EXCLUIR_CLASES = {"TkTopLevel"}    # el HUD de Jarvis

# Colores en BGR (OpenCV)
CIAN = (255, 214, 80)
CIAN_FUERTE = (255, 240, 120)
NARANJA = (60, 170, 255)
BLANCO = (245, 245, 245)
ROJO = (90, 90, 255)
MORADO = (230, 110, 200)
FUENTE = r"C:\Windows\Fonts\segoeui.ttf"
FUENTE_NEGRITA = r"C:\Windows\Fonts\seguisb.ttf"

CONEXIONES_MANO = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
                   (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
                   (17, 18), (18, 19), (19, 20)]

hablar = None  # lo pone genesis.py: hablar(texto) con la voz de Jarvis

_activo = {"escena": None, "hilo": None}
# Cómo estaban las ventanas reales que el mosaico achicó: en disco, para dejarlas igual aunque
# Jarvis se cierre de golpe a media sesión
COLOCACIONES = BASE / "datos" / "realidad_ventanas.json"
U32 = ctypes.windll.user32


# ---------- Entrada real (ratón) ----------
def _cursor(x, y):
    """Mueve el puntero real como un movimiento DE VERDAD del ratón (entrada absoluta) y luego
    lo deja en el píxel exacto. Solo con SetCursorPos el puntero "se teletransportaba": el
    Explorador no veía movimiento con el botón presionado y no empezaba a arrastrar archivos."""
    vx, vy, vw, vh = (U32.GetSystemMetrics(i) for i in (76, 77, 78, 79))
    nx = int((x - vx) * 65535 / max(1, vw - 1))
    ny = int((y - vy) * 65535 / max(1, vh - 1))
    U32.mouse_event(0x0001 | 0x8000 | 0x4000, nx, ny, 0, 0)  # MOVE | ABSOLUTE | VIRTUALDESK
    U32.SetCursorPos(int(x), int(y))


def _dar_foco(hwnd):
    """Pone la ventana real al frente para que reciba el teclado, SIN pulsar teclas (el truco
    de Alt de control.traer_al_frente activaba la barra de menú del Bloc de notas y se
    comía las letras siguientes)."""
    import win32api
    import win32con
    import win32gui
    import win32process
    try:
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        hilo_fg = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        yo = win32api.GetCurrentThreadId()
        unido = bool(hilo_fg and hilo_fg != yo and U32.AttachThreadInput(yo, hilo_fg, True))
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            U32.BringWindowToTop(hwnd)
            U32.SetForegroundWindow(hwnd)
        finally:
            if unido:
                U32.AttachThreadInput(yo, hilo_fg, False)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _boton_izq(abajo):
    U32.mouse_event(0x0002 if abajo else 0x0004, 0, 0, 0, 0)


def _rueda(unidades):
    U32.mouse_event(0x0800, 0, 0, int(unidades), 0)


def _boton_der():
    U32.mouse_event(0x0008, 0, 0, 0, 0)
    U32.mouse_event(0x0010, 0, 0, 0, 0)


class _MI(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _UI(ctypes.Union):
    _fields_ = [("mi", _MI), ("ki", _KI)]


class _IN(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _UI)]


def _enviar(eventos):
    """eventos: [(vk, scan, flags)] de teclado, en una sola llamada a SendInput."""
    arr = (_IN * len(eventos))()
    for i, (vk, scan, flags) in enumerate(eventos):
        arr[i].type = 1
        arr[i].u.ki = _KI(vk, scan, flags, 0, 0)
    U32.SendInput(len(eventos), arr, ctypes.sizeof(_IN))


def _escribir(texto):
    """Escribe cualquier texto (acentos, ñ, @...) en la ventana con el foco, como Unicode: no
    depende de la distribución del teclado."""
    eventos = []
    for unidad in texto.encode("utf-16-le").hex(" ", 2).split():
        u = int.from_bytes(bytes.fromhex(unidad), "little")
        eventos += [(0, u, 0x0004), (0, u, 0x0004 | 0x0002)]
    if eventos:
        _enviar(eventos)


VK = {"BORRAR": 0x08, "TAB": 0x09, "ENTER": 0x0D, "IZQ": 0x25, "DER": 0x27, "CTRL": 0x11,
      "C": 0x43, "V": 0x56, "Z": 0x5A}


def _tecla(*nombres):
    """Una tecla o combinación ('CTRL', 'C'): se presionan en orden y se sueltan al revés."""
    ext = {0x25, 0x27}  # flechas: teclas "extendidas"
    vks = [VK[n] for n in nombres]
    eventos = [(v, 0, 0x0001 if v in ext else 0) for v in vks]
    eventos += [(v, 0, (0x0001 if v in ext else 0) | 0x0002) for v in reversed(vks)]
    _enviar(eventos)


def _guardar_colocaciones(colocaciones):
    try:
        COLOCACIONES.parent.mkdir(parents=True, exist_ok=True)
        COLOCACIONES.write_text(json.dumps({str(h): v for h, v in colocaciones.items()}), encoding="utf-8")
    except OSError:
        pass


def restaurar_pendientes(colocaciones=None):
    """Regresa las ventanas reales a como estaban antes del mosaico (las de esta sesión o las
    que quedaron guardadas si la anterior terminó de golpe) y borra el registro."""
    import win32gui
    if colocaciones is None:
        try:
            colocaciones = {int(h): v for h, v in json.loads(COLOCACIONES.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            colocaciones = {}
    for h, (colocacion, proceso) in list(colocaciones.items()):
        try:
            # que sea la MISMA ventana (Windows reutiliza los números de ventana)
            if win32gui.IsWindow(h) and control._proceso(h) == proceso:
                f, mostrar, pmin, pmax, rect = colocacion
                win32gui.SetWindowPlacement(h, (f, mostrar, tuple(pmin), tuple(pmax), tuple(rect)))
        except Exception:
            pass
    try:
        COLOCACIONES.unlink()
    except OSError:
        pass


atexit.register(restaurar_pendientes)


def _subir(hwnd):
    """La ventana real queda arriba de las demás (debajo de la capa, que siempre va encima)
    SIN activarla: así un clic o algo que arrastras cae en ella."""
    import win32con
    import win32gui
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
    except Exception:
        pass


def _rect_visible(hwnd):
    """Rectángulo de la ventana tal como se ve (sin los bordes invisibles de Windows 10/11),
    que es justo lo que entrega Windows Graphics Capture."""
    r = ctypes.wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.wintypes.HWND(hwnd), 9, ctypes.byref(r),
                                                  ctypes.sizeof(r)) == 0:
        return r.left, r.top, r.right, r.bottom
    import win32gui
    return win32gui.GetWindowRect(hwnd)


# ---------- Dibujo ----------
@lru_cache(maxsize=1024)
def _texto(texto, tam=18, color=(255, 255, 255), negrita=False, max_ancho=0):
    """Sprite BGRA con el texto (PIL, para que salgan acentos y eñes). Se cachea."""
    f = ImageFont.truetype(FUENTE_NEGRITA if negrita else FUENTE, tam)
    if max_ancho and f.getlength(texto) > max_ancho:
        while texto and f.getlength(texto + "…") > max_ancho:
            texto = texto[:-1]
        texto += "…"
    l, t, r, b = f.getbbox(texto or " ")
    img = Image.new("RGBA", (max(1, r - l + 6), max(1, b - t + 6)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((3 - l, 4 - t), texto, font=f, fill=(0, 0, 0, 150))       # sombra
    d.text((2 - l, 3 - t), texto, font=f, fill=tuple(color) + (255,))
    return np.ascontiguousarray(np.asarray(img)[..., [2, 1, 0, 3]])


def _pegar(canvas, rgba, x, y, alfa=1.0):
    """Pega un sprite BGRA en (x, y) con transparencia, recortando si se sale."""
    h, w = rgba.shape[:2]
    x, y = int(x), int(y)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    s = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = (s[..., 3:4].astype(np.float32) / 255.0) * alfa
    roi = canvas[y0:y1, x0:x1]
    roi[:] = (roi * (1 - a) + s[..., :3] * a).astype(np.uint8)


def _pegar_bgr(canvas, img, x, y):
    h, w = img.shape[:2]
    x, y = int(x), int(y)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = img[y0 - y:y1 - y, x0 - x:x1 - x]


def _pegar_en(destino, src, x, y):
    """Copia src dentro de destino en (x, y), recortando lo que se salga."""
    h, w = src.shape[:2]
    alto, ancho = destino.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(ancho, x + w), min(alto, y + h)
    if x1 > x0 and y1 > y0:
        destino[y0:y1, x0:x1] = src[y0 - y:y1 - y, x0 - x:x1 - x]


@lru_cache(maxsize=96)  # una por tamaño: el panel grande ocupa ~0.6 MB (con 512 llegaba a 290 MB)
def _mascara(w, h, r):
    """Máscara (h, w) de 8 bits de un rectángulo redondeado (255 = dentro), para cv2.copyTo:
    con una máscara booleana de numpy, np.copyto tardaba 9 ms en el panel grande; así, 0.1."""
    m = np.zeros((h, w), np.uint8)
    r = max(1, min(r, w // 2, h // 2))
    cv2.rectangle(m, (r, 0), (w - r - 1, h - 1), 255, -1)
    cv2.rectangle(m, (0, r), (w - 1, h - r - 1), 255, -1)
    for cx, cy in ((r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)):
        cv2.circle(m, (cx, cy), r, 255, -1)
    return m


def _vidrio(canvas, x, y, w, h, radio=18, tinte=(60, 40, 25), fuerza=0.45):
    """Panel de 'vidrio esmerilado' estilo visionOS: desenfoca lo de atrás y lo tiñe.
    Todo en enteros con OpenCV (con numpy en flotantes costaba ~4 veces más por cuadro)."""
    x, y, w, h = int(x), int(y), int(w), int(h)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 - x0 < 2 or y1 - y0 < 2 or w < 2 or h < 2:
        return
    roi = canvas[y0:y1, x0:x1]
    m = _mascara(w, h, radio)[y0 - y:y1 - y, x0 - x:x1 - x]
    rw, rh = x1 - x0, y1 - y0
    chico = cv2.resize(roi, (max(1, rw // 8), max(1, rh // 8)), interpolation=cv2.INTER_AREA)
    chico = cv2.add(cv2.convertScaleAbs(cv2.GaussianBlur(chico, (0, 0), 2), alpha=1 - fuerza),
                    tuple(float(t) * fuerza for t in tinte) + (0.0,))
    vidrio = cv2.resize(chico, (rw, rh), interpolation=cv2.INTER_LINEAR)
    cv2.copyTo(vidrio, m, roi)


@lru_cache(maxsize=24)
def _sombra_sprite(w, h, r):
    """Sombra difusa (3 canales, 0-255) de un panel de w x h, con 24 px de margen."""
    b = 24
    m = np.zeros((h + 2 * b, w + 2 * b), np.uint8)
    m[b:b + h, b:b + w] = _mascara(w, h, r)
    m = cv2.GaussianBlur(m, (0, 0), 10)
    return cv2.merge([m, m, m])


def _sombra(canvas, x, y, w, h, r, fuerza):
    """Oscurece el fondo bajo un panel "levantado". El tamaño va en escalones de 16 px para
    reutilizar la sombra ya difuminada mientras el panel crece o se encoge."""
    if fuerza <= 0.01 or w < 8 or h < 8:
        return
    qw, qh = max(16, int(w) // 16 * 16), max(16, int(h) // 16 * 16)
    sp = _sombra_sprite(qw, qh, min(r, qw // 2, qh // 2))
    x, y = int(x + (w - qw) / 2) - 24, int(y + (h - qh) / 2) - 24
    sh, sw = sp.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + sw), min(H, y + sh)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    roi = canvas[y0:y1, x0:x1]
    oscuro = cv2.multiply(roi, sp[y0 - y:y1 - y, x0 - x:x1 - x], scale=min(1.0, fuerza) / 255)
    cv2.subtract(roi, oscuro, dst=roi)


def _resorte(x, v, objetivo, dt, rigidez, amortiguacion):
    """Un paso de resorte amortiguado (sube y se pasa un poquito, como en iOS/visionOS).
    En pasos de 1/120 s para que no se vuelva inestable si un cuadro tarda."""
    pasos = max(1, int(math.ceil(dt * 120)))
    h = dt / pasos
    for _ in range(pasos):
        v += (rigidez * (objetivo - x) - amortiguacion * v) * h
        x += v * h
    return x, v


def _suave(dt, rapidez):
    """Fracción para acercarse a un objetivo independiente de los fps (1 - e^-rapidez·dt)."""
    return 1.0 - math.exp(-rapidez * dt)


def _borde(canvas, x, y, w, h, r, color, grosor=1):
    x, y, w, h = int(x), int(y), int(w), int(h)
    r = max(1, min(r, w // 2, h // 2))
    aa = cv2.LINE_AA
    cv2.line(canvas, (x + r, y), (x + w - r, y), color, grosor, aa)
    cv2.line(canvas, (x + r, y + h), (x + w - r, y + h), color, grosor, aa)
    cv2.line(canvas, (x, y + r), (x, y + h - r), color, grosor, aa)
    cv2.line(canvas, (x + w, y + r), (x + w, y + h - r), color, grosor, aa)
    cv2.ellipse(canvas, (x + r, y + r), (r, r), 180, 0, 90, color, grosor, aa)
    cv2.ellipse(canvas, (x + w - r, y + r), (r, r), 270, 0, 90, color, grosor, aa)
    cv2.ellipse(canvas, (x + r, y + h - r), (r, r), 90, 0, 90, color, grosor, aa)
    cv2.ellipse(canvas, (x + w - r, y + h - r), (r, r), 0, 0, 90, color, grosor, aa)


# ---------- Teclado virtual ----------
FILAS_TECLADO = [
    [(c, c, 1.0) for c in "1234567890"] + [("Borrar", "BORRAR", 1.6)],
    [(c, c, 1.0) for c in "qwertyuiop"] + [("´", "ACENTO", 1.1)],
    [(c, c, 1.0) for c in "asdfghjklñ"] + [("Enter", "ENTER", 1.9)],
    [("Mayús", "MAYUS", 1.5)] + [(c, c, 1.0) for c in "zxcvbnm,.-"] + [("?", "?", 1.0)],
    [("Tab", "TAB", 1.3), ("Copiar", "COPIAR", 1.7), ("Pegar", "PEGAR", 1.7),
     ("espacio", "ESPACIO", 4.0), ("@", "@", 1.0), ("←", "IZQ", 1.0), ("→", "DER", 1.0),
     ("Ocultar", "OCULTAR", 1.9)],
]
CABECERA_TECLADO = 38


@lru_cache(maxsize=4)
def _disposicion_teclado(w, h):
    """[((x, y, w, h) relativo al teclado, clave, etiqueta)] de todas las teclas."""
    margen, hueco = 12, 5
    unidad = (w - 2 * margen) / max(sum(a for _e, _c, a in f) for f in FILAS_TECLADO)
    alto = (h - CABECERA_TECLADO - margen) / len(FILAS_TECLADO)
    res = []
    for i, fila in enumerate(FILAS_TECLADO):
        ancho_fila = sum(a for _e, _c, a in fila) * unidad
        x = (w - ancho_fila) / 2
        y = CABECERA_TECLADO + i * alto
        for etiqueta, clave, a in fila:
            res.append(((x + hueco / 2, y + hueco / 2, a * unidad - hueco, alto - hueco), clave, etiqueta))
            x += a * unidad
    return tuple(res)


@lru_cache(maxsize=8)
def _sprite_teclado(w, h, mayus, acento):
    """Las teclas ya dibujadas (con PIL, para la ñ y los acentos), en formato premultiplicado
    para pegarlas con dos operaciones de OpenCV: (color * alfa, 255 - alfa)."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    chica = ImageFont.truetype(FUENTE_NEGRITA, 15)
    grande = ImageFont.truetype(FUENTE, 22)
    for (x, y, tw, th), clave, etiqueta in _disposicion_teclado(w, h):
        especial = len(clave) > 1
        relleno = (120, 200, 255, 50) if especial else (255, 255, 255, 40)
        if (clave == "MAYUS" and mayus) or (clave == "ACENTO" and acento):
            relleno = (90, 210, 255, 150)
        d.rounded_rectangle((x, y, x + tw, y + th), radius=9, fill=relleno, outline=(255, 255, 255, 95))
        if len(etiqueta) == 1 and etiqueta.isalpha():
            if acento and etiqueta in "aeiou":
                etiqueta = "áéíóú"["aeiou".index(etiqueta)]
            if mayus:
                etiqueta = etiqueta.upper()
        f = grande if len(etiqueta) == 1 else chica
        l, t, r, b = f.getbbox(etiqueta)
        d.text((x + (tw - (r - l)) / 2 - l, y + (th - (b - t)) / 2 - t), etiqueta, font=f,
               fill=(255, 255, 255, 255))
    a = np.asarray(img)
    bgr = a[..., [2, 1, 0]].astype(np.uint16)
    alfa = a[..., 3:4].astype(np.uint16)
    pre = (bgr * alfa // 255).astype(np.uint8)
    inv = np.repeat(255 - a[..., 3:4], 3, axis=2).astype(np.uint8)
    return pre, inv


def _pegar_premult(canvas, sprite, x, y):
    pre, inv = sprite
    h, w = pre.shape[:2]
    x, y = int(x), int(y)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    roi = canvas[y0:y1, x0:x1]
    cv2.multiply(roi, inv[y0 - y:y1 - y, x0 - x:x1 - x], dst=roi, scale=1 / 255)
    cv2.add(roi, pre[y0 - y:y1 - y, x0 - x:x1 - x], dst=roi)


def _ajustar(img, w, h):
    """Redimensiona conservando la proporción (sin deformar) para caber en w x h."""
    ih, iw = img.shape[:2]
    k = min(w / iw, h / ih)
    interp = cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR
    return cv2.resize(img, (max(1, int(iw * k)), max(1, int(ih * k))), interpolation=interp)


# ---------- Iconos ----------
def _lienzo_icono(s, dibujar):
    """Dibuja a 4x y reduce: bordes suaves sin depender de antialiasing de cada figura."""
    g = s * 4
    img = np.zeros((g, g, 4), np.uint8)
    dibujar(img, g)
    return cv2.resize(img, (s, s), interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=16)
def _icono_marca(nombre, s=60):
    def youtube(img, g):
        x0, y0, x1, y1 = int(g * 0.06), int(g * 0.2), int(g * 0.94), int(g * 0.8)
        r = int(g * 0.16)
        m = np.zeros((g, g), np.uint8)
        cv2.rectangle(m, (x0 + r, y0), (x1 - r, y1), 255, -1)
        cv2.rectangle(m, (x0, y0 + r), (x1, y1 - r), 255, -1)
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            cv2.circle(m, (cx, cy), r, 255, -1)
        img[m > 0] = (0, 0, 255, 255)
        tri = np.array([[g * 0.41, g * 0.36], [g * 0.41, g * 0.64], [g * 0.65, g * 0.5]], np.int32)
        cv2.fillPoly(img, [tri], (255, 255, 255, 255))

    def spotify(img, g):
        cv2.circle(img, (g // 2, g // 2), int(g * 0.47), (84, 185, 29, 255), -1)
        for k, (ancho, grosor) in enumerate(((0.62, 0.075), (0.52, 0.062), (0.42, 0.05))):
            cy = int(g * (0.36 + 0.15 * k))
            cv2.ellipse(img, (g // 2, cy + int(g * 0.22)), (int(g * ancho / 2), int(g * 0.22)),
                        0, 205, 335, (0, 0, 0, 255), int(g * grosor))

    def steam(img, g):
        cv2.circle(img, (g // 2, g // 2), int(g * 0.47), (56, 40, 27, 255), -1)
        blanco = (255, 255, 255, 255)
        cv2.circle(img, (int(g * 0.64), int(g * 0.38)), int(g * 0.15), blanco, int(g * 0.05))
        cv2.circle(img, (int(g * 0.64), int(g * 0.38)), int(g * 0.07), blanco, -1)
        cv2.line(img, (int(g * 0.53), int(g * 0.48)), (int(g * 0.36), int(g * 0.62)), blanco, int(g * 0.08))
        cv2.circle(img, (int(g * 0.34), int(g * 0.64)), int(g * 0.1), blanco, -1)
        cv2.circle(img, (int(g * 0.34), int(g * 0.64)), int(g * 0.05), (56, 40, 27, 255), -1)

    return _lienzo_icono(s, {"YouTube": youtube, "Spotify": spotify, "Steam": steam}[nombre])


def _hicon_a_bgra(hicon, s):
    """Dibuja el icono sobre negro y sobre blanco; la diferencia da la transparencia real."""
    import win32gui
    import win32ui
    pantalla = win32gui.GetDC(0)
    dc = win32ui.CreateDCFromHandle(pantalla)
    capas = []
    try:
        for fondo in (0x000000, 0xFFFFFF):
            mem = dc.CreateCompatibleDC()
            bmp = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, s, s)
            mem.SelectObject(bmp)
            brocha = win32gui.CreateSolidBrush(fondo)
            win32gui.FillRect(mem.GetSafeHdc(), (0, 0, s, s), brocha)
            win32gui.DeleteObject(brocha)
            win32gui.DrawIconEx(mem.GetSafeHdc(), 0, 0, hicon, s, s, 0, None, 3)
            capas.append(np.frombuffer(bmp.GetBitmapBits(True), np.uint8)
                         .reshape(s, s, 4)[..., :3].astype(np.float32))
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
    finally:
        win32gui.ReleaseDC(0, pantalla)
    negro, blanco = capas
    alfa = np.clip(255 - (blanco - negro).max(axis=2), 0, 255)
    color = np.clip(negro * 255 / np.maximum(alfa[..., None], 1), 0, 255)
    return np.dstack([color, alfa]).astype(np.uint8)


@lru_cache(maxsize=128)
def _icono(ruta, s=22, letra="?"):
    """Icono BGRA real del programa; si no se puede, un círculo con la inicial."""
    if ruta:
        try:
            import win32gui
            hicon, ident = ctypes.c_void_p(), ctypes.c_uint()
            n = U32.PrivateExtractIconsW(ruta, 0, s, s, ctypes.byref(hicon), ctypes.byref(ident), 1, 0)
            if n > 0 and hicon.value:
                try:
                    return _hicon_a_bgra(hicon.value, s)
                finally:
                    win32gui.DestroyIcon(hicon.value)
        except Exception:
            pass
    img = np.zeros((s, s, 4), np.uint8)
    tono = (sum(map(ord, letra or "?")) * 37) % 180  # mismo color siempre para la misma letra
    color = cv2.cvtColor(np.uint8([[[tono, 150, 230]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    cv2.circle(img, (s // 2, s // 2), s // 2 - 1, color + [255], -1, cv2.LINE_AA)
    return img


# ---------- Lanzar YouTube, Spotify y Steam ----------
def _navegador():
    for c in (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
              r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
              r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
              r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"):
        ruta = os.path.expandvars(c)
        if os.path.isfile(ruta):
            return ruta
    return None


def _abrir_youtube():
    nav = _navegador()
    if nav:  # ventana tipo app: sin pestañas ni barra de direcciones, ideal para flotar
        subprocess.Popen([nav, "--app=https://www.youtube.com", "--new-window"])
    else:
        os.startfile("https://www.youtube.com")


def _abrir_spotify():
    r = apps.buscar_app("spotify")
    os.startfile(os.path.expandvars(r[1]) if r and r[2] >= 0.6 else "spotify:")


def _abrir_steam():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            winreg.QueryValueEx(k, "SteamExe")
    except OSError:
        raise RuntimeError("Steam no está instalado en esta PC.")
    os.startfile("steam://open/main")


DOCK = [
    ("YouTube", _abrir_youtube, {"titulo": ("youtube",), "proceso": ()}),
    ("Spotify", _abrir_spotify, {"titulo": (), "proceso": ("spotify.exe",)}),
    ("Steam", _abrir_steam, {"titulo": ("steam",), "proceso": ("steamwebhelper.exe", "steam.exe")}),
]


def _coincide(hwnd, claves):
    titulo = control._titulo(hwnd).lower()
    if any(t in titulo for t in claves["titulo"]):
        return True
    proceso = control._proceso(hwnd).lower()
    return proceso in claves["proceso"] and bool(titulo.strip())


# ---------- Captura de ventanas ----------
def _printwindow(hwnd, max_ancho):
    """Plan B de captura (lento, ~70 ms en una ventana grande) para lo que WGC no acepta."""
    import win32gui
    import win32ui
    try:
        x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None
    w, h = x1 - x0, y1 - y0
    if w < 50 or h < 50 or win32gui.IsIconic(hwnd):
        return None
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    mem = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc, w, h)
        mem.SelectObject(bmp)
        U32.PrintWindow(hwnd, mem.GetSafeHdc(), 2)  # 2 = PW_RENDERFULLCONTENT
        img = np.frombuffer(bmp.GetBitmapBits(True), np.uint8).reshape(h, w, 4)[..., :3]
    except Exception:
        return None
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
    if img.max() < 12:
        return None
    if w > max_ancho:
        img = cv2.resize(img, (max_ancho, int(h * max_ancho / w)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


class Captura:
    """Video en vivo de una ventana con Windows Graphics Capture."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.cuadro = None      # BGR reducido
        self.t = 0.0
        self.rapida = False
        self.caja = (520, 300)  # tamaño en que se va a dibujar: el cuadro llega ya así
        self.wgc = False        # True si WGC está entregando cuadros
        self.fallo = False
        self._ctl = None
        self._sesion = 0
        self.cerrada = False    # ya no se vuelve a iniciar (la ventana o el modo terminaron)
        # Se reinicia desde otro hilo (para no trabar el dibujo): sin candado, dos reinicios a
        # la vez dejaban una captura huérfana entregando cuadros
        self._lock = threading.Lock()

    def iniciar(self, rapida=False):
        """rapida: True = la ventana que usas (30 fps), "media" = las del mosaico (10 fps),
        False = las demás (4 fps)."""
        with self._lock:
            if not self.cerrada:
                self._iniciar(rapida)

    def _iniciar(self, rapida):
        self._detener()
        self.rapida = rapida
        self._sesion += 1
        sesion = self._sesion
        try:
            from windows_capture import WindowsCapture
            cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd,
                                 minimum_update_interval=33 if rapida is True else (100 if rapida == "media" else 250))

            @cap.event
            def on_frame_arrived(frame, _control):
                if sesion != self._sesion:
                    return
                try:
                    buf = frame.frame_buffer
                    h, w = buf.shape[:2]
                    cw, ch = self.caja
                    k = min(cw / w, ch / h)  # una sola reducción, directo al tamaño del panel
                    buf = cv2.resize(buf, (max(1, int(w * k)), max(1, int(h * k))),
                                     interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
                    self.cuadro = cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)
                    self.t = time.time()
                    self.wgc = True
                except Exception:
                    pass

            @cap.event
            def on_closed():
                if sesion == self._sesion:
                    self._ctl = None

            self._ctl = cap.start_free_threaded()
            self.fallo = False
        except Exception:
            self._ctl = None
            self.fallo = True

    def cerrar(self):
        with self._lock:
            self.cerrada = True
            self._detener()

    def _detener(self):
        ctl, self._ctl = self._ctl, None
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:
                pass


# ---------- Escena ----------
class Elemento:
    def __init__(self, tipo, titulo, cx, cy, w, h, icono=None, hwnd=None, accion=None, claves=None):
        self.tipo, self.titulo = tipo, titulo
        self.cx = self.tcx = float(cx)
        self.cy = self.tcy = float(cy)
        self.casa = (float(cx), float(cy))   # lugar fijo de los iconos del dock
        self.w0, self.h0 = w, h
        self.escala = self.tescala = 1.0
        self.icono, self.hwnd, self.accion, self.claves = icono, hwnd, accion, claves
        self.captura = None
        self.mini_pw, self.t_pw = None, 0.0   # captura de respaldo (PrintWindow)
        self.interactiva = False
        self.hover = 0.0
        self.lupa = 0.14 if tipo == "app" else 0.05   # cuánto crece con la mano encima
        self.pulso = 0.0
        self.agarrado = 0
        self.encima = False
        self.abierta = False  # (iconos) su app tiene una ventana abierta: lo calcula el hilo de ventanas
        self.botones = {}     # nombre -> rect, en la barra de título
        self.zona = None      # rect (x, y, w, h) de la imagen de la ventana dentro del panel
        self.barra = None     # rect de la barra de título
        self.cache_mini = None
        self.cache_tenue = None   # la imagen un poco apagada (ventana que no estás usando)
        # --- física de la animación ---
        self.vcx = self.vcy = self.ves = 0.0   # velocidades de los resortes
        self.vx = self.vy = 0.0                # velocidad en pantalla (px/s), suavizada
        self.giro = self.vgiro = 0.0           # balanceo como péndulo (grados)
        self.yaw = self.pitch = 0.0            # inclinación 3D hacia donde va (radianes)
        self.alzado = 0.0                      # 0-1: levantado al agarrarlo (sombra, crece)
        self.presion = 0.0                     # 0-1: hundido mientras lo pellizcas
        self.presionado = False
        self.encoger, self.tencoger = 1.0, 1.0  # se "absorbe" sobre Llevar al escritorio
        self.pivote = None
        self.alfa, self.talfa = 1.0, 1.0
        self.saltando = False                  # icono de una app que se está abriendo
        self.cerrando = 0.0                    # momento en que se pidió cerrarla
        self.t0 = time.time()
        self.mantener = False                  # botón que hay que mantener (Salir)
        self.activo = False                    # botón encendido (Teclado, Mosaico)
        self.nivel = None                      # velocidad de captura actual (ver Captura.iniciar)
        self.mayus = self.acento = False       # teclado virtual
        self.eco = ""                          # lo último que escribiste con el teclado virtual
        if tipo == "teclado":
            self.lupa = 0.0

    def rect(self):
        k = self.escala * (1 + self.lupa * self.hover)
        w, h = self.w0 * k, self.h0 * k
        return int(self.cx - w / 2), int(self.cy - h / 2), int(w), int(h)

    def contiene(self, x, y):
        rx, ry, rw, rh = self.rect()
        return rx <= x <= rx + rw and ry <= y <= ry + rh

    def imagen(self):
        if self.captura is not None and self.captura.cuadro is not None:
            return self.captura.cuadro
        return self.mini_pw

    def animar(self, dt):
        px, py = self.cx, self.cy
        if self.agarrado:
            # Pegado a la mano (rápido), pero sin teletransportarse
            a = _suave(dt, 28)
            self.cx += (self.tcx - self.cx) * a
            self.cy += (self.tcy - self.cy) * a
            self.vcx, self.vcy = (self.cx - px) / dt, (self.cy - py) / dt
        else:
            # Suelto: resorte. Al acomodarse o al lanzarlo llega con un pequeño rebote
            self.cx, self.vcx = _resorte(self.cx, self.vcx, self.tcx, dt, 190, 24)
            self.cy, self.vcy = _resorte(self.cy, self.vcy, self.tcy, dt, 190, 24)
        self.escala, self.ves = _resorte(self.escala, self.ves, self.tescala, dt, 230, 21)
        self.escala = max(0.05, self.escala)
        a = _suave(dt, 16)
        self.vx += ((self.cx - px) / dt - self.vx) * a
        self.vy += ((self.cy - py) / dt - self.vy) * a
        # Hacia donde lo arrastras: el borde de adelante se va hacia atrás (como una hoja que
        # corta el aire) y cuelga de la mano como un péndulo; los paneles grandes, menos
        f = float(np.clip(420 / max(1.0, self.w0 * self.escala, self.h0 * self.escala), 0.3, 1.0))
        a = _suave(dt, 12)
        self.yaw += (float(np.clip(self.vx / 2600, -0.45, 0.45)) * f - self.yaw) * a
        self.pitch += (float(np.clip(self.vy / 2600, -0.45, 0.45)) * f - self.pitch) * a
        self.giro, self.vgiro = _resorte(self.giro, self.vgiro,
                                         float(np.clip(self.vx * 0.006, -9, 9)) * f, dt, 170, 11)
        meta = 1.0 if self.agarrado and self.tipo == "ventana" else (0.6 if self.agarrado else 0.0)
        a = _suave(dt, 12)
        self.alzado += (meta - self.alzado) * a
        self.encoger += (self.tencoger - self.encoger) * a
        self.alfa += (self.talfa - self.alfa) * _suave(dt, 10)
        self.presion += ((1.0 if self.presionado else 0.0) - self.presion) * _suave(dt, 25)
        self.pulso = max(0.0, self.pulso - dt * 1.8)

    def deformacion(self, x, y, w, h, ahora):
        """Esquinas (4x2) del panel ya inclinado / balanceado / levantado, o None si está
        quieto (entonces se dibuja directo, sin el costo de deformarlo)."""
        s = (1 + 0.035 * self.alzado) * (1 - 0.07 * self.presion) * self.encoger
        salto = -abs(math.sin((ahora - self.t0) * 7)) * 16 if self.saltando else 0.0
        vel = math.hypot(self.vx, self.vy)
        estira = min(0.06, vel / 15000) if self.agarrado or vel > 600 else 0.0
        if (abs(s - 1) < 0.004 and abs(self.giro) < 0.25 and abs(self.yaw) < 0.006
                and abs(self.pitch) < 0.006 and salto == 0 and estira < 0.004):
            return None
        cx0, cy0 = x + w / 2, y + h / 2
        px, py = self.pivote if (self.pivote and self.encoger < 0.995) else (cx0, cy0)
        foco = 1500.0
        cyw, syw = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        g = math.radians(self.giro)
        cr, sr = math.cos(g), math.sin(g)
        dx, dy = (self.vx / vel, self.vy / vel) if vel > 1 else (1.0, 0.0)
        res = []
        for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
            X, Z = u * cyw, u * syw
            Y, Z = v * cp, Z + v * sp
            k = foco / max(200.0, foco + Z)
            X, Y = X * k, Y * k
            X, Y = X * cr - Y * sr, X * sr + Y * cr
            if estira:  # se estira en la dirección del movimiento y se adelgaza a lo ancho
                pr, pn = X * dx + Y * dy, -X * dy + Y * dx
                pr, pn = pr * (1 + estira), pn * (1 - estira / 2)
                X, Y = pr * dx - pn * dy, pr * dy + pn * dx
            gx, gy = cx0 + X, cy0 + Y
            res.append((px + (gx - px) * s, py + (gy - py) * s + salto))
        return np.float32(res)


def _dentro(r, x, y):
    return r is not None and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]


class Mano:
    def __init__(self):
        self.x = self.y = None
        self.pinza = False
        self.t0, self.p0 = 0.0, (0, 0)
        self.elem, self.off = None, (0, 0)
        self.modo = None          # "mover" | "contenido"
        self.visto = 0.0
        self.esc0 = self.d0 = None
        self.puntos = None
        self.desplazando = False
        self.raton_abajo = False
        self.boton = None         # botón de la barra de título donde empezó el pellizco
        self.ult = (0, 0)
        self.acumulado = 0.0
        self.vx = self.vy = 0.0   # velocidad de la mano (px/s): lanzar ventanas, scroll con inercia
        self.razon = 1.0          # apertura pulgar-índice (para que el anillo "se cierre")
        self.pinza_der, self.t0d = False, 0.0   # pulgar con medio: clic derecho
        self.tecla = None         # tecla del teclado virtual que se está pulsando
        self.t_rep, self.repitio = 0.0, False   # autorrepetición (Borrar sostenido)
        self.destino = None       # al arrastrar algo: la ventana donde caería


class Escena:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conf = cfg.get("realidad", {}) or {}
        self.parar = threading.Event()
        self.listo = threading.Event()
        self.error = ""
        self.lock = threading.RLock()
        self.elementos = []
        self.manos = {}
        self.crudo = None
        self.canvas = None
        self.aviso, self.t_aviso = "", 0.0
        self.al_salir = None
        self.motivo = ""          # por qué terminó (queda en el registro para diagnosticar)
        self.fps = 0.0
        self.esperando = None     # (claves, hasta): abrir interactiva la ventana que aparezca
        self.t_cursor = 0.0
        self.t_esc = None
        self._resultado = None    # (resultado de MediaPipe, ya procesado?)
        self.ondas = []           # [x, y, t0, color, radio]: el "toque" se ve
        self.salientes = []       # ventanas cerradas que se están desvaneciendo
        self.inercia = None       # scroll que sigue solo tras deslizar rápido
        self.zona_a = 0.0         # aparición de "Llevar al escritorio"
        self.t_aviso0 = 0.0
        self.hilo_v = None
        self.disposicion = "normal"   # "normal" (una grande al centro) o "mosaico" (hasta 4)
        self.orden_mosaico = []       # hwnds en el mosaico, el más reciente primero
        self.teclado = None           # Elemento del teclado virtual (si se ha mostrado)
        self.pedir_teclado = False    # un hilo vio que tocaste un campo de texto
        self.z_arriba = None          # la ventana real que dejamos arriba (para no repetir)
        self.ultimo_clic = None       # (momento, ventana, punto del panel, punto real): doble clic
        self.colocaciones = {}        # hwnd -> cómo estaba la ventana real antes del mosaico

    # --- armado y orden ---
    def _armar(self):
        n = len(DOCK)
        for i, (nombre, fn, claves) in enumerate(DOCK):
            e = Elemento("app", nombre, W / 2 - (n - 1) * 55 + i * 110, H - 62, 92, 84,
                         _icono_marca(nombre), accion=fn, claves=claves)
            e.cy = H + 90 + i * 45  # entran subiendo desde abajo, uno tras otro
            self.elementos.append(e)
        salir = Elemento("accion", "Salir", W - 78, 38, 110, 42, accion=self.parar.set)
        salir.mantener = True
        teclado = Elemento("accion", "Teclado", W - 202, 38, 118, 42,
                           accion=lambda: self._mostrar_teclado(self.teclado is None or self.teclado.talfa < 0.5))
        mosaico = Elemento("accion", "Mosaico", W - 330, 38, 118, 42, accion=self._alternar_mosaico)
        for i, b in enumerate((salir, teclado, mosaico)):
            b.cy = -50 - i * 30   # bajan uno tras otro
            self.elementos.append(b)

    def _ventanas(self):
        return [e for e in self.elementos if e.tipo == "ventana"]

    def _teclado_visible(self):
        return self.teclado is not None and self.teclado.talfa > 0.5

    def _area(self):
        """(x0, y0, x1, y1) libre para las ventanas grandes: entre la barra de arriba y el dock
        (o el teclado, si está abierto)."""
        abajo = H - 300 if self._teclado_visible() else H - 112
        return 212, 74, W - 12, abajo

    def _en_mosaico(self, ventanas):
        """Las que van en el mosaico: las últimas que usaste, hasta 4 (la enfocada siempre)."""
        vivas = {e.hwnd: e for e in ventanas}
        self.orden_mosaico = [h for h in self.orden_mosaico if h in vivas]
        for e in sorted(ventanas, key=lambda e: e.t0):
            if e.hwnd not in self.orden_mosaico:
                self.orden_mosaico.append(e.hwnd)
        return [vivas[h] for h in self.orden_mosaico[:MOSAICO_MAX]]

    def _organizar(self):
        """Normal: sin ventana en uso, cuadrícula; con una, esa grande al centro y las demás en
        una repisa a la izquierda. Mosaico: hasta 4 grandes y usables a la vez (para pasar
        archivos de una a otra), las demás en la repisa."""
        celdas = []
        with self.lock:
            ventanas = self._ventanas()
            activa = next((e for e in ventanas if e.interactiva), None)
            x0, y0, x1, y1 = self._area()
            if self.disposicion == "mosaico" and ventanas:
                grandes = self._en_mosaico(ventanas)
                n = len(grandes)
                cols, filas = (1, 1) if n == 1 else (2, 1) if n == 2 else (2, 2)
                cw, ch = (x1 - x0) / cols, (y1 - y0) / filas
                for i, e in enumerate(sorted(grandes, key=lambda e: e.t0)):
                    col, fila = i % cols, i // cols
                    e.tcx, e.tcy = x0 + cw * (col + 0.5), y0 + ch * (fila + 0.5)
                    e.tescala = min((cw - 14) / e.w0, (ch - 14) / e.h0)
                otras = [e for e in ventanas if e not in grandes]
                for i, e in enumerate(otras):
                    e.tcx, e.tcy, e.tescala = 108, 128 + min(i, 4) * 100, 0.5
                celdas = [(e, e.tcx, e.tcy, cw - 14, ch - 14) for e in grandes]
                area = (x0, y0, x1, y1)
            elif activa is not None:
                activa.tcx, activa.tcy = (x0 + x1) / 2, (y0 + y1) / 2
                activa.tescala = min((x1 - x0) / activa.w0, (y1 - y0) / activa.h0)
                otras = [e for e in ventanas if e is not activa]
                for i, e in enumerate(otras):
                    e.tcx, e.tcy, e.tescala = 108, 128 + min(i, 4) * 112, 0.6
            else:
                for i, e in enumerate(ventanas):
                    col, fila = i % 3, (i // 3) % 2
                    extra = (i // 6) * 24
                    e.tcx, e.tcy, e.tescala = 310 + col * 330 + extra, 205 + fila * 230 + extra, 1.0
        if celdas:
            self._dimensionar_reales(celdas, area)
        self._ajustar_capturas()

    def _dimensionar_reales(self, celdas, area):
        """El mosaico también en la vida real: cada ventana se acomoda en la pantalla en el
        mismo lugar y proporción que su celda, lado a lado y SIN encimarse. Así su contenido
        se reacomoda y se lee (no una ventana de 1920 px vista en 400), y arrastrar un archivo
        de una a otra es exactamente como hacerlo con el ratón (si están encimadas, Windows no
        lo suelta en la de abajo). Se guarda cómo estaba cada una para dejarla igual."""
        import win32api
        import win32con
        import win32gui
        try:
            monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
            tx0, ty0, tx1, ty1 = win32api.GetMonitorInfo(monitor)["Work"]  # sin la barra de tareas
        except Exception:
            tx0, ty0, tx1, ty1 = 0, 0, U32.GetSystemMetrics(0), U32.GetSystemMetrics(1)
        ax0, ay0, ax1, ay1 = area
        k = min((tx1 - tx0) / (ax1 - ax0), (ty1 - ty0) / (ay1 - ay0))
        for e, cx, cy, cw, ch in celdas:
            barra = 34  # la barra del panel no existe en la ventana real
            rw, rh = int(cw * k), int((ch - barra) * k)
            rx = int(tx0 + (cx - cw / 2 - ax0) * k)
            ry = int(ty0 + (cy - ch / 2 + barra - ay0) * k)
            try:
                if win32gui.IsIconic(e.hwnd):
                    continue
                x0, y0, x1, y1 = win32gui.GetWindowRect(e.hwnd)
                if max(abs(x0 - rx), abs(y0 - ry), abs((x1 - x0) - rw), abs((y1 - y0) - rh)) < 12:
                    continue
                if e.hwnd not in self.colocaciones:
                    self.colocaciones[e.hwnd] = (win32gui.GetWindowPlacement(e.hwnd), control._proceso(e.hwnd))
                    _guardar_colocaciones(self.colocaciones)  # antes de tocarla
                if win32gui.GetWindowPlacement(e.hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
                    win32gui.ShowWindow(e.hwnd, win32con.SW_SHOWNOACTIVATE)  # des-maximizar
                win32gui.SetWindowPos(e.hwnd, 0, rx, ry, rw, rh,
                                      win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            except Exception:
                continue

    def _restaurar_reales(self):
        """Las ventanas reales vuelven a como estaban antes del mosaico."""
        restaurar_pendientes(dict(self.colocaciones))
        self.colocaciones.clear()

    def _ajustar_capturas(self):
        """La que usas a 30 fps, las del mosaico a 10, las demás a 4 (solo se reinicia la
        captura que cambia de velocidad)."""
        grandes = set(self.orden_mosaico[:MOSAICO_MAX]) if self.disposicion == "mosaico" else set()
        cambios = []
        for e in self._ventanas():
            nivel = True if e.interactiva else ("media" if e.hwnd in grandes else False)
            if e.nivel != nivel and e.captura is not None:
                e.nivel = nivel
                cambios.append((e.captura, nivel))
        if cambios:
            self._recapturar(cambios)

    def _alternar_mosaico(self):
        self.disposicion = "normal" if self.disposicion == "mosaico" else "mosaico"
        if self.disposicion == "normal":
            self._restaurar_reales()
        self._avisar("Mosaico: hasta 4 ventanas para usar a la vez" if self.disposicion == "mosaico"
                     else "Una ventana grande al centro")
        self._organizar()

    def _usable(self, e):
        """La imagen de la ventana es lo bastante grande para usarla directo (clic, escribir,
        arrastrar) sin tener que agrandarla antes."""
        return e.tipo == "ventana" and e.zona is not None and e.zona[2] >= USABLE_PX

    def _enfocada(self):
        return next((v for v in self._ventanas() if v.interactiva), None)

    def _ajustar_tamano(self, e):
        """Que el panel tenga la proporción de la ventana: la imagen llena el panel y el punto
        tocado corresponde exacto a la ventana real."""
        img = e.imagen()
        if img is None:
            return
        ih, iw = img.shape[:2]
        alto = int(np.clip(284 * ih / iw + 44, 120, 300))
        if abs(alto - e.h0) > 3:
            e.h0 = alto
            self._organizar()  # con la proporción nueva, el lugar y tamaño cambian

    def _sincronizar_ventanas(self):
        import win32gui
        hwnds = []
        for h in control._todas_las_ventanas():
            try:
                if win32gui.GetClassName(h) in EXCLUIR_CLASES:
                    continue
                x0, y0, x1, y1 = win32gui.GetWindowRect(h)
                if (x1 - x0 >= 200 and y1 - y0 >= 120) or win32gui.IsIconic(h):
                    hwnds.append(h)
            except Exception:
                continue
            if len(hwnds) >= MAX_VENTANAS:
                break
        nuevas = []
        with self.lock:
            actuales = {e.hwnd: e for e in self._ventanas()}
            for h, e in actuales.items():
                if h not in hwnds:
                    if e.captura:
                        e.captura.cerrar()
                    self.elementos.remove(e)
                    if e.alfa > 0.1:  # se desvanece encogiéndose en vez de desaparecer de golpe
                        e.talfa, e.tescala, e.agarrado = 0.0, e.escala * 0.55, 0
                        self.salientes.append((e, time.time()))
                else:
                    e.titulo = control._titulo(h) or e.titulo
            for h in hwnds:
                if h in actuales:
                    continue
                titulo = control._titulo(h)
                try:
                    exe = psutil.Process(control._pid_de(h)).exe()
                except Exception:
                    exe = None
                e = Elemento("ventana", titulo, W / 2, H / 2, 300, 210, _icono(exe, 22, titulo[:1]), hwnd=h)
                e.escala, e.alfa = 0.2, 0.0  # aparece creciendo y haciéndose visible
                e.captura = Captura(h)
                if self.parar.is_set():
                    break
                e.captura.iniciar(rapida=False)
                e.nivel = False
                self.elementos.insert(0, e)
                nuevas.append(e)
        if nuevas or len(actuales) != len(hwnds):
            self._organizar()
        # El puntito de "abierta" del dock: se calcula aquí (cada 1.2 s) y no al dibujar, donde
        # eran 24 consultas a Windows por cuadro (título y proceso de cada ventana x 3 iconos)
        ventanas = self._ventanas()
        for e in [e for e in self.elementos if e.tipo == "app"]:
            e.abierta = any(_coincide(v.hwnd, e.claves) for v in ventanas)
        if self.esperando and time.time() < self.esperando[1]:
            for e in self._ventanas():
                if _coincide(e.hwnd, self.esperando[0]):
                    self.esperando = None
                    self._activar(e)
                    break

    def _hilo_ventanas(self):
        # Este hilo ve los píxeles reales (con Windows al 125% o 150% de escala, sin esto
        # PrintWindow devolvía la ventana recortada)
        try:
            U32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        ultima_sync = 0.0
        while not self.parar.is_set():
            ahora = time.time()
            if ahora - ultima_sync > 1.2:
                try:
                    self._sincronizar_ventanas()
                except Exception as e:
                    print(f"[Realidad: no pude leer las ventanas ({e})]")
                ultima_sync = ahora
            for e in self._ventanas():
                # Plan B: si WGC no entregó nada (ventana que no lo permite), PrintWindow
                sin_wgc = e.captura is None or not e.captura.wgc
                if sin_wgc and ahora - e.t_pw > (0.15 if e.interactiva else 1.0):
                    e.mini_pw, e.t_pw = _printwindow(e.hwnd, 1280 if e.interactiva else 520), ahora
                self._ajustar_tamano(e)
            time.sleep(0.05)

    # --- acciones ---
    def _avisar(self, texto, seg=2.5):
        ahora = time.time()
        if texto != self.aviso or ahora >= self.t_aviso:
            self.t_aviso0 = ahora  # (si es el mismo aviso que ya se ve, no vuelve a entrar)
        self.aviso, self.t_aviso = texto, ahora + seg

    def _activar(self, e):
        self._enfocar(e, organizar=True)

    def _enfocar(self, e, organizar=True):
        """La ventana pasa a ser la que usas: la real va al frente (debajo de la capa
        transparente) para que tus clics y lo que escribes le lleguen. organizar=False: como
        en un escritorio, usarla no la mueve de lugar (si ya se ve grande)."""
        import win32con
        import win32gui
        cambio = not e.interactiva
        with self.lock:
            for o in self._ventanas():
                if o.interactiva and o is not e:
                    o.interactiva = False
            e.interactiva = True
            e.cerrando = 0.0
            if e.hwnd in self.orden_mosaico:
                self.orden_mosaico.remove(e.hwnd)
            self.orden_mosaico.insert(0, e.hwnd)
        if cambio and self.teclado is not None:
            self.teclado.eco = ""
        try:
            if win32gui.IsIconic(e.hwnd):
                win32gui.ShowWindow(e.hwnd, win32con.SW_RESTORE)
            x0, y0, x1, y1 = _rect_visible(e.hwnd)
            ancho, alto = U32.GetSystemMetrics(0), U32.GetSystemMetrics(1)
            if x0 < -8 or y0 < -8 or x1 > ancho + 8 or y1 > alto + 8:
                win32gui.ShowWindow(e.hwnd, win32con.SW_MAXIMIZE)  # que todo punto sea clicable
        except Exception:
            pass
        self.z_arriba = e.hwnd
        if not _dar_foco(e.hwnd):  # si Windows no lo permite, el truco de siempre en otro hilo
            threading.Thread(target=control.traer_al_frente, args=(e.hwnd,), daemon=True).start()
        if organizar:
            self._organizar()
        else:
            self._ajustar_capturas()

    def _recapturar(self, cambios):
        """Reinicia capturas [(Captura, rápida)] en otro hilo: arrancar Windows Graphics
        Capture tarda decenas de ms y trababa la animación justo al tocar una ventana."""
        def hacer():
            for cap, rapida in cambios:
                if cap is not None and not self.parar.is_set():
                    cap.iniciar(rapida=rapida)
        threading.Thread(target=hacer, daemon=True, name="realidad-captura").start()

    def _reducir(self, e):
        e.interactiva = False
        self._organizar()

    def _cerrar_ventana(self, e):
        import win32con
        import win32gui
        try:
            win32gui.PostMessage(e.hwnd, win32con.WM_CLOSE, 0, 0)
            self._avisar(f"Cerrando {e.titulo[:40]}")
            # Se encoge y se apaga un poco mientras la app cierra (si pregunta "¿guardar?",
            # vuelve a la normalidad en unos segundos)
            e.cerrando, e.talfa = time.time(), 0.55
        except Exception as err:
            self._avisar(f"No pude cerrarla: {err}", 3)

    def _abrir_app(self, e):
        existente = next((v for v in self._ventanas() if _coincide(v.hwnd, e.claves)), None)
        if existente is not None:
            self._activar(existente)
            return
        self._avisar(f"Abriendo {e.titulo}...", 4)
        self.esperando = (e.claves, time.time() + 25)

        def lanzar():
            try:
                e.accion()
            except Exception as err:
                self.esperando = None
                self._avisar(str(err), 4)
        threading.Thread(target=lanzar, daemon=True).start()

    def _llevar_al_escritorio(self, e):
        self.al_salir = e.hwnd
        self.motivo = "llevar ventana al escritorio"
        self.parar.set()

    def _al_tocar(self, e, x, y):
        e.pulso = 1.0
        if e.tipo == "accion":
            e.accion()
        elif e.tipo == "app":
            self._abrir_app(e)
        elif e.tipo == "ventana":
            boton = next((n for n, r in e.botones.items() if _dentro(r, x, y)), None)
            if boton == "Reducir":
                self._reducir(e)
            elif boton == "Escritorio":
                self._llevar_al_escritorio(e)
            elif boton == "Cerrar":
                self._cerrar_ventana(e)
            elif not e.interactiva:
                self._activar(e)

    # --- traducir un punto del panel a la ventana real ---
    def _a_pantalla(self, e, x, y):
        """Punto del panel -> píxel de la ventana real. None si ya no se puede (la ventana se
        cerró o todavía no hay imagen): antes eso lanzaba un error que cerraba todo el modo."""
        import win32gui
        if e.zona is None:
            return None
        zx, zy, zw, zh = e.zona
        try:
            # WGC entrega la ventana sin los bordes invisibles; PrintWindow, con ellos
            if e.captura and e.captura.wgc:
                x0, y0, x1, y1 = _rect_visible(e.hwnd)
            else:
                x0, y0, x1, y1 = win32gui.GetWindowRect(e.hwnd)
        except Exception:
            return None
        u = min(max((x - zx) / zw, 0.0), 0.999)
        v = min(max((y - zy) / zh, 0.0), 0.999)
        return x0 + u * (x1 - x0), y0 + v * (y1 - y0)

    def _ir(self, e, x, y):
        """Pone el puntero real en el punto de la ventana; False si no se pudo."""
        punto = self._a_pantalla(e, x, y)
        if punto is None:
            return False
        if self.z_arriba != e.hwnd:  # que el clic (o lo que sueltas) caiga en ESTA ventana
            _subir(e.hwnd)
            self.z_arriba = e.hwnd
        _cursor(*punto)
        return True

    # --- manos ---
    def _bajo(self, x, y):
        with self.lock:
            for e in reversed(self.elementos):
                if e.contiene(x, y):
                    return e
        return None

    def _zona_escritorio(self):
        return W / 2 - 170, 14, 340, 50

    def _en_zona_escritorio(self, x, y):
        zx, zy, zw, zh = self._zona_escritorio()
        return zx <= x <= zx + zw and zy <= y <= zy + zh + 20

    def _onda(self, x, y, color=CIAN_FUERTE, radio=34):
        self.ondas.append([int(x), int(y), time.time(), color, radio])
        del self.ondas[:-8]

    def _presionar(self, clave, x, y):
        m = self.manos[clave]
        m.pinza, m.t0, m.p0, m.ult = True, time.time(), (x, y), (x, y)
        m.desplazando = m.raton_abajo = False
        m.acumulado = 0.0
        self.inercia = None  # un pellizco nuevo frena el scroll que seguía solo
        e = self._bajo(x, y)
        m.elem, m.modo = e, None
        m.boton = None
        if e is None:
            self._onda(x, y, CIAN, 22)
            return
        if e.tipo == "ventana":
            m.boton = next((n for n, r in e.botones.items() if _dentro(r, x, y)), None)
        if (e.tipo == "ventana" and _dentro(e.zona, x, y) and not m.boton
                and (e.interactiva or self._usable(e))):
            # Dentro de la ventana: clic / scroll / arrastrar real. Como en el escritorio, tocar
            # otra ventana que se ve grande la enfoca ahí mismo, sin moverla
            if not e.interactiva:
                self._enfocar(e, organizar=False)
            m.modo = "contenido"
            m.destino = None
            return
        if e.tipo == "teclado":
            tecla = self._tecla_en(e, x, y)
            if tecla is not None:
                m.modo, m.tecla = "tecla", tecla[0]
                m.t_rep, m.repitio = time.time() + 0.45, False
                return
        m.modo = "mover"
        self._onda(x, y, CIAN, 26)
        with self.lock:  # lo agarrado pasa al frente
            if e in self.elementos:  # (el hilo de ventanas pudo quitarla en este instante)
                self.elementos.remove(e)
                self.elementos.append(e)
        e.agarrado += 1
        otra = next((o for k, o in self.manos.items()
                     if k != clave and o.pinza and o.elem is e and o.modo == "mover"), None)
        if otra is not None and e.tipo == "ventana":  # dos manos: escalar
            m.d0 = otra.d0 = max(20.0, math.dist((x, y), (otra.x, otra.y)))
            m.esc0 = otra.esc0 = e.tescala
        else:
            m.off = (e.tcx - x, e.tcy - y)

    def _mover(self, clave, x, y):
        m = self.manos[clave]
        e = m.elem
        if e is None:
            return
        if m.modo == "contenido":
            self._mover_contenido(m, e, x, y)
            return
        if m.modo == "tecla" or e.tipo == "accion" or m.boton:
            return  # en un botón de la barra (X, Reducir...) el pellizco no arrastra la ventana
        otra = next((o for k, o in self.manos.items()
                     if k != clave and o.pinza and o.elem is e and o.modo == "mover"), None)
        if otra is not None and m.d0:
            d = math.dist((x, y), (otra.x, otra.y))
            e.tescala = float(np.clip(m.esc0 * d / m.d0, 0.45, 3.6))
            e.tcx, e.tcy = (x + otra.x) / 2, (y + otra.y) / 2
        elif e.tipo == "app":
            # Los iconos del dock son elásticos: se estiran hacia la mano y al soltarlos regresan
            hx, hy = x + m.off[0], y + m.off[1]
            e.tcx = e.casa[0] + (hx - e.casa[0]) * 0.4
            e.tcy = e.casa[1] + (hy - e.casa[1]) * 0.4
        else:
            e.tcx, e.tcy = x + m.off[0], y + m.off[1]

    def _mover_contenido(self, m, e, x, y):
        quieto = math.dist(m.p0, (x, y)) < 14
        if not m.desplazando and not m.raton_abajo:
            if quieto and time.time() - m.t0 > ESPERA_ARRASTRE:
                punto = self._a_pantalla(e, *m.p0)
                if punto is not None and self._ir(e, *m.p0):  # mantener quieto = agarrar (arrastrar)
                    _boton_izq(True)
                    # Windows empieza a arrastrar cuando el ratón se mueve unos píxeles con el
                    # botón abajo: sin esto, al llevarlo a otra ventana no "agarraba" el archivo
                    for d in (3, 6, 9):
                        time.sleep(0.012)
                        _cursor(punto[0] + d, punto[1] + d)
                    m.raton_abajo = True
                    self._onda(*m.p0, NARANJA, 30)
            elif not quieto:
                m.desplazando = True                   # moverse enseguida = scroll
                self._ir(e, *m.p0)
        if m.raton_abajo:
            # Arrastrando (un archivo, una carpeta, texto): si la mano pasa a otra ventana, esa
            # sube y el puntero real va ahí: al soltar, cae en ella (mover/copiar de verdad)
            destino = self._ventana_bajo(x, y)
            punto = self._a_pantalla(destino, x, y) if destino is not None else None
            if punto is not None:
                m.destino = destino
                _cursor(*punto)  # sin subirla: cambiar el orden a medio arrastre cancela el soltar
            else:
                m.destino = None
        elif m.desplazando:
            # Como en el celular: la mano sube -> el contenido sube (rueda hacia abajo)
            m.acumulado += (y - m.ult[1]) * SCROLL_POR_PX * max(1.0, e.escala / 2)
            if abs(m.acumulado) >= 30:
                _rueda(m.acumulado)
                m.acumulado = 0.0
        m.ult = (x, y)

    def _lanzar(self, e):
        """Soltar una ventana en movimiento la deja seguir un poco (inercia) y, si iba a salir
        de la pantalla, rebota para que su barra siga a tu alcance."""
        _, _, w, h = e.rect()
        e.vcx, e.vcy = e.vx, e.vy
        e.tcx = float(np.clip(e.cx + e.vx * 0.16, 0, W))
        e.tcy = float(np.clip(e.cy + e.vy * 0.16, h / 2, max(h / 2, H - 40 + h / 2)))

    def _seguir_inercia(self, dt):
        i = self.inercia
        if i is None:
            return
        if not i["e"].interactiva or abs(i["v"]) < 250 or time.time() - i["t"] > 2.0:
            self.inercia = None
            return
        i["acum"] += i["v"] * dt
        i["v"] *= math.exp(-3.2 * dt)
        if abs(i["acum"]) >= 40:
            _rueda(i["acum"])
            i["acum"] = 0.0

    def _soltar(self, clave, x, y, cancelar=False):
        m = self.manos[clave]
        e, modo = m.elem, m.modo
        m.elem, m.pinza, m.modo = None, False, None
        m.d0 = m.esc0 = None
        if m.raton_abajo:
            _boton_izq(False)
            m.raton_abajo = False
        if e is None:
            return
        rapido = time.time() - m.t0 < TOQUE_SEG and math.dist(m.p0, (x, y)) < TOQUE_PX
        if modo == "tecla":
            tecla, m.tecla = m.tecla, None
            sobre = self._tecla_en(e, x, y)
            if not cancelar and not m.repitio and sobre is not None and sobre[0] == tecla:
                self._pulsar_tecla(e, tecla)
            return
        if modo == "contenido":
            soltado_en = m.destino
            m.destino = None
            if soltado_en is not None and soltado_en is not e:
                self._onda(x, y, NARANJA, 44)
                self._avisar(f"Soltado en {soltado_en.titulo[:40]}", 2)
            if not cancelar and not m.desplazando and rapido:
                if self._clic(e, *m.p0):               # toque = clic real
                    e.pulso = 0.6
                    self._onda(*m.p0, NARANJA, 40)
            elif not cancelar and m.desplazando:
                # Deslizar rápido y soltar: el scroll sigue solo y se frena, como en el celular
                v = m.vy * SCROLL_POR_PX * max(1.0, e.escala / 2)
                if abs(v) > 900:
                    self.inercia = {"e": e, "v": v, "t": time.time(), "acum": m.acumulado}
            return
        e.agarrado = max(0, e.agarrado - 1)
        e.tencoger, e.pivote = 1.0, None
        if e.tipo == "app" and not e.agarrado:
            e.tcx, e.tcy = e.casa   # el icono regresa a su lugar con un rebote
        if cancelar:
            return
        if e.tipo == "ventana" and m.boton == "Cerrar":
            # Cerrar una ventana real con un pellizco accidental sería grave: hay que mantenerlo
            if time.time() - m.t0 >= MANTENER_SALIR and _dentro(e.botones.get("Cerrar"), x, y):
                self._onda(x, y, ROJO, 46)
                self._cerrar_ventana(e)
            else:
                self._avisar("Mantén el pellizco sobre la X para cerrar la ventana")
            return
        if e.tipo == "accion" and e.mantener:  # Salir: hay que mantener el pellizco encima
            if time.time() - m.t0 >= MANTENER_SALIR and e.contiene(x, y):
                self.motivo = "botón Salir"
                e.accion()
            else:
                self._avisar("Mantén el pellizco sobre Salir para salir")
            return
        if e.tipo == "accion":  # Teclado, Mosaico: un toque
            if e.contiene(x, y):
                self._onda(x, y, CIAN_FUERTE, 40)
                e.pulso = 1.0
                e.accion()
            return
        if e.tipo == "teclado":  # se movió el teclado: se queda donde lo dejaste
            e.casa = (e.tcx, e.tcy)
            return
        if m.boton:  # Reducir / Escritorio: un toque, aunque la mano se haya movido un poco
            if _dentro(e.botones.get(m.boton), x, y):
                self._onda(x, y, CIAN_FUERTE, 40)
                self._al_tocar(e, x, y)
            return
        if rapido:
            self._onda(x, y, CIAN_FUERTE, 46)
            self._al_tocar(e, x, y)
        elif e.tipo == "ventana" and self._en_zona_escritorio(x, y):
            self._llevar_al_escritorio(e)
        elif e.tipo == "ventana" and not e.agarrado:
            self._lanzar(e)

    def _ventana_bajo(self, x, y):
        """La ventana (usable) cuya imagen está bajo ese punto, la de más arriba."""
        with self.lock:
            for e in reversed(self.elementos):
                if e.contiene(x, y):
                    if e.tipo == "ventana" and _dentro(e.zona, x, y) and (e.interactiva or self._usable(e)):
                        return e
                    if e.tipo != "ventana":
                        continue  # el teclado o un botón encima no "tapan" la ventana para soltar
                    return None
        return None

    def _clic(self, e, x, y):
        """Clic real en el punto del panel. Dos toques seguidos en el mismo lugar caen en el
        MISMO píxel: Windows lo cuenta como doble clic (abrir un archivo o carpeta)."""
        punto = self._a_pantalla(e, x, y)
        if punto is None:
            return False
        u = self.ultimo_clic
        if (u is not None and time.time() - u[0] < DOBLE_SEG and u[1] is e
                and math.dist(u[2], (x, y)) < DOBLE_PX):
            punto = u[3]
        if self.z_arriba != e.hwnd:
            _subir(e.hwnd)
            self.z_arriba = e.hwnd
        _cursor(*punto)
        _boton_izq(True)
        _boton_izq(False)
        self.ultimo_clic = (time.time(), e, (x, y), punto)
        self._revisar_campo_de_texto()
        return True

    def _clic_derecho(self, x, y):
        e = self._ventana_bajo(x, y)
        if e is None:
            return
        if not e.interactiva:
            self._enfocar(e, organizar=False)
        if self._ir(e, x, y):
            time.sleep(0.03)
            _boton_der()
            self._onda(x, y, MORADO, 40)

    def _revisar_campo_de_texto(self):
        """Tras un clic: si quedó el cursor en un campo de texto, aparece el teclado (como en
        un celular). Se pregunta en otro hilo a la accesibilidad de Windows."""
        if self._teclado_visible():
            return

        def revisar():
            time.sleep(0.35)
            try:
                import comtypes
                import comtypes.client
                from pywinauto.uia_defines import IUIA
                try:
                    comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
                except OSError:
                    pass
                dll = IUIA().UIA_dll
                uia = comtypes.client.CreateObject(dll.CUIAutomation, interface=dll.IUIAutomation)
                foco = uia.GetFocusedElement()
                tipo = foco.CurrentControlType
                if tipo in (dll.UIA_EditControlTypeId, dll.UIA_ComboBoxControlTypeId):
                    self.pedir_teclado = True
                elif tipo == dll.UIA_DocumentControlTypeId:
                    # el Bloc de notas o Word son "documento" editable; una página web, no
                    solo_lectura = foco.GetCurrentPropertyValue(dll.UIA_ValueIsReadOnlyPropertyId)
                    if solo_lectura is False or solo_lectura == 0:
                        self.pedir_teclado = True
            except Exception:
                pass
        threading.Thread(target=revisar, daemon=True, name="realidad-campo").start()

    # --- teclado virtual ---
    def _mostrar_teclado(self, mostrar=True):
        with self.lock:
            if mostrar:
                if self.teclado is None:
                    self.teclado = Elemento("teclado", "Teclado", W / 2 + 100, H - 150, 860, 280)
                    self.teclado.cy = H + 160   # entra subiendo
                    self.elementos.append(self.teclado)
                self.teclado.talfa = 1.0
                self.teclado.tcx, self.teclado.tcy = self.teclado.casa
                if self.teclado in self.elementos:  # encima de todo
                    self.elementos.remove(self.teclado)
                    self.elementos.append(self.teclado)
            elif self.teclado is not None:
                self.teclado.talfa = 0.0
                self.teclado.tcy = H + 160
            for e in self.elementos:  # el dock se esconde mientras está el teclado
                if e.tipo == "app":
                    e.tcy = H + 90 if mostrar else e.casa[1]
            for b in self.elementos:
                if b.tipo == "accion" and b.titulo == "Teclado":
                    b.activo = mostrar
        self._organizar()

    def _tecla_en(self, e, x, y):
        """(clave, rect en pantalla) de la tecla bajo el punto, o None (p. ej. la barra de arriba)."""
        rx, ry, rw, rh = e.rect()
        if not (rx <= x <= rx + rw and ry <= y <= ry + rh) or e.talfa < 0.5:
            return None
        k = rw / e.w0
        for (tx, ty, tw, th), clave, _et in _disposicion_teclado(e.w0, e.h0):
            r = (rx + tx * k, ry + ty * k, tw * k, th * k)
            if _dentro(r, x, y):
                return clave, r
        return None

    def _al_teclear(self):
        """Que lo que escribes vaya a la ventana que estás usando (la que se ve enfocada)."""
        e = self._enfocada()
        if e is None:
            return
        if U32.GetForegroundWindow() != e.hwnd and _dar_foco(e.hwnd):
            time.sleep(0.03)  # que la ventana termine de tomar el foco antes de la primera letra

    def _pulsar_tecla(self, t, clave):
        t.pulso = 0.5
        if clave == "OCULTAR":
            self._mostrar_teclado(False)
            return
        if clave == "MAYUS":
            t.mayus = not t.mayus
            return
        if clave == "ACENTO":
            t.acento = not t.acento
            return
        self._al_teclear()
        if clave == "BORRAR":
            _tecla("BORRAR")
            t.eco = t.eco[:-1]
        elif clave == "ENTER":
            _tecla("ENTER")
            t.eco = ""
        elif clave == "TAB":
            _tecla("TAB")
        elif clave in ("IZQ", "DER"):
            _tecla(clave)
        elif clave == "COPIAR":
            _tecla("CTRL", "C")
            self._avisar("Copiado", 1.2)
        elif clave == "PEGAR":
            _tecla("CTRL", "V")
        else:
            letra = " " if clave == "ESPACIO" else clave
            if t.acento and letra in "aeiou":
                letra = "áéíóú"["aeiou".index(letra)]
            t.acento = False
            if t.mayus:
                letra, t.mayus = letra.upper(), False
            _escribir(letra)
            t.eco = (t.eco + letra)[-60:]

    def _repetir_teclas(self, ahora):
        """Borrar sostenido borra seguido, como en un teclado de verdad."""
        for m in self.manos.values():
            if m.pinza and m.modo == "tecla" and m.tecla == "BORRAR" and ahora >= m.t_rep:
                self._al_teclear()
                _tecla("BORRAR")
                if self.teclado is not None:
                    self.teclado.eco = self.teclado.eco[:-1]
                m.t_rep, m.repitio = ahora + 0.08, True

    def _soltar_todo(self):
        """Suelta pellizcos, arrastres y el botón del ratón real (tras un error)."""
        for m in self.manos.values():
            if m.raton_abajo:
                _boton_izq(False)
            m.raton_abajo = m.pinza = m.desplazando = m.pinza_der = False
            m.elem, m.modo, m.boton, m.tecla, m.destino = None, None, None, None, None
        with self.lock:
            for e in self.elementos:
                e.agarrado = 0

    def _procesar_manos(self, resultado, ahora):
        vistas = set()
        for i, lms in enumerate(resultado.hand_landmarks or []):
            try:
                clave = resultado.handedness[i][0].category_name
            except Exception:
                clave = str(i)
            if clave in vistas:
                clave += "2"
            vistas.add(clave)
            pts = np.array([(p.x * W, p.y * H) for p in lms], np.float32)
            m = self.manos.setdefault(clave, Mano())
            dtm = ahora - m.visto
            m.puntos, m.visto = pts, ahora
            tam = max(1.0, float(np.linalg.norm(pts[0] - pts[9])))
            razon = float(np.linalg.norm(pts[4] - pts[8])) / tam
            m.razon = razon
            # El cursor es el punto entre pulgar e índice: no brinca al pellizcar
            medio = (pts[4] + pts[8]) / 2
            cx = float(np.clip((medio[0] / W - MARGEN) / (1 - 2 * MARGEN), 0, 1)) * W
            cy = float(np.clip((medio[1] / H - MARGEN) / (1 - 2 * MARGEN), 0, 1)) * H
            if m.x is None:
                m.x, m.y = cx, cy
            else:
                vel = math.dist((cx, cy), (m.x, m.y))
                a = min(0.85, 0.3 + vel / 90)  # suaviza el temblor sin retrasar los movimientos rápidos
                px, py = m.x, m.y
                m.x += (cx - m.x) * a
                m.y += (cy - m.y) * a
                if 0.005 < dtm < 0.3:
                    m.vx += ((m.x - px) / dtm - m.vx) * 0.5
                    m.vy += ((m.y - py) / dtm - m.vy) * 0.5
            # Pulgar con el dedo MEDIO (y el índice separado) = clic derecho
            razon_medio = float(np.linalg.norm(pts[4] - pts[12])) / tam
            if not m.pinza and not m.pinza_der and razon_medio < PINZA_DER_ON and razon > 0.5:
                m.pinza_der, m.t0d = True, ahora
                self._onda(m.x, m.y, MORADO, 22)
            elif m.pinza_der:
                if razon_medio > PINZA_DER_OFF:
                    m.pinza_der = False
                    if ahora - m.t0d < 1.5:
                        self._clic_derecho(m.x, m.y)
                continue
            if not m.pinza and razon < PINZA_ON:
                self._presionar(clave, m.x, m.y)
            elif m.pinza and razon > PINZA_OFF:
                self._soltar(clave, m.x, m.y)
            elif m.pinza:
                self._mover(clave, m.x, m.y)
        self._repetir_teclas(ahora)
        # Mano que salió de cuadro: suelta lo que tuviera, sin "clic"
        for clave in [k for k, m in self.manos.items() if ahora - m.visto > 0.35]:
            m = self.manos[clave]
            if m.pinza:
                self._soltar(clave, m.x, m.y, cancelar=True)
            del self.manos[clave]
        self._puntero_flotante(ahora)

    def _puntero_flotante(self, ahora):
        """Mano sin pellizcar sobre la ventana interactiva: el puntero real la sigue (así
        aparecen los controles del video, los menús al pasar encima, etc.)."""
        if ahora - self.t_cursor < 0.03:
            return
        for m in self.manos.values():
            if m.pinza or m.x is None:
                continue
            e = next((v for v in self._ventanas() if v.interactiva), None)
            if e is not None and _dentro(e.zona, m.x, m.y) and self._bajo(m.x, m.y) is e:
                self._ir(e, m.x, m.y)
                self.t_cursor = ahora
                return

    # --- render ---
    def _animar(self, dt, ahora):
        cursores = [(m.x, m.y) for m in self.manos.values() if m.x is not None]
        sobre_zona = [m for m in self.manos.values()
                      if m.pinza and m.modo == "mover" and m.elem is not None
                      and m.elem.tipo == "ventana" and not m.boton and self._en_zona_escritorio(m.x, m.y)]
        esperando = self.esperando if self.esperando and ahora < self.esperando[1] else None
        if self.pedir_teclado:  # tocaste un campo de texto: aparece el teclado
            self.pedir_teclado = False
            self._mostrar_teclado(True)
        with self.lock:
            for e in self.elementos:
                e.encima = bool(e.agarrado) or any(e.contiene(x, y) for x, y in cursores)
                crece = e.encima and e.tipo != "ventana"
                e.hover += ((1.0 if crece else 0.0) - e.hover) * _suave(dt, 12)
                # Hundido mientras lo pellizcas sin arrastrarlo (botón que "se presiona")
                e.presionado = e.tipo in ("app", "accion") and any(
                    m.pinza and m.elem is e and math.dist(m.p0, (m.x, m.y)) < TOQUE_PX
                    for m in self.manos.values())
                mano = next((m for m in sobre_zona if m.elem is e), None)
                e.tencoger, e.pivote = (0.55, (mano.x, mano.y)) if mano else (1.0, e.pivote)
                e.saltando = e.tipo == "app" and esperando is not None and esperando[0] is e.claves
                if e.cerrando and ahora - e.cerrando > 3:  # no se cerró (p. ej. "¿guardar?")
                    e.cerrando, e.talfa = 0.0, 1.0
                e.animar(dt)
            for e, _t in self.salientes:
                e.animar(dt)
            self.salientes = [(e, t) for e, t in self.salientes if ahora - t < 0.6 and e.alfa > 0.03]
        arrastrando = any(m.pinza and m.modo == "mover" and m.elem is not None
                          and m.elem.tipo == "ventana" and not m.boton for m in self.manos.values())
        self.zona_a += ((1.0 if arrastrando else 0.0) - self.zona_a) * _suave(dt, 14)

    def _pintar(self, c, e, ahora):
        """Dibuja un elemento con su animación: sombra si está levantado; inclinado, balanceado
        o encogido (deformado en perspectiva) si se mueve; semitransparente si aparece o se va.
        Quieto, se dibuja directo: la deformación solo cuesta mientras hay movimiento."""
        dibujar = {"ventana": self._dibujar_ventana, "app": self._dibujar_app,
                   "teclado": self._dibujar_teclado}.get(e.tipo, self._dibujar_accion)
        x, y, w, h = e.rect()
        if w < 4 or h < 4 or e.alfa < 0.02:
            return
        radio = {"ventana": 16, "app": 22, "teclado": 20}.get(e.tipo, h // 2)
        esquinas = e.deformacion(x, y, w, h, ahora)
        if e.alzado > 0.02:  # la sombra se aleja del panel cuanto más "alto" está
            sx, sy, sw, sh = (x, y, w, h) if esquinas is None else cv2.boundingRect(esquinas)
            dx = float(np.clip(-e.vx * 0.012, -22, 22))
            dy = 5 + 15 * e.alzado + float(np.clip(-e.vy * 0.012, -10, 10))
            _sombra(c, sx + dx, sy + dy, sw, sh, radio, 0.55 * e.alzado * e.alfa)
        if esquinas is None and e.alfa >= 0.99:
            dibujar(c, e)
            self._borde_delantero(c, e, None, x, y, w, h, radio)
            return
        pad = int(14 + 0.22 * max(w, h))
        rx0, ry0, rx1, ry1 = max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)
        if rx1 - rx0 < 4 or ry1 - ry0 < 4:
            dibujar(c, e)
            return
        region = c[ry0:ry1, rx0:rx1]
        fondo = region.copy()
        dibujar(c, e)
        alfa = min(1.0, max(0.0, e.alfa))
        if esquinas is None:
            cv2.addWeighted(region, alfa, fondo, 1 - alfa, 0, dst=region)
            return
        panel = region.copy()
        region[:] = fondo
        rw, rh = rx1 - rx0, ry1 - ry0
        borde = 10  # incluye lo que se dibuja pegado por fuera (el pulso, el puntito del dock)
        mascara = np.zeros((rh, rw), np.uint8)
        _pegar_en(mascara, _mascara(w + 2 * borde, h + 2 * borde, radio + borde),
                  x - borde - rx0, y - borde - ry0)
        origen = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]) - np.float32([rx0, ry0])
        M = cv2.getPerspectiveTransform(origen, esquinas - np.float32([rx0, ry0]))
        plano = cv2.warpPerspective(panel, M, (rw, rh), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_REPLICATE)
        mascara = cv2.warpPerspective(mascara, M, (rw, rh), flags=cv2.INTER_NEAREST)
        if alfa < 0.99:
            plano = cv2.addWeighted(plano, alfa, region, 1 - alfa, 0)
        cv2.copyTo(plano, mascara, region)
        self._borde_delantero(c, e, esquinas, x, y, w, h, radio)

    @staticmethod
    def _borde_delantero(c, e, esquinas, x, y, w, h, radio):
        """El borde que va al frente del movimiento brilla: se ve hacia dónde va el panel."""
        vel = math.hypot(e.vx, e.vy)
        if vel < 350 or e.alfa < 0.5:
            return
        if esquinas is None:
            esquinas = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
        normales = ((0, -1), (1, 0), (0, 1), (-1, 0))  # arriba, derecha, abajo, izquierda
        i = max(range(4), key=lambda k: normales[k][0] * e.vx + normales[k][1] * e.vy)
        a, b = esquinas[i], esquinas[(i + 1) % 4]
        largo = float(np.linalg.norm(b - a))
        if largo < 2 * radio + 4:
            return
        u = (b - a) / largo * radio
        cv2.line(c, tuple(int(v) for v in a + u), tuple(int(v) for v in b - u), CIAN_FUERTE,
                 2 if vel < 1200 else 3, cv2.LINE_AA)

    def _dibujar_ondas(self, c, ahora):
        vivas = []
        for o in self.ondas:
            x, y, t0, color, rmax = o
            p = (ahora - t0) / 0.45
            if p >= 1:
                continue
            vivas.append(o)
            r = int(6 + rmax * (1 - (1 - p) ** 3))
            x0, y0, x1, y1 = max(0, x - r - 4), max(0, y - r - 4), min(W, x + r + 5), min(H, y + r + 5)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            roi = c[y0:y1, x0:x1]
            capa = roi.copy()
            cv2.circle(capa, (x - x0, y - y0), r, color, max(1, int(4 * (1 - p))), cv2.LINE_AA)
            cv2.addWeighted(capa, 1 - p, roi, p, 0, dst=roi)
        self.ondas = vivas

    def _dibujar_app(self, c, e):
        x, y, w, h = e.rect()
        brillo = e.encima or e.pulso > 0
        _vidrio(c, x, y, w, h, radio=22, tinte=(90, 60, 30) if brillo else (60, 40, 25),
                fuerza=0.38 + 0.12 * e.hover)
        k = e.escala * (1 + e.lupa * e.hover)
        icono = e.icono if abs(k - 1) < 0.03 else cv2.resize(e.icono, (max(8, int(60 * k)),) * 2)
        _pegar(c, icono, x + (w - icono.shape[1]) / 2, y + 6 * k)
        t = _texto(e.titulo, 14, (255, 255, 255), True)
        _pegar(c, t, e.cx - t.shape[1] / 2, y + h - t.shape[0] - 2)
        if e.abierta:  # puntito de "app abierta", como en el dock de macOS
            cv2.circle(c, (int(e.cx), y + h + 7), 3, BLANCO, -1, cv2.LINE_AA)
        _borde(c, x, y, w, h, 22, CIAN_FUERTE if brillo else (200, 190, 170), 2 if brillo else 1)

    def _dibujar_accion(self, c, e):
        x, y, w, h = e.rect()
        brillo = e.encima or e.pulso > 0
        if e.mantener:  # Salir: rojo
            tinte, borde = ((40, 40, 150) if brillo else (40, 40, 110)), ROJO
        else:
            tinte = (150, 100, 30) if e.activo else ((110, 75, 30) if brillo else (70, 50, 30))
            borde = CIAN_FUERTE
        _vidrio(c, x, y, w, h, radio=h // 2, tinte=tinte, fuerza=0.5)
        t = _texto(e.titulo, 18, (255, 255, 255), True)
        _pegar(c, t, e.cx - t.shape[1] / 2, e.cy - t.shape[0] / 2)
        _borde(c, x, y, w, h, h // 2, borde if (brillo or e.activo) else (200, 190, 170),
               2 if (brillo or e.activo) else 1)

    def _dibujar_teclado(self, c, e):
        x, y, w, h = e.rect()
        _vidrio(c, x, y, w, h, radio=20, tinte=(70, 45, 25), fuerza=0.6)
        sprite = _sprite_teclado(e.w0, e.h0, e.mayus, e.acento)
        if abs(w - e.w0) > 1 or abs(h - e.h0) > 1:
            sprite = tuple(cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR) for a in sprite)
        _pegar_premult(c, sprite, x, y)
        k = w / e.w0
        # Cabecera: a qué ventana va lo que escribes y lo último que escribiste
        destino = self._enfocada()
        t = _texto(f"Escribe en: {destino.titulo[:34] if destino else 'la ventana del frente'}",
                   13, (200, 230, 255), True)
        _pegar(c, t, x + 16 * k, y + (CABECERA_TECLADO * k - t.shape[0]) / 2)
        eco = e.eco[-34:] + ("|" if int(time.time() * 2) % 2 else " ")
        t = _texto(eco, 16, (255, 255, 255), False)
        _pegar(c, t, x + w - 16 * k - t.shape[1], y + (CABECERA_TECLADO * k - t.shape[0]) / 2)
        # Teclas bajo la mano (contorno) y presionadas (llenas)
        for m in self.manos.values():
            if m.x is None:
                continue
            bajo = self._tecla_en(e, m.x, m.y)
            if bajo is None:
                continue
            rx, ry, rw, rh = (int(v) for v in bajo[1])
            if m.pinza and m.modo == "tecla" and m.elem is e:
                x0, y0, x1, y1 = max(0, rx), max(0, ry), min(W, rx + rw), min(H, ry + rh)
                if x1 > x0 and y1 > y0:
                    roi = c[y0:y1, x0:x1]
                    cv2.addWeighted(roi, 0.45, np.full_like(roi, (255, 200, 90)), 0.55, 0, dst=roi)
                _borde(c, rx, ry, rw, rh, 9, BLANCO, 2)
            else:
                _borde(c, rx, ry, rw, rh, 9, CIAN_FUERTE, 2)
        _borde(c, x, y, w, h, 20, CIAN_FUERTE if e.encima else (210, 200, 180), 1)

    def _dibujar_ventana(self, c, e):
        x, y, w, h = e.rect()
        _vidrio(c, x, y, w, h, radio=16, fuerza=0.5)
        barra = max(26, min(40, int(30 * e.escala)))
        e.barra = (x, y, w, barra)
        _pegar(c, e.icono, x + 10, y + (barra - e.icono.shape[0]) / 2)
        e.botones = {}
        cursores = [(m.x, m.y) for m in self.manos.values() if m.x is not None]
        derecha = x + w - 8
        nombres = ("Cerrar", "Escritorio", "Reducir") if e.interactiva else ("Cerrar",)
        for nombre in nombres:
            if nombre == "Cerrar":
                bw = barra - 8
                r = (derecha - bw, y + 4, bw, barra - 8)
                _vidrio(c, *r, radio=bw // 2, tinte=(50, 50, 160), fuerza=0.6)
                cxb, cyb, d = r[0] + bw // 2, r[1] + r[3] // 2, max(3, bw // 5)
                cv2.line(c, (cxb - d, cyb - d), (cxb + d, cyb + d), BLANCO, 2, cv2.LINE_AA)
                cv2.line(c, (cxb - d, cyb + d), (cxb + d, cyb - d), BLANCO, 2, cv2.LINE_AA)
            else:
                t = _texto(nombre, 13, (255, 255, 255), True)
                bw = t.shape[1] + 18
                r = (derecha - bw, y + 4, bw, barra - 8)
                _vidrio(c, *r, radio=(barra - 8) // 2, tinte=(120, 80, 20), fuerza=0.6)
                _pegar(c, t, r[0] + 9, r[1] + (r[3] - t.shape[0]) / 2)
            if any(m.pinza and m.elem is e and m.boton == nombre for m in self.manos.values()):
                _borde(c, *r, r[3] // 2, BLANCO, 3)       # presionado
            elif any(_dentro(r, cx, cy) for cx, cy in cursores):
                _borde(c, *r, r[3] // 2, CIAN_FUERTE, 2)
            e.botones[nombre] = r
            derecha = r[0] - 6
        # Ancho en escalones de 32 px: mientras el panel crece o lo escalas con las manos, el ancho
        # cambiaba cada cuadro y el título se volvía a dibujar con PIL cada vez (sin caché):
        # ~6 s a 3-5 fps al entrar al modo y tirones al agrandar ventanas
        ancho_titulo = max(32, (derecha - x - 44) // 32 * 32)
        t = _texto(e.titulo or "Ventana", 14, (255, 255, 255), False, ancho_titulo)
        _pegar(c, t, x + 38, y + (barra - t.shape[0]) / 2)

        zona_w, zona_h = w - 12, h - barra - 8
        img = e.imagen()
        e.zona = None
        if img is not None and zona_w > 10 and zona_h > 10:
            if e.captura is not None:
                e.captura.caja = (zona_w, zona_h)
            ih, iw = img.shape[:2]
            if iw <= zona_w and ih <= zona_h and (iw >= zona_w - 2 or ih >= zona_h - 2):
                mini = img  # ya llegó del tamaño justo (WGC): sin redimensionar en cada cuadro
            else:
                clave = (id(img), zona_w, zona_h)
                if e.cache_mini is None or e.cache_mini[0] != clave:
                    e.cache_mini = (clave, _ajustar(img, zona_w, zona_h))
                mini = e.cache_mini[1]
            if not e.interactiva and self._enfocada() is not None:
                # La que no usas, un poco apagada: se nota cuál recibe tus clics y lo que escribes
                if e.cache_tenue is None or e.cache_tenue[0] != id(mini):
                    e.cache_tenue = (id(mini), cv2.convertScaleAbs(mini, alpha=0.78))
                mini = e.cache_tenue[1]
            mx = x + 6 + (zona_w - mini.shape[1]) // 2
            my = y + barra + 2 + (zona_h - mini.shape[0]) // 2
            _pegar_bgr(c, mini, mx, my)
            e.zona = (mx, my, mini.shape[1], mini.shape[0])
        elif zona_h > 30:
            grande = cv2.resize(e.icono, (min(64, zona_h - 20),) * 2)
            _pegar(c, grande, x + (w - grande.shape[1]) / 2, y + barra + (zona_h - grande.shape[0]) / 2)
            if not e.interactiva:
                t = _texto("Minimizada · tócala para abrirla", 13, (230, 230, 230))
                _pegar(c, t, x + (w - t.shape[1]) / 2, y + h - 26)
        brillo = e.encima or e.pulso > 0
        destino = any(m.raton_abajo and m.destino is e and m.elem is not e for m in self.manos.values())
        if destino:  # aquí caería lo que estás arrastrando
            _borde(c, x - 3, y - 3, w + 6, h + 6, 19, NARANJA, 3)
        elif e.interactiva:  # la que estás usando: doble borde luminoso
            _borde(c, x, y, w, h, 16, CIAN_FUERTE, 2)
            _borde(c, x - 4, y - 4, w + 8, h + 8, 20, (255, 214, 80), 1)
            cv2.circle(c, (int(x + 30), int(y + barra / 2) - 7), 3, CIAN_FUERTE, -1, cv2.LINE_AA)
        else:
            _borde(c, x, y, w, h, 16, CIAN_FUERTE if brillo else (210, 200, 180), 2 if brillo else 1)
        if e.pulso > 0:
            _borde(c, x - 4, y - 4, w + 8, h + 8, 20, CIAN_FUERTE, 2)

    def _dibujar_manos(self, c, ahora):
        for m in self.manos.values():
            if m.puntos is not None and ahora - m.visto < 0.3:
                for a, b in CONEXIONES_MANO:
                    cv2.line(c, tuple(m.puntos[a].astype(int)), tuple(m.puntos[b].astype(int)),
                             (230, 200, 120), 1, cv2.LINE_AA)
            if m.x is None:
                continue
            p = (int(m.x), int(m.y))
            if m.pinza_der:            # pulgar + medio: al soltar, clic derecho
                cv2.circle(c, p, 12, MORADO, -1, cv2.LINE_AA)
                cv2.circle(c, p, 19, BLANCO, 2, cv2.LINE_AA)
            elif m.raton_abajo:        # arrastrando (dentro de una ventana o hacia otra)
                cv2.circle(c, p, 13, NARANJA, -1, cv2.LINE_AA)
                cv2.circle(c, p, 20, BLANCO, 2, cv2.LINE_AA)
                origen = m.elem
                texto = (f"→ {m.destino.titulo[:26]}" if m.destino is not None and m.destino is not origen
                         else "Arrastrando")
                t = _texto(texto, 14, (255, 255, 255), True)
                fx, fy = p[0] + 26, p[1] + 14
                _vidrio(c, fx, fy, t.shape[1] + 44, 32, radio=16, tinte=(30, 90, 160), fuerza=0.6)
                if origen is not None and origen.icono is not None:
                    _pegar(c, origen.icono, fx + 8, fy + (32 - origen.icono.shape[0]) / 2)
                _pegar(c, t, fx + 36, fy + (32 - t.shape[0]) / 2)
            elif m.pinza:
                cv2.circle(c, p, 12, CIAN_FUERTE, -1, cv2.LINE_AA)
                cv2.circle(c, p, 18, BLANCO, 2, cv2.LINE_AA)
                if m.desplazando and abs(m.vy) > 60:  # flechas hacia donde va el scroll
                    sg = 1 if m.vy > 0 else -1
                    for k in (0, 1):
                        yy = p[1] + sg * (28 + k * 9)
                        cv2.polylines(c, [np.int32([[p[0] - 8, yy - sg * 6], [p[0], yy], [p[0] + 8, yy - sg * 6]])],
                                      False, CIAN_FUERTE, 2, cv2.LINE_AA)
                elif m.modo == "contenido" and not m.desplazando:  # cargando el "mantener"
                    avance = min(1.0, (ahora - m.t0) / ESPERA_ARRASTRE)
                    cv2.ellipse(c, p, (24, 24), -90, 0, int(360 * avance), NARANJA, 3, cv2.LINE_AA)
                elif m.elem is not None and (m.elem.tipo == "accion" or m.boton == "Cerrar"):  # cargando Salir / X
                    avance = min(1.0, (ahora - m.t0) / MANTENER_SALIR)
                    cv2.ellipse(c, p, (24, 24), -90, 0, int(360 * avance), ROJO, 3, cv2.LINE_AA)
            else:
                # El anillo se va cerrando mientras juntas los dedos: ves venir el pellizco, y
                # se ilumina sobre algo que se puede tocar
                prog = float(np.clip((0.75 - m.razon) / (0.75 - PINZA_ON), 0, 1))
                sobre = self._bajo(m.x, m.y) is not None
                cv2.circle(c, p, int(18 - 7 * prog), CIAN_FUERTE if sobre else CIAN, 3 if sobre else 2,
                           cv2.LINE_AA)
                cv2.circle(c, p, 3 + int(3 * prog), BLANCO, -1, cv2.LINE_AA)
        # Dos manos escalando una ventana: la línea entre ellas y el tamaño
        dos = [m for m in self.manos.values() if m.pinza and m.d0 and m.esc0 and m.elem is not None]
        if len(dos) >= 2 and dos[0].elem is dos[1].elem:
            a, b = dos[0], dos[1]
            cv2.line(c, (int(a.x), int(a.y)), (int(b.x), int(b.y)), CIAN_FUERTE, 1, cv2.LINE_AA)
            t = _texto(f"{100 * a.elem.tescala / a.esc0:.0f} %", 16, (255, 255, 255), True)
            _pegar(c, t, (a.x + b.x) / 2 - t.shape[1] / 2, (a.y + b.y) / 2 - 30)

    def _dibujar_interfaz(self, c, ahora):
        _pegar(c, _texto("JARVIS", 26, (120, 220, 255), True), 26, 18)
        _pegar(c, _texto(time.strftime("%H:%M"), 22, (255, 255, 255)), 132, 20)
        ayuda = ("Toque: clic · 2 toques: doble clic · pulgar+medio: clic derecho · mantén: arrastrar"
                 if any(e.interactiva for e in self._ventanas())
                 else "Toca una ventana o un icono para abrirlo · pellizca y mueve para acomodar")
        _pegar(c, _texto(ayuda, 14, (230, 230, 230), False, W - 400 - 210), 210, 26)
        if self.zona_a > 0.02:  # "Llevar al escritorio" baja desde arriba al arrastrar una ventana
            zx, zy, zw, zh = self._zona_escritorio()
            encima = any(m.pinza and m.modo == "mover" and m.elem is not None and m.elem.tipo == "ventana"
                         and self._en_zona_escritorio(m.x, m.y) for m in self.manos.values())
            k = 1.1 + 0.03 * math.sin(ahora * 9) if encima else 1.0
            zy = zy - (1 - self.zona_a) * 70
            zx, zw, zh2 = W / 2 - zw * k / 2, zw * k, zh * k
            _vidrio(c, zx, zy, zw, zh2, radio=int(zh2 // 2), tinte=(140, 90, 20) if encima else (60, 40, 25),
                    fuerza=0.55)
            _borde(c, zx, zy, zw, zh2, int(zh2 // 2), CIAN_FUERTE, 3 if encima else 1)
            t = _texto("Soltar aquí: llevar al escritorio", 17, (255, 255, 255), True)
            _pegar(c, t, W / 2 - t.shape[1] / 2, zy + (zh2 - t.shape[0]) / 2)
        if self.aviso and ahora < self.t_aviso:
            # entra deslizándose y se va subiendo
            a = max(0.0, min(1.0, (ahora - self.t_aviso0) / 0.18, (self.t_aviso - ahora) / 0.25))
            yy = 76 - (1 - a) * 26
            t = _texto(self.aviso, 18, (255, 255, 255), True)
            _vidrio(c, W / 2 - t.shape[1] / 2 - 18, yy, t.shape[1] + 36, 40, radio=20, fuerza=0.5)
            _pegar(c, t, W / 2 - t.shape[1] / 2, yy + (40 - t.shape[0]) / 2, alfa=0.3 + 0.7 * a)

    def _dibujar(self, c, ahora, dt=1 / 30):
        self._animar(dt, ahora)
        with self.lock:
            lista = list(self.elementos)
            salientes = [e for e, _t in self.salientes]
        for e in salientes + lista:
            self._pintar(c, e, ahora)
        self._dibujar_interfaz(c, ahora)
        self._dibujar_ondas(c, ahora)
        self._dibujar_manos(c, ahora)

    # --- ventana de la capa ---
    def _preparar_capa(self):
        """Pantalla completa, encima de todo, sin robar el foco y TRANSPARENTE AL CLIC: así los
        clics que generan las manos caen en la ventana real que está debajo."""
        import win32con
        import win32gui
        hwnd = win32gui.FindWindow(None, TITULO)
        if not hwnd:
            return None
        ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW
               | win32con.WS_EX_NOACTIVATE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
        win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)
        self._encima(hwnd)
        return hwnd

    @staticmethod
    def _encima(hwnd):
        import win32con
        import win32gui
        try:
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                  win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        except Exception:
            pass

    def _esc_mantenida(self, ahora):
        """La capa no tiene el foco (es transparente), así que Esc se lee del teclado global;
        hay que mantenerla 1 s para no salir por un Esc dentro de YouTube."""
        if U32.GetAsyncKeyState(0x1B) & 0x8000:
            self.t_esc = self.t_esc or ahora
            return ahora - self.t_esc > 1.0
        self.t_esc = None
        return False

    # --- bucle ---
    def correr(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision as mpv

        # Este hilo trabaja en píxeles reales: la capa cubre el monitor exacto y SetCursorPos
        # usa las mismas coordenadas que las ventanas (con escala de Windows al 125%/150%)
        try:
            U32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        # La webcam se lee del lector compartido de camara.py, el mismo que usan los gestos y la
        # presencia: Windows solo deja abrir la cámara a uno a la vez (si cada quien la abría
        # por su lado, se la quitaban entre sí)
        indice = int(self.conf.get("camara_indice", camara.indice_usuario(self.cfg)))
        lector = camara.lector(indice)
        try:
            try:
                lector.foto(indice)  # espera el primer cuadro (o avisa si la cámara no abre)
            except camara.CamaraError as e:
                raise RuntimeError(str(e)) from e
            # LIVE_STREAM: MediaPipe detecta en su propio hilo mientras aquí se dibuja. En modo
            # VIDEO (en serie) eran ~40 ms de manos + ~28 ms de dibujo por cuadro: 14 fps.
            def al_detectar(resultado, _imagen, _ts):
                self._resultado = [resultado, False]
            opciones = mpv.HandLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=str(MODELO)),
                running_mode=mpv.RunningMode.LIVE_STREAM, num_hands=2, result_callback=al_detectar,
                min_hand_detection_confidence=0.55, min_tracking_confidence=0.5)
            detector = mpv.HandLandmarker.create_from_options(opciones)
            self._armar()
            self.hilo_v = threading.Thread(target=self._hilo_ventanas, daemon=True, name="realidad-ventanas")
            self.hilo_v.start()
            camara.EXTERNO = lambda: self.crudo

            cv2.namedWindow(TITULO, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(TITULO, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            self.listo.set()
            t_inicio, t_fps, cuadros, capa, t_encima, ts = time.time(), time.time(), 0, None, 0.0, 0
            fallos = 0
            ultimo_ts = 0.0
            fondo, t_prev = None, time.time()
            while not self.parar.is_set():
                cuadro, ts_cuadro = lector.ultimo(indice)
                nuevo = cuadro is not None and ts_cuadro != ultimo_ts
                ahora = time.time()
                # Se dibuja con cada cuadro de la cámara y, si la cámara va lenta (poca luz: a
                # veces 1-15 fps), igual cada 1/30 s sobre el último fondo: antes la interfaz
                # entera (ventanas en vivo, animaciones) iba a los fps de la cámara
                if not nuevo and (fondo is None or ahora - t_prev < 1 / 30):
                    time.sleep(0.004)
                    continue
                dt = min(1 / 15, max(1 / 240, ahora - t_prev))
                t_prev = ahora
                if nuevo:
                    ultimo_ts = ts_cuadro
                    self.crudo = cuadro
                    espejo = cv2.flip(cuadro, 1)
                    ch, cw = espejo.shape[:2]
                    alto16 = int(cw * 9 / 16)
                    if alto16 < ch:  # recorta a 16:9 por el centro
                        espejo = espejo[(ch - alto16) // 2:(ch - alto16) // 2 + alto16]
                    chico = cv2.resize(espejo, (640, 360))
                    imagen = mp.Image(image_format=mp.ImageFormat.SRGB,
                                      data=cv2.cvtColor(chico, cv2.COLOR_BGR2RGB))
                    ts = max(ts + 1, int((ahora - t_inicio) * 1000))
                    detector.detect_async(imagen, ts)
                    fondo = cv2.resize(espejo, (W, H), interpolation=cv2.INTER_LINEAR)
                c = fondo.copy()
                try:
                    res = self._resultado
                    if res is not None and not res[1]:
                        res[1] = True
                        self._procesar_manos(res[0], ahora)
                    self._seguir_inercia(dt)
                    self._dibujar(c, ahora, dt)
                    fallos = 0
                except Exception:
                    # Un cuadro con error (p. ej. la ventana que usabas se cerró a medio gesto)
                    # ya no cierra todo el modo: se anota, se suelta lo agarrado y se sigue.
                    fallos += 1
                    if fallos == 1:
                        print("[Realidad: error en un cuadro; sigo]")
                        traceback.print_exc()
                    self._soltar_todo()
                    if fallos > 60:  # algo roto de verdad: mejor salir que congelarse
                        raise
                self.canvas = c
                cv2.imshow(TITULO, c)
                if capa is None:
                    capa = self._preparar_capa()
                elif ahora - t_encima > 2:  # que la barra de tareas no le gane
                    self._encima(capa)
                    t_encima = ahora
                cv2.waitKey(1)
                if self._esc_mantenida(ahora):
                    self.motivo = "Esc mantenida"
                    break
                try:
                    if cv2.getWindowProperty(TITULO, cv2.WND_PROP_VISIBLE) < 1:
                        self.motivo = "la ventana de la capa se cerró"
                        break
                except cv2.error:
                    self.motivo = "la ventana de la capa dejó de existir"
                    break
                cuadros += 1
                if ahora - t_fps >= 2:
                    self.fps, cuadros, t_fps = cuadros / (ahora - t_fps), 0, ahora
        except Exception as e:
            self.error = str(e)
            self.motivo = f"error: {e}"
            traceback.print_exc()
            self.listo.set()
        finally:
            self.motivo = self.motivo or ("orden de apagar" if self.parar.is_set() else "desconocido")
            self.parar.set()
            camara.EXTERNO = None
            for m in self.manos.values():
                if m.raton_abajo:
                    _boton_izq(False)
            if self.hilo_v is not None:
                self.hilo_v.join(timeout=2)  # que no cree una captura nueva tras cerrarlas
            self._restaurar_reales()
            for e in self._ventanas():
                if e.captura:
                    e.captura.cerrar()
            try:
                cv2.destroyWindow(TITULO)
                cv2.waitKey(1)
            except cv2.error:
                pass
            if self.al_salir:
                control.traer_al_frente(self.al_salir)
            print(f"[Realidad aumentada terminada ({self.fps:.0f} fps) · motivo: {self.motivo}]")


def _asegurar_modelo():
    if MODELO.exists() and MODELO.stat().st_size > 1_000_000:
        return
    MODELO.parent.mkdir(parents=True, exist_ok=True)
    print("[Descargando el modelo de manos de MediaPipe (~8 MB)...]")
    urllib.request.urlretrieve(URL_MODELO, MODELO)


def activo():
    hilo = _activo["hilo"]
    return hilo is not None and hilo.is_alive()


@skill("modo_realidad",
       "Activa o desactiva el modo realidad aumentada (tipo gafas de realidad mixta / Vision Pro): "
       "la pantalla completa muestra la cámara de la laptop y encima flotan las ventanas abiertas "
       "en vivo y los iconos de YouTube, Spotify y Steam; el usuario las abre y las usa con las "
       "manos (clic, scroll, arrastrar). Úsala con 'modo realidad aumentada', 'entorno virtual', "
       "'modo holograma', 'modo Vision Pro', y con activar=false para 'sal del modo realidad'.",
       {"activar": {"type": "boolean", "description": "true para entrar, false para salir"}},
       requeridos=[])
def modo_realidad(activar=True):
    if isinstance(activar, str):
        activar = activar.strip().lower() in ("true", "1", "si", "sí", "activar")
    if not activar:
        if not activo():
            return "El modo realidad aumentada no estaba activo."
        _activo["escena"].motivo = "orden de voz"
        _activo["escena"].parar.set()
        _activo["hilo"].join(timeout=5)
        return "Listo, volvimos al escritorio."
    if activo():
        return "El modo realidad aumentada ya está activo."
    restaurar_pendientes()  # si la sesión anterior terminó de golpe con ventanas achicadas
    try:
        _asegurar_modelo()
    except Exception as e:
        return f"No pude descargar el modelo de manos ({type(e).__name__}); revisa el internet."
    escena = Escena(skills._CFG)
    hilo = threading.Thread(target=escena.correr, daemon=True, name="realidad")
    _activo.update(escena=escena, hilo=hilo)
    hilo.start()
    escena.listo.wait(timeout=20)
    if escena.error:
        return f"No pude iniciar la realidad aumentada: {escena.error}"
    return "Realidad aumentada activada."  # corto: antes la frase de bienvenida duraba 10 s


if __name__ == "__main__":
    skills.configurar(json.loads((BASE / "config.json").read_text(encoding="utf-8")))
    apps.iniciar()
    time.sleep(3)
    print(modo_realidad(True))
    while activo():
        time.sleep(0.5)
