"""Lo que Jarvis aprende de cómo quieres que haga las cosas, y lo sigue haciendo hasta que le
digas otra cosa.

    "De ahora en adelante usa Opera como navegador"       -> preferencia navegador = Opera GX
    "Para la música prefiero YouTube"                      -> preferencia musica = YouTube
    "Siempre que te pida un resumen, guárdalo en Word"     -> regla permanente
    "Cuando diga 'modo estudio', pon lofi y abre Notion"   -> regla permanente
    "Ya no uses Opera, vuelve a Chrome"                    -> preferencia actualizada

Dos tipos de memoria, porque actúan distinto:

- PREFERENCIAS (categoría -> valor): las aplica el CÓDIGO, sin depender de que el modelo se
  acuerde. Abrir una página o buscar en internet usa tu navegador y tu buscador; "abre el
  navegador", "abre mi editor de código" abre el que elegiste. También van al contexto del
  modelo para lo que él decide (qué servicio de música usar, en qué formato guardar).
- REGLAS (instrucciones en tus palabras): van al contexto del modelo en cada orden, como
  "instrucciones permanentes". Sirven para todo lo que no es una simple preferencia.

Todo se guarda en datos/genesis.db (la misma base que la memoria) y se puede ver ("¿qué
preferencias tienes de mí?") o deshacer ("olvida la regla del modo estudio").

Seguridad: fijar una preferencia o una regla es "sensible" (genesis.py pide confirmación si en
la conversación entró texto de terceros): un mensaje de Teams o una página no deben poder
sembrarle a Jarvis una instrucción permanente.
"""
import os
import subprocess
import threading
import webbrowser
from urllib.parse import quote_plus

import memoria
import skills
from skills import Fallo, skill

# Categorías conocidas: nombre canónico -> (cómo se dice, si es una app que se abre)
CATEGORIAS = {
    "navegador": (("navegador", "browser", "explorador de internet", "explorador web", "internet",
                   "navegador web"), True),
    "buscador": (("buscador", "motor de busqueda", "busquedas"), False),
    "musica": (("musica", "canciones", "reproductor de musica", "servicio de musica"), False),
    "videos": (("videos", "video"), False),
    "codigo": (("editor de codigo", "codigo", "ide", "programar", "editor para programar"), True),
    "notas": (("notas", "editor de texto", "bloc de notas", "texto"), True),
    "documentos": (("documentos", "procesador de texto", "formato de documentos", "resumenes"), False),
    "hojas": (("hojas de calculo", "hoja de calculo", "tablas", "excel"), True),
    "presentaciones": (("presentaciones", "diapositivas"), True),
    "correo": (("correo", "email", "mail", "correo electronico"), True),
    "mensajes": (("mensajes", "mensajeria", "chat", "chatear"), True),
    "terminal": (("terminal", "consola", "linea de comandos"), True),
    "archivos": (("explorador de archivos", "archivos", "gestor de archivos"), True),
}
BUSCADORES = {
    "google": "https://www.google.com/search?q={}", "bing": "https://www.bing.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}", "brave": "https://search.brave.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "perplexity": "https://www.perplexity.ai/search?q={}", "ecosia": "https://www.ecosia.org/search?q={}",
}
NOMBRES = {"musica": "música", "codigo": "editor de código", "notas": "editor de notas",
           "hojas": "hojas de cálculo", "archivos": "explorador de archivos"}
MAX_REGLAS = 25


def _nombre_cat(cat):
    return NOMBRES.get(cat, cat)
_lock = threading.Lock()


# ---------- Almacén ----------
def _q(sql, params=(), escribir=False):
    con = memoria._conectar()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS preferencias (categoria TEXT PRIMARY KEY, "
                    "valor TEXT NOT NULL, destino TEXT, actualizado TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS reglas (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "texto TEXT NOT NULL, creado TEXT NOT NULL)")
        filas = con.execute(sql, params).fetchall()
        if escribir:
            con.commit()
        return filas
    finally:
        con.close()


def categoria_de(texto):
    """'el navegador' / 'mi browser' -> 'navegador'. Si no es una conocida, la propia frase
    normalizada (así también sirven preferencias nuevas: 'lenguaje de programacion')."""
    t = skills._norm(texto)
    for prefijo in ("el ", "la ", "los ", "las ", "mi ", "mis ", "tu ", "de ", "para "):
        if t.startswith(prefijo):
            t = t[len(prefijo):]
    for nombre, (dichos, _app) in CATEGORIAS.items():
        if t == nombre or t in dichos:
            return nombre
    for nombre, (dichos, _app) in CATEGORIAS.items():
        if any(d in t for d in dichos if len(d) > 5):
            return nombre
    return t


