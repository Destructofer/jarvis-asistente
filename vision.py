"""Ver: le pasa una imagen a un modelo con visión y devuelve lo que diría Jarvis.

Nube (por defecto Groq, formato OpenAI) si hay internet y clave, con respaldos en otras nubes
gratis (config.json → vision.respaldos, p. ej. Google Gemini) cuando Groq llega a su límite;
si nada responde, un modelo local de Ollama con visión (qwen3.5, qwen2.5vl, gemma3...).

Límites de uso: cuando una nube contesta 429, queda en pausa el tiempo que pide (o 60 s) y las
siguientes miradas van directo a la siguiente o al modelo local, sin perder tiempo en otro 429
seguro (antes cada mirada chocaba primero con Groq). Lo de fondo (ver(..., fondo=True): los
vistazos y el saludo de presencia.py) usa primero el modelo local: así no se come la cuota que
necesitan tus peticiones.
"""
import time
import base64
import io
import os
import re

import cerebro

# Va en el código y no en config.json a propósito: es una protección, no una preferencia.
REGLAS_PERSONAS = (
    "REGLAS AL VER PERSONAS: nunca intentes identificar a nadie ni adivinar nombres, y si te "
    "preguntan quién es alguien, di con elegancia que no identificas personas. No comentes "
    "rasgos físicos, cuerpo, edad, etnia, ropa de alguien en particular ni juzgues apariencias. "
    "Habla del grupo en general (cuántas personas hay aproximadamente, el ambiente, si están "
    "atentos, el lugar, objetos, pantallas, letreros). Puedes notar si alguien parece tener una "
    "duda o estar confundido (para ofrecerle ayuda), pero nunca lo digas en voz alta como "
    "descripción de su cara o su cuerpo, ni lo pongas en evidencia. Sé cálido y respetuoso con "
    "el público.")

# Cuando Jarvis te mira a TI por la cámara de la computadora: eres su compañero y te ve porque
# tú lo activaste, así que puede comentar lo que le preguntas, con tacto.
REGLAS_USUARIO = (
    "REGLAS AL VER A TU COMPAÑERO: la persona frente a la cámara de la computadora es con quien "
    "trabajas y activó que lo veas. Puedes comentar con tacto y naturalidad lo que venga al caso: "
    "su expresión (contento, concentrado, cansado), su postura, lo que tiene en las manos o "
    "muestra a la cámara, e incluso cómo se ve su ropa si te lo pregunta. Nunca adivines su "
    "edad, etnia, salud ni nada sensible, y no juzgues su cuerpo. Si aparecen otras personas, "
    "no intentes identificarlas.")


def _b64(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _limpiar(texto):
    # Algunos modelos con razonamiento devuelven <think>...</think> antes de la respuesta
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S)
    return texto.strip()


def _proveedores(conf):
    """Nubes con visión a probar, en orden: vision.nube y luego vision.respaldos (las que no
    tienen su clave puesta se saltan solas)."""
    lista = [conf.get("nube") or {}] + list(conf.get("respaldos") or [])
    return [n for n in lista if n.get("url") and n.get("modelo")
            and os.environ.get(n.get("clave_env", ""), "")]


def _nube_varias(conf, nube, sistema, instruccion, lista_b64):
    clave = os.environ.get(nube.get("clave_env", ""), "")
    cli = cerebro.cliente(nube["url"], clave, nube.get("timeout", 25))
    partes = [{"type": "text", "text": instruccion}]
    partes += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
               for b in lista_b64]
    kwargs = {"model": nube["modelo"], "temperature": 0.5,
              "max_tokens": int(conf.get("max_tokens", 400)) * len(lista_b64),
              "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": partes}]}
    if nube.get("razonamiento") and cerebro.acepta_razonamiento(nube["modelo"]):
        kwargs["reasoning_effort"] = nube["razonamiento"]  # Gemini: "minimal" = contesta más rápido
    try:
        r = cli.chat.completions.create(**kwargs)
    except Exception as e:
        if cerebro.es_error_de_red(e):
            cerebro.marcar_conexion(cerebro.host_de(nube["url"]), False)
        raise
    return r.choices[0].message.content or ""


PAUSA_POR_LIMITE = 60   # s que una nube queda en pausa tras un 429 que no dice cuánto esperar


def _en_pausa(nube, ahora=None):
    """¿Esa nube dijo hace poco que llegó a su límite? (se comparte con cerebro.py)"""
    return cerebro._saturado.get(nube["modelo"], 0) > (time.time() if ahora is None else ahora)


def libres(conf):
    """Las nubes con visión que no están en pausa por límite de uso."""
    return [n for n in _proveedores(conf) if not _en_pausa(n)]


