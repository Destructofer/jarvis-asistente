"""La parte "pensante" de Jarvis, independiente del modelo que esté usando (en línea o local).

El modelo de lenguaje es solo el motor; aquí está CÓMO piensa Jarvis:

1. Cuánto pensar (nivel): una orden directa ("abre Spotify") se resuelve rápido; una pregunta
   que pide razonar ("¿cómo organizo mi día?", "¿qué me conviene?", "explícame por qué...") se
   piensa a fondo. Medido: con razonamiento bajo, gpt-oss armó un horario sin sentido ("receso
   de 2 a 2") en 0.5 s; con razonamiento alto, uno coherente en 1.6 s. Pensar cuesta ~1 s, así
   que solo se hace cuando la petición lo merece. Se decide con reglas (sin gastar una petición).

2. Cómo pensar (reglas): entender lo que de verdad necesita la persona, usar el contexto,
   advertir riesgos, no afirmar lo que no verificó, intentar otra vía si algo falla y ofrecer
   el siguiente paso cuando sea obvio, como el J.A.R.V.I.S. de la película.

3. Aprender (memoria automática): de lo que el usuario dice de sí mismo ("me gusta...", "mi
   hermana se llama...", "estudio...") se guardan hechos duraderos, en segundo plano y solo de
   SUS palabras (nunca de documentos, pantallas ni páginas: alguien podría sembrar ahí una
   instrucción).

4. Contexto: qué ventana tiene abierta en este momento, para entender "esto", "aquí", "ahí".
"""
import json
import re
import threading

import memoria
import skills

# ---------- 1. Cuánto pensar ----------
# Órdenes de acción directas: rápidas, sin razonar de más
ACCION = re.compile(
    r"^(?:\w+\s+){0,2}(?:abre|abrir|cierra|cerrar|pon|ponme|reproduce|pausa|sube|baja|silencia|"
    r"apaga|reinicia|bloquea|captura|busca|buscar|escribe|presiona|dale|da clic|clic|ve a|"
    r"cambia|enfoca|minimiza|maximiza|siguiente|anterior|regresa|recuerdame|pon un|activa|"
    r"desactiva|descomprime|lee|mira|escanea)\b")
# Pedidos que piden razonar: planear, decidir, comparar, explicar a fondo, resolver
PROFUNDO = re.compile(
    r"\b(por que|porque|como funciona|como puedo|como hago|como le hago|como organizo|"
    r"organiza|organizar|planea|planear|plan para|estrategia|que me conviene|conviene|"
    r"deberia|que harias|que hago|ayudame a (?:decidir|pensar|entender|resolver|planear)|"
    r"compara|comparar|diferencia entre|ventajas|desventajas|pros y contras|analiza|"
    r"evalua|razona|piensa|reflexiona|explicame|explica (?:bien|a fondo|paso a paso)|"
    r"resuelve|calcula|problema|solucion|cual es (?:la )?mejor|recomiendas|que opinas|"
    r"opinas|que piensas|crees que|vale la pena|que pasaria si|y si|consejo|aconsejas)\b")


# Te está PLATICANDO algo (una situación, un problema, algo que le pasó o le preocupa), no
# dando una orden: ahí se piensa a fondo y se contesta como una persona que escucha y analiza
SITUACION = re.compile(
    r"\b(me paso|me sucedio|te cuento|te platico|dejame contarte|fijate que|resulta que|"
    r"tengo un problema|tengo un dilema|no se que hacer|no se si|que hago|que harias|que le digo|"
    r"como le digo|estoy pensando en|estoy pensando si|me late|me preocupa|me da miedo|me siento|"
    r"me senti|estoy (?:triste|preocupad[oa]|estresad[oa]|nervios[oa]|enojad[oa]|confundid[oa]|"
    r"cansad[oa]|harto|harta|agobiad[oa]|feliz|emocionad[oa])|"
    r"mi (?:jefe|jefa|novia|novio|pareja|esposa|esposo|mama|papa|amigo|amiga|maestro|maestra|"
    r"profesor|profesora|companero|companera|hermano|hermana|familia) (?:me|no|dijo|quiere|esta|"
    r"hizo|piensa|cree)|"
    r"me pelee|nos peleamos|me dijeron|me dijo|me corrieron|me rechazaron|me ofrecieron|"
    r"termine con|me cortaron|me cambiaron|reprobe|me fue mal|me fue bien|"
    r"como ves (?:que|esto|la situacion)|que opinas de que|analiza (?:esto|esta situacion|la situacion|"
    r"lo que)|ayudame a ver|tu que harias)\b")


