import json
import os
import re
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

import ollama

HOST_POR_DEFECTO = "api.groq.com"
_conexion = {}   # host -> (momento de la última comprobación, ¿hubo conexión?)
_clientes = {}   # (url, clave, timeout) -> cliente OpenAI reutilizable
_lock_clientes = threading.Lock()


def host_de(url):
    """'https://api.groq.com/openai/v1' -> 'api.groq.com'."""
    try:
        return urlparse(url).hostname or HOST_POR_DEFECTO
    except ValueError:
        return HOST_POR_DEFECTO


def _conecta(host, puerto=443, limite=2.5):
    """True si se puede abrir una conexión TCP al servicio. Corre en un hilo con tiempo
    límite: con el DNS roto, resolver el nombre puede bloquearse varios segundos."""
    resultado = []

    def probar():
        try:
            socket.create_connection((host, puerto), timeout=limite).close()
            resultado.append(True)
        except OSError:
            resultado.append(False)
    hilo = threading.Thread(target=probar, daemon=True)
    hilo.start()
    hilo.join(limite + 0.5)
    return bool(resultado and resultado[0])


def hay_internet(host=HOST_POR_DEFECTO):
    """¿Se llega al servicio en la nube? (cacheado 30 s para no frenar cada respuesta).

    Antes se probaba el DNS de Cloudflare (1.1.1.1:53); muchas redes escolares y de eventos
    bloquean DNS externo y Jarvis creía que no había internet aunque Groq sí funcionara.
    Ahora se prueba el servidor real al que se le va a hablar."""
    t, ok = _conexion.get(host, (0.0, False))
    if time.time() - t < 30:
        return ok
    ok = _conecta(host)
    _conexion[host] = (time.time(), ok)
    return ok


def marcar_conexion(host, ok):
    """Lo que se aprende al usar el servicio vale más que la prueba: si una petición real
    falló por red, las siguientes no pierden tiempo intentándolo durante 30 s."""
    _conexion[host] = (time.time(), bool(ok))


def _http_persistente(timeout):
    """El SDK de OpenAI cierra las conexiones tras 5 s sin uso (keepalive_expiry de httpx):
    entre una orden de voz y la siguiente siempre pasan más de 5 s, así que CADA pregunta
    repetía el saludo TLS con Groq (~0.3 s desde México). Se conservan 50 s (por debajo de lo
    que suelen aguantar los servidores) y genesis.py las mantiene vivas durante la exposición."""
    try:
        import httpx2
        from openai import DefaultHttpx2Client
        timeout = httpx2.Timeout(*_tiempos(timeout)[0:1], connect=_tiempos(timeout)[1])
        return DefaultHttpx2Client(timeout=timeout, limits=httpx2.Limits(
            max_connections=20, max_keepalive_connections=10, keepalive_expiry=50))
    except ImportError:
        return None


def _tiempos(timeout):
    """(sin recibir datos, para conectar) en segundos. Con internet inestable, esperar los 20-25 s
    configurados por CADA modelo de la nube antes de pasar al local hacía respuestas de 30-45 s.
    Groq empieza a contestar en 1-2 s y en streaming manda datos seguido: 10 s callado ya es
    una red caída."""
    total = float(timeout)
    return min(total, 10.0), min(total, 4.0)


def _timeout_sdk(timeout):
    try:
        import httpx
        lectura, conectar = _tiempos(timeout)
        return httpx.Timeout(lectura, connect=conectar)
    except ImportError:
        return _tiempos(timeout)[0]


def conexion_rota(e):
    """Error de conexión "rápido" (no un timeout): típico de reutilizar una conexión que el
    servidor ya cerró. Se reintenta al instante con una nueva en vez de pasar al respaldo."""
    return type(e).__name__ in ("APIConnectionError", "RemoteProtocolError", "ReadError",
                                "ConnectError", "WriteError")


def cliente(url, clave, timeout=20):
    """Cliente OpenAI reutilizable. Crear uno por petición repetía el saludo TLS cada vez
    (~0.1-0.3 s). max_retries=0: si algo falla se pasa de inmediato al siguiente respaldo."""
    from openai import OpenAI
    k = (url, clave, float(timeout))
    with _lock_clientes:
        c = _clientes.get(k)
        if c is None:
            http = _http_persistente(timeout)
            c = OpenAI(api_key=clave, base_url=url, timeout=_timeout_sdk(timeout), max_retries=0,
                       **({"http_client": http} if http is not None else {}))
            _clientes[k] = c
        return c


