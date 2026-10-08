"""La voz de Jarvis.

Todo lo que dice pasa por una Locucion: el texto se parte en frases, cada frase se genera en
segundo plano (varias a la vez) y se reproduce en orden en cuanto está lista. Así:

- Con el modelo en streaming (genesis.py), Jarvis empieza a hablar con la PRIMERA frase
  mientras el modelo todavía escribe las siguientes.
- Con ElevenLabs el audio llega en streaming (PCM): la frase empieza a sonar antes de que
  termine de generarse.
- Frases que se repiten (rellenos como "Claro.", narraciones ensayadas de la demo) se guardan
  ya generadas en datos/voz_cache/ y suenan al instante.
- detener() la corta a media frase (para interrumpirlo diciendo "Hey Jarvis").
- Las locuciones se atienden por turno: un recordatorio nunca se encima con una respuesta.

Motores, en orden (config.json → voz_motor = "auto"): ElevenLabs (si hay clave y voz) → voces
neuronales de Edge (gratis, requieren internet) → Piper (local, voces/*.onnx) → voz de
Windows. Si uno falla, esa frase se genera con el siguiente, y el que falló se salta un minuto.
"""
import hashlib
import io
import json
import os
import queue
import re
import tempfile
import threading
import time
import wave
import winsound
from collections import OrderedDict
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

VOCES_DIR = Path(__file__).parent / "voces"
CACHE_DIR = Path(__file__).parent / "datos" / "voz_cache"
TASA = 24000                # todo se reproduce a 24 kHz, mono, int16
TROZO = TASA // 10          # se escribe al altavoz en pedazos de 0.1 s (para poder cortar rápido)
_piper = {}                 # ruta del modelo -> PiperVoice cargada
_salidas = {}               # nombre pedido -> índice de sounddevice (se recalcula si falla)
_CONF = {}                  # config.json completo; lo pone genesis.py con configurar()
_caidos = {}                # motor -> momento hasta el que no se intenta (falló hace poco)
_fallos = {}                # motor -> fallos seguidos (uno solo no basta para dejarlo de lado)
_memoria = OrderedDict()    # clave -> pcm (las últimas frases generadas, para no repetir trabajo)
_MAX_MEMORIA = 64
_generando = threading.BoundedSemaphore(3)  # frases generándose a la vez
# ElevenLabs limita las peticiones simultáneas por plan; pasarse daba error 429 y esa frase
# salía con OTRA voz (el respaldo) a media respuesta.
_eleven_sem = threading.BoundedSemaphore(2)

HABLANDO = threading.Event()   # activo mientras suena una locución
# detener() sube este contador: toda locución creada ANTES del corte se calla (incluidas las
# que esperaban turno), las creadas después suenan normal. Con un simple Event, un corte que
# llegaba justo cuando otra locución empezaba se perdía.
_corte = {"gen": 0}

# Turnos: cada locución toma un número al crearse y se reproduce cuando le toca. Con un
# candado simple, un relleno ("Claro.") y la respuesta que va después podían sonar al revés.
_turno_cond = threading.Condition()
_turno = {"siguiente": 0, "atendiendo": 0}


def configurar(cfg):
    global _CONF
    _CONF = cfg or {}


# ---------- Texto ----------
def _limpiar(texto):
    """Quita símbolos de markdown y espacios raros para que no los lea en voz alta."""
    texto = str(texto or "").replace(" ", " ").replace("\xa0", " ").replace("‑", "-")
    texto = re.sub(r"[*#`>~|]", "", texto).replace("_", " ")  # "abrir_app" -> "abrir app"
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


_FIN_FRASE = re.compile(r"(?<=[.!?…;:])\s+|\n+")


def partir_frases(texto, minimo=40):
    """Frases para generar y reproducir en cadena. Las muy cortas se juntan con la siguiente
    para que la entonación sea natural."""
    partes = [p for p in _FIN_FRASE.split(str(texto).strip()) if p and p.strip()]
    frases, actual = [], ""
    for p in partes:
        actual = f"{actual} {p}".strip()
        if len(actual) >= minimo:
            frases.append(actual)
            actual = ""
    if actual:
        if frases and len(actual) < minimo // 2:
            frases[-1] += " " + actual
        else:
            frases.append(actual)
    return frases or ([texto] if str(texto).strip() else [])