def es_situacion(texto):
    return bool(SITUACION.search(skills._norm(texto or "")))


def nivel(texto):
    """'rapido' (orden directa), 'normal' o 'profundo' (merece pensarse)."""
    t = skills._norm(texto)
    palabras = len(t.split())
    if PROFUNDO.search(t) or palabras >= 28 or SITUACION.search(t):
        return "profundo"
    if ACCION.search(t) and palabras <= 14:
        return "rapido"
    return "normal"


# reasoning_effort de gpt-oss (en línea) y "think" de los modelos locales por nivel
ESFUERZO = {"rapido": "low", "normal": "low", "profundo": "high"}


def esfuerzo(n, cfg):
    conf = (cfg.get("cognicion") or {}).get("esfuerzo") or {}
    return conf.get(n, ESFUERZO[n])


# ---------- 2. Cómo pensar ----------
REGLAS = (
    "\n\nCÓMO PIENSAS (como J.A.R.V.I.S.: servicial, pero con cabeza propia): entiende qué "
    "necesita de verdad la persona, no solo lo literal, y usa el contexto (la hora, lo que tiene "
    "abierto, lo que recuerdas de ella). Si una orden es ambigua o puede salir mal (borrar, "
    "cerrar algo sin guardar, apagar), advierte o pregunta en una frase. Si una herramienta "
    "falla, intenta otra vía razonable antes de rendirte, y nunca digas que hiciste algo que no "
    "se confirmó. Cuando termines algo y haya un siguiente paso claramente útil, ofrécelo en "
    "pocas palabras (no siempre; nada de '¿algo más?').")
REGLA_PROFUNDA = (
    "\n\nESTA PETICIÓN MERECE PENSARSE: razona por dentro paso a paso (no narres el "
    "razonamiento), revisa tus supuestos, cuentas y fechas, considera alternativas y riesgos. "
    "Responde primero la conclusión o recomendación en 1-2 frases y después solo lo esencial. "
    "Si te falta un dato clave para responder bien, pregunta solo eso.")


REGLA_SITUACION = (
    "\n\nTE ESTÁ PLATICANDO UNA SITUACIÓN (no es una orden): contesta como una persona que de "
    "verdad escucha y piensa, no como un buscador. Primero muestra en una frase natural que "
    "entendiste lo importante (y cómo se siente, si aplica). Luego analízalo: qué está pasando de "
    "fondo, qué opciones tiene, qué ganaría o arriesgaría con cada una y qué harías tú, con una "
    "postura clara y tu razón principal. Usa lo que recuerdas de esa persona. Si falta un dato que "
    "lo cambia todo, pregunta solo eso. Sin listas, sin sermones y sin frases de manual: 3 a 6 "
    "frases en tono de plática. Si la persona la está pasando muy mal o hay riesgo para alguien, "
    "dilo con cuidado y sugiere apoyarse en alguien de confianza o un profesional.")


def reglas(n, texto=""):
    extra = REGLA_SITUACION if texto and es_situacion(texto) else ""
    return REGLAS + (REGLA_PROFUNDA if n == "profundo" else "") + extra


# ---------- 3. Aprender del usuario ----------
# Solo se intenta cuando la persona habla de sí misma (no se gasta una petición por orden)
SOBRE_SI = re.compile(
    r"\b(me gusta|me encanta|me gustan|me encantan|odio|no me gusta|prefiero|soy|estudio|"
    r"trabajo|vivo|mi (?:novia|novio|esposa|esposo|mama|papa|hermana|hermano|hijo|hija|amigo|"
    r"amiga|jefe|maestro|maestra|profesor|perro|gato|carrera|escuela|cumpleanos|equipo|proyecto|"
    r"materia|clase|trabajo|nombre|casa|cuarto|meta)|me llamo|tengo \d+ anos|siempre|nunca|"
    r"suelo|cada (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo|manana|noche)|"
    r"mi (?:\w+ )?favorit[oa]|alergic)\b")

# "De ahora en adelante usa Opera", "prefiero YouTube": son preferencias (preferencias.py), no
# hechos sueltos; si se guardaran también como hecho, al cambiarlas quedaría el dato viejo
PREFERENCIA = re.compile(r"\b(de ahora en adelante|a partir de ahora|desde ahora|por defecto|"
                         r"predeterminad|prefiero|ya no uses|en vez de|en lugar de|siempre que|"
                         r"cada vez que)\b")