def mantener_caliente(cfg):
    """Una petición mínima (lista de modelos, gratis) para que la conexión con la nube siga
    abierta y la siguiente pregunta no pague el arranque. Lo llama genesis.py cada ~40 s
    mientras hay exposición."""
    provs = _proveedores(cfg)
    if not provs:
        return
    p = provs[0]
    try:
        cliente(p["url"], os.environ.get(p.get("clave_env", ""), ""), p.get("timeout", 20)).models.list()
        marcar_conexion(host_de(p["url"]), True)
    except Exception as e:
        if es_error_de_red(e):
            marcar_conexion(host_de(p["url"]), False)


def es_error_de_red(e):
    nombre = type(e).__name__
    return nombre in ("APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
                      "ReadTimeout", "TimeoutException") or isinstance(e, (OSError, TimeoutError))


# ---------- Conversión del historial (formato neutro) ----------
def _a_ollama(history):
    out = []
    for m in history:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant", "content": m.get("content") or "",
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["args"]}}
                               for c in m["tool_calls"]],
            })
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_name": m["name"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _firma(c):
    """Gemini 3 firma cada llamada a herramienta (extra_content.google.thought_signature) y
    exige recibirla de vuelta; si la llamada la hizo otro modelo (Groq), se usa el valor que
    Google documenta para saltarse la revisión."""
    return {"google": {"thought_signature": c.get("firma") or "skip_thought_signature_validator"}}


def _extra(tc):
    """La firma de Gemini que viene en una llamada a herramienta (None si no hay)."""
    extra = getattr(tc, "extra_content", None) or (getattr(tc, "model_extra", None) or {}).get("extra_content")
    try:
        return (extra or {}).get("google", {}).get("thought_signature")
    except AttributeError:
        return None


