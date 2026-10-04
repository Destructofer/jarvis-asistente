"""Gestos con la mano, estilo Iron Man: Jarvis ve tus manos por la cámara de la computadora
(camara.usuario_indice) y reacciona sin que digas nada.

Todo se reconoce EN LA COMPUTADORA con MediaPipe (gratis, sin internet, ~15 ms por cuadro): la
imagen de la cámara nunca sale a la nube por los gestos.

Gestos (config.json → gestos.acciones los cambia; "" = no hace nada):
- ✋ palma abierta, quieta     → "callar": Jarvis deja de hablar al instante
- ☝ dedo índice arriba        → "escuchar": Jarvis te escucha sin que digas su nombre
- 👍 / 👎 pulgar               → "si" / "no": contesta las confirmaciones ("¿lo envío?")
- ✌ victoria                  → "mirame": Jarvis te mira y comenta lo que ve
- 🤟 rock (índice, meñique y pulgar) → "modo_mouse": la mano mueve el mouse; juntar pulgar e
                                 índice = clic. Otro 🤟 (o 20 s sin mano) lo apaga.
- 👋 deslizar la mano a la izquierda / derecha → "siguiente" / "anterior": diapositiva (como
  pasar páginas en el celular); sin presentación, cambia de ventana (gestos.deslizar_sin_presentacion).
Además, cualquier gesto puede lanzar una orden de voz ("orden:pon música") o teclas
("teclas:ctrl+s").

Un gesto cuenta cuando se sostiene ~0.5 s con la mano quieta y no se repite hasta que lo
sueltas: así un ademán al platicar no dispara nada.
"""
import math
import os
import queue
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import camara
import skills
from skills import Callado, skill

# MediaPipe llena la consola de avisos técnicos al arrancar; solo se dejan los errores
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

MODELOS = Path(__file__).parent / "modelos"
URLS = {
    "gesture_recognizer.task": "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
                               "gesture_recognizer/float16/latest/gesture_recognizer.task",
    "blaze_face_short_range.tflite": "https://storage.googleapis.com/mediapipe-models/face_detector/"
                                     "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
}

# Nombres de MediaPipe -> los de config.json
NOMBRES = {"Open_Palm": "palma", "Closed_Fist": "puno", "Pointing_Up": "dedo_arriba",
           "Thumb_Up": "pulgar_arriba", "Thumb_Down": "pulgar_abajo", "Victory": "victoria",
           "ILoveYou": "rock"}
ACCIONES = {"palma": "callar", "dedo_arriba": "escuchar", "pulgar_arriba": "si",
            "pulgar_abajo": "no", "victoria": "mirame", "rock": "modo_mouse", "puno": "",
            "deslizar_izquierda": "siguiente", "deslizar_derecha": "anterior"}
EMOJI = {"palma": "✋", "dedo_arriba": "☝", "pulgar_arriba": "👍", "pulgar_abajo": "👎",
         "victoria": "✌", "rock": "🤟", "puno": "✊", "deslizar_izquierda": "👈",
         "deslizar_derecha": "👉"}

# Los pone genesis.py: acción -> función sin argumentos ("si"/"no" reciben True/False y
# devuelven True si había una confirmación esperando; "orden" recibe el texto)
hooks = {}

_estado = {"hilo": None, "manual": None, "raton": False, "error": ""}
_cola = queue.Queue()


def _cfg():
    return skills._CFG or {}


def _conf():
    return _cfg().get("gestos", {}) or {}


def activo():
    if _estado["manual"] is not None:
        return _estado["manual"]
    return bool(_conf().get("activo", True))


class silencio_nativo:
    """MediaPipe (C++) escribe directo a la consola una docena de avisos técnicos al cargar sus
    modelos, sin hacer caso a GLOG_minloglevel. Mientras se cargan, la salida de errores de
    bajo nivel se manda a la nada (los errores de Python siguen saliendo normal después)."""

    def __enter__(self):
        import sys
        try:
            sys.stderr.flush()
            self._fd = os.dup(2)
            nulo = os.open(os.devnull, os.O_WRONLY)
            os.dup2(nulo, 2)
            os.close(nulo)
        except OSError:
            self._fd = None
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            os.dup2(self._fd, 2)
            os.close(self._fd)
        return False


