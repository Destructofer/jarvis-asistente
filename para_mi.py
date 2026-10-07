"""¿Era para mí? Una red neuronal local que decide si lo que dijiste SIN llamarlo era para Jarvis.

El problema: en modo conversación Jarvis escucha todo. Lo que suena a video, a juego o a una
plática con otra persona se lo preguntaba al modelo en la nube (~0.8 s y cuota por cada frase), que
a veces contestaba igual (en el registro hay respuestas a narraciones de videos), y sin internet
se callaba con todo.

La solución es destilación (deep learning): una red chica aprende a imitar las buenas
decisiones del modelo grande y de las reglas, con ejemplos limpios:
- SÍ era para él: lo que le dijiste llamándolo por su nombre (se le quita el "Jarvis" para que la
  red aprenda el TIPO de frase, no el nombre), las órdenes de práctica generadas en tu PC y
  seguimientos sin nombre ("¿y cuánto cuesta?", "ahora la siguiente").
- NO era para él: lo que el micrófono captó saliendo de la computadora (videos, juegos), lo que
  el modelo descartó, y ruido de fondo de práctica (videos, llamadas, pláticas, canciones).
Red: granite-embedding (preentrenada, 768 números por frase) -> MLP de 2 capas -> probabilidad.

Cómo se usa (genesis._no_es_para_mi), solo con lo dicho SIN llamarlo:
- prob <= config para_mi.callarse (5% por omisión; 0 lo apaga): se calla al instante, sin gastar
  la nube. Medido con tus frases (validación cruzada): silencia ~40% del ruido de fondo y de lo
  que silencia, 83-91% de verdad no era para él; ignoraría ~5-7% de los seguimientos reales
  (los repites con "Jarvis").
- sin internet y prob >= umbral alto (calibrado al 97%): contesta el modelo local (antes se
  callaba con todo).
- en medio: decide el modelo, como antes.

Aprende de sus errores: si silencia algo y en los siguientes 30 s lo repites llamándolo por su
nombre, queda como corrección (datos/para_mi/correcciones.jsonl) y entra como "sí" al reentrenar.

Entrenar: python para_mi.py entrenar   (o "Jarvis, reentrena tu detector")
Probar:   python para_mi.py probar "y luego qué pasó con el carro"
Todo vive en datos/para_mi/ (sale de tus conversaciones: no se sube al repo).
"""
import hashlib
import json
import random
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from skills import skill

BASE = Path(__file__).resolve().parent
CARPETA = BASE / "datos" / "para_mi"
MODELO = CARPETA / "modelo.joblib"
ORDENES = CARPETA / "ordenes.json"     # órdenes de práctica por herramienta (caché)
FONDO = CARPETA / "fondo.json"         # ruido de fondo y seguimientos de práctica (caché)
REGISTRO = BASE / "datos" / "genesis.log"
CODIFICADOR = "granite-embedding:278m"
GENERADOR = "qwen2.5:7b"               # local y abierto
PRECISION = 0.97                       # lo que se exige a las decisiones que se toman sin el modelo
MAX_ORDENES = 900                      # órdenes de práctica que entran (para no desbalancear)
CORRECCIONES = CARPETA / "correcciones.jsonl"
CALLARSE = 0.05                        # por omisión (config: para_mi.callarse)
VENTANA_CORRECCION = 30                # s para repetir con "Jarvis" algo que silenció

hablar = None                          # lo pone genesis.py: fn(texto) al terminar de entrenar
_estado = {"modelo": None, "entrenando": False}
_lock = threading.Lock()
_cache = OrderedDict()                 # texto -> vector (lo último que se codificó)
_silenciados = []                      # [(ts, texto)] lo último que silenció (para las correcciones)