def _a_openai(history, gemini=False):
    out = []
    for m in history:
        if m["role"] == "assistant" and m.get("tool_calls"):
            llamadas = []
            for c in m["tool_calls"]:
                llamada = {"id": c["id"], "type": "function",
                           "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                if gemini:  # a Groq no: rechaza campos que no conoce
                    llamada["extra_content"] = _firma(c)
                llamadas.append(llamada)
            out.append({"role": "assistant", "content": m.get("content") or None,
                        "tool_calls": llamadas})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


# ---------- Cerebros ----------
class SinCerebro(Exception):
    """Ni la nube ni el modelo local respondieron; el mensaje ya viene listo para decirlo."""


class CorteEnVivo(SinCerebro):
    """La respuesta se cortó a media frase (ya se estaba diciendo en voz alta): no se puede
    pasar en silencio a otro modelo porque el público ya oyó el principio."""


def _proveedores(cfg):
    """Lista de cerebros en la nube a probar, en orden. El plan gratis de Groq da un límite de
    tokens por minuto POR MODELO: si uno se satura, el siguiente modelo tiene su propio límite.
    config.json → nube.respaldos (modelos del mismo proveedor) y nubes_extra (otros servicios
    gratis: Google Gemini, Cerebras...). Los que no tienen su clave puesta se saltan solos.
    nube.preferir_extras = true pone los de nubes_extra antes que Groq."""
    base = dict(cfg.get("nube", {}) or {})
    lista = [base] if base.get("url") else []
    for m in base.get("respaldos", ["openai/gpt-oss-20b"]):
        if m and m != base.get("modelo"):
            lista.append(dict(base, modelo=m))
    extras = [e for e in cfg.get("nubes_extra", []) or [] if e.get("url") and e.get("modelo")]
    lista = extras + lista if base.get("preferir_extras") else lista + extras
    return [p for p in lista if os.environ.get(p.get("clave_env", ""), "")]


def acepta_razonamiento(modelo):
    """gpt-oss (Groq, Cerebras) y Gemini aceptan reasoning_effort; a los demás (qwen en Groq)
    se les manda sin él para no gastar una petición fallida."""
    return any(m in (modelo or "").lower() for m in ("gpt-oss", "gemini"))


def _args(texto):
    try:
        datos = json.loads(texto or "{}")
        return datos if isinstance(datos, dict) else {}
    except json.JSONDecodeError:
        return {}


def _kwargs_nube(prov, history, tools, temperatura):
    kwargs = {"model": prov["modelo"],
              "messages": _a_openai(history, gemini="gemini" in prov["modelo"].lower()),
              "temperature": temperatura}
    if prov.get("max_tokens"):
        kwargs["max_tokens"] = int(prov["max_tokens"])
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if prov.get("razonamiento") and acepta_razonamiento(prov["modelo"]):
        kwargs["reasoning_effort"] = prov["razonamiento"]  # "low" = responde más rápido
        # Lo que piensa gpt-oss cuenta dentro de max_tokens: con 400 y razonamiento alto se le
        # acababa pensando y la respuesta llegaba VACÍA ("no logré procesar eso")
        minimo = {"high": 2000, "medium": 1200}.get(prov["razonamiento"])
        if minimo and kwargs.get("max_tokens", minimo) < minimo:
            kwargs["max_tokens"] = minimo
    return kwargs


def _crear(cli, kwargs):
    """chat.completions.create; si el modelo no acepta reasoning_effort, se reintenta sin él."""
    try:
        return cli.chat.completions.create(**kwargs)
    except Exception as e:
        if "reasoning_effort" in kwargs and "reasoning" in str(e).lower():
            kwargs.pop("reasoning_effort")
            return cli.chat.completions.create(**kwargs)
        raise


def _chat_nube(prov, history, tools, temperatura, al_texto=None):
    clave = os.environ.get(prov.get("clave_env", ""), "")
    # max_retries=0 (en cliente()): si el servicio está saturado NO se espera (antes se quedaba
    # hasta 45 s reintentando en silencio); se pasa de inmediato al siguiente modelo.
    cli = cliente(prov["url"], clave, prov.get("timeout", 20))
    kwargs = _kwargs_nube(prov, history, tools, temperatura)

    if al_texto is None:
        msg = _crear(cli, kwargs).choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "args": _args(c.function.arguments),
                  "firma": _extra(c)} for c in (msg.tool_calls or [])]
        return {"content": msg.content or "", "tool_calls": calls}

    # Streaming: el texto se entrega en cuanto llega (para empezar a hablar con la primera
    # frase) y las llamadas a herramientas se arman pedazo a pedazo.
    kwargs["stream"] = True
    contenido, llamadas, ultima = [], {}, 0
    for trozo in _crear(cli, kwargs):
        if not trozo.choices:
            continue
        delta = trozo.choices[0].delta
        if getattr(delta, "content", None):
            contenido.append(delta.content)
            al_texto(delta.content)
        for tc in getattr(delta, "tool_calls", None) or []:
            # Gemini manda cada llamada completa en un pedazo y sin "index": se separan por id
            clave = tc.index if tc.index is not None else (tc.id or ultima)
            ultima = clave
            slot = llamadas.setdefault(clave, {"id": "", "name": "", "args": "", "firma": None})
            if tc.id:
                slot["id"] = tc.id
            slot["firma"] = _extra(tc) or slot["firma"]
            f = tc.function
            if f is not None:
                if f.name and f.name != slot["name"]:
                    slot["name"] += f.name
                if f.arguments:
                    slot["args"] += f.arguments
    calls = [{"id": s["id"] or f"call_{uuid.uuid4().hex[:8]}", "name": s["name"],
              "args": _args(s["args"]), "firma": s["firma"]} for s in llamadas.values() if s["name"]]
    return {"content": "".join(contenido), "tool_calls": calls}


_sin_think = set()  # modelos locales que no aceptan "think" (los viejos: qwen2.5, llama3.2)


def _chat_local(cfg, history, tools, temperatura, al_texto=None, razonamiento=None):
    # num_ctx: Ollama usa por defecto una ventana de ~4k tokens y solo las ~40 herramientas
    # ocupan ~6k; sin esto recortaba en silencio las instrucciones y respondía a ciegas.
    opciones = {"temperature": temperatura, "num_ctx": cfg.get("ollama_ctx", 16384)}
    # Sin internet: el modelo rápido (cabe entero en la GPU, ~0.6 s) para órdenes y plática, y
    # el más capaz solo para lo que merece pensarse (config.json → model_profundo)
    modelo = cfg["model"]
    if razonamiento in ("medium", "high") and cfg.get("model_profundo"):
        modelo = cfg["model_profundo"]
    kwargs = {"model": modelo, "messages": _a_ollama(history), "tools": tools or None,
              "options": opciones, "keep_alive": cfg.get("ollama_keep_alive", "30m")}
    # Los modelos locales nuevos (qwen3.5...) razonan SIEMPRE por defecto: para "abre Spotify"
    # eso eran segundos de más. Solo piensan cuando la petición lo merece (cognicion.nivel).
    if modelo not in _sin_think:
        # Razonar en la laptop es lento (el 9B no cabe entero en la GPU: 30+ s): por defecto el
        # "pensar más" sin internet es usar el modelo más capaz, sin razonamiento interno
        # (config.json → cognicion.pensar_local: true para activarlo)
        pensar = bool((cfg.get("cognicion") or {}).get("pensar_local", False))
        kwargs["think"] = pensar and razonamiento in ("medium", "high")
    limite = float((cfg.get("cognicion") or {}).get("limite_pensar_local_seg", 15))
    try:
        r = _ollama_chat(kwargs, al_texto, limite if kwargs.get("think") else None)
    except Exception as e:
        if "think" in kwargs and "think" in str(e).lower():
            _sin_think.add(modelo)
            kwargs.pop("think")
            return _ollama_chat(kwargs, al_texto)
        raise
    if r is None:  # pensó demasiado sin llegar a nada (un modelo chico puede dar vueltas minutos)
        print(f"[El modelo local pensó más de {limite:.0f} s sin responder; contesto sin razonar]")
        kwargs["think"] = False
        r = _ollama_chat(kwargs, al_texto)
    return r


