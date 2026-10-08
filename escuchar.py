"""Oídos de Jarvis: micrófono, palabra de activación y transcripción.

El micrófono queda ABIERTO todo el tiempo en segundo plano (clase Microfono) y reparte el
audio a quien lo necesite (el detector de "Hey Jarvis", la grabación de una orden, el que
vigila si interrumpen a Jarvis). Antes se abría y cerraba en cada orden: eso dejaba huecos en
los que se perdía lo que decías y sumaba ~0.1-0.3 s de arranque cada vez.

Además:
- Micrófono de respaldo: si el principal se queda mudo (p. ej. el cable virtual de la
  videollamada de los lentes cuando se cae la llamada) o se desconecta (Bluetooth), se pasa
  solo al de respaldo, y se regresa en cuanto el principal vuelve a tener sonido.
- Memoria de los últimos ~45 s: permite "Jarvis, responde la pregunta que me hicieron".
- Transcripción con filtro: se descartan los segmentos que Whisper "inventa" sobre ruido
  ("Subtítulos realizados por la comunidad de Amara.org", "Gracias por ver el video"...).
"""
import json
import os
import queue
import re
import sysconfig
import threading
import time
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import sounddevice as sd

import skills
from skills import skill

SAMPLE_RATE = 16000
BLOQUE = 1280                      # 80 ms: lo que espera openWakeWord
SEG_BLOQUE = BLOQUE / SAMPLE_RATE
CONFIG_PATH = Path(__file__).parent / "config.json"
SILENCIO_DIGITAL = 1e-5            # por debajo de esto el "audio" son ceros: nadie manda nada
_modelos = {}

# Cuando está activo (icono de bandeja → Pausar), Jarvis no escucha nada (el micrófono se cierra)
PAUSA = threading.Event()

# Se activa para cortar una escucha en curso (p. ej. cuando la orden llegó escrita en la ventana)
CANCELAR = threading.Event()

# Lo activa el icono de la bandeja ("Escribir una orden"): despierta a Jarvis sin la palabra de
# activación, para que se pueda usar por teclado aunque el micrófono no esté oyendo nada.
DESPERTAR = threading.Event()

# Lo activa genesis.py cuando Jarvis habló por iniciativa propia (el observador preguntó si
# hay dudas): la espera de la palabra de activación se corta para escuchar la respuesta de
# la persona sin que nadie tenga que decir "Jarvis".
CONVERSAR = threading.Event()
A_CONVERSAR = object()   # lo que devuelven esperar_palabra / esperar_oww en ese caso

# Micrófono principal y de respaldo (índices de sounddevice). None en el principal = el de
# Windows. Se fijan desde config.json → mic_dispositivo / mic_respaldo con iniciar().
DISPOSITIVO = None
RESPALDO = None

# Tras hablar, Jarvis ignora el micrófono un momento: si su propia voz sale por bocinas que el
# micrófono alcanza a oír (o regresa por la videollamada de los lentes), no se escucha a sí mismo.
IGNORAR_HASTA = 0.0

# Segundos de silencio que esperan para dar por terminada tu frase (config.json →
# silencio_seg). Menos = responde antes, pero si haces pausas al hablar te puede cortar.
SILENCIO_SEG = 0.8

# Lo pone genesis.py: el "bip" y el HUD en cuanto se detecta la palabra (sin esperar la orden)
AL_ACTIVAR = None

ULTIMA_TRANSCRIPCION = {"seg": 0.0, "origen": ""}   # para los tiempos que muestra genesis.py
ULTIMA_ORDEN = {"inicio": 0.0, "fin": 0.0}          # cuándo empezó/terminó la última orden

# Modo Whisper: todo lo que se transcribe buscando la palabra de activación y NO era para
# Jarvis (una pregunta del público, lo que explicabas) queda aquí unos minutos.
ESCUCHADO = deque(maxlen=60)   # (momento, texto)


def ignorar_por(segundos):
    global IGNORAR_HASTA
    IGNORAR_HASTA = max(IGNORAR_HASTA, time.time() + segundos)


def _cfg():
    return skills._CFG or {}


# ---------- Dispositivos ----------
def listar_dispositivos():
    """(índice, nombre) de cada entrada de audio que Windows ve, sea el micrófono del equipo,
    unos audífonos/lentes Bluetooth o un cable virtual (VB-Cable)."""
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def _entrada_predeterminada():
    try:
        i = sd.default.device[0]
        if i is not None and i >= 0:
            return int(i)
        return int(sd.query_devices(kind="input")["index"])
    except Exception:
        return None


def resolver_dispositivo(valor, avisar=True):
    """config.json → índice actual. Acepta el nombre guardado (lo normal) o un número (configs
    viejas). Si el aparato ya no está conectado, None: se usa el micrófono de Windows en vez
    de quedarse sordo."""
    if valor in (None, ""):
        return None
    entradas = listar_dispositivos()
    if isinstance(valor, int) or str(valor).strip().isdigit():
        i = int(valor)
        return i if any(i == j for j, _ in entradas) else None
    for i, n in entradas:
        if n == valor:
            return i
    for i, n in entradas:  # MME recorta los nombres largos: se acepta que uno empiece como el otro
        if n.startswith(valor[:25]) or valor.startswith(n[:25]):
            return i
    if avisar:
        print(f"[El micrófono '{valor}' no está conectado; uso el de Windows por defecto.]")
    return None


def _nombre_dispositivo(indice):
    try:
        return sd.query_devices(indice if indice is not None else _entrada_predeterminada())["name"]
    except Exception:
        return "micrófono de Windows"


# ---------- Micrófono continuo ----------
class _Fuente:
    def __init__(self, clave, indice, nombre_config=""):
        self.clave, self.indice, self.nombre_config = clave, indice, nombre_config
        self.stream = None
        self.ultimo_bloque = 0.0
        self.ultimo_sonido = 0.0
        self.reintento = 0.0