# Ruido de fondo y seguimientos de práctica: (categoría, si/no era para Jarvis, instrucción)
CATEGORIAS = [
    ("video", 0, "frases que dice un youtuber o narrador en un video (reseñas, tutoriales, historias, noticias)"),
    ("juego", 0, "frases de un videojuego o de un streamer jugando (narración, personajes, 'suscríbete')"),
    ("platica", 0, "frases que una persona le dice a OTRA persona en su casa (mamá, hermano, amigo), no a un asistente"),
    ("llamada", 0, "frases de alguien hablando por teléfono o en una videollamada con otra gente"),
    ("clase", 0, "frases de un maestro dando clase o de compañeros en una reunión de Teams"),
    ("cancion", 0, "versos de canciones en español (reguetón, corridos, pop) como se oyen de fondo"),
    ("solo", 0, "frases que alguien dice en voz alta para sí mismo mientras trabaja ('a ver dónde quedó esto')"),
    ("tele", 0, "frases de la televisión: novelas, comerciales, partidos de fútbol"),
    ("seguimiento", 1, "preguntas o pedidos de seguimiento que alguien le hace a su asistente de voz SIN decir su "
                       "nombre, justo después de que el asistente le contestó ('¿y cuánto cuesta?', 'ahora la "
                       "siguiente', 'no, mejor la otra', 'explícame eso')"),
    ("pregunta", 1, "preguntas que alguien le hace directamente a su asistente de voz sobre cualquier tema, sin "
                    "decir su nombre ('¿qué opinas de...?', '¿cómo hago...?', 'dime...')"),
]
POR_CATEGORIA = 40


# ---------- Datos ----------
_TU = re.compile(r"^Tú( \(sin llamarme\))?: (.+)$")
_NOMBRE = re.compile(r"^\W*((oye|hey|ey|ok|okey)\W+)?(jarvis|yarvis|jervis|harvis|jarbis)\b\W*", re.I)


def quitar_nombre(texto):
    """'Jarvis, abre Teams' -> 'abre Teams' (la red aprende el tipo de frase, no el nombre)."""
    t = _NOMBRE.sub("", (texto or "").strip())
    t = re.sub(r"\W*\b(jarvis|yarvis|jervis)\W*$", "", t, flags=re.I)
    return t.strip()


def del_registro(lineas):
    """(sí, no, dudosos) del registro de Jarvis.
    sí: le hablaste llamándolo y contestó. no: el micrófono lo captó de la computadora, o el
    modelo lo descartó. dudosos: sin llamarlo y contestó (puede haber sido un error: no se usan
    para entrenar)."""
    si, no, dudosos = [], [], []
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        pc = re.match(r"^\[Ignoro «(.+?)»: venía de la computadora", linea)
        if pc:
            no.append(pc.group(1).rstrip(". …"))
        m = _TU.match(linea)
        if m:
            sin_nombre, texto = bool(m.group(1)), m.group(2).strip()
            destino = None
            j = i + 1
            while j < len(lineas) and not lineas[j].startswith("Tú"):
                l = lineas[j]
                if l.startswith("[No era para mí"):
                    destino = no
                    break
                if l.startswith("[Sonaba algo"):
                    destino = no
                    break
                if l.startswith("[Frase corta"):
                    break
                if l.startswith("[Skill]") or re.match(r"^\w+: ", l):
                    if "no logré procesar" in l:
                        break
                    destino = dudosos if sin_nombre else si
                    break
                j += 1
            limpio = quitar_nombre(texto)
            if destino is not None and 3 <= len(limpio) <= 200:
                destino.append(limpio)
        i += 1
    return si, no, dudosos


def frase_valida(f):
    """Quita la introducción del modelo ("Claro, aquí tienes 24 frases...") y lo que no es español."""
    f = (f or "").strip()
    if not 3 <= len(f) <= 160 or f.endswith(":"):
        return False
    if re.search(r"\b(aqui tienes|aquí tienes|frases|claro, aqu)", f.lower()):
        return False
    return not re.search(r"[^\x00-ɏ¿¡«»“”‘’—–…♪]", f)


