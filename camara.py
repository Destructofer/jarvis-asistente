"""De dónde saca Jarvis la imagen de "lo que ven tus lentes".

Hoy Meta NO deja leer la cámara de las Ray-Ban desde una PC por Bluetooth: la cámara solo sale
por las apps de Meta (videollamadas de WhatsApp/Messenger, transmisiones de Instagram) o por su
SDK para apps de celular (Wearables Device Access Toolkit, todavía en vista previa). Por eso
aquí hay varias "fuentes" intercambiables en config.json → camara.fuente:

- "webcam": cualquier cámara que Windows vea. La recomendada es la CÁMARA VIRTUAL DE OBS
  capturando la ventana de la videollamada de WhatsApp en la que los lentes transmiten su
  vista. OBS captura la ventana aunque quede tapada por PowerPoint en pantalla completa.
- "ventana": sin OBS; captura directamente la ventana cuyo título se parezca a
  camara.ventana_titulo (p. ej. "WhatsApp"). Funciona aunque esté detrás de otra ventana,
  pero no si está minimizada.
- "http": una app de celular (por ejemplo una hecha con el SDK de Meta) manda fotos JPEG por
  POST a http://IP-DE-TU-PC:8765/frame. Es el camino "oficial" a futuro.
- "pantalla": la pantalla principal (para que Jarvis vea tu propia pantalla).
- "archivo": una imagen fija, para probar la visión sin lentes.

Aparte está la cámara de la COMPUTADORA, la que te ve a ti (camara.usuario_indice, normalmente
0): la usan gestos.py (manos, estilo Iron Man) y presencia.py (Jarvis te ve). Cada cámara tiene
su propio lector, así que puede estar viendo los lentes y tu webcam a la vez.
"""
import io
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

ULTIMA_PATH = Path(__file__).parent / "datos" / "ultima_vista.jpg"
MAX_LADO = 1024


class CamaraError(Exception):
    """El mensaje ya viene listo para decírselo al usuario."""


def _conf(cfg):
    return cfg.get("camara", {}) or {}


def _reducir(img):
    img = img.convert("RGB")
    img.thumbnail((MAX_LADO, MAX_LADO))
    return img


def _recortar(img, recorte):
    """recorte = [x0, y0, x1, y1] en fracciones (0 a 1): quita barras y botones de la
    videollamada para que Jarvis vea solo el video de los lentes."""
    if not recorte or list(recorte) == [0, 0, 1, 1]:
        return img
    x0, y0, x1, y1 = [float(v) for v in recorte]
    w, h = img.size
    return img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))


def _casi_negra(img):
    muestra = img.convert("L").resize((32, 32))
    return max(muestra.getdata()) < 12


# ---------- Webcam / cámara virtual de OBS ----------
class _Lector:
    """Lee la cámara en un hilo y guarda solo el último cuadro. Leer bajo pedido abriendo y
    cerrando la cámara tarda 1-2 s y a veces entrega un cuadro viejo del búfer; así la foto
    siempre es de "ahora". Se apaga sola tras un rato sin uso para no gastar batería/CPU."""

    def __init__(self):
        self.cuadro = None
        self.ts = 0.0
        self.ultimo_uso = 0.0
        self.indice = None
        self.hilo = None
        self.error = ""
        self._parar = threading.Event()

    def _bucle(self, indice):
        import cv2
        cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(indice)
        if not cap.isOpened():
            self.error = f"No pude abrir la cámara número {indice}."
            return
        try:
            while not self._parar.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    self.cuadro = frame
                    self.ts = time.time()
                else:
                    time.sleep(0.05)
                if time.time() - self.ultimo_uso > 180:
                    break
        finally:
            cap.release()

    def asegurar(self, indice):
        self.ultimo_uso = time.time()
        if self.hilo is not None and self.hilo.is_alive() and self.indice == indice:
            return
        self.parar()
        self._parar.clear()
        self.indice, self.error, self.cuadro = indice, "", None
        self.hilo = threading.Thread(target=self._bucle, args=(indice,), daemon=True,
                                     name="camara")
        self.hilo.start()

    def parar(self):
        self._parar.set()
        if self.hilo is not None:
            self.hilo.join(timeout=2)

    def ultimo(self, indice):
        """(cuadro BGR de OpenCV, momento) más reciente sin convertir, para lo que analiza
        video continuo (gestos). (None, 0) si aún no hay imagen."""
        self.asegurar(indice)
        if self.error:
            raise CamaraError(self.error)
        return self.cuadro, self.ts

    def foto(self, indice, espera=6.0):
        self.asegurar(indice)
        limite = time.time() + espera
        while time.time() < limite:
            if self.error:
                raise CamaraError(self.error)
            if self.cuadro is not None and time.time() - self.ts < 1.5:
                import cv2
                rgb = cv2.cvtColor(self.cuadro, cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb)
            time.sleep(0.05)
        raise CamaraError("La cámara no entregó imagen. Revisa que OBS tenga iniciada la "
                          "cámara virtual o que el número de cámara sea el correcto.")


