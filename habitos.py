"""Hábitos: Jarvis nota tus rutinas y te ofrece lo que sueles hacer, sin que se lo pidas.

    Estás en VS Code sin música, y siempre que programas a esta hora tienes algo sonando:
        "Oye, Abraham, cuando estás en VS Code a esta hora sueles tener música. ¿Te pongo
         Chill Vibes Night, como siempre?"  ->  "sí" / "va" / "no, gracias"
    Casi todos los días entre semana, a las 9:30, le pides que abra Spotify:
        "Normalmente a esta hora abres Spotify. ¿Lo abro?"
    "¿Qué hábitos has aprendido de mí?" · "deja de sugerirme cosas" · "olvida mis hábitos"

Cómo aprende (todo local, en datos/habitos.db):
- Cada minuto, si estás usando la computadora: qué app tienes al frente y si suena música de
  fondo (los controles multimedia de Windows dicen qué suena y en qué app; música "de fondo" =
  suena en una app distinta a la que tienes al frente, o en Spotify).
- Lo que le pides a Jarvis (abrir apps, YouTube, Spotify, páginas, carpetas...) y a qué hora.
- Qué contestas a cada sugerencia: si dices que no dos veces en una situación, deja de
  ofrecerla ahí una semana. Nunca más de una sugerencia cada 30 minutos, ni mientras expones,
  ni si estás ocupado con una orden.
"""
import ctypes
import datetime
import json
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

import skills
from skills import Fallo, skill

BASE = Path(__file__).parent
DB = BASE / "datos" / "habitos.db"
CADA_SEG = 60
INACTIVO_SEG = 300          # sin tocar teclado ni ratón este tiempo: no estás trabajando
DIAS_MEMORIA = 30
ENTRE_SUGERENCIAS = 30 * 60
ESPERA_RESPUESTA = 45
# Lo que se aprende como rutina (lo demás que le pides no se cuenta)
RUTINABLES = {"abrir_app", "youtube", "spotify", "abrir_web", "abrir_carpeta", "modo_realidad",
              "abrir_presentacion", "abrir_archivo"}
NAVEGADORES = {"chrome.exe", "msedge.exe", "opera.exe", "opera_gx.exe", "brave.exe", "firefox.exe",
               "vivaldi.exe"}
AMIGABLES = {
    "code.exe": "VS Code", "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
    "notepad.exe": "el Bloc de notas", "devenv.exe": "Visual Studio", "explorer.exe": "el Explorador",
    "chrome.exe": "Chrome", "msedge.exe": "Edge", "opera.exe": "Opera", "opera_gx.exe": "Opera",
    "brave.exe": "Brave", "firefox.exe": "Firefox", "ms-teams.exe": "Teams", "teams.exe": "Teams",
    "whatsapp.exe": "WhatsApp", "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ",
    "obsidian.exe": "Obsidian", "notion.exe": "Notion", "figma.exe": "Figma", "spotify.exe": "Spotify",
    "photoshop.exe": "Photoshop", "blender.exe": "Blender", "steam.exe": "Steam",
    "windowsterminal.exe": "la terminal", "acrord32.exe": "Acrobat", "discord.exe": "Discord",
}
IGNORAR = {"pythonw.exe", "python.exe", "searchhost.exe", "shellexperiencehost.exe", "lockapp.exe",
           "startmenuexperiencehost.exe", ""}

_estado = {"hilo": None, "pendiente": None, "ultima": 0.0, "ofrecer": None, "libre": None,
           "cfg": {}}
_lock = threading.Lock()


# ---------- Almacén ----------
def _con():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS obs (ts REAL, dia INTEGER, hora REAL, app TEXT, "
                "musica INTEGER, fuente TEXT, titulo TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS acciones (ts REAL, dia INTEGER, hora REAL, clave TEXT, "
                "skill TEXT, args TEXT, app TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS ofertas (ts REAL, tipo TEXT, clave TEXT, respuesta TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS ajustes (k TEXT PRIMARY KEY, v TEXT)")
    return con


