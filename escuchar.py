import json
import os
import re
import threading
import time
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import sounddevice as sd

from skills import skill

SAMPLE_RATE = 16000
CONFIG_PATH = Path(__file__).parent / "config.json"
_modelos = {}

# Cuando está activo (icono de bandeja → Pausar), Genesis no escucha la palabra de activación
PAUSA = threading.Event()

# Se activa para cortar una escucha en curso (p. ej. cuando la orden llegó escrita en la ventana)
CANCELAR = threading.Event()

# Lo activa el icono de la bandeja ("Escribir una orden"): despierta a Genesis sin la palabra de
# activación, para que se pueda usar por teclado aunque el micrófono no esté oyendo nada.
DESPERTAR = threading.Event()

# Micrófono a usar (índice de sounddevice); None = el que tenga Windows por defecto.
# Se fija desde config.json → mic_dispositivo si el automático da problemas.
DISPOSITIVO = None


def _cargar_modelo(nombre):
    if nombre not in _modelos:
        from faster_whisper import WhisperModel

        print(f"[Cargando modelo de voz '{nombre}'...]")
        _modelos[nombre] = WhisperModel(nombre, device="cpu", compute_type="int8")
    return _modelos[nombre]


def calibrar_umbral(minimo=0.0025, segundos=2.0):
    """Mide el ruido de fondo real del micrófono y fija el umbral a partir de ahí.

    Un número fijo en config.json no sirve para todos los cuartos ni micrófonos: si queda
    muy por encima del volumen real al que hablas, Genesis nunca detecta que empezaste a
    hablar (parece que "no escucha" o "no contesta"); si queda muy por debajo, cualquier
    ruido de fondo dispara una grabación falsa y esa transcripción de basura ocupa el hueco
    justo en el que podrías haber dicho "Genesis" de verdad. minimo es el piso por si el
    cuarto está completamente en silencio (para no quedar en un umbral de prácticamente 0).
    """
    bloque = int(SAMPLE_RATE * 0.1)
    niveles = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=DISPOSITIVO,
                            dtype="float32", blocksize=bloque) as stream:
            for _ in range(max(1, int(segundos / 0.1))):
                data, _ = stream.read(bloque)
                niveles.append(float(np.sqrt(np.mean(data ** 2))))
    except Exception as e:
        print(f"[No pude calibrar el micrófono ({type(e).__name__}); uso el umbral por defecto.]")
        return minimo
    NIVELES.extend(niveles)  # el umbral dinámico arranca ya con el ruido real del cuarto
    niveles.sort()
    # percentil 25 en vez de la mediana: si justo durante la calibración hubo un ruido
    # puntual (un clic, una puerta), que no arrastre el umbral de todo el resto de la sesión
    ambiente = niveles[len(niveles) // 4]
    umbral = max(minimo, ambiente * 2.5)
    print(f"[Micrófono calibrado: ruido de fondo {ambiente:.4f}, umbral {umbral:.4f}]")
    return umbral


# Tras hablar, Jarvis ignora el micrófono un momento: si su propia voz sale por bocinas que el
# micrófono alcanza a oír (o regresa por la videollamada de los lentes), no se escucha a sí mismo.
IGNORAR_HASTA = 0.0

# Segundos de silencio que esperan para dar por terminada tu frase (config.json →
# silencio_seg). Menos = responde antes, pero si haces pausas al hablar te puede cortar.
SILENCIO_SEG = 0.8


def ignorar_por(segundos):
    global IGNORAR_HASTA
    IGNORAR_HASTA = max(IGNORAR_HASTA, time.time() + segundos)


# ---------- Umbral que sigue al ruido del cuarto ----------
# Antes el umbral se medía una vez al arrancar y, si pasaban 30 s sin "voz", se bajaba solo...
# hasta 0.003, aunque el ruido real del cuarto fuera 0.02-0.04. Con el umbral por debajo del
# ruido, la grabación nunca encontraba silencio: grababa los 20 s máximos de ruido, Whisper
# tardaba hasta 21 s en transcribirlo y las órdenes se pegaban entre sí. Ahora se lleva el
# nivel de los últimos ~15 s y el umbral siempre queda por encima del piso de ruido real.
NIVELES = deque(maxlen=150)   # volumen (RMS) de cada bloque de 0.1 s
MARGEN_RUIDO = 2.5            # cuántas veces el ruido de fondo hace falta para contar como voz
UMBRAL_MINIMO = 0.0035


def umbral_actual(base):
    """Umbral para este momento: base (calibrado) hasta tener datos, y luego el piso de ruido
    de los últimos segundos (percentil 10: lo más bajo, sin los silencios absolutos) x margen."""
    if len(NIVELES) < 30:
        return base
    piso = float(np.percentile(NIVELES, 10))
    return max(UMBRAL_MINIMO, piso * MARGEN_RUIDO)


def _grabar_de(stream, umbral, silencio_seg, max_seg, espera_seg, previo_inicial=None):
    """Graba de un stream ya abierto: espera voz, graba y corta al quedarse en silencio.
    umbral es el valor de partida; en cuanto hay datos del cuarto manda umbral_actual()."""
    bloque = int(SAMPLE_RATE * 0.1)  # bloques de 0.1 s
    previo = deque(previo_inicial or [], maxlen=max(4, len(previo_inicial or [])))
    frames = []
    hablando = False
    silencio = 0.0
    inicio = time.time()
    inicio_voz = None
    umbral_vivo = umbral_actual(umbral)

    while True:
        if CANCELAR.is_set() or DESPERTAR.is_set():
            return None
        data, _ = stream.read(bloque)
        ahora = time.time()
        if ahora < IGNORAR_HASTA:
            inicio = ahora  # el tiempo de espera cuenta desde que se deja de ignorar
            continue
        nivel = float(np.sqrt(np.mean(data ** 2)))
        NIVELES.append(nivel)
        if not hablando:  # mientras hablas el umbral no se mueve (no corta la frase a medias)
            umbral_vivo = umbral_actual(umbral)

        if nivel > umbral_vivo:
            if not hablando:
                frames.extend(previo)
                inicio_voz = ahora
            hablando = True
            silencio = 0.0
        elif hablando:
            silencio += 0.1

        if hablando:
            frames.append(data.copy())
        else:
            previo.append(data.copy())

        if hablando and silencio >= silencio_seg:
            break
        if hablando and ahora - inicio_voz > max_seg:
            break
        if not hablando and ahora - inicio > espera_seg:
            return None

    return np.concatenate(frames).flatten()


def grabar(umbral=0.004, silencio_seg=None, max_seg=20, espera_seg=6):
    """Espera a que hables, graba y corta cuando haces silencio."""
    silencio_seg = SILENCIO_SEG if silencio_seg is None else silencio_seg
    bloque = int(SAMPLE_RATE * 0.1)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=DISPOSITIVO,
                        dtype="float32", blocksize=bloque) as stream:
        return _grabar_de(stream, umbral, silencio_seg, max_seg, espera_seg)