def _pedir(instruccion, n):
    import ollama
    r = ollama.chat(model=GENERADOR, messages=[{"role": "user", "content": instruccion}],
                    options={"temperature": 0.9, "num_predict": 1200}, keep_alive="10m")
    lineas = [re.sub(r"^\s*(\d+[\.\)]|[-*•])\s*", "", l).strip(" \"'") for l in r["message"]["content"].splitlines()]
    return [l for l in lineas if frase_valida(l)][:n]


def _leer(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _escribir(ruta, datos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def de_practica(avisar=print, generar=True):
    """(sí, no) de práctica: las categorías de CATEGORIAS (con caché) y las órdenes por herramienta."""
    cache = _leer(FONDO)
    for nombre, _, desc in CATEGORIAS:
        huella = hashlib.sha1(desc.encode("utf-8")).hexdigest()[:12]
        if not generar or cache.get(nombre, {}).get("huella") == huella:
            continue
        avisar(f"[¿Para mí?: generando ejemplos de {nombre}]")
        try:
            frases = _pedir(f"Escribe {POR_CATEGORIA} {desc}, en español de México, como las transcribiría "
                            "un dictado (sin comillas). Varía mucho el largo y el tema. Una por línea, sin "
                            "numerar y sin explicar nada.", POR_CATEGORIA)
        except Exception as e:
            avisar(f"[¿Para mí?: no pude generar {nombre} ({type(e).__name__})]")
            continue
        cache[nombre] = {"huella": huella, "frases": frases}
        _escribir(FONDO, cache)
    si, no = [], []
    for nombre, para_mi, _ in CATEGORIAS:
        (si if para_mi else no).extend(f for f in cache.get(nombre, {}).get("frases", []) if frase_valida(f))
    ordenes = [f for v in _leer(ORDENES).values() for f in v.get("frases", []) if frase_valida(f)]
    random.Random(5).shuffle(ordenes)
    si.extend(quitar_nombre(f) for f in ordenes[:MAX_ORDENES])
    return si, no


# ---------- Codificador ----------
def codificar(textos):
    import ollama
    faltan = [t for t in dict.fromkeys(textos) if t not in _cache]
    for i in range(0, len(faltan), 64):
        trozo = faltan[i:i + 64]
        r = ollama.embed(model=CODIFICADOR, input=trozo, options={"num_gpu": 0}, keep_alive="30m")
        m = np.asarray(r["embeddings"], dtype=np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
        for t, v in zip(trozo, m):
            _cache[t] = v
    salida = np.vstack([_cache[t] for t in textos]) if textos else np.zeros((0, 768), np.float32)
    while len(_cache) > 20000:
        _cache.popitem(last=False)
    return salida


# ---------- Entrenar ----------
def _red(n):
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), MLPClassifier(
        hidden_layer_sizes=(256, 64), activation="relu", alpha=1e-2, max_iter=500,
        early_stopping=n >= 600, validation_fraction=0.12, n_iter_no_change=25, random_state=7))


def umbrales(probs, etiquetas, precision=PRECISION):
    """(bajo, alto): con prob <= bajo, al menos 'precision' de los casos de verdad NO eran para
    él; con prob >= alto, al menos 'precision' SÍ lo eran. Si no se alcanza: (0, 1) = nunca."""
    probs, y = np.asarray(probs), np.asarray(etiquetas)
    bajo, alto = 0.0, 1.0
    for u in np.unique(probs):
        sel = probs <= u
        if sel.sum() >= 5 and (y[sel] == 0).mean() >= precision:
            bajo = float(u)
        sel = probs >= u
        if sel.sum() >= 5 and (y[sel] == 1).mean() >= precision and alto == 1.0:
            alto = float(u)
    return min(bajo, 0.5), max(alto, 0.5)


def entrenar(avisar=print, generar=True, datos=None, callarse=CALLARSE):
    """Entrena con validación cruzada (5 partes) sobre TUS frases reales para medir y calibrar
    los umbrales, y al final con todo. datos=(si, no, dudosos, si_practica, no_practica) para pruebas."""
    import joblib
    if datos is None:
        try:
            lineas = REGISTRO.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lineas = []
        si, no, dudosos = del_registro(lineas)
        si = [t for t in si if len(t.split()) >= 3] + correcciones()
        no = [t for t in no if len(t.split()) >= 3]
        si_p, no_p = de_practica(avisar, generar)
    else:
        si, no, dudosos, si_p, no_p = datos
    reales = [(t, 1) for t in si] + [(t, 0) for t in no]
    if len(reales) < 20 or not si or not no:
        raise RuntimeError("Faltan frases reales para entrenar.")
    practica = [(t, 1) for t in si_p] + [(t, 0) for t in no_p]
    avisar(f"[¿Para mí?: {len(si)} sí y {len(no)} no tuyas · {len(si_p)} sí y {len(no_p)} no de práctica · codificando]")
    textos = sorted({t for t, _ in reales + practica})
    V = dict(zip(textos, codificar(textos)))
    rnd = random.Random(3)
    mezcla = reales[:]
    rnd.shuffle(mezcla)
    partes = [mezcla[i::5] for i in range(5)]
    probs, ys = [], []
    for k in range(5):
        prueba = partes[k]
        entreno = [x for j in range(5) if j != k for x in partes[j]] * 3 + practica
        red = _red(len(entreno)).fit(np.vstack([V[t] for t, _ in entreno]), [y for _, y in entreno])
        probs += list(red.predict_proba(np.vstack([V[t] for t, _ in prueba]))[:, 1])
        ys += [y for _, y in prueba]
    probs, ys = np.array(probs), np.array(ys)
    bajo, alto = umbrales(probs, ys)
    calla = probs <= callarse
    met = {"si": len(si), "no": len(no), "si_practica": len(si_p), "no_practica": len(no_p),
           "exactitud": round(float(((probs >= 0.5) == ys).mean()), 3),
           "bajo": round(bajo, 3), "alto": round(alto, 3), "callarse": callarse,
           # con el umbral que se usa: de lo que NO era para él, cuánto se calla sin la nube...
           "callados_sin_nube": round(float(calla[ys == 0].mean()), 3),
           # ...de lo que calla, cuánto de verdad no era para él...
           "acierto_al_callar": round(float((ys[calla] == 0).mean()), 3) if calla.any() else None,
           # ...y de lo que SÍ era para él, cuánto se callaría por error
           "perdidos": round(float(calla[ys == 1].mean()), 3),
           "fecha": time.strftime("%Y-%m-%d %H:%M")}
    todo = reales * 3 + practica
    red = _red(len(todo)).fit(np.vstack([V[t] for t, _ in todo]), [y for _, y in todo])
    if dudosos:   # lo que contestó sin que lo llamaras: ¿cuántos parecen videos o pláticas?
        pd = red.predict_proba(codificar(dudosos))[:, 1]
        met["dudosos"] = len(dudosos)
        met["dudosos_que_callaria"] = int((pd <= callarse).sum())
        met["ejemplos_dudosos"] = [t for t, p in sorted(zip(dudosos, pd), key=lambda x: x[1])[:6]]
    MODELO.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"red": red, "metricas": met, "version": 1}, MODELO)
    with _lock:
        _estado["modelo"] = None
    return met