def _ollama_chat(kwargs, al_texto, limite_pensar=None):
    """Siempre en streaming (aunque nadie lo escuche): así se puede cortar un razonamiento que
    se alarga. Devuelve None si pensó más de limite_pensar segundos sin escribir la respuesta."""
    partes, llamadas, t0 = [], [], time.time()
    flujo = ollama.chat(stream=True, **kwargs)
    for trozo in flujo:
        m = trozo.message
        if m.content:
            partes.append(m.content)
            if al_texto is not None:
                al_texto(m.content)
        llamadas += list(m.tool_calls or [])
        if (limite_pensar and not partes and not llamadas
                and time.time() - t0 > limite_pensar):
            try:
                flujo.close()
            except Exception:
                pass
            return None
    if limite_pensar and not "".join(partes).strip() and not llamadas:
        return None  # se le acabó el razonamiento sin respuesta: otra vez, sin pensar
    calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": c.function.name,
              "args": dict(c.function.arguments or {})} for c in llamadas]
    return {"content": "".join(partes), "tool_calls": calls}


def _motivo(e):
    t = str(e)
    if "413" in t or "too large" in t.lower():
        return "petición demasiado grande para el plan"
    if "429" in t or "rate limit" in t.lower():
        espera = espera_sugerida(e)
        return "límite por minuto alcanzado" + (f" (libre en {espera:.1f}s)" if espera else "")
    return f"{type(e).__name__}: {t[:100]}"


_saturado = {}  # modelo -> momento hasta el que Groq dijo que estaba al límite


def espera_sugerida(e):
    """Segundos que el servicio pide esperar tras un 429 ('Please try again in 2.3s' / '850ms'),
    o None si no lo dice."""
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)(ms|s)", str(e), re.I)
    if not m:
        return None
    minutos = int(m.group(1) or 0)
    valor = float(m.group(2))
    return minutos * 60 + (valor / 1000 if m.group(3).lower() == "ms" else valor)


def nube_disponible(cfg):
    """¿Contestaría ahora un modelo de la nube? (hay red y al menos uno no está al límite)."""
    if cfg.get("modo", "auto") == "offline":
        return False
    proveedores = _proveedores(cfg)
    if not proveedores or not hay_internet(host_de(proveedores[0]["url"])):
        return False
    return any(_saturado.get(p["modelo"], 0) <= time.time() for p in proveedores)