def modelo(nombre):
    """Ruta del modelo de MediaPipe; lo descarga la primera vez (~8 MB)."""
    ruta = MODELOS / nombre
    if not ruta.exists():
        MODELOS.mkdir(parents=True, exist_ok=True)
        print(f"[Descargando el modelo {nombre} (solo la primera vez)...]")
        temporal = ruta.with_suffix(".descarga")
        urllib.request.urlretrieve(URLS[nombre], temporal)
        temporal.replace(ruta)
    return ruta


# ---------- Geometría de la mano (21 puntos de MediaPipe, x/y de 0 a 1, imagen en espejo) ----------
def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def centro(puntos):
    """Centro de la palma: muñeca y nudillos."""
    ids = (0, 5, 9, 13, 17)
    return (sum(puntos[i][0] for i in ids) / 5, sum(puntos[i][1] for i in ids) / 5)


def dedos_extendidos(puntos):
    """Cuántos dedos (sin el pulgar) están estirados: la punta más lejos de la muñeca que la
    articulación del medio."""
    muneca = puntos[0]
    return sum(1 for punta, medio in ((8, 6), (12, 10), (16, 14), (20, 18))
               if _dist(puntos[punta], muneca) > _dist(puntos[medio], muneca) * 1.1)


def pellizco(puntos):
    """Distancia pulgar-índice relativa al tamaño de la mano (pequeña = juntos)."""
    return _dist(puntos[4], puntos[8]) / max(_dist(puntos[0], puntos[9]), 1e-6)


def cerca(puntos, conf=None):
    """¿La mano está cerca de la laptop (es la tuya)? Una mano lejana, como alguien del público
    levantando la mano para preguntar, sale pequeña y no debe callar a Jarvis."""
    minimo = float((conf or {}).get("tamano_mano", 0.10))
    return puntos is not None and _dist(puntos[0], puntos[9]) >= minimo


class Detector:
    """Convierte lo que MediaPipe ve cuadro a cuadro en EVENTOS: un gesto sostenido o un
    deslizamiento. Separado de la cámara para poder probarlo con datos inventados."""

    def __init__(self, conf=None):
        conf = conf or {}
        self.sostener = float(conf.get("sostener_seg", 0.45))
        self.confianza = float(conf.get("confianza", 0.6))
        self.distancia = float(conf.get("deslizar_distancia", 0.28))
        self.ventana = float(conf.get("deslizar_seg", 0.6))
        self.actual, self.desde, self.ancla = None, 0.0, None
        self.disparado, self.visto_disparado = None, 0.0
        self.sin_mano_desde = None
        self.rastro = deque(maxlen=40)   # (t, x, y) del centro de la palma
        self.ultimo_desliz = -10.0

    def actualizar(self, t, gesto, score, puntos):
        """gesto: nombre de MediaPipe ('Open_Palm'...) o None; puntos: 21 (x, y) o None si no
        hay mano. Devuelve el evento ('palma', 'deslizar_izquierda'...) o None."""
        if puntos is None:
            self.rastro.clear()
            if self.sin_mano_desde is None:
                self.sin_mano_desde = t
            if t - self.sin_mano_desde > 0.3:
                self.actual = self.disparado = None
            return None
        self.sin_mano_desde = None
        cx, cy = centro(puntos)
        self.rastro.append((t, cx, cy))

        evento = self._deslizar(t, puntos)
        if evento:
            return evento

        nombre = NOMBRES.get(gesto) if gesto and score >= self.confianza else None
        if nombre is not None and nombre == self.disparado:
            self.visto_disparado = t
        elif self.disparado is not None and t - self.visto_disparado > 0.6:
            self.disparado = None  # lo soltó de verdad (no un cuadro mal reconocido)
        if nombre != self.actual:
            self.actual, self.desde, self.ancla = nombre, t, (cx, cy)
            return None
        if nombre is None or nombre == self.disparado:
            return None
        if _dist(self.ancla, (cx, cy)) > 0.08:
            self.desde, self.ancla = t, (cx, cy)  # la mano se mueve: es un ademán, no un gesto
            return None
        if t - self.desde >= self.sostener and t - self.ultimo_desliz > 1.2:
            self.disparado, self.visto_disparado = nombre, t
            return nombre
        return None

    def _deslizar(self, t, puntos):
        if t - self.ultimo_desliz < 1.0 or dedos_extendidos(puntos) < 3:
            return None
        recientes = [(ts, x, y) for ts, x, y in self.rastro if t - ts <= self.ventana]
        if len(recientes) < 3:
            return None
        _, x0, y0 = recientes[0]
        _, x1, y1 = recientes[-1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) >= self.distancia and abs(dy) <= 0.5 * abs(dx):
            self.ultimo_desliz = t
            self.rastro.clear()
            self.actual, self.disparado = None, None
            return "deslizar_izquierda" if dx < 0 else "deslizar_derecha"
        return None