# ---------- Usar ----------
def _cargar():
    with _lock:
        if _estado["modelo"] is None:
            _estado["modelo"] = False
            if MODELO.exists():
                try:
                    import joblib
                    _estado["modelo"] = joblib.load(MODELO)
                except Exception as e:
                    print(f"[¿Para mí?: no pude cargar la red ({type(e).__name__})]")
        return _estado["modelo"] or None


def probabilidad(texto):
    """Qué tan probable es que lo dicho fuera para Jarvis (0-1), o None si no hay red."""
    m = _cargar()
    t = quitar_nombre(texto)
    if not m or not t:
        return None
    try:
        return float(m["red"].predict_proba(codificar([t]))[0, 1])
    except Exception:
        return None


def decidir(texto, callarse=CALLARSE):
    """'no' (callarse sin preguntarle al modelo), 'si' (muy segura de que era para él) o None
    (que decida el modelo)."""
    p = probabilidad(texto)
    if p is None:
        return None
    met = _cargar()["metricas"]
    if p <= callarse:
        print(f"[¿Para mí? no ({p:.0%})]")
        _silenciados.append((time.time(), quitar_nombre(texto)))
        del _silenciados[:-10]
        return "no"
    if p >= met["alto"]:
        return "si"
    return None


def _palabras(t):
    return {w for w in re.findall(r"\w+", (t or "").lower()) if len(w) > 2}