def chat(cfg, history, tools, temperatura=0.2, al_texto=None, razonamiento=None):
    """Devuelve {'content', 'tool_calls', 'origen'}. Prueba los cerebros en la nube en orden
    y, si ninguno responde, el modelo local.

    al_texto(fragmento): si se pasa, la respuesta llega en streaming y cada pedazo de texto se
    entrega en cuanto el modelo lo escribe (genesis.py empieza a hablar con la primera frase).
    Si algo falla ANTES de entregar texto se prueba el siguiente cerebro como siempre; si falla
    DESPUÉS, se lanza CorteEnVivo (no se puede "deshacer" lo que ya se dijo)."""
    modo = cfg.get("modo", "auto")
    proveedores = _proveedores(cfg) if modo != "offline" else []
    usar_nube = bool(proveedores) and (modo == "online" or hay_internet(host_de(proveedores[0]["url"])))

    estado = {"emitido": False}

    def emitir(fragmento):
        estado["emitido"] = True
        al_texto(fragmento)
    cb = emitir if al_texto is not None else None

    caidos = set()  # servicios sin red en esta petición: sus otros modelos tampoco llegarían

    def probar(prov):
        """Una petición a ese modelo (con un reintento si la conexión vieja estaba cerrada).
        Devuelve la respuesta o None."""
        host = host_de(prov["url"])
        p = dict(prov, razonamiento=razonamiento) if razonamiento else prov
        for intento in range(2):
            try:
                r = _chat_nube(p, history, tools, temperatura, cb)
                marcar_conexion(host, True)
                _saturado.pop(prov["modelo"], None)
                r["origen"] = f"nube · {prov['modelo']}"
                return r
            except Exception as e:
                if estado["emitido"]:
                    raise CorteEnVivo("Perdón, se me cortó la conexión.") from e
                if intento == 0 and conexion_rota(e):
                    continue  # conexión vieja cerrada por el servidor: otra vez, ya
                if intento == 0 and "validation" in str(e).lower():
                    # "Tool call validation failed": el modelo armó mal los argumentos de una
                    # herramienta; al segundo intento casi siempre sale bien
                    continue
                print(f"[{prov['modelo']} no respondió ({_motivo(e)}); pruebo el siguiente]")
                if "429" in str(e) or "rate limit" in str(e).lower():
                    _saturado[prov["modelo"]] = time.time() + (espera_sugerida(e) or 10)
                if es_error_de_red(e):
                    marcar_conexion(host, False)
                    caidos.add(host)
                return None
        return None

    if usar_nube:
        ahora = time.time()
        # Los que Groq dijo hace poco que están al límite van al final: no se pierde una
        # petición (ni su tiempo) en un 429 seguro
        libres = [p for p in proveedores if _saturado.get(p["modelo"], 0) <= ahora]
        for prov in libres + [p for p in proveedores if p not in libres]:
            if host_de(prov["url"]) in caidos:
                continue
            r = probar(prov)
            if r is not None:
                return r
        # Todos al límite: si el que se libera antes lo hace en pocos segundos, se espera. El
        # modelo local en CPU tarda ~26 s: esperar 2-3 s a la nube es mucho mejor.
        max_espera = float((cfg.get("nube") or {}).get("esperar_saturacion_seg", 4))
        pendientes = [(_saturado[p["modelo"]] - time.time(), p) for p in proveedores
                      if p["modelo"] in _saturado and host_de(p["url"]) not in caidos]
        if pendientes:
            espera, prov = min(pendientes, key=lambda x: x[0])
            if espera <= max_espera:
                print(f"[Todos los modelos al límite; espero {max(0.0, espera):.1f}s a {prov['modelo']}]")
                time.sleep(max(0.0, espera) + 0.2)
                r = probar(prov)
                if r is not None:
                    return r

    try:
        r = _chat_local(cfg, history, tools, temperatura, cb, razonamiento)
    except Exception as e:
        if estado["emitido"]:
            raise CorteEnVivo("Perdón, perdí el hilo. ¿Me lo repites?") from e
        print(f"[El modelo local falló: {type(e).__name__}: {str(e)[:120]}]")
        if usar_nube:
            raise SinCerebro("Mis servidores están saturados en este momento. Dame unos "
                             "segundos y vuelve a intentarlo.") from e
        raise SinCerebro("No tengo internet y el modelo local no responde; revisa que Ollama "
                         "esté abierto.") from e
    r["origen"] = "local"
    return r


def precalentar(cfg):
    """Abre de antemano la conexión con la nube (y carga el modelo local si no hay red), para
    que la primera pregunta de la exposición no pague el arranque en frío."""
    try:
        provs = _proveedores(cfg)
        if provs and hay_internet(host_de(provs[0]["url"])):
            p = provs[0]
            cliente(p["url"], os.environ.get(p.get("clave_env", ""), ""), p.get("timeout", 20)).models.list()
        elif cfg.get("model"):
            ollama.chat(model=cfg["model"], messages=[{"role": "user", "content": "hola"}],
                        options={"num_predict": 1}, keep_alive=cfg.get("ollama_keep_alive", "30m"))
    except Exception as e:
        print(f"[Precalentar el cerebro falló: {type(e).__name__}: {str(e)[:100]}]")


if __name__ == "__main__":
    from pathlib import Path

    cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
    print("Internet:", hay_internet())
    for modo in ("offline", "online"):
        cfg["modo"] = modo
        r = chat(cfg, [{"role": "user", "content": "Saluda en una frase."}], [])
        print(f"{modo} -> {r['origen']} | {r['content']}")