EXTRAER = (
    "Del mensaje del usuario, extrae SOLO hechos duraderos sobre él que sirvan para ayudarlo en "
    "el futuro: gustos, preferencias, personas importantes, qué estudia o en qué trabaja, sus "
    "proyectos, rutinas, metas. Nada pasajero ('tengo hambre'), nada que sea una orden, nada de "
    "contraseñas, cuentas, teléfonos, direcciones exactas ni salud. Cada hecho en tercera persona "
    "('Le gusta el rock', 'Su hermana se llama Ana'), en español, máximo 3. Responde SOLO con una "
    "lista JSON de textos; si no hay ninguno, [].")


def _parecido(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, skills._norm(a), skills._norm(b)).ratio()


def _extraer(cfg, texto):
    import cerebro
    historia = [{"role": "system", "content": EXTRAER},
                {"role": "user", "content": texto[:600]}]
    # Lo más barato que haya: sin herramientas, razonamiento bajo
    r = cerebro.chat(cfg, historia, [], 0.1, razonamiento="low")
    contenido = re.sub(r"<think>.*?</think>", "", r.get("content") or "", flags=re.S)
    m = re.search(r"\[.*\]", contenido, re.S)
    if not m:
        return []
    try:
        hechos = json.loads(m.group(0))
    except ValueError:
        return []
    return [str(h).strip() for h in hechos if isinstance(h, str) and 6 <= len(h.strip()) <= 160][:3]


# Un "hecho sobre ti" que habla de Jarvis viene de otro lado: así se guardaron "Se llama
# Jarvis" (una canción) y "Su nombre es Abraham Jarvis" (un video)
SOBRE_JARVIS = re.compile(r"\b(jarvis|yarvis|jervis|el asistente|la ia)\b", re.I)


def _guardar(hechos):
    existentes = [h["texto"] for h in memoria.listar_hechos()]
    nuevos = []
    for h in hechos:
        if memoria.PROHIBIDO.search(h) or memoria.NUMERO_LARGO.search(h) or SOBRE_JARVIS.search(h):
            continue
        if any(_parecido(h, e) > 0.8 for e in existentes + nuevos):
            continue
        memoria.agregar_hecho(h)
        nuevos.append(h)
    return nuevos


def aprender(cfg, texto):
    """En segundo plano: si el usuario habló de sí mismo, guarda lo que valga la pena recordar.
    texto debe ser lo que DIJO el usuario (nunca contenido de terceros)."""
    if not (cfg.get("cognicion") or {}).get("aprender", True) or not memoria._activa():
        return
    if not texto or not SOBRE_SI.search(skills._norm(texto)):
        return
    if PREFERENCIA.search(skills._norm(texto)):
        return  # eso lo guarda fijar_preferencia/aprender_regla (y se puede cambiar después)

    def hacer():
        try:
            nuevos = _guardar(_extraer(cfg, texto))
            if nuevos:
                print(f"[Aprendí: {' · '.join(nuevos)}]")
        except Exception as e:
            print(f"[No pude aprender de eso: {type(e).__name__}: {str(e)[:80]}]")
    threading.Thread(target=hacer, daemon=True, name="aprender").start()


# ---------- 4. Contexto ----------
def contexto():
    """Lo que el usuario tiene enfrente ahora (para entender "esto", "aquí", "ahí")."""
    try:
        import control
        h = control.ventana_activa()
        titulo = control._titulo(h) if h else ""
    except Exception:
        titulo = ""
    if not titulo:
        return ""
    return f"\nVentana activa del usuario ahora: «{titulo[:90]}»."


# ---------- 5. Herramientas para pensar con exactitud ----------
# Los modelos (sobre todo los locales) se equivocan en cuentas y fechas aunque "razonen": en
# las pruebas, ninguno supo qué día de la semana cae el 15 de octubre. Como una persona usa la
# calculadora o el calendario, Jarvis decide CUÁNDO calcular y el resultado sale exacto.
import ast  # noqa: E402
import datetime  # noqa: E402
import math  # noqa: E402

from skills import skill  # noqa: E402

_FUNCIONES = {"raiz": math.sqrt, "sqrt": math.sqrt, "abs": abs, "round": round, "redondear": round,
              "min": min, "max": max, "log": math.log10, "ln": math.log, "sen": math.sin,
              "sin": math.sin, "cos": math.cos, "tan": math.tan, "pi": math.pi, "e": math.e}
_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)