class Raton:
    """Modo mouse: la punta del índice mueve el cursor y juntar pulgar e índice hace clic.
    Solo una zona central de la cámara cubre toda la pantalla (no hay que estirarse)."""

    def __init__(self, ancho, alto, conf=None):
        conf = conf or {}
        self.ancho, self.alto = ancho, alto
        self.zona = conf.get("zona_mouse", [0.2, 0.15, 0.8, 0.7])
        self.suavizado = float(conf.get("suavizado_mouse", 0.35))
        self.pos = None
        self.apretado = False

    def actualizar(self, puntos):
        """Lista de acciones: ('mover', x, y) y/o ('clic',)."""
        acciones = []
        p = pellizco(puntos)
        if not self.apretado and p < 0.28:
            self.apretado = True
            acciones.append(("clic",))
        elif self.apretado and p > 0.42:
            self.apretado = False
        if self.apretado:
            return acciones  # el cursor se congela mientras haces clic (si no, tiembla)
        x0, y0, x1, y1 = self.zona
        fx = min(max((puntos[8][0] - x0) / (x1 - x0), 0.0), 1.0)
        fy = min(max((puntos[8][1] - y0) / (y1 - y0), 0.0), 1.0)
        objetivo = (fx * (self.ancho - 1), fy * (self.alto - 1))
        if self.pos is None:
            self.pos = objetivo
        else:
            a = self.suavizado
            self.pos = (self.pos[0] + a * (objetivo[0] - self.pos[0]),
                        self.pos[1] + a * (objetivo[1] - self.pos[1]))
        acciones.append(("mover", int(self.pos[0]), int(self.pos[1])))
        return acciones


# ---------- Acciones ----------
def _mouse(accion):
    import ctypes
    u = ctypes.windll.user32
    if accion[0] == "mover":
        u.SetCursorPos(accion[1], accion[2])
    elif accion[0] == "clic":
        u.mouse_event(0x0002, 0, 0, 0, 0)  # botón izquierdo abajo
        u.mouse_event(0x0004, 0, 0, 0, 0)  # y arriba


def alternar_raton(encender=None):
    _estado["raton"] = (not _estado["raton"]) if encender is None else bool(encender)
    _avisar("🖱 Modo mouse " + ("activado: junta pulgar e índice para hacer clic"
                                if _estado["raton"] else "desactivado"))
    if "sonido" in hooks:
        hooks["sonido"]()
    return _estado["raton"]


def _avisar(texto):
    print(f"[{texto}]")
    try:
        import hud
        hud.subtitulo(texto)
    except Exception:
        pass


