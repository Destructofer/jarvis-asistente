"""Ver: le pasa una imagen a un modelo con visión y devuelve lo que diría Jarvis.

Nube (por defecto Groq, formato OpenAI) si hay internet y clave; si no, un modelo local de
Ollama con visión (qwen2.5vl, llama3.2-vision, gemma3...). Todo configurable en
config.json → vision, igual que el cerebro normal.
"""
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
    "atentos, el lugar, objetos, pantallas, letreros). Sé cálido y respetuoso con el público.")


def _b64(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _limpiar(texto):
    # Algunos modelos con razonamiento devuelven <think>...</think> antes de la respuesta
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S)
    return texto.strip()


def _nube(conf, sistema, instruccion, b64):
    return _nube_varias(conf, sistema, instruccion, [b64])


def _nube_varias(conf, sistema, instruccion, lista_b64):
    from openai import OpenAI

    nube = conf.get("nube", {})
    clave = os.environ.get(nube.get("clave_env", ""), "")
    if not clave:
        raise RuntimeError("sin clave de visión en las variables de entorno")
    cliente = OpenAI(api_key=clave, base_url=nube["url"], timeout=nube.get("timeout", 25),
                     max_retries=0)
    partes = [{"type": "text", "text": instruccion}]
    partes += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
               for b in lista_b64]
    r = cliente.chat.completions.create(
        model=nube["modelo"],
        temperature=0.5,
        max_tokens=int(conf.get("max_tokens", 400)) * len(lista_b64),
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": partes}])
    return r.choices[0].message.content or ""


def _local(conf, sistema, instruccion, b64):
    return _local_varias(conf, sistema, instruccion, [b64])


def _local_varias(conf, sistema, instruccion, lista_b64):
    import ollama

    r = ollama.chat(model=conf.get("local_modelo", "qwen2.5vl:7b"),
                    messages=[{"role": "system", "content": sistema},
                              {"role": "user", "content": instruccion, "images": lista_b64}],
                    options={"temperature": 0.5})
    return r.message.content or ""


def _por_nube(conf, llamar):
    """Intenta la nube respetando su límite por minuto: si Groq pidió esperar pocos segundos,
    espera (sale mucho más rápido que el modelo local, ~40 s en CPU); si es más, va directo al
    local. Devuelve el texto, o None si hay que usar el modelo local."""
    import time
    modelo = conf.get("nube", {}).get("modelo", "")
    for _ in range(2):
        espera = cerebro.libre_en(modelo)
        if espera > 8:
            print(f"[Visión en la nube en espera {espera:.0f} s; uso el modelo local]")
            return None
        if espera:
            time.sleep(espera)
        try:
            return _limpiar(llamar())
        except Exception as e:
            if cerebro.es_limite(e):
                cerebro.marcar_limite(modelo, e)
                continue
            print(f"[La visión en la nube falló ({type(e).__name__}: {str(e)[:120]}); pruebo local]")
            return None
    return None


def ver(cfg, img, instruccion, sistema):
    """Devuelve el texto que respondió el modelo de visión (o lanza RuntimeError)."""
    conf = cfg.get("vision", {}) or {}
    modo = conf.get("modo", "auto")
    sistema = sistema + "\n\n" + REGLAS_PERSONAS
    b64 = _b64(img)
    clave_env = conf.get("nube", {}).get("clave_env", "")
    usar_nube = modo == "online" or (modo == "auto" and cerebro.hay_internet()
                                     and bool(os.environ.get(clave_env)))
    if usar_nube:
        texto = _por_nube(conf, lambda: _nube(conf, sistema, instruccion, b64))
        if texto is not None:
            return texto
    try:
        return _limpiar(_local(conf, sistema, instruccion, b64))
    except Exception as e:
        raise RuntimeError("No pude usar ningún modelo de visión. Revisa la clave de Groq o "
                           f"descarga uno local con 'ollama pull {conf.get('local_modelo', 'qwen2.5vl:7b')}'.") from e


def ver_varias(cfg, imagenes, instruccion, sistema):
    """Varias imágenes en UNA sola petición (Groq acepta hasta 3). Sirve para recorrer una
    página: el modelo ve todas las partes juntas y arma una explicación con hilo."""
    conf = cfg.get("vision", {}) or {}
    modo = conf.get("modo", "auto")
    sistema = sistema + "\n\n" + REGLAS_PERSONAS
    lista = []
    for i in imagenes[:3]:
        chica = i.convert("RGB")
        chica.thumbnail((1280, 1280))  # menos peso de subida; el detalle alcanza para leer
        lista.append(_b64(chica))
    clave_env = conf.get("nube", {}).get("clave_env", "")
    if modo == "online" or (modo == "auto" and cerebro.hay_internet() and os.environ.get(clave_env)):
        texto = _por_nube(conf, lambda: _nube_varias(conf, sistema, instruccion, lista))
        if texto is not None:
            return texto
    try:
        return _limpiar(_local_varias(conf, sistema, instruccion, lista))
    except Exception as e:
        raise RuntimeError("No pude usar ningún modelo de visión.") from e
