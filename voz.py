import ctypes
import io
import json
import os
import queue
import re
import tempfile
import threading
import wave
import winsound
from difflib import SequenceMatcher
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import numpy as np
import pyttsx3

VOCES_DIR = Path(__file__).parent / "voces"
_cache = {}
_candado = threading.Lock()
_salidas = {}  # nombre pedido -> índice de sounddevice (se recalcula si falla)
_CONF = {}     # config.json completo; lo pone genesis.py con configurar()


def configurar(cfg):
    global _CONF
    _CONF = cfg or {}


def _limpiar(texto):
    """Quita símbolos de markdown para que no los lea en voz alta."""
    texto = re.sub(r"[*_#`>~]", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


# ---------- Elegir por dónde sale el audio ----------
# Con los lentes conectados a la PC, Windows suele mandar TODO el audio a los lentes. En modo
# expositor queremos lo contrario: la voz de Jarvis por las bocinas/proyector para el público.
# Por eso se puede elegir la salida por nombre ("Altavoces", "Realtek", "HDMI", "Ray-Ban"...).
# Vacío = la salida predeterminada de Windows (el comportamiento original de Genesis).
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


def _reproducir_pcm(datos, rate, dispositivo):
    import sounddevice as sd
    try:
        sd.play(datos, rate, device=dispositivo)
        sd.wait()
    except Exception as e:
        # la salida pudo desconectarse (lentes apagados, HDMI quitado): se reintenta en la
        # predeterminada en vez de quedarse mudo
        print(f"[La salida de audio falló ({type(e).__name__}); uso la predeterminada]")
        _salidas.clear()
        sd.play(datos, rate)
        sd.wait()


def _reproducir_wav(ruta, dispositivo):
    if dispositivo is None:
        winsound.PlaySound(str(ruta), winsound.SND_FILENAME)
        return
    with wave.open(str(ruta), "rb") as w:
        rate, canales, ancho = w.getframerate(), w.getnchannels(), w.getsampwidth()
        crudo = w.readframes(w.getnframes())
    tipo = {1: np.int8, 2: np.int16, 4: np.int32}[ancho]
    datos = np.frombuffer(crudo, dtype=tipo).reshape(-1, canales)
    _reproducir_pcm(datos, rate, dispositivo)


def pitido(dispositivo_nombre="", frecuencia=880, ms=150):
    """El 'bip' de "te escucho", por la misma salida que la voz privada."""
    dispositivo = resolver_salida(dispositivo_nombre)
    if dispositivo is None:
        winsound.Beep(frecuencia, ms)
        return
    t = np.linspace(0, ms / 1000, int(22050 * ms / 1000), endpoint=False)
    onda = (0.25 * np.sin(2 * np.pi * frecuencia * t)).astype(np.float32)
    onda *= np.minimum(1, np.minimum(t, t[::-1]) * 60)  # sin chasquidos al inicio/fin
    _reproducir_pcm(onda, 22050, dispositivo)


# ---------- ElevenLabs (voz natural en la nube, opcional) ----------
# Se activa poniendo ELEVENLABS_API_KEY y "elevenlabs_voz" (el ID de una voz de tu cuenta,
# elevenlabs.io/app/voice-library) en config.json. Sin esos dos datos, ni lo intenta.
# Añade 1-2 s de red por respuesta a cambio de sonar mucho más natural que Piper.
def _reproducir_mp3(ruta):
    """MP3 con el códec de Windows (winmm/MCI): nada de librerías nuevas ni ffmpeg."""
    mci = ctypes.windll.winmm
    alias = "genesis_voz_mp3"
    mci.mciSendStringW(f'open "{ruta}" type mpegvideo alias {alias}', None, 0, None)
    try:
        mci.mciSendStringW(f"play {alias} wait", None, 0, None)
    finally:
        mci.mciSendStringW(f"close {alias}", None, 0, None)


def _mp3_a_pcm(ruta):
    import av  # ya viene instalado con faster-whisper
    fuente = io.BytesIO(ruta) if isinstance(ruta, (bytes, bytearray)) else str(ruta)
    with av.open(fuente) as cont:
        remuestreo = av.AudioResampler(format="s16", layout="mono", rate=24000)
        trozos = []
        for frame in cont.decode(audio=0):
            for f in remuestreo.resample(frame):
                trozos.append(f.to_ndarray().reshape(-1))
    return np.concatenate(trozos).astype(np.int16), 24000


def _eleven_pcm(frase, voice_id, clave):
    """Una frase con ElevenLabs. Por defecto usa eleven_flash_v2_5: su modelo de latencia más
    baja (~0.3 s), multilingüe; eleven_multilingual_v2 suena un poco mejor pero tarda más."""
    conf = _CONF.get("elevenlabs", {}) or {}
    payload = json.dumps({
        "text": frase,
        "model_id": conf.get("modelo", "eleven_flash_v2_5"),
        "language_code": "es",
        "voice_settings": {"stability": conf.get("estabilidad", 0.45),
                           "similarity_boost": conf.get("similitud", 0.8),
                           "style": conf.get("estilo", 0.3), "use_speaker_boost": True,
                           "speed": conf.get("velocidad", 1.0)},
    }).encode("utf-8")
    req = Request(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                  data=payload, headers={"xi-api-key": clave, "Content-Type": "application/json",
                                        "Accept": "audio/mpeg"})
    try:
        audio = urlopen(req, timeout=12).read()
    except Exception as e:
        detalle = ""
        if hasattr(e, "read"):
            try:
                detalle = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
        raise RuntimeError(f"{e} {detalle}".strip()) from e
    return _mp3_a_pcm(audio)


def _hablar_elevenlabs(texto, voice_id, clave, dispositivo):
    _en_cadena(texto, lambda f: _eleven_pcm(f, voice_id, clave), dispositivo)

# ---------- Voces neuronales de Microsoft Edge (gratis, muy naturales, requiere internet) ----------
# Son las mismas voces de "Leer en voz alta" de Edge / Azure. Para escoger una:
#   python diagnostico.py voces              -> lista las voces en español y multilingües
#   python diagnostico.py voces es-MX-JorgeNeural   -> la escuchas
# y la pones en config.json → "edge_voz".
def _partir_frases(texto, minimo=40):
    """Frases para ir generando y reproduciendo en cadena: mientras suena la primera ya se
    está generando la segunda, así la respuesta empieza a sonar antes y sin pausas largas.
    Las frases muy cortas se juntan con la siguiente para que la entonación sea natural."""
    partes = re.split(r"(?<=[.!?¿¡…;:])\s+", texto.strip())
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
    return frases or [texto]


def _edge_pcm(frase, voz, velocidad, tono):
    import asyncio

    import edge_tts

    async def generar():
        com = edge_tts.Communicate(frase, voz, rate=velocidad, pitch=tono,
                                   connect_timeout=6, receive_timeout=20)
        buf = bytearray()
        async for trozo in com.stream():
            if trozo["type"] == "audio":
                buf.extend(trozo["data"])
        return bytes(buf)

    mp3 = asyncio.run(generar())
    if not mp3:
        raise RuntimeError("el servicio de voz no devolvió audio")
    return _mp3_a_pcm(mp3)


def _en_cadena(texto, generar, dispositivo):
    """Genera frase por frase en un hilo y va reproduciendo: la primera frase suena en cuanto
    está lista mientras se preparan las siguientes (sirve para Edge y para ElevenLabs)."""
    frases = _partir_frases(texto)
    cola = queue.Queue(maxsize=3)

    def producir():
        for f in frases:
            try:
                cola.put(generar(f))
            except Exception as e:
                cola.put(e)
                return
        cola.put(None)

    threading.Thread(target=producir, daemon=True, name="voz-cadena").start()
    sono_algo = False
    while True:
        try:
            item = cola.get(timeout=15)
        except queue.Empty:
            item = TimeoutError("la voz en línea tardó demasiado")
        if item is None:
            return
        if isinstance(item, Exception):
            if not sono_algo:
                raise item  # nada sonó todavía: hablar() prueba el siguiente motor
            print(f"[La voz en línea se cortó: {type(item).__name__}]")
            return
        datos, rate = item
        _reproducir_pcm(datos, rate, dispositivo)
        sono_algo = True


def _hablar_edge(texto, dispositivo):
    voz = _CONF.get("edge_voz", "es-MX-JorgeNeural")
    velocidad = _CONF.get("edge_velocidad", "+5%")
    tono = _CONF.get("edge_tono", "+0Hz")
    _en_cadena(texto, lambda f: _edge_pcm(f, voz, velocidad, tono), dispositivo)

# ---------- Piper (voz local, la más natural sin depender de la nube) ----------
def _hablar_piper(texto, modelo, dispositivo):
    from piper import PiperVoice

    if modelo not in _cache:
        _cache[modelo] = PiperVoice.load(str(modelo))
    voice = _cache[modelo]

    wav_path = Path(tempfile.gettempdir()) / "genesis_voz.wav"
    with wave.open(str(wav_path), "wb") as wav:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(texto, wav)
        else:  # versiones antiguas de piper
            voice.synthesize(texto, wav)
    _reproducir_wav(wav_path, dispositivo)


# ---------- Voz de Windows (último respaldo) ----------
def _elegir_voz(engine, preferida=""):
    voces = engine.getProperty("voices")
    if preferida:
        for v in voces:
            if preferida.lower() in v.name.lower():
                return v.id
    for v in voces:
        if "spanish" in v.name.lower() or "es-" in v.id.lower():
            return v.id
    return None


def _hablar_sapi(texto, velocidad, voz, dispositivo):
    """Voz de Windows directo por SAPI (COM). Más confiable que pyttsx3, que con Python 3.13
    a veces termina sin error pero sin sonar."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    sp = win32com.client.Dispatch("SAPI.SpVoice")
    voces = sp.GetVoices()
    elegida = None
    preferida = (voz or "").lower()
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
    sp.Rate = max(-10, min(10, round((int(velocidad) - 180) / 20)))
    if dispositivo is None:
        sp.Speak(texto)  # síncrono: regresa cuando termina de hablar
        return
    ruta = Path(tempfile.gettempdir()) / "genesis_voz_sapi.wav"
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 22  # SAFT22kHz16BitMono
    stream.Open(str(ruta), 3)  # SSFMCreateForWrite
    sp.AudioOutputStream = stream
    sp.Speak(texto)
    stream.Close()
    _reproducir_wav(ruta, dispositivo)


def _hablar_windows(texto, velocidad, voz, dispositivo):
    try:
        _hablar_sapi(texto, velocidad, voz, dispositivo)
        return
    except Exception as e:
        print(f"[La voz de Windows (SAPI) falló: {type(e).__name__}: {str(e)[:120]}; pruebo pyttsx3]")
    engine = pyttsx3.init()
    engine.setProperty("rate", velocidad)
    voz_id = _elegir_voz(engine, voz)
    if voz_id:
        engine.setProperty("voice", voz_id)
    if dispositivo is None:
        engine.say(texto)
        engine.runAndWait()
        return
    ruta = Path(tempfile.gettempdir()) / "genesis_voz_win.wav"
    engine.save_to_file(texto, str(ruta))
    engine.runAndWait()
    _reproducir_wav(ruta, dispositivo)


def hablar(texto, velocidad=180, voz="", voz_natural="", salida=""):
    """voz_natural: ID de voz de ElevenLabs (config.json → elevenlabs_voz). Vacío = no usarla.
    salida: nombre (aproximado) de la salida de audio; vacío = la predeterminada de Windows."""
    texto = _limpiar(texto)
    if not texto:
        return

    # Los avisos hablan desde otro hilo: el candado evita que se pisen con la respuesta
    with _candado:
        dispositivo = resolver_salida(salida)
        # voz_motor: "auto" (ElevenLabs si está configurado, si no Edge, si no Piper, si no
        # Windows), o fijo: "elevenlabs", "edge", "piper", "windows"
        motor = _CONF.get("voz_motor", "auto")
        clave = os.environ.get("ELEVENLABS_API_KEY", "")
        destino = f"salida {dispositivo}" if dispositivo is not None else "salida predeterminada de Windows"
        if voz_natural and clave and motor in ("auto", "elevenlabs"):
            try:
                print(f"[Voz: ElevenLabs {voz_natural[:8]}… → {destino}]")
                _hablar_elevenlabs(texto, voz_natural, clave, dispositivo)
                return
            except Exception as e:
                print(f"[ElevenLabs falló ({type(e).__name__}: {str(e)[:160]}); uso Edge]")
        if motor in ("auto", "edge"):
            try:
                print(f"[Voz: Edge {_CONF.get('edge_voz', 'es-MX-JorgeNeural')} → {destino}]")
                _hablar_edge(texto, dispositivo)
                return
            except Exception as e:
                print(f"[La voz en línea (Edge) falló: {type(e).__name__}: {str(e)[:120]}; uso Piper]")
        if motor == "windows":
            voz = ""  # fuerza a saltar Piper

        modelo = VOCES_DIR / f"{voz}.onnx"
        if voz and modelo.exists() and motor != "windows":
            try:
                print(f"[Voz: Piper → {destino}]")
                _hablar_piper(texto, modelo, dispositivo)
                return
            except Exception as e:
                print(f"[Piper falló, uso voz de Windows: {e}]")

        try:
            _hablar_windows(texto, velocidad, voz, dispositivo)
        except Exception as e:
            print(f"[No pude hablar: {type(e).__name__}: {str(e)[:150]}]")


if __name__ == "__main__":
    print("Salidas de audio:", listar_salidas())
    print("Probando voz por la salida predeterminada...")
    try:
        configurar(json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    hablar(
        "Hola, soy Jarvis. Sistema de voz en línea.",
        voz="es_MX-claude-high",
    )