# ---------- Por dónde sale el audio ----------
# Con los lentes conectados a la PC, Windows suele mandar TODO el audio a los lentes. En modo
# expositor queremos lo contrario: la voz de Jarvis por las bocinas/proyector para el público.
# Por eso se puede elegir la salida por nombre ("Altavoces", "Realtek", "HDMI", "Ray-Ban"...).
# Vacío = la salida predeterminada de Windows.
def listar_salidas():
    import sounddevice as sd
    vistos, res = set(), []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and d["name"] not in vistos:
            vistos.add(d["name"])
            res.append((i, d["name"]))
    return res


def _norm(t):
    import unicodedata
    t = unicodedata.normalize("NFD", str(t).lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def resolver_salida(nombre):
    """Índice de sounddevice de la salida cuyo nombre se parece a 'nombre', o None."""
    if not nombre:
        return None
    if nombre in _salidas:
        return _salidas[nombre]
    objetivo = _norm(nombre)
    mejor, mejor_p = None, 0.0
    for i, n in listar_salidas():
        ln = _norm(n)
        p = SequenceMatcher(None, objetivo, ln).ratio()
        if objetivo in ln:
            p = max(p, 0.85)
        if p > mejor_p:
            mejor, mejor_p = i, p
    if mejor_p < 0.45:
        print(f"[No encontré la salida de audio '{nombre}'; uso la predeterminada]")
        mejor = None
    _salidas[nombre] = mejor
    return mejor


def pitido(dispositivo_nombre="", frecuencia=880, ms=150):
    """El 'bip' de "te escucho", por la misma salida que la voz privada."""
    dispositivo = resolver_salida(dispositivo_nombre)
    if dispositivo is None:
        winsound.Beep(frecuencia, ms)
        return
    import sounddevice as sd
    t = np.linspace(0, ms / 1000, int(22050 * ms / 1000), endpoint=False)
    onda = (0.25 * np.sin(2 * np.pi * frecuencia * t)).astype(np.float32)
    onda *= np.minimum(1, np.minimum(t, t[::-1]) * 60)  # sin chasquidos al inicio/fin
    try:
        sd.play(onda, 22050, device=dispositivo)
        sd.wait()
    except Exception:
        winsound.Beep(frecuencia, ms)


# ---------- Conversión de audio ----------
def _remuestrear(pcm, tasa):
    """int16 mono a TASA (interpolación lineal: de sobra para voz)."""
    pcm = np.asarray(pcm)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    if tasa == TASA or len(pcm) == 0:
        return pcm.astype(np.int16)
    n = int(round(len(pcm) * TASA / tasa))
    x = np.linspace(0, len(pcm) - 1, n)
    return np.interp(x, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)


def _wav_a_pcm(fuente):
    with wave.open(fuente, "rb") as w:
        tasa, canales, ancho = w.getframerate(), w.getnchannels(), w.getsampwidth()
        crudo = w.readframes(w.getnframes())
    tipo = {1: np.int8, 2: np.int16, 4: np.int32}[ancho]
    datos = np.frombuffer(crudo, dtype=tipo).reshape(-1, canales)
    if ancho == 4:
        datos = (datos >> 16).astype(np.int16)
    elif ancho == 1:
        datos = (datos.astype(np.int16) - 128) << 8
    return _remuestrear(datos, tasa)


def _mp3_a_pcm(mp3):
    import av  # ya viene instalado con faster-whisper
    fuente = io.BytesIO(mp3) if isinstance(mp3, (bytes, bytearray)) else str(mp3)
    with av.open(fuente) as cont:
        remuestreo = av.AudioResampler(format="s16", layout="mono", rate=TASA)
        trozos = []
        for frame in cont.decode(audio=0):
            for f in remuestreo.resample(frame):
                trozos.append(f.to_ndarray().reshape(-1))
    return np.concatenate(trozos).astype(np.int16) if trozos else np.zeros(0, np.int16)


# ---------- Motores ----------
def _conf_eleven():
    return _CONF.get("elevenlabs", {}) or {}


_ROTAS = ("ReadError", "RemoteProtocolError", "ConnectError", "WriteError", "LocalProtocolError")


def _eleven_trozos(frase, voice_id, clave):
    """ElevenLabs con reintentos cortos ANTES de que salga audio: si el plan rechaza por
    demasiadas peticiones a la vez (429) o si la conexión reutilizada ya la había cerrado el
    servidor. Una vez que empezó a sonar, no se reintenta (se repetiría el principio)."""
    for intento in range(3):
        dio_audio = False
        try:
            with _eleven_sem:
                for trozo in _eleven_pedir(frase, voice_id, clave):
                    dio_audio = True
                    yield trozo
            return
        except Exception as e:
            reintentable = ("HTTP 429" in str(e)) or type(e).__name__ in _ROTAS
            if dio_audio or not reintentable or intento == 2:
                raise
            time.sleep(0.1 if type(e).__name__ in _ROTAS else 0.4 * (intento + 1))


_http = {"cliente": None}
_http_lock = threading.Lock()


def _cliente_http():
    """Conexión HTTP persistente con ElevenLabs: reutilizarla ahorra ~0.2 s por frase (medido:
    primer audio 0.49 s con conexión nueva, 0.29 s reutilizada)."""
    with _http_lock:
        if _http["cliente"] is None:
            import httpx
            _http["cliente"] = httpx.Client(
                timeout=httpx.Timeout(12.0, connect=5.0),
                limits=httpx.Limits(max_connections=6, max_keepalive_connections=4,
                                    keepalive_expiry=50))
        return _http["cliente"]


def _eleven_pedir(frase, voice_id, clave, con_idioma=True):
    """ElevenLabs en streaming: va entregando PCM de 24 kHz conforme llega (la frase empieza
    a sonar ~0.3 s después de pedirla, sin esperar el archivo completo)."""
    conf = _conf_eleven()
    cuerpo = {
        "text": frase,
        "model_id": conf.get("modelo", "eleven_flash_v2_5"),
        "voice_settings": {"stability": conf.get("estabilidad", 0.45),
                           "similarity_boost": conf.get("similitud", 0.8),
                           "style": conf.get("estilo", 0.3), "use_speaker_boost": True,
                           "speed": conf.get("velocidad", 1.0)},
    }
    if con_idioma:
        cuerpo["language_code"] = "es"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_24000"
    with _cliente_http().stream("POST", url, json=cuerpo, headers={"xi-api-key": clave}) as r:
        if r.status_code != 200:
            detalle = r.read().decode("utf-8", "ignore")[:200]
            if con_idioma and r.status_code in (400, 422) and "language" in detalle.lower():
                yield from _eleven_pedir(frase, voice_id, clave, con_idioma=False)
                return
            raise RuntimeError(f"HTTP {r.status_code} {detalle}".strip())
        resto = b""
        for datos in r.iter_bytes(TROZO * 2):
            if not datos:
                continue
            datos = resto + datos
            n = len(datos) // 2 * 2
            resto = datos[n:]
            if n:
                yield np.frombuffer(datos[:n], dtype="<i2").copy()


def mantener_caliente():
    """Petición mínima (gratis) para que la conexión con ElevenLabs siga abierta y la próxima
    frase no pague el saludo TLS. La llama genesis.py cada ~40 s durante la exposición."""
    clave = os.environ.get("ELEVENLABS_API_KEY", "")
    if not (clave and _CONF.get("elevenlabs_voz") and _CONF.get("voz_motor", "auto") in ("auto", "elevenlabs")):
        return
    try:
        _cliente_http().get("https://api.elevenlabs.io/v1/models", headers={"xi-api-key": clave})
    except Exception:
        pass


def _edge_trozos(frase):
    """Voz de Edge EN STREAMING: va entregando PCM (int16, TASA) conforme llega el MP3, en vez
    de esperar la frase completa. El primer audio suena ~0.2-0.4 s antes (más en frases largas)."""
    import asyncio

    import av
    import edge_tts
    voz = _CONF.get("edge_voz", "es-MX-JorgeNeural")
    llegadas = queue.Queue()

    async def generar():
        com = edge_tts.Communicate(frase, voz, rate=_CONF.get("edge_velocidad", "+5%"),
                                   pitch=_CONF.get("edge_tono", "+0Hz"),
                                   connect_timeout=2.5, receive_timeout=10)  # red inestable: a Piper rápido
        async for trozo in com.stream():
            if trozo["type"] == "audio":
                llegadas.put(trozo["data"])

    def correr():
        try:
            asyncio.run(generar())
            llegadas.put(None)
        except Exception as e:  # se reenvía al consumidor: decide si probar otro motor
            llegadas.put(e)

    threading.Thread(target=correr, daemon=True, name="edge-stream").start()
    codec = av.CodecContext.create("mp3", "r")
    remuestreo = av.AudioResampler(format="s16", layout="mono", rate=TASA)
    while True:
        datos = llegadas.get(timeout=25)
        if datos is None:
            break
        if isinstance(datos, Exception):
            raise datos
        for paquete in codec.parse(datos):
            for cuadro in codec.decode(paquete):
                for f in remuestreo.resample(cuadro):
                    pcm = f.to_ndarray().reshape(-1).astype(np.int16)
                    if len(pcm):
                        yield pcm
    for cuadro in codec.decode(None):  # lo que quedó en el decodificador
        for f in remuestreo.resample(cuadro):
            pcm = f.to_ndarray().reshape(-1).astype(np.int16)
            if len(pcm):
                yield pcm


def _edge_pcm(frase):
    import asyncio

    import edge_tts
    voz = _CONF.get("edge_voz", "es-MX-JorgeNeural")

    async def generar():
        com = edge_tts.Communicate(frase, voz, rate=_CONF.get("edge_velocidad", "+5%"),
                                   pitch=_CONF.get("edge_tono", "+0Hz"),
                                   connect_timeout=2.5, receive_timeout=10)  # red inestable: a Piper rápido
        buf = bytearray()
        async for trozo in com.stream():
            if trozo["type"] == "audio":
                buf.extend(trozo["data"])
        return bytes(buf)

    mp3 = asyncio.run(generar())
    if not mp3:
        raise RuntimeError("el servicio de voz no devolvió audio")
    return _mp3_a_pcm(mp3)


def _modelo_piper():
    nombre = _CONF.get("voz_nombre", "")
    ruta = VOCES_DIR / f"{nombre}.onnx"
    return ruta if nombre and ruta.exists() else None


def _piper_pcm(frase):
    from piper import PiperVoice
    ruta = _modelo_piper()
    if ruta is None:
        raise RuntimeError("no hay modelo de Piper")
    if ruta not in _piper:
        _piper[ruta] = PiperVoice.load(str(ruta))
    voice = _piper[ruta]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(frase, w)
        else:  # versiones antiguas de piper
            voice.synthesize(frase, w)
    buf.seek(0)
    return _wav_a_pcm(buf)


def _sapi_pcm(frase):
    """Voz de Windows directo por SAPI (COM), a un WAV temporal."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    sp = win32com.client.Dispatch("SAPI.SpVoice")
    voces = sp.GetVoices()
    elegida, preferida = None, (_CONF.get("voz_nombre") or "").lower()
    for i in range(voces.Count):
        desc = voces.Item(i).GetDescription().lower()
        if preferida and preferida in desc:
            elegida = voces.Item(i)
            break
        if elegida is None and any(x in desc for x in ("spanish", "español", "espanol", "sabina",
                                                          "helena", "raul", "laura", "pablo")):
            elegida = voces.Item(i)
    if elegida is not None:
        sp.Voice = elegida
    # pyttsx3 usa palabras por minuto (~180 normal); SAPI usa -10..10 (0 normal)
    sp.Rate = max(-10, min(10, round((int(_CONF.get("voz_velocidad", 180)) - 180) / 20)))
    fd, ruta = tempfile.mkstemp(suffix=".wav", prefix="jarvis_sapi_")
    os.close(fd)
    try:
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 22  # SAFT22kHz16BitMono
        stream.Open(ruta, 3)  # SSFMCreateForWrite
        sp.AudioOutputStream = stream
        sp.Speak(frase)
        stream.Close()
        return _wav_a_pcm(ruta)
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass


def _todos_los_motores(voz_natural, forzado=None):
    """Motores en orden de preferencia según config.json (sin mirar cuáles fallaron)."""
    if forzado:
        return [forzado]
    motor = _CONF.get("voz_motor", "auto")
    clave = os.environ.get("ELEVENLABS_API_KEY", "")
    lista = []
    if voz_natural and clave and motor in ("auto", "elevenlabs"):
        lista.append("elevenlabs")
    if motor in ("auto", "edge", "elevenlabs"):
        lista.append("edge")
    if motor != "windows" and _modelo_piper() is not None:
        lista.append("piper")
    lista.append("windows")
    return lista


def _motores(voz_natural, forzado=None):
    """Motores a probar para esta frase: los preferidos, sin los que fallaron hace poco."""
    lista = _todos_los_motores(voz_natural, forzado)
    if forzado:
        return lista
    ahora = time.time()
    vivos = [m for m in lista if _caidos.get(m, 0) < ahora]
    if any(m in ("elevenlabs", "edge") for m in vivos):
        # Sin internet, cada frase esperaba hasta 6 s a que Edge no conectara antes de pasar a
        # Piper: se pregunta antes (respuesta guardada 30 s, no cuesta nada)
        try:
            import cerebro
            if not cerebro.hay_internet("speech.platform.bing.com"):
                vivos = [m for m in vivos if m not in ("elevenlabs", "edge")]
        except Exception:
            pass
    return vivos or ["windows"]


def _clave_cache(frase, voz_natural, forzado=None):
    """Identifica una frase generada con la voz PREFERIDA actual. Si se guardara lo que generó
    un respaldo, al volver ElevenLabs sonarían dos voces distintas en la misma demo."""
    motor = _todos_los_motores(voz_natural, forzado)[0]
    ajustes = {"elevenlabs": [voz_natural, _conf_eleven()],
               "edge": [_CONF.get("edge_voz"), _CONF.get("edge_velocidad"), _CONF.get("edge_tono")]
               }.get(motor, [_CONF.get("voz_nombre"), _CONF.get("voz_velocidad")])
    base = json.dumps([motor, ajustes, frase], ensure_ascii=False, sort_keys=True, default=str)
    return motor, hashlib.sha1(base.encode("utf-8")).hexdigest()


def _leer_cache(clave):
    if clave in _memoria:
        _memoria.move_to_end(clave)
        return _memoria[clave]
    ruta = CACHE_DIR / f"{clave}.wav"
    if ruta.exists():
        try:
            pcm = _wav_a_pcm(str(ruta))
            _recordar(clave, pcm)
            return pcm
        except Exception:
            return None
    return None


def _recordar(clave, pcm):
    _memoria[clave] = pcm
    _memoria.move_to_end(clave)
    while len(_memoria) > _MAX_MEMORIA:
        _memoria.popitem(last=False)


def _guardar_disco(clave, pcm):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / f"{clave}.tmp"
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TASA)
            w.writeframes(np.asarray(pcm, np.int16).tobytes())
        os.replace(tmp, CACHE_DIR / f"{clave}.wav")
    except OSError as e:
        print(f"[No pude guardar la voz en caché: {e}]")


class _Clip:
    """El audio de una frase, que se va llenando en segundo plano (a pedazos si el motor hace
    streaming, de golpe si no)."""

    def __init__(self, frase):
        self.frase = frase
        self._q = queue.Queue()
        self.error = None
        self.t_creado = time.time()
        self.t_primer_audio = None   # cuándo llegó el primer pedazo generado (para medir)
        self.motor = ""

    def poner(self, pcm):
        if len(pcm):
            if self.t_primer_audio is None:
                self.t_primer_audio = time.time()
            self._q.put(pcm)

    def terminar(self, error=None):
        self.error = error
        self._q.put(None)

    def trozos(self, limite=20.0):
        while True:
            try:
                pcm = self._q.get(timeout=limite)
            except queue.Empty:
                self.error = TimeoutError("la voz tardó demasiado")
                return
            if pcm is None:
                return
            yield pcm


def _generar(clip, voz_natural, guardar=False, forzado=None):
    """Llena el clip: caché → motores en orden. Si un motor falla ANTES de dar audio se prueba
    el siguiente; si falla a media frase (streaming), la frase se corta ahí."""
    if not re.search(r"\w", clip.frase or ""):
        # Solo "..." o signos: ningún motor tiene nada que decir. Antes Edge y Piper "fallaban"
        # con eso y, a los dos fallos, Edge quedaba apartado 30 s (frases reales con la voz de
        # respaldo)
        clip.motor = "nada"
        clip.terminar()
        return
    motor_pref, clave = _clave_cache(clip.frase, voz_natural, forzado)
    pcm = _leer_cache(clave)
    if pcm is not None:
        clip.motor = "caché"
        clip.poner(pcm)
        clip.terminar()
        return
    with _generando:
        for motor in _motores(voz_natural, forzado):
            dio_audio = False
            clip.motor = motor
            try:
                if motor in ("elevenlabs", "edge"):  # los dos en streaming
                    partes = []
                    trozos = (_eleven_trozos(clip.frase, voz_natural,
                                             os.environ.get("ELEVENLABS_API_KEY", ""))
                              if motor == "elevenlabs" else _edge_trozos(clip.frase))
                    for trozo in trozos:
                        dio_audio = True
                        partes.append(trozo)
                        clip.poner(trozo)
                    if not partes:
                        raise RuntimeError(f"{motor} no devolvió audio")
                    pcm = np.concatenate(partes)
                else:
                    pcm = {"edge": _edge_pcm, "piper": _piper_pcm, "windows": _sapi_pcm}[motor](clip.frase)
                    clip.poner(pcm)
                if motor == motor_pref:
                    _recordar(clave, pcm)
                    if guardar:
                        _guardar_disco(clave, pcm)
                _fallos[motor] = 0
                clip.terminar()
                return
            except Exception as e:
                print(f"[Voz: {motor} falló ({type(e).__name__}: {str(e)[:120]})"
                      + ("" if dio_audio else "; pruebo el siguiente") + "]")
                # Un fallo suelto (un corte de red de un segundo) no debe mandar el resto de la
                # exposición a la voz de respaldo, que además es más lenta: se aparta el motor
                # solo tras dos fallos seguidos, y por 30 s.
                _fallos[motor] = _fallos.get(motor, 0) + 1
                sin_conexion = type(e).__name__ in ("ConnectionTimeoutError", "ClientConnectorError",
                                                    "ConnectError", "ConnectTimeout", "TimeoutError")
                if motor != "windows" and (_fallos[motor] >= 2 or sin_conexion):
                    # sin conexión no es un tropiezo suelto: cada frase esperaría lo mismo
                    _caidos[motor] = time.time() + 30
                if dio_audio:
                    clip.terminar(e)
                    return
    clip.terminar(RuntimeError("ningún motor de voz respondió"))


# ---------- Locución ----------
class Locucion:
    """Una intervención de Jarvis. Se le puede ir agregando texto (streaming del modelo) y
    suena frase por frase, en orden, por la salida elegida.

        loc = Locucion(salida="Realtek", al_frase=hud.subtitulo)
        loc.agregar("Claro. "); loc.agregar("Esto es...")
        loc.cerrar(); loc.esperar()

    filtro(frase) -> bool: si devuelve False (p. ej. la frase trae caracteres chinos), la
    locución se cancela sin decir esa frase y queda rechazada=True."""

    def __init__(self, salida="", voz_natural=None, al_frase=None, al_empezar=None,
                 filtro=None, guardar=False, motor=None, primera_min=12, resto_min=40,
                 dispositivo=None):
        self._gen = _corte["gen"]
        self.dispositivo = dispositivo if dispositivo is not None else resolver_salida(salida)
        self.voz_natural = _CONF.get("elevenlabs_voz", "") if voz_natural is None else voz_natural
        self.al_frase, self.al_empezar, self.filtro = al_frase, al_empezar, filtro
        self.guardar, self.motor = guardar, motor
        self.primera_min, self.resto_min = primera_min, resto_min
        self.dijo_algo = False
        self.rechazada = False
        self.cancelada = False
        self.t_primer_audio = None
        self.t_primera_frase = None   # cuándo se tuvo la primera frase completa (para medir)
        self.primer_clip = None
        self.texto_dicho = []
        self._pendiente = ""
        self._frases_encoladas = 0
        self._clips = queue.Queue()
        self._cerrada = False
        with _turno_cond:
            self._ticket = _turno["siguiente"]
            _turno["siguiente"] += 1
        self._hilo = threading.Thread(target=self._reproducir, daemon=True, name="voz")
        self._hilo.start()

    # --- texto que entra ---
    def agregar(self, fragmento):
        if self._cerrada or self.cancelada:
            return
        self._pendiente += str(fragmento)
        while True:
            m = _FIN_FRASE.search(self._pendiente)
            if not m:
                break
            candidata = self._pendiente[:m.start() + 1].strip()
            minimo = self.primera_min if self._frases_encoladas == 0 else self.resto_min
            if len(candidata) < minimo:
                # muy corta para sonar natural: se espera a juntarla con la siguiente, salvo
                # que ya no quede nada más por buscar en el buffer
                siguiente = _FIN_FRASE.search(self._pendiente, m.end())
                if not siguiente:
                    break
                candidata = self._pendiente[:siguiente.start() + 1].strip()
                self._pendiente = self._pendiente[siguiente.end():]
            else:
                self._pendiente = self._pendiente[m.end():]
            self._encolar(candidata)

    def cerrar(self):
        if self._cerrada:
            return
        if self._pendiente.strip() and not self.cancelada:
            self._encolar(self._pendiente.strip())
        self._pendiente = ""
        self._cerrada = True
        self._clips.put(None)

    def _encolar(self, frase):
        frase = _limpiar(frase)
        if not frase or self.cancelada:
            return
        if self.filtro is not None and not self.filtro(frase):
            self.rechazada = True
            self.cancelar()
            return
        self._frases_encoladas += 1
        clip = _Clip(frase)
        if self.primer_clip is None:
            self.primer_clip = clip
            self.t_primera_frase = clip.t_creado
        self._clips.put(clip)
        threading.Thread(target=_generar, args=(clip, self.voz_natural, self.guardar, self.motor),
                         daemon=True, name="voz-generar").start()

    # --- control ---
    def cancelar(self):
        self.cancelada = True
        if not self._cerrada:
            self._cerrada = True
            self._clips.put(None)

    def esperar(self, limite=None):
        self._hilo.join(limite)
        return not self._hilo.is_alive()

    @property
    def terminada(self):
        return not self._hilo.is_alive()

    def _cortada(self):
        return self.cancelada or _corte["gen"] != self._gen

    # --- reproducción (hilo propio) ---
    def _esperar_turno(self):
        with _turno_cond:
            while _turno["atendiendo"] != self._ticket:
                _turno_cond.wait(0.5)

    def _soltar_turno(self):
        with _turno_cond:
            _turno["atendiendo"] = self._ticket + 1
            _turno_cond.notify_all()

    def _abrir(self, dispositivo):
        import sounddevice as sd
        s = sd.OutputStream(samplerate=TASA, channels=1, dtype="int16", device=dispositivo,
                            latency="low")
        s.start()
        return s

    def _reproducir(self):
        self._esperar_turno()
        stream = None
        try:
            while True:
                try:
                    # Red de seguridad: una locución que nadie cierra no debe tener tomado el
                    # turno para siempre (callaría a Jarvis el resto de la sesión).
                    clip = self._clips.get(timeout=45)
                except queue.Empty:
                    print("[Una locución quedó abierta sin texto; la cierro]")
                    break
                if clip is None or self._cortada():
                    break
                empezo = False
                for pcm in clip.trozos():
                    if self._cortada():
                        break
                    if stream is None:
                        try:
                            stream = self._abrir(self.dispositivo)
                        except Exception as e:
                            # la salida pudo desconectarse (lentes apagados, HDMI quitado): se
                            # usa la predeterminada en vez de quedarse mudo
                            print(f"[La salida de audio falló ({type(e).__name__}); uso la predeterminada]")
                            _salidas.clear()
                            stream = self._abrir(None)
                    if not empezo:
                        empezo = True
                        self.texto_dicho.append(clip.frase)
                        if self.t_primer_audio is None:
                            self.t_primer_audio = time.time()
                            HABLANDO.set()
                            if self.al_empezar:
                                self._llamar(self.al_empezar)
                        if self.al_frase:
                            self._llamar(self.al_frase, clip.frase)
                    for i in range(0, len(pcm), TROZO):
                        if self._cortada():
                            break
                        stream.write(pcm[i:i + TROZO].reshape(-1, 1))
                    self.dijo_algo = True
                if clip.error and not empezo:
                    print(f"[No pude decir: '{clip.frase[:60]}' ({clip.error})]")
        except Exception as e:
            print(f"[Error de audio: {type(e).__name__}: {str(e)[:150]}]")
        finally:
            if stream is not None:
                try:
                    if self._cortada():
                        stream.abort()
                    else:
                        stream.stop()   # espera a que termine de sonar lo que ya se escribió
                    stream.close()
                except Exception:
                    pass
            HABLANDO.clear()
            self._soltar_turno()

    @staticmethod
    def _llamar(fn, *args):
        try:
            fn(*args)
        except Exception as e:
            print(f"[Error en aviso de voz: {e}]")


def detener():
    """Corta lo que esté diciendo Jarvis y todo lo que esperaba turno para hablar (se usa al
    interrumpirlo con "Hey Jarvis"). Lo que se pida decir después suena normal."""
    _corte["gen"] += 1


def hablar(texto, velocidad=180, voz="", voz_natural="", salida="", al_frase=None, motor=None):
    """Dice un texto completo y regresa cuando terminó (compatibilidad con el código viejo).
    velocidad y voz se leen ahora de config.json (voz_velocidad, voz_nombre)."""
    texto = _limpiar(texto)
    if not texto:
        return None
    loc = Locucion(salida=salida, voz_natural=voz_natural, al_frase=al_frase, motor=motor)
    loc.agregar(texto)
    loc.cerrar()
    loc.esperar()
    return loc


def en_cache(frase, voz_natural=None):
    """True si la frase ya está generada con la voz actual (suena al instante)."""
    vn = _CONF.get("elevenlabs_voz", "") if voz_natural is None else voz_natural
    return _leer_cache(_clave_cache(_limpiar(frase), vn)[1]) is not None


def precalentar(frases, voz_natural=None):
    """Genera y guarda en disco las frases (rellenos, narraciones ensayadas) para que luego
    suenen al instante. Se llama en segundo plano; no reproduce nada."""
    vn = _CONF.get("elevenlabs_voz", "") if voz_natural is None else voz_natural
    hechas = 0
    for frase in frases:
        frase = _limpiar(frase)
        if not frase or en_cache(frase, vn):
            continue
        clip = _Clip(frase)
        _generar(clip, vn, guardar=True)
        for _ in clip.trozos():
            pass
        hechas += clip.error is None
    return hechas


# ---------- Compatibilidad (diagnostico.py) ----------
def _hablar_edge(texto, dispositivo=None):
    """Dice un texto con la voz de Edge configurada (diagnostico.py voces)."""
    loc = Locucion(motor="edge", dispositivo=dispositivo)
    loc.agregar(texto)
    loc.cerrar()
    loc.esperar()


if __name__ == "__main__":
    print("Salidas de audio:", listar_salidas())
    print("Probando voz por la salida predeterminada...")
    try:
        configurar(json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    hablar("Hola, soy Jarvis. Sistema de voz en línea.")