def _en_la_nube(conf, sistema, instruccion, lista_b64):
    """Prueba cada nube con visión que no esté en pausa; la primera que conteste gana.
    RuntimeError si ninguna."""
    ultimo = None
    for nube in libres(conf):
        try:
            texto = _limpiar(_nube_varias(conf, nube, sistema, instruccion, lista_b64))
            cerebro._saturado.pop(nube["modelo"], None)
            return texto
        except Exception as e:
            ultimo = e
            if "429" in str(e) or "rate limit" in str(e).lower():
                espera = cerebro.espera_sugerida(e) or PAUSA_POR_LIMITE
                cerebro._saturado[nube["modelo"]] = time.time() + espera
                print(f"[La visión en {nube['modelo']} llegó a su límite; la dejo en pausa {espera:.0f} s]")
            else:
                print(f"[La visión en {nube['modelo']} falló ({type(e).__name__}: {str(e)[:120]}); "
                      "pruebo la siguiente]")
    raise RuntimeError("ninguna nube con visión respondió") from ultimo


def _usar_nube(conf):
    modo = conf.get("modo", "auto")
    nubes = libres(conf)
    if modo == "offline" or not nubes:
        return False
    if modo == "online":
        return True
    return cerebro.hay_internet(cerebro.host_de(nubes[0]["url"]))


def _local_varias(conf, sistema, instruccion, lista_b64):
    import ollama

    kwargs = dict(model=conf.get("local_modelo", "qwen3.5:4b"),
                  messages=[{"role": "system", "content": sistema},
                            {"role": "user", "content": instruccion, "images": lista_b64}],
                  options={"temperature": 0.5}, keep_alive="30m")
    # Los modelos nuevos (qwen3.5) razonan por defecto: para describir una foto eran ~60 s
    try:
        r = ollama.chat(think=False, **kwargs)
    except Exception as e:
        if "think" not in str(e).lower():
            raise
        r = ollama.chat(**kwargs)  # modelo viejo que no acepta "think"
    return r.message.content or ""


def _ver(conf, sistema, instruccion, lista_b64, fondo=False):
    if fondo:   # de fondo: primero tu GPU; la nube solo si el local falla y hay una libre
        try:
            return _limpiar(_local_varias(conf, sistema, instruccion, lista_b64))
        except Exception as e:
            if not _usar_nube(conf):
                raise RuntimeError("No pude usar el modelo de visión local.") from e
            return _en_la_nube(conf, sistema, instruccion, lista_b64)
    if _usar_nube(conf):
        try:
            return _en_la_nube(conf, sistema, instruccion, lista_b64)
        except RuntimeError:
            print("[Ninguna nube con visión respondió; pruebo el modelo local]")
    try:
        return _limpiar(_local_varias(conf, sistema, instruccion, lista_b64))
    except Exception as e:
        raise RuntimeError("No pude usar ningún modelo de visión. Revisa la clave de Groq (o la "
                           "de Gemini) o descarga uno local con 'ollama pull "
                           f"{conf.get('local_modelo', 'qwen3.5:4b')}'.") from e


def ver(cfg, img, instruccion, sistema, max_tokens=None, lado=None, reglas=REGLAS_PERSONAS,
        fondo=False):
    """Devuelve el texto que respondió el modelo de visión (o lanza RuntimeError).
    fondo=True: nadie la pidió (vistazos, saludos): va primero al modelo local.
    lado: reduce la imagen a ese tamaño máximo (menos tokens: el plan gratis de Groq limita
    los tokens de entrada por minuto y cada foto grande cuesta ~2.000).
    reglas: REGLAS_PERSONAS (el público) o REGLAS_USUARIO (te mira a ti)."""
    conf = dict(cfg.get("vision", {}) or {})
    if max_tokens:
        conf["max_tokens"] = max_tokens
    if lado:
        img = img.copy()
        img.thumbnail((lado, lado))
    return _ver(conf, sistema + "\n\n" + reglas, instruccion, [_b64(img)], fondo)


def ver_varias(cfg, imagenes, instruccion, sistema):
    """Varias imágenes en UNA sola petición (Groq acepta hasta 3). Sirve para recorrer una
    página: el modelo ve todas las partes juntas y arma una explicación con hilo."""
    conf = cfg.get("vision", {}) or {}
    lista = []
    for i in imagenes[:3]:
        chica = i.convert("RGB")
        # 1024 y no 1280: tres capturas a 1280 pedían ~6.200 tokens y el plan gratis de Groq
        # permite 7.000 por minuto (así falló "baja la página y explícala"); el texto se sigue
        # leyendo bien a este tamaño.
        chica.thumbnail((1024, 1024))
        lista.append(_b64(chica))
    return _ver(conf, sistema + "\n\n" + REGLAS_PERSONAS, instruccion, lista)