def _q(sql, params=(), escribir=False):
    with _lock:
        con = _con()
        try:
            filas = con.execute(sql, params).fetchall()
            if escribir:
                con.commit()
            return filas
        finally:
            con.close()


def _cuando(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return d.weekday(), d.hour + d.minute / 60


def _cerca(h1, h2, margen):
    d = abs(h1 - h2) % 24
    return min(d, 24 - d) <= margen


def _finde(dia):
    return dia >= 5


def _fecha(ts):
    return datetime.date.fromtimestamp(ts)


def amigable(exe):
    exe = (exe or "").lower()
    return AMIGABLES.get(exe, exe[:-4].capitalize() if exe.endswith(".exe") else exe)


# ---------- Observar ----------
def _inactivo_seg():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0


def musica_de_fondo(app_frente, sonando):
    """(musica 0/1, fuente, título). sonando: [(app, título, artista, estado)] de los controles
    multimedia. Cuenta como música lo que suena en Spotify, o en OTRA app distinta a la que usas
    (si es la del frente, lo más probable es que estés viendo un video, no trabajando con música)."""
    activos = [s for s in sonando if s[3] == 4]
    if not activos:
        return 0, "", ""
    app, titulo = activos[0][0], activos[0][1]
    frente = amigable(app_frente)
    if app == "Spotify" or skills._norm(app) != skills._norm(frente):
        return 1, app, titulo or ""
    return 0, app, titulo or ""


def observar():
    """Una observación de ahora (dict) o None si no estás usando la computadora."""
    import control
    import interaccion
    if _inactivo_seg() > INACTIVO_SEG:
        return None
    h = control.ventana_activa()
    app = control._proceso(h).lower() if h else ""
    if app in IGNORAR:
        return None
    musica, fuente, titulo = musica_de_fondo(app, interaccion.lo_que_suena())
    ahora = time.time()
    dia, hora = _cuando(ahora)
    return {"ts": ahora, "dia": dia, "hora": hora, "app": app, "musica": musica,
            "fuente": fuente, "titulo": titulo[:120]}


def guardar_obs(o):
    _q("INSERT INTO obs VALUES (?, ?, ?, ?, ?, ?, ?)",
       (o["ts"], o["dia"], o["hora"], o["app"], o["musica"], o["fuente"], o["titulo"]), escribir=True)


def _clave_accion(skill_nombre, args):
    principal = {"abrir_app": "nombre", "youtube": "consulta", "spotify": "consulta",
                 "abrir_web": "url", "abrir_carpeta": "nombre", "abrir_presentacion": "consulta",
                 "abrir_archivo": "consulta", "modo_realidad": "activar"}.get(skill_nombre, "")
    valor = skills._norm(str((args or {}).get(principal, "")))[:60]
    return f"{skill_nombre}:{valor}"


def registrar_accion(skill_nombre, args, resultado=None):
    """Lo llama genesis.py tras cada herramienta: las rutinas que se repiten se ofrecen solas."""
    if skill_nombre not in RUTINABLES or isinstance(resultado, Fallo):
        return
    if skill_nombre == "modo_realidad" and str((args or {}).get("activar", True)).lower() in ("false", "0"):
        return
    try:
        import control
        h = control.ventana_activa()
        app = control._proceso(h).lower() if h else ""
    except Exception:
        app = ""
    ahora = time.time()
    dia, hora = _cuando(ahora)
    try:
        _q("INSERT INTO acciones VALUES (?, ?, ?, ?, ?, ?, ?)",
           (ahora, dia, hora, _clave_accion(skill_nombre, args), skill_nombre,
            json.dumps(args or {}, ensure_ascii=False)[:400], app), escribir=True)
    except Exception as e:
        print(f"[Hábitos: no pude anotar la acción ({type(e).__name__})]")


# ---------- Decidir (funciones puras: se prueban con historiales inventados) ----------
def evaluar_musica(historial, recientes):
    """¿Ofrecer música? historial: observaciones de los últimos días; recientes: las últimas
    (la más nueva primero). Devuelve {app, proporcion, dias, fuente, titulo} o None.

    Se ofrece si llevas ≥5 min en la misma app sin música y, en esa app (a esta hora y en el
    mismo tipo de día, o si no hay suficientes datos, a cualquier hora), tuviste música de fondo
    al menos el 45 % del tiempo, en 3 días distintos o más."""
    if len(recientes) < 5:
        return None
    app = recientes[0]["app"]
    if any(r["app"] != app or r["musica"] for r in recientes[:5]):
        return None
    if recientes[0]["ts"] - recientes[4]["ts"] > 8 * 60:  # que sean 5 minutos seguidos
        return None
    dia, hora = recientes[0]["dia"], recientes[0]["hora"]
    en_app = [r for r in historial if r["app"] == app]
    contexto = [r for r in en_app if _cerca(r["hora"], hora, 1.5) and _finde(r["dia"]) == _finde(dia)]
    if len(contexto) < 20:
        contexto = en_app
    if len(contexto) < 20:
        return None
    con = [r for r in contexto if r["musica"]]
    proporcion = len(con) / len(contexto)
    dias = len({_fecha(r["ts"]) for r in con})
    if proporcion < 0.45 or dias < 3:
        return None
    fuente, titulo = Counter((r["fuente"], r["titulo"]) for r in con).most_common(1)[0][0]
    return {"app": app, "proporcion": proporcion, "dias": dias, "fuente": fuente, "titulo": titulo}


def evaluar_rutinas(acciones, ahora_ts, hechas_hoy, ofrecidas_hoy):
    """¿Ofrecer algo que sueles pedir a esta hora? Lo que pediste en ≥3 días distintos (de las
    últimas 3 semanas, mismo tipo de día) a una hora típica a ±30 min de ahora, y que hoy
    todavía no has hecho ni se te ofreció. Devuelve {clave, skill, args, dias, hora} o None."""
    dia, hora = _cuando(ahora_ts)
    hoy = _fecha(ahora_ts)
    grupos = {}
    for a in acciones:
        if ahora_ts - a["ts"] > 21 * 86400 or _finde(a["dia"]) != _finde(dia) or _fecha(a["ts"]) == hoy:
            continue
        grupos.setdefault(a["clave"], []).append(a)
    mejor = None
    for clave, lista in grupos.items():
        if clave in hechas_hoy or clave in ofrecidas_hoy:
            continue
        cerca = [a for a in lista if _cerca(a["hora"], hora, 0.75)]
        dias = len({_fecha(a["ts"]) for a in cerca})
        if dias < 3:
            continue
        horas = sorted(a["hora"] for a in cerca)
        tipica = horas[len(horas) // 2]
        if not _cerca(tipica, hora, 0.5):
            continue
        ultima = max(cerca, key=lambda a: a["ts"])
        cand = {"clave": clave, "skill": ultima["skill"], "args": json.loads(ultima["args"] or "{}"),
                "dias": dias, "hora": tipica}
        if mejor is None or dias > mejor["dias"]:
            mejor = cand
    return mejor


def rechazos(ofertas, tipo, clave, ahora_ts, dias=7):
    return sum(1 for o in ofertas if o["tipo"] == tipo and o["clave"] == clave
               and o["respuesta"] == "no" and ahora_ts - o["ts"] < dias * 86400)


# ---------- Cómo se dice y qué se hace ----------
def _hora_texto(h):
    hh, mm = int(h), int(round((h - int(h)) * 60 / 15) * 15) % 60
    sufijo = "de la mañana" if hh < 12 else ("de la tarde" if hh < 19 else "de la noche")
    h12 = hh % 12 or 12
    return f"{h12}{':' + str(mm).zfill(2) if mm else ''} {sufijo}"


def describir_accion(skill_nombre, args):
    a = args or {}
    if skill_nombre == "abrir_app":
        return f"abres {a.get('nombre', 'una app')}"
    if skill_nombre == "youtube":
        return f"pones {a['consulta']} en YouTube" if a.get("consulta") else "abres YouTube"
    if skill_nombre == "spotify":
        return f"pones {a.get('consulta', 'música')} en Spotify"
    if skill_nombre == "abrir_web":
        return f"entras a {str(a.get('url', 'una página')).replace('https://', '').split('/')[0]}"
    if skill_nombre == "abrir_carpeta":
        return f"abres la carpeta {a.get('nombre', '')}".strip()
    if skill_nombre == "modo_realidad":
        return "entras al modo realidad aumentada"
    if skill_nombre in ("abrir_presentacion", "abrir_archivo"):
        return f"abres {a.get('consulta', 'un archivo')}"
    return f"usas {skill_nombre}"


def _para_decir(titulo, maximo=60):
    """El título como se dice en voz alta: sin emojis ni símbolos y sin lo que va tras '|'."""
    t = re.split(r"\s[|•]\s", titulo or "")[0]
    t = re.sub(r"[^\w\s,.'¿?¡!&-]", " ", t)
    t = " ".join(t.split())
    return t[:maximo].rsplit(" ", 1)[0] if len(t) > maximo else t


def accion_musica(r, sonando):
    """(skill, args, cómo decirlo): reanudar lo que dejaste en pausa, o poner tu favorito."""
    pausadas = [s for s in sonando if s[3] == 5 and s[1]]
    if pausadas:
        app, titulo = pausadas[0][0], pausadas[0][1]
        return ("controlar_reproduccion", {"accion": "reanudar"},
                f"¿Le doy play a {_para_decir(titulo)}, que dejaste en {app}?")
    titulo = (r.get("titulo") or "").strip()
    if r.get("fuente") == "Spotify" and titulo:
        return "spotify", {"consulta": titulo}, f"¿Te pongo {_para_decir(titulo)} en Spotify, como siempre?"
    if titulo:
        return ("youtube", {"consulta": titulo[:80], "modo": "reproducir"},
                f"¿Te pongo {_para_decir(titulo)}, como siempre?")
    return ("youtube", {"consulta": "música para concentrarse", "modo": "reproducir", "tipo": "playlist"},
            "¿Te pongo algo de música?")


def _nombre_usuario():
    n = ((_estado["cfg"].get("expositor") or {}).get("presentador") or "").split()
    return n[0] if n else ""


# ---------- Sugerir ----------
def pendiente():
    """La sugerencia que espera tu sí o no (o None)."""
    p = _estado["pendiente"]
    if p is not None and time.time() > p["hasta"]:
        _anotar_oferta(p, "sin_respuesta")
        _estado["pendiente"] = p = None
    return p


def responder(acepta):
    """genesis.py: dijiste sí (True) o no (False) a la sugerencia. Devuelve (skill, args) si
    hay que hacerla, o None."""
    p = _estado["pendiente"]
    _estado["pendiente"] = None
    if p is None:
        return None
    _anotar_oferta(p, "si" if acepta else "no")
    return (p["skill"], p["args"]) if acepta else None


def _anotar_oferta(p, respuesta):
    try:
        _q("INSERT INTO ofertas VALUES (?, ?, ?, ?)", (p["ts"], p["tipo"], p["clave"], respuesta),
           escribir=True)
    except Exception:
        pass


def _ofrecer(tipo, clave, skill_nombre, args, texto):
    hablar = _estado["ofrecer"]
    if hablar is None:
        return False
    ahora = time.time()
    _estado["pendiente"] = {"ts": ahora, "tipo": tipo, "clave": clave, "skill": skill_nombre,
                            "args": args, "texto": texto, "hasta": ahora + ESPERA_RESPUESTA}
    _estado["ultima"] = ahora
    print(f"[Hábitos: ofrezco {skill_nombre} {args}]")
    if not hablar(texto):
        _estado["pendiente"] = None
        return False
    return True


def sugerencias_activas():
    filas = _q("SELECT v FROM ajustes WHERE k = 'sugerir'")
    if filas:
        return filas[0]["v"] == "1"
    return bool((_estado["cfg"].get("habitos") or {}).get("sugerir", True))


def quizas_sugerir(ahora=None):
    ahora = ahora or time.time()
    if (pendiente() is not None or ahora - _estado["ultima"] < ENTRE_SUGERENCIAS
            or not sugerencias_activas()):
        return
    libre = _estado["libre"]
    if libre is not None and not libre():
        return
    desde = ahora - DIAS_MEMORIA * 86400
    ofertas = [dict(o) for o in _q("SELECT * FROM ofertas WHERE ts > ?", (ahora - 14 * 86400,))]
    nombre = _nombre_usuario()
    oye = f"Oye, {nombre}" if nombre else "Oye"

    # 1) Música mientras trabajas
    recientes = [dict(r) for r in _q("SELECT * FROM obs WHERE ts > ? ORDER BY ts DESC LIMIT 6", (ahora - 900,))]
    if recientes:
        historial = [dict(r) for r in _q("SELECT * FROM obs WHERE ts > ? AND ts < ?", (desde, ahora - 600))]
        r = evaluar_musica(historial, recientes)
        clave = recientes[0]["app"]
        ofrecida = [o for o in ofertas if o["tipo"] == "musica" and o["clave"] == clave
                    and ahora - o["ts"] < 3 * 3600]
        if r and not ofrecida and rechazos(ofertas, "musica", clave, ahora) < 2:
            import interaccion
            skill_nombre, args, pregunta = accion_musica(r, interaccion.lo_que_suena())
            texto = f"{oye}, cuando estás en {amigable(r['app'])} sueles tener música. {pregunta}"
            if _ofrecer("musica", clave, skill_nombre, args, texto):
                return

    # 2) Lo que sueles pedir a esta hora
    hoy = datetime.datetime.combine(datetime.date.fromtimestamp(ahora), datetime.time()).timestamp()
    acciones = [dict(a) for a in _q("SELECT * FROM acciones WHERE ts > ?", (ahora - 21 * 86400,))]
    hechas_hoy = {a["clave"] for a in acciones if a["ts"] >= hoy}
    ofrecidas_hoy = {o["clave"] for o in ofertas if o["tipo"] == "rutina" and o["ts"] >= hoy}
    r = evaluar_rutinas(acciones, ahora, hechas_hoy, ofrecidas_hoy)
    if r and rechazos(ofertas, "rutina", r["clave"], ahora) < 2:
        texto = (f"{oye}, normalmente a esta hora {describir_accion(r['skill'], r['args'])}. "
                 "¿Lo hago?")
        _ofrecer("rutina", r["clave"], r["skill"], r["args"], texto)


def _bucle():
    while True:
        time.sleep(CADA_SEG)
        try:
            if not activo():
                continue
            o = observar()
            if o is not None:
                guardar_obs(o)
                quizas_sugerir()
        except Exception as e:
            print(f"[Hábitos: {type(e).__name__}: {str(e)[:100]}]")
            time.sleep(30)


def activo():
    return bool((_estado["cfg"].get("habitos") or {}).get("activo", True))


def iniciar(cfg, ofrecer=None, libre=None):
    """ofrecer(texto) -> bool: Jarvis lo dice y se queda escuchando. libre() -> bool: si se
    puede interrumpir ahora (no expones, no hay una orden en curso)."""
    _estado.update(cfg=cfg, ofrecer=ofrecer, libre=libre)
    if _estado["hilo"] is None and activo():
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="habitos")
        _estado["hilo"].start()


# ---------- Skills ----------
def resumen(ahora=None):
    ahora = ahora or time.time()
    obs = [dict(r) for r in _q("SELECT * FROM obs WHERE ts > ?", (ahora - DIAS_MEMORIA * 86400,))]
    acciones = [dict(a) for a in _q("SELECT * FROM acciones WHERE ts > ?", (ahora - 21 * 86400,))]
    if len(obs) < 60 and len(acciones) < 3:
        horas = len(obs) / 60
        return (f"Todavía estoy aprendiendo: llevo {horas:.1f} horas viéndote trabajar. En unos días "
                "te diré qué hábitos tienes y te empezaré a ofrecer lo que sueles hacer.")
    partes = []
    por_app = {}
    for o in obs:
        por_app.setdefault(o["app"], []).append(o)
    for app, lista in sorted(por_app.items(), key=lambda kv: -len(kv[1]))[:4]:
        if len(lista) < 20:
            continue
        con = [o for o in lista if o["musica"]]
        p = len(con) / len(lista)
        horas = len(lista) / 60
        if p >= 0.3:
            fuente = Counter(o["fuente"] for o in con).most_common(1)[0][0]
            partes.append(f"en {amigable(app)} ({horas:.0f} h) tienes música el {p:.0%} del tiempo, "
                          f"casi siempre en {fuente}")
        else:
            partes.append(f"en {amigable(app)} ({horas:.0f} h) casi siempre trabajas sin música")
    grupos = {}
    for a in acciones:
        grupos.setdefault(a["clave"], []).append(a)
    for clave, lista in sorted(grupos.items(), key=lambda kv: -len(kv[1]))[:3]:
        dias = len({_fecha(a["ts"]) for a in lista})
        if dias < 3:
            continue
        horas = sorted(a["hora"] for a in lista)
        partes.append(f"{describir_accion(lista[-1]['skill'], json.loads(lista[-1]['args'] or '{}'))} "
                      f"seguido ({dias} días), casi siempre como a las {_hora_texto(horas[len(horas) // 2])}")
    if not partes:
        return "Ya te he visto un buen rato, pero todavía no encuentro un hábito claro."
    return "Esto he notado: " + "; ".join(partes) + "."


@skill("mis_habitos",
       "Dice qué hábitos y rutinas ha aprendido Jarvis del usuario (cuándo pone música, qué abre a "
       "cierta hora): '¿qué hábitos has aprendido de mí?', '¿qué sabes de mis rutinas?'.",
       terminal=True)
def mis_habitos():
    return resumen()


@skill("sugerencias_habitos",
       "Activa o desactiva que Jarvis ofrezca solo lo que el usuario suele hacer (música mientras "
       "trabaja, apps a cierta hora): 'deja de sugerirme cosas', 'vuelve a sugerirme'.",
       {"activar": {"type": "boolean", "description": "true para que sugiera, false para que no"}},
       requeridos=["activar"])
def sugerencias_habitos(activar=True):
    if isinstance(activar, str):
        activar = skills._norm(activar) in ("true", "si", "1")
    _q("INSERT OR REPLACE INTO ajustes VALUES ('sugerir', ?)", ("1" if activar else "0",), escribir=True)
    if activar:
        return "Va: cuando vea que sueles hacer algo, te lo ofrezco."
    return "De acuerdo, ya no te sugiero nada por mi cuenta (pero sigo aprendiendo, por si cambias de idea)."


@skill("olvidar_habitos",
       "Borra todo lo que Jarvis aprendió de los hábitos y rutinas del usuario ('olvida mis "
       "hábitos').", riesgo="confirmar", pregunta="¿Borro todo lo que aprendí de tus hábitos?",
       sensible=True)
def olvidar_habitos():
    for tabla in ("obs", "acciones", "ofertas"):
        _q(f"DELETE FROM {tabla}", escribir=True)
    _estado["pendiente"] = None
    return "Listo, olvidé tus hábitos; empiezo a aprender de cero."


@skill("sugerencia_rechazada",
       "Uso interno: el usuario dijo que no a una sugerencia de hábito (ya quedó anotado).",
       terminal=True)
def sugerencia_rechazada():
    return "Va, sin problema."