def _deslizar(accion):
    import presentacion
    try:
        if presentacion.en_curso():
            import control
            control.ejecutor("presentacion", {"accion": accion})
            return
    except Exception as e:
        print(f"[Gesto: no pude mover la presentación: {type(e).__name__}]")
        return
    if _conf().get("deslizar_sin_presentacion", "ventanas") == "ventanas":
        import pyautogui
        pyautogui.hotkey("alt", "tab") if accion == "siguiente" else pyautogui.hotkey("alt", "shift", "tab")


def ejecutar(evento):
    """Lo que hace un evento según config.json → gestos.acciones."""
    accion = {**ACCIONES, **(_conf().get("acciones") or {})}.get(evento, "")
    if not accion:
        return
    _avisar(f"Gesto {EMOJI.get(evento, '')} {evento.replace('_', ' ')} → {accion}")
    if accion == "modo_mouse":
        alternar_raton()
    elif accion in ("siguiente", "anterior"):
        _deslizar(accion)
    elif accion in ("si", "no"):
        fn = hooks.get("confirmar")
        if fn is None or not fn(accion == "si"):
            print("[Gesto: no había nada que confirmar]")
    elif accion.startswith("orden:"):
        if "orden" in hooks:
            hooks["orden"](accion[len("orden:"):].strip())
    elif accion.startswith("teclas:"):
        import pyautogui
        pyautogui.hotkey(*[t.strip() for t in accion[len("teclas:"):].split("+") if t.strip()])
    elif accion in hooks:
        hooks[accion]()
    else:
        print(f"[Gesto: no conozco la acción '{accion}']")


def _trabajador():
    """Ejecuta las acciones en orden y fuera del hilo del video (algunas tardan, como hablar
    con PowerPoint) para no perder cuadros mientras tanto."""
    ultima = {}
    while True:
        evento = _cola.get()
        ahora = time.time()
        if ahora - ultima.get(evento, 0) < float(_conf().get("pausa_entre_gestos_seg", 1.2)):
            continue
        ultima[evento] = ahora
        try:
            ejecutar(evento)
        except Exception as e:
            print(f"[Gesto: la acción de '{evento}' falló: {type(e).__name__}: {str(e)[:100]}]")


# ---------- Bucle de video ----------
def _reconocedor():
    import mediapipe as mp  # noqa: F401  (carga las librerías nativas)
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    opciones = mp_vision.GestureRecognizerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(modelo("gesture_recognizer.task"))),
        running_mode=mp_vision.RunningMode.VIDEO, num_hands=1,
        min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5)
    with silencio_nativo():
        return mp_vision.GestureRecognizer.create_from_options(opciones)


def analizar(reconocedor, cuadro_bgr, t_ms):
    """(gesto, score, puntos) de un cuadro; puntos=None si no hay mano. El cuadro se voltea
    en espejo: así "derecha" es TU derecha, como al verte en un espejo."""
    import cv2
    import mediapipe as mp
    rgb = cv2.cvtColor(cv2.flip(cuadro_bgr, 1), cv2.COLOR_BGR2RGB)
    r = reconocedor.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), t_ms)
    if not r.hand_landmarks:
        return None, 0.0, None
    puntos = [(p.x, p.y) for p in r.hand_landmarks[0]]
    if r.gestures and r.gestures[0]:
        g = r.gestures[0][0]
        nombre = None if g.category_name in ("None", "") else g.category_name
        return nombre, float(g.score), puntos
    return None, 0.0, puntos