ULTIMA_TRANSCRIPCION = {"seg": 0.0, "origen": ""}  # para los tiempos que muestra genesis.py


def _transcribir_nube(audio, prompt):
    """Whisper grande en Groq: ~0.3-0.6 s y más preciso que el 'small' local en CPU."""
    import io
    import wave

    from openai import OpenAI

    from skills import _CFG
    stt = _CFG.get("stt", {}) or {}
    clave = os.environ.get(stt.get("clave_env", "GROQ_API_KEY"), "")
    if not clave:
        raise RuntimeError("sin clave")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    cliente = OpenAI(api_key=clave, base_url=stt.get("url", "https://api.groq.com/openai/v1"),
                     timeout=stt.get("timeout", 10))
    r = cliente.audio.transcriptions.create(
        file=("orden.wav", buf.getvalue()), model=stt.get("modelo", "whisper-large-v3-turbo"),
        language="es", prompt=prompt or None, temperature=0)
    return (r.text or "").strip()


def transcribir(audio, modelo="small", prompt=None, nube=False):
    """nube=True: intenta Groq primero (config.json → stt.modo). La palabra de activación
    siempre se transcribe local, porque escucha todo el tiempo y no debe gastar la cuota."""
    t0 = time.time()
    from skills import _CFG
    modo = (_CFG.get("stt", {}) or {}).get("modo", "auto")
    if nube and modo in ("auto", "online"):
        try:
            texto = _transcribir_nube(audio, prompt)
            ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="nube")
            return texto
        except Exception as e:
            if str(e) != "sin clave":
                print(f"[La transcripción en la nube falló ({type(e).__name__}: {str(e)[:100]}); uso Whisper local]")
    whisper = _cargar_modelo(modelo)
    segmentos, _ = whisper.transcribe(
        audio, language="es", beam_size=1, vad_filter=True,
        initial_prompt=prompt,
    )
    texto = " ".join(s.text.strip() for s in segmentos).strip()
    ULTIMA_TRANSCRIPCION.update(seg=time.time() - t0, origen="local")
    return texto


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