class Microfono:
    """Captura continua con respaldo automático. Cada consumidor se "suscribe" y recibe su
    propia copia de los bloques de 80 ms que llegan después de suscribirse."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = []
        self._anillo = deque(maxlen=int(45 / SEG_BLOQUE))
        self._fuentes = {}
        self.activa = "principal"
        self.silencio_respaldo = 8.0
        self._vigia = None
        self._corriendo = False
        self._pausado = False

    # --- ciclo de vida ---
    def iniciar(self, principal=None, respaldo=None, nombre_principal="", nombre_respaldo=""):
        self.detener()
        self._fuentes = {"principal": _Fuente("principal", principal, nombre_principal)}
        if respaldo is not None and respaldo != principal:
            self._fuentes["respaldo"] = _Fuente("respaldo", respaldo, nombre_respaldo)
        self.activa = "principal"
        self._corriendo = True
        for f in self._fuentes.values():
            self._abrir(f)
        self._vigia = threading.Thread(target=self._vigilar, daemon=True, name="microfono")
        self._vigia.start()

    def asegurar(self):
        """Arranca con el micrófono de Windows si nadie lo inició (pruebas, diagnóstico)."""
        if not self._corriendo:
            self.iniciar(DISPOSITIVO, None)

    def detener(self):
        self._corriendo = False
        for f in self._fuentes.values():
            self._cerrar(f)

    def _abrir(self, f):
        self._cerrar(f)
        try:
            f.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      blocksize=BLOQUE, device=f.indice,
                                      callback=lambda d, n, t, s, fuente=f: self._llega(fuente, d))
            f.stream.start()
            f.ultimo_bloque = time.time()
            return True
        except Exception as e:
            print(f"[No pude abrir el micrófono {f.clave} ({_nombre_dispositivo(f.indice)}): "
                  f"{type(e).__name__}: {str(e)[:80]}]")
            f.stream = None
            return False

    @staticmethod
    def _cerrar(f):
        if f.stream is not None:
            try:
                f.stream.abort()
                f.stream.close()
            except Exception:
                pass
            f.stream = None

    # --- audio que llega (hilo de PortAudio: debe ser rápido) ---
    def _llega(self, f, datos):
        if self._fuentes.get(f.clave) is not f:
            return  # un bloque atrasado de un flujo que ya se reemplazó
        ahora = time.time()
        bloque = datos[:, 0].copy()
        f.ultimo_bloque = ahora
        if float(np.max(np.abs(bloque))) > SILENCIO_DIGITAL:
            f.ultimo_sonido = ahora
        if f.clave != self.activa:
            return
        self._anillo.append((ahora, bloque))
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((ahora, bloque))
            except queue.Full:  # un consumidor lento no debe frenar a los demás
                try:
                    q.get_nowait()
                    q.put_nowait((ahora, bloque))
                except (queue.Empty, queue.Full):
                    pass

    # --- vigilante: pausa, reconexión y cambio a respaldo ---
    def _vigilar(self):
        while self._corriendo:
            time.sleep(0.4)
            try:
                self._revisar()
            except Exception as e:
                print(f"[Error vigilando el micrófono: {type(e).__name__}: {str(e)[:100]}]")

    def _revisar(self):
        ahora = time.time()
        if PAUSA.is_set():
            if not self._pausado:
                self._pausado = True
                for f in self._fuentes.values():
                    self._cerrar(f)
            return
        if self._pausado:
            self._pausado = False
            for f in self._fuentes.values():
                self._abrir(f)
        for f in self._fuentes.values():
            vivo = f.stream is not None and f.stream.active and ahora - f.ultimo_bloque < 2.0
            if not vivo and ahora >= f.reintento:
                # Se desconectó (Bluetooth, USB) o nunca abrió: reintentar por NOMBRE, porque
                # Windows renumera los dispositivos al conectar/desconectar
                f.reintento = ahora + 3.0
                if f.nombre_config:
                    nuevo = resolver_dispositivo(f.nombre_config, avisar=False)
                    if nuevo is not None:
                        f.indice = nuevo
                self._abrir(f)
        p, r = self._fuentes.get("principal"), self._fuentes.get("respaldo")
        principal_ok = (p is not None and p.stream is not None and p.stream.active
                        and ahora - p.ultimo_bloque < 2.0
                        and ahora - p.ultimo_sonido < self.silencio_respaldo)
        deseada = "principal" if principal_ok or r is None else "respaldo"
        if deseada != self.activa:
            self.activa = deseada
            nombre = _nombre_dispositivo(self._fuentes[deseada].indice)
            print(f"[Micrófono: ahora escucho por el {deseada} ({nombre})]")

    # --- consumidores ---
    def suscribir(self, maximo=250):
        self.asegurar()
        q = queue.Queue(maxsize=maximo)
        with self._lock:
            self._subs.append(q)
        return q

    def desuscribir(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def reciente(self, segundos, hasta=None):
        """Audio de los últimos 'segundos' (del micrófono activo), opcionalmente hasta un
        momento dado (para no incluir la propia orden)."""
        hasta = hasta or time.time()
        desde = hasta - segundos
        partes = [b for t, b in list(self._anillo) if desde <= t <= hasta]
        return np.concatenate(partes) if partes else np.zeros(0, np.float32)

    def estado(self):
        return {k: {"dispositivo": _nombre_dispositivo(f.indice),
                    "abierto": bool(f.stream is not None and f.stream.active),
                    "con_sonido_hace": round(time.time() - f.ultimo_sonido, 1) if f.ultimo_sonido else None}
                for k, f in self._fuentes.items()} | {"activo": self.activa}


MIC = Microfono()


# ---------- ¿Está sonando algo en la computadora? ----------
# Con un video o música sonando, el micrófono oye a otras personas hablando. Sin esto, Jarvis
# se activaba con palabras del video que "se parecían" a su nombre y luego, en el modo
# conversación, le contestaba al video durante minutos. Windows dice cuánto sonido sale por las
# bocinas (el medidor del mezclador de volumen): se guarda cada 0.1 s, sin contar la voz del
# propio Jarvis, y se consulta para el intervalo exacto en que se grabó cada frase.
SALIDA = deque(maxlen=1200)   # (momento, pico 0-1) de los últimos ~2 minutos
UMBRAL_SALIDA = 0.03          # pico de salida a partir del cual "algo está sonando"


def _medir_salida():
    # Lo PRIMERO del hilo: COM en modo MTA, antes de importar nada que use COM (si no, la
    # importación deja el hilo en STA y ya no se puede cambiar). En MTA un objeto COM se puede
    # liberar desde cualquier hilo; en STA, cuando el recolector de basura de Python lo
    # liberaba desde OTRO hilo, Windows cerraba Jarvis de golpe (acceso inválido en _ctypes.pyd).
    import ctypes
    import sys
    ctypes.windll.ole32.CoInitializeEx(None, 0)  # 0 = COINIT_MULTITHREADED
    if "comtypes" not in sys.modules:  # al importarse por primera vez, comtypes inicializa COM
        sys.coinit_flags = 0           # en el hilo que lo importa: que sea en el mismo modo
    try:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    except ImportError:
        print("[Sin medidor de audio de la PC (pip install pycaw): no distingo un video de tu voz]")
        return
    vivos = []  # los objetos COM nunca se sueltan (son pocos): así nadie los libera a destiempo
    medidor, id_salida, revisar = None, None, 0.0
    while True:
        ahora = time.time()
        try:
            if medidor is None:
                dev = AudioUtilities.GetSpeakers()
                interfaz = getattr(dev, "_dev", dev).Activate(IAudioMeterInformation._iid_,
                                                               CLSCTX_ALL, None)
                medidor = cast(interfaz, POINTER(IAudioMeterInformation))
                vivos += [dev, interfaz, medidor]
                id_salida, revisar = getattr(dev, "id", None), ahora + 60
            pico = 0.0 if _jarvis_habla() else float(medidor.GetPeakValue())
            SALIDA.append((ahora, pico))
            # Si cambiaste de bocinas a audífonos, Windows cambia la salida predeterminada:
            # cada minuto se revisa (y solo se vuelve a pedir el medidor si es otra)
            if ahora > revisar:
                revisar = ahora + 60
                actual = AudioUtilities.GetSpeakers()
                vivos.append(actual)
                if getattr(actual, "id", None) != id_salida:
                    medidor = None
        except Exception:
            medidor = None
            time.sleep(2)
        time.sleep(0.1)


def pc_sonando(desde, hasta):
    """Fracción (0-1) del intervalo en que sonaba algo en la PC (sin contar a Jarvis)."""
    muestras = [p for t, p in list(SALIDA) if desde <= t <= hasta]
    if not muestras:
        return 0.0
    return sum(p > UMBRAL_SALIDA for p in muestras) / len(muestras)


# ---------- El micrófono silenciado en Windows ----------
# Pasó: la tecla de silenciar micrófono (o una app de llamadas) lo dejó en silencio y Jarvis
# "dejó de oír" sin decir nada: la calibración marcó ruido 0.0000 y nunca detectaba voz.
_mic = {"vol": None, "avisado": False}


def _volumen_mic():
    """IAudioEndpointVolume del micrófono predeterminado (en el hilo vigía, COM en MTA)."""
    if _mic["vol"] is None:
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetMicrophone()
        interfaz = getattr(dev, "_dev", dev).Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        _mic["vol"] = cast(interfaz, POINTER(IAudioEndpointVolume))
        _mic["vivos"] = [dev, interfaz]  # que nadie suelte los objetos COM a destiempo
    return _mic["vol"]


def microfono_silenciado():
    """True/False, o None si no se puede saber."""
    try:
        return bool(_volumen_mic().GetMute())
    except Exception:
        _mic["vol"] = None
        return None


def activar_microfono():
    try:
        _volumen_mic().SetMute(0, None)
        return not _volumen_mic().GetMute()
    except Exception:
        _mic["vol"] = None
        return False


def _sin_senal(segundos=20):
    """El micrófono manda silencio DIGITAL (ni el ruido del cuarto): privacidad de Windows o
    un dispositivo que no es el que usas."""
    recientes = list(NIVELES)[-int(segundos / SEG_BLOQUE):]
    return len(recientes) > 20 and max(recientes) < 1e-4


def vigilar_microfono(avisar):
    """Hilo: al arrancar, si el micrófono está silenciado lo reactiva (abriste Jarvis para
    hablarle); si se silencia después, avisa una vez (pudo ser a propósito, en una llamada) y
    la seña ☝ lo reactiva. avisar(texto) lo dice en voz alta."""
    def bucle():
        import ctypes
        import sys
        ctypes.windll.ole32.CoInitializeEx(None, 0)  # MTA, como el medidor de salida
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0
        primera = True
        while True:
            silenciado = microfono_silenciado()
            if silenciado and primera:
                if activar_microfono():
                    print("[Micrófono: estaba silenciado en Windows; lo reactivé]")
                    avisar("Tu micrófono estaba silenciado en Windows; lo volví a activar para poder oírte.")
            elif silenciado and not _mic["avisado"]:
                _mic["avisado"] = True
                print("[Micrófono: se silenció en Windows]")
                avisar("Ojo: tu micrófono está silenciado y no te puedo oír. Haz la seña de te "
                       "escucho, el dedo índice arriba, y lo reactivo.")
            elif silenciado is False and _sin_senal() and not _mic["avisado"]:
                _mic["avisado"] = True
                print("[Micrófono: llega silencio total (¿privacidad de Windows u otro dispositivo?)]")
                avisar("No me llega nada de sonido del micrófono. Revisa en la configuración de "
                       "Windows que el micrófono tenga permiso y sea el correcto.")
            elif silenciado is False and not _sin_senal():
                _mic["avisado"] = False  # ya se arregló: si vuelve a pasar, se avisa otra vez
            primera = False
            time.sleep(20)
    threading.Thread(target=bucle, daemon=True, name="vigia-microfono").start()


def frase_de_la_pc(minimo=0.5):
    """True si la última frase grabada coincidió con audio saliendo de la computadora."""
    return pc_sonando(ULTIMA_ORDEN["inicio"], ULTIMA_ORDEN["fin"] or time.time()) >= minimo


def iniciar(cfg):
    """Abre el micrófono según config.json: mic_dispositivo (principal), mic_respaldo
    ("auto" = el de Windows si el principal es otro; "" o "ninguno" = sin respaldo) y
    mic_respaldo_silencio_seg (cuánto silencio TOTAL del principal hace pasar al respaldo)."""
    global DISPOSITIVO, RESPALDO
    nombre = cfg.get("mic_dispositivo", "") or ""
    DISPOSITIVO = resolver_dispositivo(nombre)
    resp_cfg = cfg.get("mic_respaldo", "auto")
    if resp_cfg in ("", "ninguno", None):
        RESPALDO = None
    elif resp_cfg == "auto":
        RESPALDO = _entrada_predeterminada() if DISPOSITIVO is not None else None
    else:
        RESPALDO = resolver_dispositivo(resp_cfg)
    MIC.silencio_respaldo = float(cfg.get("mic_respaldo_silencio_seg", 8))
    MIC.iniciar(DISPOSITIVO, RESPALDO, nombre_principal=nombre,
                nombre_respaldo="" if resp_cfg in ("auto", "", "ninguno", None) else resp_cfg)
    print(f"[Micrófono: {_nombre_dispositivo(DISPOSITIVO)}"
          + (f"; respaldo: {_nombre_dispositivo(RESPALDO)}" if RESPALDO is not None else "") + "]")
    if not any(h.name == "medidor-salida" for h in threading.enumerate()):
        threading.Thread(target=_medir_salida, daemon=True, name="medidor-salida").start()


# ---------- Umbral que sigue al ruido del cuarto ----------
# Con un umbral fijo (o bajado a ciegas tras un rato sin voz) podía quedar por DEBAJO del ruido
# real del cuarto: la grabación nunca encontraba silencio, grababa los segundos máximos de
# ruido, Whisper tardaba hasta 20 s en transcribirlo y las órdenes se pegaban entre sí. Ahora se
# lleva el nivel de los últimos ~12 s y el umbral siempre queda por encima del piso de ruido.
NIVELES = deque(maxlen=150)   # volumen (RMS) de cada bloque de 80 ms
MARGEN_RUIDO = 2.5            # cuántas veces el ruido de fondo hace falta para contar como voz
UMBRAL_MINIMO = 0.0035


def umbral_actual(base):
    """Umbral para este momento: base (calibrado) hasta tener datos, y luego el piso de ruido
    de los últimos segundos (percentil 10) por el margen."""
    if len(NIVELES) < 30:
        return base
    return max(UMBRAL_MINIMO, float(np.percentile(NIVELES, 10)) * MARGEN_RUIDO)


def calibrar_umbral(minimo=0.0025, segundos=2.0):
    """Mide el ruido de fondo real del micrófono y fija el umbral a partir de ahí.

    Un número fijo en config.json no sirve para todos los cuartos ni micrófonos: si queda
    muy por encima del volumen real al que hablas, Jarvis nunca detecta que empezaste a
    hablar (parece que "no escucha" o "no contesta"); si queda muy por debajo, cualquier
    ruido de fondo dispara una grabación falsa. minimo es el piso por si el cuarto está
    completamente en silencio (para no quedar en un umbral de prácticamente 0)."""
    sub = MIC.suscribir()
    niveles = []
    try:
        fin = time.time() + segundos
        while time.time() < fin:
            try:
                _t, data = sub.get(timeout=1.0)
            except queue.Empty:
                break
            niveles.append(float(np.sqrt(np.mean(data ** 2))))
    finally:
        MIC.desuscribir(sub)
    if not niveles:
        print("[No pude calibrar el micrófono; uso el umbral por defecto.]")
        return minimo
    NIVELES.extend(niveles)  # el umbral dinámico arranca ya con el ruido real del cuarto
    niveles.sort()
    # percentil 25 en vez de la mediana: si justo durante la calibración hubo un ruido
    # puntual (un clic, una puerta), que no arrastre el umbral de todo el resto de la sesión
    ambiente = niveles[len(niveles) // 4]
    umbral = max(minimo, ambiente * 2.5)
    print(f"[Micrófono calibrado: ruido de fondo {ambiente:.4f}, umbral {umbral:.4f}]")
    if niveles[-1] < 1e-4:
        print("[Ojo: el micrófono no manda NADA de sonido (¿silenciado o sin permiso?)]")
    return umbral


# ---------- Grabar una frase ----------
def _jarvis_habla():
    try:
        import voz
        return voz.HABLANDO.is_set()
    except Exception:
        return False


def en_pausa(segundos=1.3, umbral=None):
    """True si en los últimos 'segundos' nadie habló cerca del micrófono (el expositor hizo
    una pausa): para que Jarvis no lo interrumpa a media frase."""
    umbral = float(umbral if umbral is not None else (_cfg().get("mic_umbral", 0.004) or 0.004))
    audio = MIC.reciente(segundos)
    if len(audio) < int(segundos * SAMPLE_RATE * 0.8):
        return False  # sin audio suficiente (micrófono recién abierto): mejor no arriesgar
    n = int(0.1 * SAMPLE_RATE)
    return all(float(np.sqrt(np.mean(audio[i:i + n] ** 2))) <= umbral * 1.2
               for i in range(0, len(audio) - n + 1, n))


def _grabar_de_sub(sub, umbral, silencio_seg, max_seg, espera_seg, previo_inicial=None,
                   atento_a_conversar=False):
    """Graba de una suscripción: espera voz, graba y corta al quedarse en silencio.
    atento_a_conversar: la espera de la palabra de activación se corta si genesis.py pide
    escuchar una respuesta sin palabra (CONVERSAR)."""
    n_previo = max(3, int(0.3 / SEG_BLOQUE))
    previo = deque(previo_inicial or [], maxlen=max(n_previo, len(previo_inicial or [])))
    frames = []
    hablando = False
    silencio = 0.0
    inicio = time.time()
    inicio_voz = None
    umbral_vivo = umbral_actual(umbral)
    while True:
        if CANCELAR.is_set() or DESPERTAR.is_set() or (atento_a_conversar and CONVERSAR.is_set()):
            return None
        try:
            t, data = sub.get(timeout=0.5)
        except queue.Empty:
            if not hablando and time.time() - inicio > espera_seg:
                return None
            continue
        # Mientras Jarvis habla (también cuando lo hace por iniciativa propia, desde otro hilo)
        # no se graba: si no, transcribía su propia voz como si fuera una orden.
        if t < IGNORAR_HASTA or (not hablando and _jarvis_habla()):
            inicio = time.time()  # el tiempo de espera cuenta desde que se deja de ignorar
            previo.clear()
            continue
        nivel = float(np.sqrt(np.mean(data ** 2)))
        NIVELES.append(nivel)
        if not hablando:  # mientras hablas el umbral no se mueve (no corta la frase a medias)
            umbral_vivo = umbral_actual(umbral)
        if nivel > umbral_vivo:
            if not hablando:
                frames.extend(previo)
                inicio_voz = t
                ULTIMA_ORDEN["inicio"] = t - len(previo) * SEG_BLOQUE
            hablando = True
            silencio = 0.0
        elif hablando:
            silencio += SEG_BLOQUE
        if hablando:
            frames.append(data)
        else:
            previo.append(data)
        if hablando and silencio >= silencio_seg:
            break
        if hablando and t - inicio_voz > max_seg:
            break
        if not hablando and time.time() - inicio > espera_seg:
            return None
    ULTIMA_ORDEN["fin"] = time.time()
    return np.concatenate(frames).flatten()


def grabar(umbral=0.004, silencio_seg=None, max_seg=20, espera_seg=6, atento_a_conversar=False):
    """Espera a que hables, graba y corta cuando haces silencio. atento_a_conversar: se corta
    (None) si llega algo que atender (CONVERSAR: una orden del teléfono, un gesto)."""
    silencio_seg = SILENCIO_SEG if silencio_seg is None else silencio_seg
    sub = MIC.suscribir()
    try:
        return _grabar_de_sub(sub, umbral, silencio_seg, max_seg, espera_seg,
                              atento_a_conversar=atento_a_conversar)
    finally:
        MIC.desuscribir(sub)


# ---------- Transcribir ----------
# Frases que Whisper "inventa" sobre ruido o silencio (vienen de subtítulos de YouTube con
# los que se entrenó). Se comparan sin acentos ni signos.
ALUCINACIONES = (
    "subtitulos realizados por la comunidad de amara org", "subtitulos por la comunidad de amara org",
    "amara org", "gracias por ver el video", "gracias por ver", "suscribete", "suscribanse",
    "no olvides suscribirte", "dale like", "musica", "aplausos", "risas", "silencio",
    "subtitulado por", "transcripcion", "www", "sous titrage")


def _normalizar(texto):
    """Minúsculas, sin acentos ni signos, para comparar palabras."""
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _es_alucinacion(texto):
    t = _normalizar(texto)
    return not t or any(t == a or (len(a) > 12 and a in t) for a in ALUCINACIONES)


def _segmento_valido(no_speech, logprob, compresion, texto):
    """Mismo criterio que Whisper para descartar segmentos que no eran voz."""
    if no_speech is not None and logprob is not None and no_speech > 0.6 and logprob < -1.0:
        return False
    if compresion is not None and compresion > 2.4:   # repite lo mismo una y otra vez
        return False
    if logprob is not None and logprob < -1.5:        # muy dudoso: casi seguro ruido
        return False
    return not _es_alucinacion(texto)


def _de(obj, campo):
    return obj.get(campo) if isinstance(obj, dict) else getattr(obj, campo, None)


def _transcribir_nube(audio, prompt):
    """Whisper grande en Groq: ~0.3-0.6 s y más preciso que el local. None si no se puede
    (sin clave, sin red, error): quien llama pasa al local sin esperar reintentos."""
    import io
    import wave

    import cerebro
    stt = _cfg().get("stt", {}) or {}
    clave = os.environ.get(stt.get("clave_env", "GROQ_API_KEY"), "")
    url = stt.get("url", "https://api.groq.com/openai/v1")
    if not clave or not cerebro.hay_internet(cerebro.host_de(url)):
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    try:
        # max_retries=0 y timeout corto (cerebro.cliente): antes el SDK reintentaba 2 veces
        # con esperas y, con Wi-Fi malo, una orden tardaba ~30 s antes de caer al local.
        cli = cerebro.cliente(url, clave, float(stt.get("timeout", 6)))
        r = cli.audio.transcriptions.create(
            file=("orden.wav", buf.getvalue()), model=stt.get("modelo", "whisper-large-v3-turbo"),
            language="es", prompt=prompt or None, temperature=0, response_format="verbose_json")
    except Exception as e:
        print(f"[La transcripción en la nube falló ({type(e).__name__}: {str(e)[:100]}); uso Whisper local]")
        if cerebro.es_error_de_red(e):
            cerebro.marcar_conexion(cerebro.host_de(url), False)
        return None
    segmentos = _de(r, "segments")
    if not segmentos:
        texto = (_de(r, "text") or "").strip()
        return "" if _es_alucinacion(texto) else texto
    partes = [(_de(s, "text") or "").strip() for s in segmentos
              if _segmento_valido(_de(s, "no_speech_prob"), _de(s, "avg_logprob"),
                                  _de(s, "compression_ratio"), _de(s, "text") or "")]
    return " ".join(p for p in partes if p).strip()


def _preparar_cuda():
    """Agrega al buscador de DLL las librerías de CUDA instaladas con pip
    (nvidia-cublas-cu12, nvidia-cudnn-cu12): con ellas Whisper corre en la GPU."""
    base = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
        d = base / sub / "bin"
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except (OSError, AttributeError):
                pass
            if str(d) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


_lock_modelos = threading.Lock()
_gpu = {"ok": None}  # None = sin probar; True/False = resultado (se prueba UNA vez por sesión)
_lock_gpu = threading.Lock()  # la escucha y el precalentado preguntan a la vez: que esperen el resultado

_PRUEBA_GPU = r"""
import sys, numpy as np
sys.path.insert(0, sys.argv[1])
import escuchar
escuchar._preparar_cuda()
from faster_whisper import WhisperModel
m = WhisperModel("tiny", device="cuda", compute_type="float16")
list(m.transcribe(np.zeros(16000, np.float32), language="es", beam_size=1)[0])
print("GPU_OK")
"""


def _gpu_sirve():
    """¿Whisper puede usar la GPU NVIDIA? Se prueba en un PROCESO APARTE con límite de tiempo:
    si faltan las DLL de CUDA (cublas64_12.dll), el primer intento lanza un error pero el
    siguiente se queda colgado para siempre dentro de la librería nativa, y eso congelaba a
    Jarvis completo al arrancar (no volvía a oír ninguna orden)."""
    if _gpu["ok"] is not None:
        return _gpu["ok"]
    with _lock_gpu:
        if _gpu["ok"] is None:
            _gpu["ok"] = _probar_gpu()
            if not _gpu["ok"]:
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        _reprobar_gpu_luego()   # hay tarjeta: el fallo puede ser pasajero
                except Exception:
                    pass
    return _gpu["ok"]


REPROBAR_GPU_SEG = 300   # si la GPU falló al arrancar (VRAM llena, error de CUDA), se vuelve a probar


def _reprobar_gpu_luego():
    """La GPU falló (a veces es pasajero: la tarjeta estaba llena con otro modelo): en unos
    minutos se vuelve a probar y, si ya sirve, la próxima carga de Whisper va a la GPU (antes se
    quedaba en la CPU toda la sesión, más lento y gastando RAM)."""
    def reprobar():
        time.sleep(REPROBAR_GPU_SEG)
        if _probar_gpu():
            with _lock_modelos:
                _gpu["ok"] = True
                _modelos.clear()
            print("[Whisper: la GPU ya responde; la uso desde la próxima orden]")
    threading.Thread(target=reprobar, daemon=True, name="reprobar-gpu").start()


def _probar_gpu():
    ok = False
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() == 0:
            return False
        import subprocess
        import sys
        r = subprocess.run([sys.executable, "-c", _PRUEBA_GPU, str(Path(__file__).parent)],
                           capture_output=True, text=True, timeout=90,
                           creationflags=0x08000000)  # sin ventana de consola
        ok = "GPU_OK" in (r.stdout or "")
        if not ok:
            error = (r.stderr or "").strip().splitlines()[-1:] or ["sin detalle"]
            print(f"[Whisper no puede usar la GPU ({error[0][:110]}); uso la CPU. Para la GPU: "
                  "pip install -r requirements-gpu.txt]")
    except Exception as e:
        print(f"[No pude probar la GPU para Whisper ({type(e).__name__}); uso la CPU]")
    return ok


def _cargar_modelo(nombre):
    if nombre in _modelos:
        return _modelos[nombre]
    with _lock_modelos:  # el precalentado y la escucha no deben cargar el mismo modelo a la vez
        if nombre in _modelos:
            return _modelos[nombre]
        from faster_whisper import WhisperModel
        if _cfg().get("whisper_dispositivo", "auto") in ("auto", "cuda") and _gpu_sirve():
            try:
                _preparar_cuda()
                print(f"[Cargando modelo de voz '{nombre}' en la GPU...]")
                _modelos[nombre] = WhisperModel(nombre, device="cuda", compute_type="float16")
                return _modelos[nombre]
            except Exception as e:
                print(f"[Whisper no pudo usar la GPU ({type(e).__name__}: {str(e)[:100]}); uso la CPU]")
        print(f"[Cargando modelo de voz '{nombre}'...]")
        _modelos[nombre] = WhisperModel(nombre, device="cpu", compute_type="int8")
        return _modelos[nombre]


class SinMemoria(RuntimeError):
    """Whisper no pudo transcribir porque la computadora se quedó sin memoria."""


AL_FALTAR_MEMORIA = None   # lo pone genesis.py: fn(texto) para avisarte (máx. cada 30 min)
_aviso_memoria = {"t": 0.0}


def es_falta_de_memoria(e):
    t = str(e).lower()
    return isinstance(e, MemoryError) or any(x in t for x in (
        "mkl_malloc", "failed to allocate", "out of memory", "bad_alloc", "cuda_error_out_of_memory",
        "cublas_status_alloc_failed", "not enough memory"))


def quien_come_memoria():
    """(nombre, GB) del programa que más RAM usa, sumando todos sus procesos."""
    import collections
    import psutil
    uso = collections.Counter()
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            uso[p.info["name"]] += p.info["memory_info"].rss
        except Exception:
            pass
    if not uso:
        return None
    nombre, b = uso.most_common(1)[0]
    return nombre.removesuffix(".exe"), b / 2 ** 30


def liberar_memoria():
    """Suelta lo que Jarvis puede soltar: basura de Python y los modelos de Ollama que viven en
    la RAM (los de embeddings; se vuelven a cargar solos cuando se necesitan)."""
    import gc
    gc.collect()
    try:
        import ollama
        for m in ollama.ps().models:
            if not getattr(m, "size_vram", 0):   # 100% en CPU = en la RAM
                if "embed" in m.model or "paraphrase" in m.model or "minilm" in m.model:
                    ollama.embed(model=m.model, input=[], keep_alive=0)
                else:
                    ollama.generate(model=m.model, prompt="", keep_alive=0)
                print(f"[Memoria: descargué {m.model} de la RAM]")
    except Exception:
        pass
    gc.collect()


def _avisar_memoria():
    ahora = time.time()
    if ahora - _aviso_memoria["t"] < 1800:
        return
    _aviso_memoria["t"] = ahora
    quien = None
    try:
        quien = quien_come_memoria()
    except Exception:
        pass
    texto = "Tu computadora se quedó sin memoria y casi no te pude escuchar."
    if quien and quien[1] >= 1.5:
        texto += f" {quien[0].capitalize()} está usando {quien[1]:.1f} gigas; ciérrale algunas pestañas o ventanas."
    print(f"[Memoria: {texto}]")
    if AL_FALTAR_MEMORIA is not None:
        try:
            AL_FALTAR_MEMORIA(texto)
        except Exception:
            pass


def _transcribir_local(audio, modelo, prompt):
    """Si se acaba la memoria: libera lo que puede y reintenta una vez; si vuelve a fallar,
    SinMemoria (antes el error subía hasta el bucle principal y tumbaba a Jarvis)."""
    for intento in range(2):
        try:
            whisper = _cargar_modelo(modelo)
            segmentos, _ = whisper.transcribe(audio, language="es", beam_size=1, vad_filter=True,
                                              initial_prompt=prompt)
            partes = [s.text.strip() for s in segmentos
                      if _segmento_valido(s.no_speech_prob, s.avg_logprob, s.compression_ratio, s.text)]
            return " ".join(p for p in partes if p).strip()
        except Exception as e:
            if not es_falta_de_memoria(e):
                raise
            print(f"[Whisper se quedó sin memoria ({str(e)[:60]}); libero memoria y reintento]")
            liberar_memoria()
            if intento == 1:
                _avisar_memoria()
                raise SinMemoria(str(e)) from e
    return ""


def transcribir(audio, modelo="small", prompt=None, nube=False):
    """nube=True: intenta Groq primero (config.json → stt.modo "auto"/"online"; "local" = solo
    en el equipo). La palabra de activación siempre se transcribe local, porque escucha todo
    el tiempo y no debe gastar la cuota."""
    t0 = time.time()
    modo = (_cfg().get("stt", {}) or {}).get("modo", "auto")
    # Con la GPU, 'small' local transcribe una orden en ~0.25 s, sin red ni cuota: más rápido
    # que ir a Groq (~0.5-1 s). En "auto" se usa primero; si falla, la nube de respaldo.
    if nube and modo == "auto" and _gpu["ok"]:
        try:
            texto = _transcribir_local(audio, modelo, prompt)
            ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="local GPU")
            return texto
        except Exception as e:
            print(f"[Whisper en la GPU falló ({type(e).__name__}); uso la nube]")
    if nube and modo in ("auto", "online"):
        texto = _transcribir_nube(audio, prompt)
        if texto is not None:
            ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="nube")
            return texto
    try:
        texto = _transcribir_local(audio, modelo, prompt)
    except SinMemoria:
        # rescate: la transcripción en la nube no usa tu memoria
        texto = _transcribir_nube(audio, prompt) if modo in ("auto", "online") else None
        if texto is None:
            print("[No te pude transcribir: falta memoria]")
            return ""
        ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="nube (sin memoria)")
        return texto
    ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="local")
    return texto


def modelo_activacion(cfg):
    """Modelo para reconocer "Jarvis". Con la GPU se usa el bueno ('small', ~0.25 s): esa misma
    transcripción ya sirve como la orden y no hace falta transcribir dos veces. Sin GPU, el
    ligero ('base'), porque escucha todo el tiempo en la CPU."""
    if cfg.get("whisper_dispositivo", "auto") in ("auto", "cuda") and _gpu_sirve():
        return cfg.get("whisper_modelo", "small")
    return cfg.get("whisper_modelo_wake", "base")


def precalentar(cfg):
    """Carga de antemano el Whisper local (en la GPU si se puede) para que el primer
    respaldo o la palabra de activación no tarden en arrancar."""
    try:
        if cfg.get("motor_activacion", "whisper") != "openwakeword":
            _cargar_modelo(modelo_activacion(cfg))
        # Una transcripción de prueba por la MISMA ruta que una orden (con el filtro de voz, que
        # carga su propio modelo): en la GPU, CUDA y el filtro se inicializan hasta la primera
        # transcripción real, y eso le sumaba ~1.5 s a la primera orden del día
        ruido = np.random.default_rng(0).normal(0, 0.01, SAMPLE_RATE * 2).astype(np.float32)
        for modelo in {modelo_activacion(cfg), cfg.get("whisper_modelo", "small")}:
            _transcribir_local(ruido, modelo, None)
    except Exception as e:
        print(f"[No pude precargar Whisper: {type(e).__name__}: {str(e)[:100]}]")
        if es_falta_de_memoria(e):   # se vuelve a intentar en un rato (la primera orden no espera la carga)
            threading.Timer(120, precalentar, args=(cfg,)).start()


def escuchar(modelo="small", umbral=0.004, prompt=None):
    """prompt: nombres de apps, etc., que Whisper debe reconocer bien."""
    try:
        audio = grabar(umbral=umbral)
    except sd.PortAudioError as e:
        print(f"[El micrófono dio un error al escuchar la orden: {str(e)[:80]}]")
        return ""
    if audio is None:
        return ""
    return transcribir(audio, modelo, prompt, nube=True)


# ---------- Palabra de activación con Whisper ("Jarvis") ----------
def quitar_activacion(texto, palabras):
    """'Hey Jarvis, siguiente diapositiva' -> 'siguiente diapositiva'."""
    # "vis"/"arvis": restos de la palabra que a veces quedan al inicio de la grabación
    alternativas = "|".join([re.escape(p) for p in palabras if p] + ["[a-z]{0,3}rvis", "vis"])
    if not alternativas:
        return texto.strip()
    patron = rf"^\W*(?:(?:hey|ey|oye|oiga|ok|okey|hola)\W+)?(?:{alternativas})\b[\W]*"
    return re.sub(patron, "", texto.strip(), flags=re.I).strip()


def esperar_palabra(palabras, modelo_wake="base", umbral=0.004, pista="Jarvis"):
    """Bloquea hasta oír la palabra de activación. Devuelve (resto, audio): lo que dijiste
    después de ella ('' si solo la dijiste) y la grabación completa, para volver a
    transcribirla con un modelo mejor. None si se despertó desde la bandeja.

    El umbral del micrófono lo ajusta solo umbral_actual() según el ruido real del cuarto
    (antes se bajaba a ciegas tras cada rato sin voz y terminaba por debajo del ruido)."""
    palabras = [_normalizar(p) for p in palabras]
    sub = MIC.suscribir()  # una sola suscripción: sin huecos entre una frase y la siguiente
    try:
        while True:
            if DESPERTAR.is_set():
                DESPERTAR.clear()
                return None  # obtener_entrada lo toma como "abre la ventana para escribir"
            if CONVERSAR.is_set():
                CONVERSAR.clear()
                return A_CONVERSAR
            if PAUSA.is_set():
                time.sleep(0.5)
                continue
            # max_seg amplio: la orden completa puede ir en la misma frase que el nombre
            audio = _grabar_de_sub(sub, umbral, SILENCIO_SEG, max_seg=20, espera_seg=15,
                                   atento_a_conversar=True)
            if audio is None:
                continue
            # Un golpe, un clic o una tos (< 0.25 s de voz, más ~0.3 s previos y el silencio
            # final) no pueden ser "Jarvis": no se gasta CPU en transcribirlos
            if len(audio) < SAMPLE_RATE * (0.25 + 0.3 + SILENCIO_SEG):
                continue
            original = transcribir(audio, modelo_wake, prompt=pista)
            texto = _normalizar(original)
            span = _buscar_nombre(texto, palabras)
            if span and not _exacto(texto, palabras) and frase_de_la_pc():
                # "se parece" a Jarvis, pero lo dijo el video o la música que está sonando
                print(f"[Ignoro «{original[:60]}»: sonó parecido a mi nombre, pero venía de la computadora]")
                span = None
            if span:
                # Lo que dijo además del nombre, vaya antes o después ("Abre Chrome, Jarvis"):
                # si no, con el nombre al final parecía que solo lo había llamado
                despues = texto[span[1]:].strip()
                resto = despues if len(despues) >= 4 else (texto[:span[0]] + " " + despues).strip()
                resto = re.sub(r"^(?:hey|ey|oye|oiga|ok|okey|hola)\b\s*", "", resto)
                return resto, audio, original
            if texto:
                ESCUCHADO.append((time.time(), original))
    finally:
        MIC.desuscribir(sub)


PARECIDO_NOMBRE = 0.8  # "yaervis" ~ "yarvis" 0.92; "travis" ~ "jarvis" 0.67; "davis" 0.73


def _buscar_nombre(texto, palabras):
    """(inicio, fin) del nombre dentro del texto, o None. Primero exacto contra la lista de
    variantes; si no, por parecido: Whisper deforma "Jarvis" de muchas formas ("yaervis",
    "jarbis"...) y con una lista exacta, cada deformación nueva hacía que no se activara."""
    for p in palabras:
        m = re.search(rf"\b{re.escape(p)}\b", texto)
        if m:
            return m.span()
    for m in re.finditer(r"\w+", texto):
        w = m.group()
        # Además de parecerse, tiene que SONAR a "jar-vis": una r seguida de v/b/f ("jarbis",
        # "yaervis", "charvis"). Así palabras de un video como "servicio" o "Harvey" no cuentan.
        if (5 <= len(w) <= 8 and re.search(r"r[vbf]", w)
                and any(SequenceMatcher(None, w, p).ratio() >= PARECIDO_NOMBRE for p in palabras)):
            return m.span()
    return None


def _exacto(texto, palabras):
    return any(re.search(rf"\b{re.escape(p)}\b", texto) for p in palabras)


# ---------- Palabra de activación con openWakeWord ("hey Jarvis") ----------
# Un modelo diminuto entrenado solo para reconocer "hey Jarvis". A diferencia de transcribir
# todo con Whisper para buscar la palabra, casi no usa CPU aunque hables sin parar durante la
# exposición, reacciona en ~0.3 s y no parte tus órdenes en pedazos. La orden se graba del
# MISMO flujo de audio, sin cortes: puedes decir "hey Jarvis, siguiente diapositiva" de corrido.
_oww = {}


def _cargar_oww(nombre, instancia="principal"):
    clave = (nombre, instancia)
    if clave not in _oww:
        from openwakeword.model import Model

        print(f"[Cargando detector de palabra '{nombre}'...]")
        if not os.path.isfile(nombre):  # no es un modelo propio (.onnx): es uno de los de fábrica
            try:
                from openwakeword.utils import download_models
                download_models([nombre])  # solo descarga la primera vez (~3 MB)
            except ImportError:
                pass  # versiones viejas de openwakeword ya traen los modelos dentro
        _oww[clave] = Model(wakeword_models=[nombre], inference_framework="onnx")
    return _oww[clave]


def _detecta(detector, data, ganancia, sensibilidad):
    # Micrófonos de laptop (arreglos Intel) entregan la voz muy bajita y el detector casi no
    # reacciona: mic_ganancia la amplifica solo para él.
    pcm = (np.clip(data * ganancia, -1, 1) * 32767).astype(np.int16)
    puntos = detector.predict(pcm)
    return max(puntos.values() or [0]) >= sensibilidad


def esperar_oww(modelo="hey_jarvis", sensibilidad=0.5, umbral_voz=0.004,
                whisper_modelo="small", pista="", palabras=(), ganancia=1.0):
    """Bloquea hasta oír la palabra y devuelve la orden ya transcrita ('' si no dijo nada
    después), o None si se despertó desde la bandeja."""
    detector = _cargar_oww(modelo)
    detector.reset()
    sub = MIC.suscribir()
    try:
        previo = deque(maxlen=4)  # ~0.3 s: lo último antes de detectar la palabra
        while True:
            if DESPERTAR.is_set():
                DESPERTAR.clear()
                return None
            if CONVERSAR.is_set():
                CONVERSAR.clear()
                return A_CONVERSAR
            if PAUSA.is_set():
                time.sleep(0.5)
                continue
            try:
                t, data = sub.get(timeout=0.5)
            except queue.Empty:
                continue
            if t < IGNORAR_HASTA or _jarvis_habla():
                detector.reset()
                previo.clear()
                continue
            previo.append(data)
            if not _detecta(detector, data, ganancia, sensibilidad):
                continue
            detector.reset()
            ULTIMA_ORDEN["inicio"] = t
            if AL_ACTIVAR:
                threading.Thread(target=AL_ACTIVAR, daemon=True).start()
            # Se sigue grabando del mismo flujo: lo que dijiste justo después de la palabra no
            # se pierde. Se da poco margen de espera por si solo dijo la palabra.
            audio = _grabar_de_sub(sub, umbral_voz, silencio_seg=SILENCIO_SEG, max_seg=15,
                                   espera_seg=5, previo_inicial=list(previo)[-2:])
            if audio is None:
                return ""
            texto = transcribir(audio, whisper_modelo, pista, nube=True)
            return quitar_activacion(texto, palabras)
    finally:
        MIC.desuscribir(sub)


class Interruptor:
    """Mientras Jarvis habla, sigue escuchando: si le dicen "Hey Jarvis" se calla al instante
    (al_interrumpir) y graba la nueva orden, que queda en ORDENES para el bucle principal.
    Así se le puede cortar cuando se extiende, como a una persona."""

    ORDENES = queue.Queue()

    def __init__(self, cfg, al_interrumpir):
        self.cfg, self.al_interrumpir = cfg, al_interrumpir
        self._activo = threading.Event()
        self._hilo = None
        try:
            self.detector = _cargar_oww(cfg.get("oww_modelo", "hey_jarvis"), "interruptor")
        except Exception as e:
            print(f"[Sin interrupción por voz: openWakeWord no está disponible ({type(e).__name__})]")
            self.detector = None

    @property
    def disponible(self):
        return self.detector is not None

    def iniciar(self):
        if self.detector is None or self._activo.is_set():
            return
        self._activo.set()
        self._interrumpido = threading.Event()
        self._hilo = threading.Thread(target=self._vigilar, daemon=True, name="interruptor")
        self._hilo.start()
        # Con la GPU también se le puede cortar diciendo solo "Jarvis" (como se le llama
        # siempre), no únicamente "Hey Jarvis": cada frase se transcribe en ~0.25 s
        self._hilo_nombre = None
        if _gpu["ok"]:
            self._hilo_nombre = threading.Thread(target=self._vigilar_nombre, daemon=True,
                                                 name="interruptor-nombre")
            self._hilo_nombre.start()

    def detener(self):
        self._activo.clear()
        for hilo in (self._hilo, getattr(self, "_hilo_nombre", None)):
            if hilo is not None:
                hilo.join(timeout=1.5)
        self._hilo = self._hilo_nombre = None

    def _vigilar_nombre(self):
        """Mientras Jarvis habla: graba cada frase que suene fuerte y, si al transcribirla trae
        el nombre ("Jarvis, ya", "Jarvis, mejor abre YouTube"), lo interrumpe. Su propia voz por
        las bocinas también se transcribe, pero casi nunca dice su nombre."""
        cfg = self.cfg
        palabras = [_normalizar(p) for p in (cfg.get("palabras_activacion") or ["jarvis"])]
        modelo = cfg.get("whisper_modelo", "small")
        sub = MIC.suscribir()
        try:
            frames, voz_seg, silencio = [], 0.0, 0.0
            while self._activo.is_set() and not self._interrumpido.is_set():
                try:
                    _t, data = sub.get(timeout=0.3)
                except queue.Empty:
                    continue
                # umbral más alto que el normal: la voz de Jarvis por las bocinas también llega
                fuerte = float(np.sqrt(np.mean(data ** 2))) > umbral_actual(
                    float(cfg.get("mic_umbral", 0.004))) * 1.6
                if fuerte:
                    frames.append(data)
                    voz_seg += SEG_BLOQUE
                    silencio = 0.0
                elif frames:
                    frames.append(data)
                    silencio += SEG_BLOQUE
                if frames and (silencio >= 0.45 or voz_seg >= 6):
                    audio = np.concatenate(frames).flatten()
                    frames, voz_seg, silencio = [], 0.0, 0.0
                    if len(audio) < SAMPLE_RATE * 0.5:
                        continue
                    try:
                        texto = _transcribir_local(audio, modelo, None)
                    except Exception:
                        continue
                    # Solo el nombre exacto: mientras Jarvis habla no se puede saber si un video
                    # está sonando, y un parecido de la narración lo cortaría sin razón
                    span = _exacto(_normalizar(texto), palabras)
                    if not span or self._interrumpido.is_set() or not self._activo.is_set():
                        continue
                    self._interrumpido.set()
                    print(f"[Me interrumpieron por nombre: «{texto}»]")
                    self.al_interrumpir()
                    orden = quitar_activacion(texto, cfg.get("palabras_activacion") or ["jarvis"])
                    if len(_normalizar(orden)) < 3:  # solo "Jarvis": escucha lo que sigue
                        audio = _grabar_de_sub(sub, float(cfg.get("mic_umbral", 0.004)),
                                               SILENCIO_SEG, max_seg=15, espera_seg=5)
                        orden = transcribir(audio, modelo, None, nube=True) if audio is not None else ""
                    Interruptor.ORDENES.put(orden)
                    return
        finally:
            MIC.desuscribir(sub)

    def _vigilar(self):
        cfg = self.cfg
        sens = float(cfg.get("oww_sensibilidad", 0.5))
        ganancia = float(cfg.get("mic_ganancia", 1.0))
        self.detector.reset()
        sub = MIC.suscribir()
        try:
            while self._activo.is_set() and not self._interrumpido.is_set():
                try:
                    _t, data = sub.get(timeout=0.3)
                except queue.Empty:
                    continue
                if not _detecta(self.detector, data, ganancia, sens):
                    continue
                self.detector.reset()
                if self._interrumpido.is_set():  # ya lo cortó el detector del nombre
                    return
                self._interrumpido.set()
                print("[Me interrumpieron: me callo y escucho]")
                self.al_interrumpir()
                audio = _grabar_de_sub(sub, float(cfg.get("mic_umbral", 0.004)), SILENCIO_SEG,
                                       max_seg=15, espera_seg=5)
                texto = ""
                if audio is not None:
                    texto = quitar_activacion(
                        transcribir(audio, cfg.get("whisper_modelo", "small"), None, nube=True),
                        cfg.get("palabras_activacion") or ["jarvis"])
                Interruptor.ORDENES.put(texto)
                return
        finally:
            MIC.desuscribir(sub)


def texto_reciente(segundos=45, hasta=None):
    """Lo que se dijo cerca de Jarvis en los últimos segundos, antes de la orden actual (para
    "responde la pregunta que me hicieron"). Con la palabra por Whisper ya está transcrito;
    con openWakeWord se transcribe el audio guardado en memoria."""
    hasta = hasta or ULTIMA_ORDEN["inicio"] or time.time()
    desde = hasta - segundos
    textos = [t for m, t in list(ESCUCHADO) if desde <= m <= hasta + 1]
    if textos:
        return " ".join(textos)
    audio = MIC.reciente(segundos, hasta)
    if len(audio) < SAMPLE_RATE:
        return ""
    # Solo los pedazos con voz (más rápido de transcribir y sin minutos de silencio)
    umbral = float(_cfg().get("mic_umbral", 0.004))
    n = int(0.25 * SAMPLE_RATE)
    trozos = [audio[i:i + n] for i in range(0, len(audio), n)]
    con_voz = [np.sqrt(np.mean(t ** 2)) > umbral for t in trozos]
    elegidos = [t for i, t in enumerate(trozos)
                if any(con_voz[max(0, i - 2):i + 3])]  # con un poco de margen alrededor
    if not elegidos or len(elegidos) * 0.25 < 0.6:
        return ""
    return transcribir(np.concatenate(elegidos), _cfg().get("whisper_modelo", "small"), None, nube=True)


# ---------- Elegir micrófono por voz ----------
def _normalizar_simple(texto):
    t = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _guardar_dispositivo(nombre):
    """Guarda el NOMBRE del micrófono (o '' para el automático) en config.json. No el índice:
    Windows renumera los dispositivos cada vez que se conecta o desconecta algo por
    Bluetooth, y un índice guardado acabaría apuntando a otro aparato."""
    import configuracion
    try:
        configuracion.guardar_campos({"mic_dispositivo": nombre})
    except (OSError, json.JSONDecodeError) as e:
        print(f"[No pude guardar el micrófono elegido en config.json: {type(e).__name__}]")


def _funciona(indice):
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=indice, dtype="float32",
                            blocksize=1600) as stream:
            stream.read(1600)
        return True
    except Exception:
        return False


@skill("listar_microfonos",
       "Lista los micrófonos que el equipo detecta ahora mismo (el del equipo, o unos "
       "audífonos, lentes u otro dispositivo conectado por Bluetooth). Úsala antes de "
       "cambiar_microfono si no sabes cómo aparece el dispositivo nuevo.", terminal=False)
def listar_microfonos():
    entradas = listar_dispositivos()
    if not entradas:
        return "No encontré ningún micrófono."
    return "Micrófonos disponibles: " + " | ".join(f"{i}) {n}" for i, n in entradas)


@skill("cambiar_microfono",
       "Cambia qué micrófono usa Jarvis para escuchar, por ejemplo unos audífonos o lentes "
       "recién conectados por Bluetooth. Tienen que estar ya emparejados en Windows antes de "
       "pedir esto (eso se hace a mano, una vez, desde Configuración > Bluetooth). Sin nombre, "
       "vuelve a elegir el micrófono automáticamente.",
       {"nombre": {"type": "string",
                   "description": "Nombre aproximado del micrófono, p. ej. 'lentes' o "
                                  "'auriculares'; vacío para el automático de Windows"}},
       requeridos=[], riesgo="confirmar",
       pregunta="¿Cambio el micrófono con el que te escucho?")
def cambiar_microfono(nombre=""):
    global DISPOSITIVO
    if not nombre.strip():
        DISPOSITIVO = None
        _guardar_dispositivo("")
        MIC.iniciar(None, None)
        return "Vuelvo al micrófono automático de Windows."

    entradas = listar_dispositivos()
    if not entradas:
        return skills.Fallo("No encontré ningún micrófono.")
    objetivo = _normalizar_simple(nombre)
    mejor_i, mejor_nombre, mejor_p = None, None, 0.0
    for i, n in entradas:
        ln = _normalizar_simple(n)
        p = SequenceMatcher(None, objetivo, ln).ratio()
        if objetivo in ln:
            p = max(p, 0.8)
        if p > mejor_p:
            mejor_i, mejor_nombre, mejor_p = i, n, p
    if mejor_i is None or mejor_p < 0.3:
        return skills.Fallo(f"No encontré ningún micrófono parecido a '{nombre}'. " + listar_microfonos())
    if not _funciona(mejor_i):  # que un cambio a un aparato que no graba no deje a Jarvis sordo
        return skills.Fallo(f"'{mejor_nombre}' aparece pero no pude grabar con él; sigo con el micrófono actual.")

    DISPOSITIVO = mejor_i
    _guardar_dispositivo(mejor_nombre)
    MIC.iniciar(DISPOSITIVO, RESPALDO if RESPALDO != DISPOSITIVO else None,
                nombre_principal=mejor_nombre)
    return f"Listo, ahora escucho por '{mejor_nombre}'."


if __name__ == "__main__":
    print("Prueba: di 'Jarvis' seguido de algo (Ctrl+C para salir)...")
    r = esperar_palabra(["jarvis", "yarvis"])
    print("Resto de la frase:", repr(r[0] if r else r))