def _evaluar(nodo):
    if isinstance(nodo, ast.Expression):
        return _evaluar(nodo.body)
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.Name) and isinstance(_FUNCIONES.get(nodo.id), float):
        return _FUNCIONES[nodo.id]
    if isinstance(nodo, ast.UnaryOp) and isinstance(nodo.op, _OPS):
        v = _evaluar(nodo.operand)
        return -v if isinstance(nodo.op, ast.USub) else v
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, _OPS):
        a, b = _evaluar(nodo.left), _evaluar(nodo.right)
        if isinstance(nodo.op, ast.Pow) and abs(b) > 100:
            raise ValueError("exponente demasiado grande")
        op = type(nodo.op)
        if op in (ast.Div, ast.FloorDiv, ast.Mod) and b == 0:
            raise ValueError("división entre cero")
        if op is ast.Add:
            return a + b
        if op is ast.Sub:
            return a - b
        if op is ast.Mult:
            return a * b
        if op is ast.Div:
            return a / b
        if op is ast.FloorDiv:
            return a // b
        if op is ast.Mod:
            return a % b
        return a ** b
    if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
            and callable(_FUNCIONES.get(nodo.func.id)) and not nodo.keywords):
        return _FUNCIONES[nodo.func.id](*[_evaluar(a) for a in nodo.args])
    raise ValueError("expresión no permitida")


def _numero(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return f"{int(v):,}".replace(",", " ")
    return f"{v:,.6g}".replace(",", " ") if isinstance(v, float) else f"{v:,}".replace(",", " ")


@skill("calcular",
       "Calcula una expresión matemática con exactitud (sumas, restas, multiplicaciones, "
       "divisiones, porcentajes, potencias, raíz). Úsala SIEMPRE que la respuesta dependa de una "
       "cuenta, en vez de calcular de memoria. Ejemplo: '3*45 + 2*12.5', '200 - 160', "
       "'1500 * 0.16', 'raiz(144)'.",
       {"expresion": {"type": "string", "description": "La operación con números y + - * / ** % ( )"}},
       terminal=False)
def calcular(expresion):
    texto = str(expresion).replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    texto = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", texto)  # 16% -> (16/100)
    try:
        resultado = _evaluar(ast.parse(texto, mode="eval"))
    except Exception as e:
        return skills.Fallo(f"No pude calcular '{expresion}': {e}")
    return f"{expresion} = {_numero(resultado)}"


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _fecha(texto):
    t = skills._norm(str(texto or "hoy"))
    hoy = datetime.date.today()
    if t in ("hoy", ""):
        return hoy
    if t == "manana":
        return hoy + datetime.timedelta(days=1)
    if t == "ayer":
        return hoy - datetime.timedelta(days=1)
    m = re.match(r"(\d{4})\s(\d{1,2})\s(\d{1,2})$", t)       # 2026-10-15
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})\s(\d{1,2})(?:\s(\d{2,4}))?$", t)  # 15/10[/2026]
    if m:
        anio = int(m.group(3) or hoy.year)
        return datetime.date(anio + (2000 if anio < 100 else 0), int(m.group(2)), int(m.group(1)))
    raise ValueError(f"no entendí la fecha '{texto}' (usa AAAA-MM-DD)")


def _decir(f):
    return f"{DIAS[f.weekday()]} {f.day} de {skills.MESES[f.month - 1]} de {f.year}"


@skill("calendario",
       "Cálculos de fechas exactos: qué día de la semana cae una fecha, cuántos días faltan o hay "
       "entre dos fechas, o qué fecha será dentro de N días. Úsala SIEMPRE para preguntas de "
       "fechas en vez de calcular de memoria. Fechas como AAAA-MM-DD, o 'hoy', 'mañana'.",
       {"operacion": {"type": "string", "enum": ["dia_de_la_semana", "dias_entre", "sumar_dias"]},
        "fecha": {"type": "string", "description": "AAAA-MM-DD, 'hoy' o 'mañana'"},
        "fecha2": {"type": "string", "description": "Segunda fecha (para dias_entre)"},
        "dias": {"type": "integer", "description": "Días a sumar (negativo para restar)"}},
       requeridos=["operacion", "fecha"], terminal=False)
def calendario(operacion, fecha, fecha2="", dias=0):
    try:
        f = _fecha(fecha)
        if operacion == "dia_de_la_semana":
            return f"El {f.day} de {skills.MESES[f.month - 1]} de {f.year} cae en {DIAS[f.weekday()]}."
        if operacion == "sumar_dias":
            n = int(dias or 0)
            return f"{n} días después del {_decir(f)} es {_decir(f + datetime.timedelta(days=n))}."
        if operacion == "dias_entre":
            g = _fecha(fecha2)
            return (f"Entre el {_decir(f)} y el {_decir(g)} hay {abs((g - f).days)} días"
                    f" ({'faltan' if g >= f else 'pasaron'}).")
    except Exception as e:
        return skills.Fallo(f"No pude calcular la fecha: {e}")
    return skills.Fallo(f"Operación de calendario desconocida: {operacion}")