def llamado(texto, ahora=None):
    """Lo llamaste por su nombre: si repite algo que la red silenció hace poco, fue un error de
    la red y queda como corrección. Devuelve True si se anotó una."""
    ahora = time.time() if ahora is None else ahora
    nuevo = _palabras(quitar_nombre(texto))
    for ts, viejo in reversed(_silenciados):
        if ahora - ts > VENTANA_CORRECCION:
            continue
        a = _palabras(viejo)
        if a and nuevo and len(a & nuevo) / len(a | nuevo) >= 0.5:
            CARPETA.mkdir(parents=True, exist_ok=True)
            with open(CORRECCIONES, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ahora, "texto": viejo}, ensure_ascii=False) + "\n")
            _silenciados.remove((ts, viejo))
            print(f"[¿Para mí?: me equivoqué con «{viejo[:60]}»; lo anoto para aprender]")
            return True
    return False


def correcciones():
    try:
        lineas = CORRECCIONES.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    salida = []
    for l in lineas:
        try:
            salida.append(json.loads(l)["texto"])
        except (ValueError, KeyError):
            pass
    return salida


def describir(m):
    texto = (f"Listo: aprendí con {m['si']} frases que sí eran para mí y {m['no']} que no, más "
             f"{m['si_practica'] + m['no_practica']} de práctica. Con frases tuyas que no había visto acierto "
             f"{m['exactitud']:.0%}.")
    if m.get("acierto_al_callar") is not None:
        texto += (f" Me callo solo, sin gastar la nube, en {m['callados_sin_nube']:.0%} de lo que no es para "
                  f"mí (acertando {m['acierto_al_callar']:.0%} de esas veces) y me equivocaría callándome en "
                  f"{m['perdidos']:.0%} de lo que sí.")
    else:
        texto += " Con el umbral actual casi nunca me callo solo: lo sigue decidiendo el modelo."
    return texto


@skill("entrenar_detector",
       "Reentrena la red neuronal con la que Jarvis decide si lo que se dice sin llamarlo era para "
       "él (videos, pláticas con otros): 'reentrena tu detector', 'aprende cuándo te hablo a ti'.",
       terminal=True)
def entrenar_detector():
    if _estado["entrenando"]:
        return "Ya estoy entrenando; te aviso cuando termine."
    avisar = hablar or print

    def trabajar():
        _estado["entrenando"] = True
        try:
            avisar(describir(entrenar(print)))
        except Exception as e:
            avisar(f"No pude entrenar el detector ({type(e).__name__}: {str(e)[:100]}).")
        finally:
            _estado["entrenando"] = False
    threading.Thread(target=trabajar, daemon=True, name="entrenar-para-mi").start()
    return "Va, me pongo a estudiar cuándo me hablas a mí; tardo unos minutos y te aviso."


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "entrenar":
        m = entrenar()
        print(json.dumps(m, ensure_ascii=False, indent=1))
        print(describir(m))
    elif len(sys.argv) > 2 and sys.argv[1] == "probar":
        texto = " ".join(sys.argv[2:])
        print(f"{probabilidad(texto)!r} -> {decidir(texto)}")
    else:
        print(__doc__)