def obtener(categoria):
    """(valor, destino) de esa categoría, o (None, None)."""
    filas = _q("SELECT valor, destino FROM preferencias WHERE categoria = ?",
               (categoria_de(categoria),))
    return (filas[0]["valor"], filas[0]["destino"]) if filas else (None, None)


def todas():
    return [(f["categoria"], f["valor"]) for f in
            _q("SELECT categoria, valor FROM preferencias ORDER BY categoria")]


def reglas():
    return [(f["id"], f["texto"]) for f in _q("SELECT id, texto FROM reglas ORDER BY id")]


# ---------- Lo que aplica el código ----------
def abrir_url(url):
    """Abre una página con TU navegador (si elegiste uno); si no, con el de Windows."""
    valor, destino = obtener("navegador")
    if destino:
        try:
            os.startfile(os.path.expandvars(destino), "open", url)
            return valor
        except OSError:
            try:
                subprocess.Popen([os.path.expandvars(destino), url])
                return valor
            except OSError:
                pass  # se desinstaló o se movió: el de Windows
    webbrowser.open(url)
    return None


def url_busqueda(consulta):
    valor, _ = obtener("buscador")
    plantilla = BUSCADORES.get(skills._norm(valor or "google").replace(" ", ""), BUSCADORES["google"])
    return plantilla.format(quote_plus(consulta))


def app_para(nombre):
    """Si 'nombre' es una categoría ('el navegador', 'mi editor de código') con una app
    elegida, devuelve (nombre de la app, destino); si no, None."""
    cat = categoria_de(nombre)
    if cat not in CATEGORIAS or not CATEGORIAS[cat][1]:
        return None
    valor, destino = obtener(cat)
    return (valor, destino) if destino else None


def contexto():
    """Para el prompt: lo que el modelo debe respetar en cada orden (corto)."""
    prefs = todas()
    partes = []
    if prefs:
        partes.append("PREFERENCIAS DEL USUARIO (úsalas siempre, salvo que en esta orden pida "
                      "otra cosa): " + "; ".join(f"{c}: {v}" for c, v in prefs) + ".")
    rs = reglas()
    if rs:
        partes.append("INSTRUCCIONES PERMANENTES QUE TE DIO (síguelas siempre que apliquen):\n"
                      + "\n".join(f"- {t}" for _, t in rs))
    return ("\n\n" + "\n".join(partes)) if partes else ""


# ---------- Skills ----------
@skill("fijar_preferencia",
       "Guarda una preferencia PERMANENTE del usuario sobre qué usar para algo y desde ese momento "
       "Jarvis la aplica solo. Úsala cuando diga 'de ahora en adelante usa X', 'mi navegador es X', "
       "'prefiero X para Y', 'usa X por defecto', 'ya no uses X, usa Z'. categoria: navegador, "
       "buscador, musica, videos, codigo, notas, documentos, hojas, presentaciones, correo, "
       "mensajes, terminal, archivos (u otra en pocas palabras). valor: lo elegido "
       "(Opera, Chrome, YouTube, Spotify, Word, Bing...).",
       {"categoria": {"type": "string", "description": "Para qué es (navegador, musica, codigo...)"},
        "valor": {"type": "string", "description": "Lo que quiere usar (Opera, YouTube, Word...)"}},
       sensible=True)
def fijar_preferencia(categoria, valor):
    cat = categoria_de(categoria)
    valor = " ".join(str(valor).split())
    if not cat or not valor:
        return Fallo("Dime para qué es y qué quieres usar.")
    destino = None
    nombre = valor
    if cat in CATEGORIAS and CATEGORIAS[cat][1]:
        # Es una app que hay que abrir: se busca entre las instaladas (como abrir_app)
        import apps
        encontrada = apps.buscar_app(valor)
        if encontrada is None:
            return Fallo(f"No encontré '{valor}' instalado en la computadora; no cambié tu {cat}.")
        nombre, destino, _p = encontrada
        if nombre == nombre.lower():  # el índice guarda algunos en minúsculas ("opera gx")
            nombre = nombre.title() if skills._norm(valor) in skills._norm(nombre) else valor
    elif cat == "buscador" and skills._norm(valor).replace(" ", "") not in BUSCADORES:
        return Fallo(f"No conozco el buscador '{valor}'. Puedo usar: {', '.join(BUSCADORES)}.")
    with _lock:
        anterior, _ = obtener(cat)
        _q("INSERT OR REPLACE INTO preferencias (categoria, valor, destino, actualizado) "
           "VALUES (?, ?, ?, ?)", (cat, nombre, destino, memoria._ahora()), escribir=True)
    if anterior and skills._norm(anterior) != skills._norm(nombre):
        return (f"Listo: cambié tu {_nombre_cat(cat)} de {anterior} a {nombre}, y así lo usaré de "
                "ahora en adelante.")
    return f"Listo: de ahora en adelante uso {nombre} como tu {_nombre_cat(cat)}."