_lectores = {}   # índice de cámara -> _Lector
_lock_lectores = threading.Lock()


def lector(indice):
    with _lock_lectores:
        if indice not in _lectores:
            _lectores[indice] = _Lector()
        return _lectores[indice]


def indice_usuario(cfg):
    """La cámara de la computadora, la que te ve a ti (no la de los lentes)."""
    return int(_conf(cfg).get("usuario_indice", 0))


def cuadro_usuario(cfg):
    """(cuadro BGR, momento) de tu webcam, para gestos y presencia."""
    return lector(indice_usuario(cfg)).ultimo(indice_usuario(cfg))


def foto_usuario(cfg):
    """Foto PIL de tu webcam, ya reducida (para preguntarle al modelo de visión)."""
    img = _reducir(lector(indice_usuario(cfg)).foto(indice_usuario(cfg)))
    try:
        ULTIMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        img.save(ULTIMA_PATH, "JPEG", quality=85)
    except OSError:
        pass
    return img

# Cuando otra parte de Jarvis ya tiene la webcam abierta (el modo de realidad aumentada),
# pone aquí una función que devuelve su último cuadro (BGR de OpenCV). Windows no deja abrir
# la misma cámara dos veces, así que mirar/escanear toman la foto de ahí.
EXTERNO = None


def calentar(cfg):
    """Abre la cámara por adelantado (al activar el modo expositor) para que la primera
    pregunta no espere a que arranque."""
    c = _conf(cfg)
    if c.get("fuente", "webcam") == "webcam":
        try:
            indice = int(c.get("webcam_indice", 0))
            lector(indice).asegurar(indice)
        except Exception:
            pass
    elif c.get("fuente") == "http":
        iniciar_servidor(cfg)


# ---------- Ventana (captura directa) ----------
def _capturar_ventana(titulo):
    import ctypes

    import win32gui
    import win32ui

    import control
    h, t, p = control.buscar_ventana(titulo)
    if h is None or p < 0.5:
        raise CamaraError(f"No encontré una ventana abierta que se llame como '{titulo}'. "
                          "Abre la videollamada de los lentes en la PC.")
    if win32gui.IsIconic(h):
        raise CamaraError(f"La ventana '{t}' está minimizada; déjala abierta (puede estar detrás).")
    x0, y0, x1, y1 = win32gui.GetWindowRect(h)
    w, alto = x1 - x0, y1 - y0
    hdc = win32gui.GetWindowDC(h)
    mfc = win32ui.CreateDCFromHandle(hdc)
    mem = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, alto)
    mem.SelectObject(bmp)
    try:
        # 2 = PW_RENDERFULLCONTENT: pide a la app que se dibuje aunque esté tapada (necesario
        # en apps modernas como WhatsApp, que dibujan con DirectX)
        ctypes.windll.user32.PrintWindow(h, mem.GetSafeHdc(), 2)
        info = bmp.GetInfo()
        datos = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), datos, "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(h, hdc)
    if _casi_negra(img):
        # Algunas apps no se dejan dibujar así. Último intento: copiar de la pantalla, pero SOLO
        # si la ventana está al frente; si no, lo que hay en ese rectángulo es otra cosa (la
        # presentación a pantalla completa) y Jarvis "vería" la diapositiva creyendo que es la
        # vista de los lentes.
        if win32gui.GetForegroundWindow() != h:
            raise CamaraError(f"La ventana '{t}' sale en negro cuando está detrás de otra. Usa OBS "
                              "con su cámara virtual (fuente 'webcam'), que sí la captura tapada.")
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True)
        if _casi_negra(img):
            raise CamaraError(f"La ventana '{t}' sale en negro. Usa OBS con su cámara virtual "
                              "(fuente 'webcam'), que sí la puede capturar.")
    return img