def _bucle():
    try:
        reconocedor = _reconocedor()
    except Exception as e:
        _estado["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        print(f"[Gestos: no pude cargar MediaPipe ({_estado['error']}). "
              "Instálalo con: .venv\\Scripts\\pip install mediapipe]")
        return
    import ctypes
    detector = Detector(_conf())
    u = ctypes.windll.user32
    raton = Raton(u.GetSystemMetrics(0), u.GetSystemMetrics(1), _conf())
    fps = float(_conf().get("fps", 15))
    ultimo_ts, ultima_mano, inicio = 0.0, time.time(), time.monotonic()
    print("[Gestos activos: ✋ calla · ☝ te escucho · 👍👎 sí/no · ✌ mírame · 🤟 mouse · 👋 deslizar]")
    while activo():
        if camara.EXTERNO is not None:  # realidad aumentada activa: las manos son suyas
            time.sleep(0.3)
            continue
        try:
            cuadro, ts = camara.cuadro_usuario(_cfg())
        except camara.CamaraError as e:
            print(f"[Gestos: {e} Reintento en 10 s.]")
            time.sleep(10)
            continue
        if cuadro is None or ts == ultimo_ts:
            time.sleep(0.01)
            continue
        ultimo_ts = ts
        t_inicio = time.monotonic()
        try:
            gesto, score, puntos = analizar(reconocedor, cuadro, int((t_inicio - inicio) * 1000))
        except Exception as e:
            print(f"[Gestos: falló el análisis: {type(e).__name__}: {str(e)[:100]}]")
            time.sleep(1)
            continue
        if not cerca(puntos, _conf()):
            gesto, score, puntos = None, 0.0, None
        ahora = time.time()
        if puntos is not None:
            ultima_mano = ahora
        if _estado["raton"]:
            if puntos is not None:
                # en modo mouse solo cuenta otro 🤟 (para salir); lo demás mueve el cursor
                if detector.actualizar(ahora, gesto, score, puntos) == "rock":
                    _cola.put("rock")
                elif NOMBRES.get(gesto) != "rock":
                    for accion in raton.actualizar(puntos):
                        _mouse(accion)
            elif ahora - ultima_mano > float(_conf().get("apagar_mouse_seg", 20)):
                alternar_raton(False)
        else:
            raton.pos = None
            evento = detector.actualizar(ahora, gesto, score, puntos)
            if evento:
                _cola.put(evento)
        time.sleep(max(0.0, 1.0 / fps - (time.monotonic() - t_inicio)))
    _estado["raton"] = False


def iniciar():
    """Arranca el hilo de gestos (si está activo en config.json y no estaba corriendo)."""
    hilo = _estado["hilo"]
    if not activo() or (hilo is not None and hilo.is_alive()):
        return False
    if not any(t.name == "gestos-acciones" for t in threading.enumerate()):
        threading.Thread(target=_trabajador, daemon=True, name="gestos-acciones").start()
    _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="gestos")
    _estado["hilo"].start()
    return True


@skill("gestos",
       "Activa o desactiva el control con gestos de la mano por la cámara (estilo Iron Man) o "
       "el modo mouse con la mano: 'activa los gestos', 'deja de ver mis manos', 'modo mouse', "
       "'quita el mouse con la mano', '¿qué gestos hay?'.",
       {"accion": {"type": "string", "enum": ["activar", "desactivar", "mouse", "quitar_mouse", "lista"],
                   "description": "Qué hacer"}},
       requeridos=["accion"])
def gestos(accion="activar"):
    if accion == "lista":
        return ("Palma abierta me callo; dedo índice arriba te escucho; pulgar arriba o abajo "
                "es sí o no; con la V de victoria te miro; con la mano de rock controlas el mouse, "
                "y deslizando la mano cambias de diapositiva.")
    if accion in ("mouse", "quitar_mouse"):
        if accion == "mouse" and not (_estado["hilo"] and _estado["hilo"].is_alive()):
            _estado["manual"] = True
            iniciar()
        alternar_raton(accion == "mouse")
        return Callado("Mouse con la mano activado." if accion == "mouse" else "Mouse con la mano desactivado.")
    _estado["manual"] = accion == "activar"
    if _estado["manual"]:
        iniciar()
        return "Listo, ya veo tus manos." if not _estado["error"] else \
            "No pude activar los gestos: falta MediaPipe."
    _estado["raton"] = False
    return "Dejo de ver tus manos."
