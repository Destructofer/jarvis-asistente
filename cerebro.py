import json
import os
import socket
import time
import uuid

import ollama

_ultimo = (0.0, False)


def hay_internet():
    """Comprueba conexión (cacheado 30 s para no frenar cada respuesta)."""
    global _ultimo
    t, ok = _ultimo
    if time.time() - t < 30:
        return ok
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.5).close()
        ok = True
    except OSError:
        ok = False
    _ultimo = (time.time(), ok)
    return ok


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


def _a_openai(history):
    out = []
    for m in history:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant", "content": m.get("content") or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"],
                                             "arguments": json.dumps(c["args"])}}
                               for c in m["tool_calls"]],
            })
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["id"], "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


# ---------- Cerebros ----------
class SinCerebro(Exception):
    """Ni la nube ni el modelo local respondieron; el mensaje ya viene listo para decirlo."""


def _chat_local(cfg, history, tools, temperatura):
    # num_ctx: Ollama usa por defecto una ventana de ~4k tokens y solo las ~40 herramientas
    # ocupan ~6k; sin esto recortaba en silencio las instrucciones y respondía a ciegas.
    resp = ollama.chat(model=cfg["model"], messages=_a_ollama(history),
                       tools=tools or None,
                       options={"temperature": temperatura,
                                "num_ctx": cfg.get("ollama_ctx", 16384)})
    msg = resp.message
    calls = [{"id": f"call_{uuid.uuid4().hex[:8]}", "name": c.function.name,
              "args": dict(c.function.arguments)} for c in (msg.tool_calls or [])]
    return {"content": msg.content or "", "tool_calls": calls}


def _proveedores(cfg):
    """Lista de cerebros en la nube a probar, en orden. El plan gratis de Groq da 8,000 tokens
    por minuto POR MODELO: si uno se satura, el siguiente modelo tiene su propio límite.
    config.json → nube.respaldos (modelos del mismo proveedor) y nubes_extra (otros, p. ej.
    Claude de Anthropic)."""
    base = dict(cfg.get("nube", {}) or {})
    lista = [base] if base.get("url") else []
    for m in base.get("respaldos", ["openai/gpt-oss-20b"]):
        if m and m != base.get("modelo"):
            lista.append(dict(base, modelo=m))
    for extra in cfg.get("nubes_extra", []) or []:
        if extra.get("url") and extra.get("modelo"):
            lista.append(extra)
    return [p for p in lista if os.environ.get(p.get("clave_env", ""), "")]


def _chat_nube(prov, history, tools, temperatura):
    from openai import OpenAI

    clave = os.environ.get(prov.get("clave_env", ""), "")
    # max_retries=0: si el servicio está saturado NO se espera (antes se quedaba hasta 45 s
    # reintentando en silencio); se pasa de inmediato al siguiente modelo de la lista.
    cliente = OpenAI(api_key=clave, base_url=prov["url"], timeout=prov.get("timeout", 20),
                     max_retries=0)
    kwargs = {"model": prov["modelo"], "messages": _a_openai(history),
              "temperature": temperatura}
    if prov.get("max_tokens"):
        kwargs["max_tokens"] = int(prov["max_tokens"])
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if prov.get("razonamiento") and "gpt-oss" in prov["modelo"]:
        kwargs["reasoning_effort"] = prov["razonamiento"]  # "low" = responde más rápido
    try:
        msg = cliente.chat.completions.create(**kwargs).choices[0].message
    except Exception as e:
        if "reasoning_effort" in kwargs and "reasoning" in str(e).lower():
            kwargs.pop("reasoning_effort")
            msg = cliente.chat.completions.create(**kwargs).choices[0].message
        else:
            raise

    calls = []
    for c in msg.tool_calls or []:
        try:
            args = json.loads(c.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": c.id, "name": c.function.name, "args": args})
    return {"content": msg.content or "", "tool_calls": calls}


def _motivo(e):
    t = str(e)
    if "413" in t or "too large" in t.lower():
        return "petición demasiado grande para el plan"
    if "429" in t or "rate limit" in t.lower():
        return "límite por minuto alcanzado"
    return f"{type(e).__name__}: {t[:100]}"


def chat(cfg, history, tools, temperatura=0.2):
    """Devuelve {'content', 'tool_calls', 'origen'}. Prueba los cerebros en la nube en orden
    y, si ninguno responde, el modelo local."""
    modo = cfg.get("modo", "auto")
    proveedores = _proveedores(cfg) if modo != "offline" else []
    usar_nube = bool(proveedores) and (modo == "online" or hay_internet())

    if usar_nube:
        for prov in proveedores:
            try:
                r = _chat_nube(prov, history, tools, temperatura)
                r["origen"] = f"nube · {prov['modelo']}"
                return r
            except Exception as e:
                print(f"[{prov['modelo']} no respondió ({_motivo(e)}); pruebo el siguiente]")

    try:
        r = _chat_local(cfg, history, tools, temperatura)
    except Exception as e:
        print(f"[El modelo local falló: {type(e).__name__}: {str(e)[:120]}]")
        if usar_nube:
            raise SinCerebro("Mis servidores están saturados en este momento. Dame unos "
                             "segundos y vuelve a intentarlo.") from e
        raise SinCerebro("No tengo internet y el modelo local no responde; revisa que Ollama "
                         "esté abierto.") from e
    r["origen"] = "local"
    return r


if __name__ == "__main__":
    from pathlib import Path

    cfg = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
    print("Internet:", hay_internet())
    for modo in ("offline", "online"):
        cfg["modo"] = modo
        r = chat(cfg, [{"role": "user", "content": "Saluda en una frase."}], [])
        print(f"{modo} -> {r['origen']} | {r['content']}")