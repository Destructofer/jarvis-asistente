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
    "atentos, el lugar, objetos, pantallas, letreros). Puedes notar si alguien parece tener una "
    "duda o estar confundido (para ofrecerle ayuda), pero nunca lo digas en voz alta como "
    "descripción de su cara o su cuerpo, ni lo pongas en evidencia. Sé cálido y respetuoso con "
    "el público.")


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
    nube = conf.get("nube", {})
    clave = os.environ.get(nube.get("clave_env", ""), "")
    if not clave:
        raise RuntimeError("sin clave de visión en las variables de entorno")
    cli = cerebro.cliente(nube["url"], clave, nube.get("timeout", 25))
    partes = [{"type": "text", "text": instruccion}]
    partes += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
               for b in lista_b64]
    try:
        r = cli.chat.completions.create(
            model=nube["modelo"],
            temperature=0.5,
            max_tokens=int(conf.get("max_tokens", 400)) * len(lista_b64),
            messages=[{"role": "system", "content": sistema}, {"role": "user", "content": partes}])
    except Exception as e:
        if cerebro.es_error_de_red(e):
            cerebro.marcar_conexion(cerebro.host_de(nube["url"]), False)
        raise
    return r.choices[0].message.content or ""


def _usar_nube(conf):
    modo = conf.get("modo", "auto")
    nube = conf.get("nube", {}) or {}
    if modo == "offline" or not nube.get("url"):
        return False
    if modo == "online":
        return True
    return bool(os.environ.get(nube.get("clave_env", ""))) and cerebro.hay_internet(cerebro.host_de(nube["url"]))


def _local(conf, sistema, instruccion, b64):
    return _local_varias(conf, sistema, instruccion, [b64])


def _local_varias(conf, sistema, instruccion, lista_b64):
    import ollama

    r = ollama.chat(model=conf.get("local_modelo", "qwen2.5vl:7b"),
                    messages=[{"role": "system", "content": sistema},
                              {"role": "user", "content": instruccion, "images": lista_b64}],
                    options={"temperature": 0.5})
    return r.message.content or ""


def ver(cfg, img, instruccion, sistema, max_tokens=None, lado=None):
    """Devuelve el texto que respondió el modelo de visión (o lanza RuntimeError).
    lado: reduce la imagen a ese tamaño máximo (menos tokens: el plan gratis de Groq limita
    los tokens de entrada por minuto y cada foto grande cuesta ~2.000)."""
    conf = dict(cfg.get("vision", {}) or {})
    if max_tokens:
        conf["max_tokens"] = max_tokens
    if lado:
        img = img.copy()
        img.thumbnail((lado, lado))
    sistema = sistema + "\n\n" + REGLAS_PERSONAS
    b64 = _b64(img)
    if _usar_nube(conf):
        try:
            return _limpiar(_nube(conf, sistema, instruccion, b64))
        except Exception as e:
            print(f"[La visión en la nube falló ({type(e).__name__}: {str(e)[:120]}); pruebo local]")
    try:
        return _limpiar(_local(conf, sistema, instruccion, b64))
    except Exception as e:
        raise RuntimeError("No pude usar ningún modelo de visión. Revisa la clave de Groq o "
                           f"descarga uno local con 'ollama pull {conf.get('local_modelo', 'qwen2.5vl:7b')}'.") from e


def ver_varias(cfg, imagenes, instruccion, sistema):
    """Varias imágenes en UNA sola petición (Groq acepta hasta 3). Sirve para recorrer una
    página: el modelo ve todas las partes juntas y arma una explicación con hilo."""
    conf = cfg.get("vision", {}) or {}
    sistema = sistema + "\n\n" + REGLAS_PERSONAS
    lista = []
    for i in imagenes[:3]:
        chica = i.convert("RGB")
        # 1024 y no 1280: tres capturas a 1280 pedían ~6.200 tokens y el plan gratis de Groq
        # permite 7.000 por minuto (así falló "baja la página y explícala"); el texto se sigue
        # leyendo bien a este tamaño.
        chica.thumbnail((1024, 1024))
        lista.append(_b64(chica))
    if _usar_nube(conf):
        try:
            return _limpiar(_nube_varias(conf, sistema, instruccion, lista))
        except Exception as e:
            print(f"[La visión en la nube falló ({type(e).__name__}: {str(e)[:120]}); pruebo local]")
    try:
        return _limpiar(_local_varias(conf, sistema, instruccion, lista))
    except Exception as e:
        raise RuntimeError("No pude usar ningún modelo de visión.") from e