@skill("aprender_regla",
       "Guarda una INSTRUCCIÓN PERMANENTE del usuario que Jarvis debe seguir siempre a partir de "
       "ahora, cuando no es una simple preferencia de app. Ejemplos: 'siempre que te pida un "
       "resumen, guárdalo en Word', 'cuando diga modo estudio, pon música lofi y silencia "
       "WhatsApp', 'háblame de tú', 'no me pidas confirmación para cerrar el Bloc de notas'. "
       "Escríbela clara y completa, como una instrucción para ti.",
       {"regla": {"type": "string", "description": "La instrucción, completa y clara"}},
       sensible=True)
def aprender_regla(regla):
    texto = " ".join(str(regla).split())
    if len(texto) < 8:
        return Fallo("Dime la instrucción completa.")
    if memoria.PROHIBIDO.search(texto) or memoria.NUMERO_LARGO.search(texto):
        return Fallo("Esa instrucción trae datos sensibles (contraseñas, cuentas); no la guardo.")
    from difflib import SequenceMatcher
    with _lock:
        existentes = reglas()
        for id_, t in existentes:  # la misma instrucción dicha otra vez (o casi): se actualiza
            if SequenceMatcher(None, skills._norm(t), skills._norm(texto)).ratio() > 0.75:
                _q("UPDATE reglas SET texto = ?, creado = ? WHERE id = ?",
                   (texto, memoria._ahora(), id_), escribir=True)
                return "Listo, actualicé esa instrucción y la seguiré de ahora en adelante."
        if len(existentes) >= MAX_REGLAS:
            return Fallo(f"Ya tengo {MAX_REGLAS} instrucciones permanentes; pídeme olvidar alguna.")
        _q("INSERT INTO reglas (texto, creado) VALUES (?, ?)", (texto, memoria._ahora()), escribir=True)
    return "Entendido, lo haré así de ahora en adelante."


@skill("ver_preferencias",
       "Dice las preferencias e instrucciones permanentes que Jarvis ha aprendido del usuario.",
       terminal=False)
def ver_preferencias():
    prefs, rs = todas(), reglas()
    if not prefs and not rs:
        return "Todavía no me has dado preferencias ni instrucciones permanentes."
    partes = []
    if prefs:
        partes.append("Preferencias: " + "; ".join(f"{c}: {v}" for c, v in prefs) + ".")
    if rs:
        partes.append("Instrucciones: " + " | ".join(f"{i}) {t}" for i, (_, t) in enumerate(rs, 1)))
    return " ".join(partes)


@skill("olvidar_preferencia",
       "Borra una preferencia o una instrucción permanente (vuelve al comportamiento normal). "
       "Con 'tema' dices cuál: una categoría ('navegador') o palabras de la instrucción ('modo "
       "estudio'). Con tema 'todo' borra todas.",
       {"tema": {"type": "string", "description": "Categoría o palabras de la instrucción, o 'todo'"}},
       sensible=True)
def olvidar_preferencia(tema):
    t = skills._norm(tema)
    if t in ("todo", "todas", "todas las preferencias"):
        _q("DELETE FROM preferencias", escribir=True)
        _q("DELETE FROM reglas", escribir=True)
        return "Listo, olvidé todas tus preferencias e instrucciones permanentes."
    cat = categoria_de(tema)
    borradas = []
    if obtener(cat)[0]:
        _q("DELETE FROM preferencias WHERE categoria = ?", (cat,), escribir=True)
        borradas.append(f"tu preferencia de {cat}")
    palabras = [p for p in t.split() if len(p) > 3]
    for id_, texto in reglas():
        if palabras and all(p in skills._norm(texto) for p in palabras):
            _q("DELETE FROM reglas WHERE id = ?", (id_,), escribir=True)
            borradas.append(f"la instrucción «{texto[:60]}»")
    if not borradas:
        return Fallo(f"No encontré ninguna preferencia ni instrucción sobre '{tema}'.")
    return "Listo, olvidé " + " y ".join(borradas) + "."