# ---------- HTTP (una app del celular manda las fotos) ----------
_http = {"img": None, "ts": 0.0, "servidor": None}


def iniciar_servidor(cfg):
    if _http["servidor"] is not None:
        return
    c = _conf(cfg)
    puerto = int(c.get("http_puerto", 8765))
    token = str(c.get("http_token", ""))
    # Sin token, el receptor solo acepta fotos de ESTA computadora: en el Wi-Fi de un evento
    # cualquiera podría mandarle imágenes a Jarvis (y hacerle "ver" lo que quiera).
    host = "0.0.0.0" if token else "127.0.0.1"
    if not token:
        print("[Cámara HTTP: sin camara.http_token solo acepto imágenes de esta misma PC. "
              "Pon un token en config.json para recibirlas del celular.]")

    class Manejador(BaseHTTPRequestHandler):
        def _autorizado(self):
            return not token or self.headers.get("X-Token") == token

        def do_POST(self):
            if self.path.split("?")[0] != "/frame" or not self._autorizado():
                self.send_response(403)
                self.end_headers()
                return
            largo = int(self.headers.get("Content-Length", 0))
            if not 0 < largo < 8_000_000:
                self.send_response(413)
                self.end_headers()
                return
            try:
                img = Image.open(io.BytesIO(self.rfile.read(largo)))
                img.load()
                _http["img"], _http["ts"] = img, time.time()
                self.send_response(204)
            except Exception:
                self.send_response(400)
            self.end_headers()

        def do_GET(self):
            edad = time.time() - _http["ts"] if _http["ts"] else None
            texto = ("Jarvis: recibiendo imágenes (última hace %.1f s)" % edad) if edad is not None \
                else "Jarvis: esperando imágenes en POST /frame"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(texto.encode("utf-8"))

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer((host, puerto), Manejador)
    except OSError as e:
        print(f"[No pude abrir el puerto {puerto} para la cámara: {e}]")
        return
    _http["servidor"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True, name="camara-http").start()
    print(f"[Cámara: esperando imágenes en http://<IP de esta PC>:{puerto}/frame]")


# ---------- Punto de entrada ----------
def capturar(cfg, fuente=None):
    """Imagen PIL de lo que "ven" los lentes (o la pantalla). Lanza CamaraError con un mensaje
    listo para decir si algo falla. Guarda una copia en datos/ultima_vista.jpg (el HUD la
    muestra y sirve para revisar después qué vio Jarvis)."""
    c = _conf(cfg)
    fuente = fuente or c.get("fuente", "webcam")
    # Con la realidad aumentada encendida, ella es la dueña de la webcam: la foto sale de su video
    cuadro = EXTERNO() if (EXTERNO is not None and fuente == "webcam") else None
    if cuadro is not None:
        import cv2
        img = Image.fromarray(cv2.cvtColor(cuadro, cv2.COLOR_BGR2RGB))
    elif fuente == "webcam":
        indice = int(c.get("webcam_indice", 0))
        img = lector(indice).foto(indice)
    elif fuente == "ventana":
        img = _capturar_ventana(c.get("ventana_titulo", "WhatsApp"))
    elif fuente == "http":
        iniciar_servidor(cfg)
        if _http["img"] is None or time.time() - _http["ts"] > 10:
            raise CamaraError("No me ha llegado ninguna imagen reciente del celular.")
        img = _http["img"]
    elif fuente == "pantalla":
        from PIL import ImageGrab
        img = ImageGrab.grab()
    elif fuente == "archivo":
        ruta = os.path.expandvars(c.get("archivo", ""))
        if not os.path.isfile(ruta):
            raise CamaraError("La imagen de prueba de camara.archivo no existe.")
        img = Image.open(ruta)
    else:
        raise CamaraError(f"No conozco la fuente de cámara '{fuente}'.")
    if fuente != "pantalla":
        img = _recortar(img, c.get("recorte"))
    img = _reducir(img)
    try:
        ULTIMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        img.save(ULTIMA_PATH, "JPEG", quality=85)
    except OSError:
        pass
    return img