def _normalizar(texto):
    """Minúsculas, sin acentos ni signos, para comparar palabras."""
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _orden_de(audio, resto, palabras, modelo_orden, prompt_orden):
    """'Jarvis, abre Spotify y pon música' dicho de corrido: la palabra se detectó con el
    Whisper pequeño (barato, escucha todo el tiempo), pero ese modelo se come palabras de la
    orden. Así que la MISMA grabación se vuelve a transcribir con el bueno (Groq si hay) y se le
    quita el nombre. Si eso falla, se usa lo que entendió el pequeño."""
    try:
        texto = limpiar_texto(transcribir(audio, modelo_orden, prompt_orden, nube=True))
    except Exception as e:
        print(f"[No pude re-transcribir la orden ({type(e).__name__}); uso la del detector]")
        return resto
    orden = quitar_activacion(texto, palabras)
    if orden == texto.strip():  # el nombre no iba al inicio ("abre Chrome, Jarvis")
        alternativas = "|".join(re.escape(p) for p in palabras if p)
        orden = re.sub(rf"\W*\b(?:{alternativas})\b\W*", " ", texto, flags=re.I).strip(" ,.")
    return orden if len(_normalizar(orden)) >= 3 else resto


def limpiar_texto(texto):
    return re.sub(r"\s+", " ", texto or "").strip()


def esperar_palabra(palabras, modelo_wake="base", umbral=0.004, pista="Jarvis",
                    modelo_orden=None, prompt_orden=None):
    """Bloquea hasta oír la palabra de activación.
    Devuelve lo que dijiste después de ella ('' si solo la dijiste).
    Con modelo_orden, la orden dicha junto con el nombre se re-transcribe con ese modelo
    (o con Groq) para que llegue completa y bien escrita.

    El umbral del micrófono lo ajusta solo umbral_actual() según el ruido real del cuarto
    (antes se bajaba a ciegas tras cada rato de silencio y terminaba por debajo del ruido).
    """
    global DISPOSITIVO
    originales = list(palabras)
    palabras = [_normalizar(p) for p in palabras]
    while True:
        if DESPERTAR.is_set():
            DESPERTAR.clear()
            return None  # obtener_entrada lo toma como "abre la ventana para escribir"
        if PAUSA.is_set():
            time.sleep(0.5)
            continue
        try:
            # max_seg amplio: la orden completa puede ir en la misma frase que el nombre
            audio = grabar(umbral=umbral, silencio_seg=SILENCIO_SEG, max_seg=20, espera_seg=15)
        except sd.PortAudioError as e:
            # Pasa al conectar/desconectar audífonos o al volver de suspensión. Antes esto
            # tumbaba todo el bucle; ahora se vuelve al micrófono de Windows y se reintenta.
            print(f"[El micrófono dio un error ({str(e)[:80]}); reintento en 3 s]")
            if DISPOSITIVO is not None:
                DISPOSITIVO = None
                print("[Vuelvo al micrófono de Windows por defecto]")
            time.sleep(3)
            continue
        if audio is None:
            continue
        # Un golpe, un clic o una tos (< 0.25 s de voz) no pueden ser "Jarvis": no se gasta CPU en
        # transcribirlos (Whisper corre en el procesador y compite con todo lo demás)
        # (la grabación trae además ~0.4 s previos y el silencio final, que no son voz)
        if len(audio) < SAMPLE_RATE * (0.25 + 0.4 + SILENCIO_SEG):
            continue
        texto = _normalizar(transcribir(audio, modelo_wake, prompt=pista))
        span = _buscar_nombre(texto, palabras)
        if span:
            inicio, fin = span
            resto = texto[fin:].strip()
            # Solo dijo el nombre (sin orden): no vale la pena re-transcribir
            if modelo_orden and len(_normalizar(texto[:inicio] + " " + texto[fin:])) >= 4:
                return _orden_de(audio, resto, originales, modelo_orden, prompt_orden)
            return resto


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
        if 5 <= len(w) <= 8 and any(SequenceMatcher(None, w, p).ratio() >= PARECIDO_NOMBRE
                                    for p in palabras):
            return m.span()
    return None


# ---------- Palabra de activación con openWakeWord ("hey Jarvis") ----------
# Un modelo diminuto entrenado solo para reconocer "hey Jarvis". A diferencia de transcribir
# todo con Whisper para buscar la palabra, casi no usa CPU aunque hables sin parar durante la
# exposición, reacciona en ~0.3 s y no parte tus órdenes en pedazos. La orden se graba en el
# MISMO flujo de audio, sin cortes: puedes decir "hey Jarvis, siguiente diapositiva" de corrido.
_oww = {}


def _cargar_oww(nombre):
    if nombre not in _oww:
        from openwakeword.model import Model

        print(f"[Cargando detector de palabra '{nombre}'...]")
        if not os.path.isfile(nombre):  # no es un modelo propio (.onnx): es uno de los de fábrica
            try:
                from openwakeword.utils import download_models
                download_models([nombre])  # solo descarga la primera vez (~3 MB)
            except ImportError:
                pass  # versiones viejas de openwakeword ya traen los modelos dentro
        _oww[nombre] = Model(wakeword_models=[nombre], inference_framework="onnx")
    return _oww[nombre]


def quitar_activacion(texto, palabras):
    """'Hey Jarvis, siguiente diapositiva' -> 'siguiente diapositiva'."""
    # "vis"/"arvis": restos de la palabra que a veces quedan al inicio de la grabación
    alternativas = "|".join([re.escape(p) for p in palabras if p] + ["[a-z]{0,3}rvis", "vis"])
    if not alternativas:
        return texto.strip()
    patron = rf"^\W*(?:(?:hey|ey|oye|oiga|ok|okey|hola)\W+)?(?:{alternativas})\b[\W]*"
    return re.sub(patron, "", texto.strip(), flags=re.I).strip()


def esperar_oww(modelo="hey_jarvis", sensibilidad=0.5, umbral_voz=0.004,
                whisper_modelo="small", pista="", palabras=(), ganancia=1.0):
    """Bloquea hasta oír la palabra y devuelve la orden ya transcrita ('' si no dijo nada
    después), o None si se despertó desde la bandeja."""
    global DISPOSITIVO
    detector = _cargar_oww(modelo)
    bloque_oww = 1280  # 80 ms, lo que espera openWakeWord
    while True:
        if DESPERTAR.is_set():
            DESPERTAR.clear()
            return None
        if PAUSA.is_set():
            time.sleep(0.5)
            continue
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=DISPOSITIVO,
                                dtype="float32", blocksize=bloque_oww) as stream:
                previo = deque(maxlen=10)  # ~0.8 s: lo último antes de detectar la palabra
                while True:
                    if DESPERTAR.is_set():
                        DESPERTAR.clear()
                        return None
                    if PAUSA.is_set():
                        break
                    data, _ = stream.read(bloque_oww)
                    if time.time() < IGNORAR_HASTA:
                        detector.reset()
                        continue
                    previo.append(data.copy())
                    # Micrófonos de laptop (arreglos Intel) entregan la voz muy bajita y el
                    # detector casi no reacciona: mic_ganancia la amplifica solo para él.
                    pcm = (np.clip(data[:, 0] * ganancia, -1, 1) * 32767).astype(np.int16)
                    puntos = detector.predict(pcm)
                    if max(puntos.values() or [0]) < sensibilidad:
                        continue
                    detector.reset()
                    if AL_ACTIVAR:
                        threading.Thread(target=AL_ACTIVAR, daemon=True).start()
                    # Se sigue grabando del mismo stream: lo que dijiste justo después de la
                    # palabra no se pierde. Se da poco margen de espera por si solo dijo la palabra.
                    audio = _grabar_de(stream, umbral_voz, silencio_seg=SILENCIO_SEG, max_seg=15,
                                       espera_seg=5, previo_inicial=list(previo)[-2:])
                    if audio is None:
                        return ""
                    texto = transcribir(audio, whisper_modelo, pista, nube=True)
                    return quitar_activacion(texto, palabras)
        except sd.PortAudioError as e:
            print(f"[El micrófono dio un error ({str(e)[:80]}); reintento en 3 s]")
            if DISPOSITIVO is not None:
                DISPOSITIVO = None
                print("[Vuelvo al micrófono de Windows por defecto]")
            time.sleep(3)


# Lo pone genesis.py: el "bip" y el HUD en cuanto se detecta la palabra (sin esperar la orden)
AL_ACTIVAR = None


def _normalizar_simple(texto):
    t = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def listar_dispositivos():
    """(índice, nombre) de cada entrada de audio que Windows ve, sea el micrófono del equipo
    o unos audífonos/lentes conectados por Bluetooth."""
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def _guardar_dispositivo(nombre):
    """Guarda el NOMBRE del micrófono (o '' para el automático) en config.json. No el índice:
    Windows renumera los dispositivos cada vez que se conecta o desconecta algo por
    Bluetooth, y un índice guardado acabaría apuntando a otro aparato."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg["mic_dispositivo"] = nombre
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError) as e:
        print(f"[No pude guardar el micrófono elegido en config.json: {type(e).__name__}]")


def resolver_dispositivo(valor):
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
    print(f"[El micrófono '{valor}' no está conectado; uso el de Windows por defecto.]")
    return None


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
        return "Vuelvo al micrófono automático de Windows."

    entradas = listar_dispositivos()
    if not entradas:
        return "No encontré ningún micrófono."
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
        return (f"No encontré ningún micrófono parecido a '{nombre}'. " + listar_microfonos())
    if not _funciona(mejor_i):  # que un cambio a un aparato que no graba no deje a Genesis sordo
        return f"'{mejor_nombre}' aparece pero no pude grabar con él; sigo con el micrófono actual."

    DISPOSITIVO = mejor_i
    _guardar_dispositivo(mejor_nombre)
    return f"Listo, ahora escucho por '{mejor_nombre}'."


def medir_nivel():
    """Muestra el nivel del micrófono para calibrar el umbral."""
    print("Habla y luego calla. Ctrl+C para salir.")
    bloque = int(SAMPLE_RATE * 0.1)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, device=DISPOSITIVO,
                        dtype="float32", blocksize=bloque) as stream:
        while True:
            data, _ = stream.read(bloque)
            nivel = float(np.sqrt(np.mean(data ** 2)))
            print(f"nivel: {nivel:.4f} " + "#" * int(nivel * 400))


if __name__ == "__main__":
    print("Prueba: di 'Jarvis' seguido de algo (Ctrl+C para salir)...")
    print("Resto de la frase:", repr(esperar_palabra(["jarvis", "yarvis"])))