"""El día y la noche de Jarvis.

MAÑANA — la primera vez del día que te ve (o que le hablas), en lugar del saludo de siempre:
    "Buenos días, Abraham. Son las 9:20. En Cuautitlán hace 16 grados... lleva paraguas.
     De ayer te quedó pendiente: terminar el reporte de cálculo. Hoy tienes un recordatorio a
     las 4: junta de equipo. En WhatsApp tienes 2 chats sin responder: Mamá (2 sin leer)...
     En tu correo hay 3 importantes: de salud, Hospital Ángeles: tu cita...; de bancos..."
  El clima, WhatsApp y el correo se consultan a la vez (en paralelo) para no hacerte esperar.
  También a demanda: "dame mi resumen del día", "¿qué tengo pendiente?".

NOCHE — después de ciclo.hora_noche (23:00 por defecto), si sigues en la computadora:
    "Son las 11:05 de la noche. Te noto cansado: los ojos se te cierran seguido. ¿Y si lo
     dejamos aquí y seguimos mañana? Dime qué te quedó pendiente y mañana te lo recuerdo."
  El cansancio lo mide cansancio.py con la cámara (ojos cerrados y bostezos), sin mandar fotos.
  Lo que le digas que quedó pendiente se anota y sale en el resumen de la mañana siguiente.
  Máximo 3 avisos por noche, separados 45 min (o antes si de pronto te ve muy cansado).
"""
import datetime
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import memoria
import skills
from skills import Fallo, skill

_estado = {"hilo": None, "ofrecer": None, "libre": None, "cfg": {}, "avisos_noche": 0,
           "ultimo_aviso_noche": 0.0, "noche": None, "cansado_avisado": False, "dando_resumen": False}
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


# ---------- Almacén (datos/genesis.db, la misma base de la memoria) ----------
def _q(sql, params=(), escribir=False):
    con = memoria._conectar()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS pendientes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "texto TEXT NOT NULL, creado TEXT NOT NULL, para TEXT NOT NULL, hecho INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS ciclo (k TEXT PRIMARY KEY, v TEXT)")
        filas = con.execute(sql, params).fetchall()
        if escribir:
            con.commit()
        return filas
    finally:
        con.close()


def _ajuste(k, v=None):
    if v is None:
        f = _q("SELECT v FROM ciclo WHERE k = ?", (k,))
        return f[0]["v"] if f else None
    _q("INSERT OR REPLACE INTO ciclo VALUES (?, ?)", (k, str(v)), escribir=True)


def _conf():
    return (_estado["cfg"].get("ciclo") or {})


def _hora(clave, defecto):
    h, _, m = str(_conf().get(clave, defecto)).partition(":")
    return int(h) + int(m or 0) / 60


def _nombre():
    n = ((_estado["cfg"].get("expositor") or {}).get("presentador") or "").split()
    return n[0] if n else ""


def dia_logico(ahora=None):
    """La fecha "del día": antes de las 5 a. m. todavía cuenta como la noche anterior."""
    ahora = ahora or datetime.datetime.now()
    return (ahora - datetime.timedelta(hours=5)).date()


def hora_dicha(ahora=None):
    ahora = ahora or datetime.datetime.now()
    h, m = ahora.hour, ahora.minute
    parte = ("de la madrugada" if h < 6 else "de la mañana" if h < 12 else
             "de la tarde" if h < 19 else "de la noche")
    return f"{h % 12 or 12}{':' + str(m).zfill(2) if m else ''} {parte}"


# ---------- Pendientes ----------
def _fecha(para, hoy=None):
    hoy = hoy or datetime.date.today()
    p = skills._norm(para or "manana")
    if p in ("hoy", "ahorita", "today"):
        return hoy
    if p in ("manana", "mañana", "tomorrow"):
        return hoy + datetime.timedelta(days=1)
    if p in ("pasado manana", "pasado mañana"):
        return hoy + datetime.timedelta(days=2)
    for i, d in enumerate(DIAS):
        if skills._norm(d) in p:
            delta = (i - hoy.weekday()) % 7 or 7
            return hoy + datetime.timedelta(days=delta)
    try:
        return datetime.date.fromisoformat(para)
    except (TypeError, ValueError):
        return hoy + datetime.timedelta(days=1)


def pendientes_para(dia=None):
    """Los pendientes sin hacer cuya fecha ya llegó (incluye los de días anteriores)."""
    dia = dia or datetime.date.today()
    return [dict(f) for f in _q("SELECT * FROM pendientes WHERE hecho = 0 AND para <= ? ORDER BY para, id",
                                (dia.isoformat(),))]


@skill("anotar_pendiente",
       "Anota algo que le quedó pendiente al usuario para recordárselo después (por defecto mañana, "
       "en el resumen de la mañana): 'me quedó pendiente terminar el reporte', 'mañana tengo que "
       "llamar al doctor', 'anota que debo pagar la luz el viernes'.",
       {"texto": {"type": "string", "description": "El pendiente, claro y corto"},
        "para": {"type": "string", "description": "hoy, mañana (por defecto), pasado mañana, un día de la semana o AAAA-MM-DD"}},
       requeridos=["texto"])
def anotar_pendiente(texto, para="mañana"):
    texto = " ".join(str(texto).split()).strip(" .")
    if not texto:
        return Fallo("¿Qué te quedó pendiente?")
    fecha = _fecha(para)
    _q("INSERT INTO pendientes (texto, creado, para) VALUES (?, ?, ?)",
       (texto, memoria._ahora(), fecha.isoformat()), escribir=True)
    cuando = ("hoy" if fecha == datetime.date.today() else "mañana"
              if fecha == datetime.date.today() + datetime.timedelta(days=1)
              else f"el {DIAS[fecha.weekday()]} {fecha.day}")
    return f"Anotado: {texto}. Te lo recuerdo {cuando}."


@skill("ver_pendientes",
       "Dice los pendientes del usuario (lo que quedó de días anteriores y lo de hoy): '¿qué tengo "
       "pendiente?', '¿qué me quedó de ayer?'.", requeridos=[], terminal=True)
def ver_pendientes():
    lista = pendientes_para()
    if not lista:
        return "No tienes pendientes anotados."
    return "Tienes pendiente: " + "; ".join(p["texto"] for p in lista) + "."


@skill("completar_pendiente",
       "Marca como hecho un pendiente ('ya terminé el reporte', 'tacha lo de la luz').",
       {"texto": {"type": "string", "description": "Palabras del pendiente que ya hizo"}},
       requeridos=["texto"])
def completar_pendiente(texto):
    from difflib import SequenceMatcher
    lista = [dict(f) for f in _q("SELECT * FROM pendientes WHERE hecho = 0")]
    if not lista:
        return "No tenías pendientes anotados."
    t = skills._norm(texto)
    mejor = max(lista, key=lambda p: (t in skills._norm(p["texto"]),
                                      SequenceMatcher(None, t, skills._norm(p["texto"])).ratio()))
    if t not in skills._norm(mejor["texto"]) and SequenceMatcher(None, t, skills._norm(mejor["texto"])).ratio() < 0.45:
        return Fallo(f"No encontré un pendiente parecido a '{texto}'.")
    _q("UPDATE pendientes SET hecho = 1 WHERE id = ?", (mejor["id"],), escribir=True)
    return f"Tachado: {mejor['texto']}."


# ---------- El resumen de la mañana ----------
def _recordatorios_de_hoy():
    try:
        import recordatorios
        hoy = datetime.date.today().isoformat()
        filas = recordatorios._q("SELECT cuando, texto FROM avisos WHERE cuando LIKE ? ORDER BY cuando",
                                 (hoy + "%",))
        return [(datetime.datetime.fromisoformat(f["cuando"]), f["texto"]) for f in filas]
    except Exception:
        return []


def armar_resumen(ahora=None, clima_fn=None, whatsapp_fn=None, correo_fn=None):
    """El resumen completo (texto para decir). Las consultas de red van en paralelo."""
    ahora = ahora or datetime.datetime.now()
    import clima
    import correo
    import whatsapp
    fuentes = {"clima": clima_fn or clima.resumen_clima,
               "whatsapp": whatsapp_fn or whatsapp.resumen_whatsapp,
               "correo": correo_fn or correo.resumen_correos}
    resultados = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futuros = {k: ex.submit(f) for k, f in fuentes.items()}
        for k, f in futuros.items():
            try:
                resultados[k] = f.result(timeout=25) or ""
            except Exception as e:
                print(f"[Resumen: {k} falló ({type(e).__name__})]")
                resultados[k] = ""
    saludo = ("Buenos días" if ahora.hour < 12 else "Buenas tardes" if ahora.hour < 19 else "Buenas noches")
    nombre = _nombre()
    partes = [f"{saludo}{', ' + nombre if nombre else ''}. Son las {hora_dicha(ahora)}."]
    if resultados["clima"]:
        partes.append(resultados["clima"])
    hoy = ahora.date()
    pend = pendientes_para(hoy)
    viejos = [p["texto"] for p in pend if p["para"] < hoy.isoformat()]
    de_hoy = [p["texto"] for p in pend if p["para"] == hoy.isoformat()]
    if viejos:
        partes.append("Te quedó pendiente de antes: " + "; ".join(viejos) + ".")
    if de_hoy:
        partes.append("Para hoy tienes: " + "; ".join(de_hoy) + ".")
    recs = _recordatorios_de_hoy()
    if recs:
        partes.append("Recordatorios de hoy: " + "; ".join(
            f"a las {hora_dicha(c)}: {t}" for c, t in recs[:4]) + ".")
    if resultados["whatsapp"]:
        partes.append(resultados["whatsapp"])
    if resultados["correo"]:
        partes.append(resultados["correo"])
    if len(partes) <= 2 and not resultados["clima"]:
        partes.append("No tienes pendientes ni nada urgente.")
    partes.append("¿Por dónde empezamos?")
    return " ".join(partes)


@skill("resumen_del_dia",
       "Da el resumen del día: saludo, hora, clima y pronóstico de donde está, pendientes de ayer, "
       "recordatorios de hoy, WhatsApp sin responder y correos importantes: 'dame mi resumen', "
       "'¿cómo viene el día?', 'ponme al día'.", requeridos=[], terminal=False, externo=True)
def resumen_del_dia():
    _ajuste("resumen", dia_logico().isoformat())
    return armar_resumen()


def resumen_pendiente_hoy(ahora=None):
    """¿Falta el resumen de hoy? (presencia.py no saluda aparte: el resumen ya saluda)."""
    ahora = ahora or datetime.datetime.now()
    if not _conf().get("resumen_manana", True) or es_de_noche(ahora):
        return False
    return ahora.hour + ahora.minute / 60 >= _hora("hora_manana", "5:00") and \
        _ajuste("resumen") != dia_logico(ahora).isoformat()


# ---------- La noche ----------
def es_de_noche(ahora=None):
    ahora = ahora or datetime.datetime.now()
    h = ahora.hour + ahora.minute / 60
    return h >= _hora("hora_noche", "23:00") or h < _hora("hora_manana", "5:00")


def mensaje_noche(ahora, cansado, motivo, primero):
    nombre = _nombre()
    texto = f"Son las {hora_dicha(ahora)}."
    if cansado:
        texto += (f" {nombre + ', t' if nombre else 'T'}e noto cansado: {motivo}. ¿Y si lo dejamos aquí "
                  "y seguimos mañana con energía? Dime qué te quedó pendiente y mañana a primera "
                  "hora te lo recuerdo.")
    elif primero:
        texto += (" Ya es tarde. Si te quedó algo pendiente, dímelo y mañana te lo recuerdo; "
                  "descansar también es avanzar.")
    else:
        texto += " Sigues despierto. Cuando quieras cerramos el día: lo que falte, mañana te lo recuerdo."
    return texto


def _inactivo_seg():
    try:
        import habitos
        return habitos._inactivo_seg()
    except Exception:
        return 0.0


def revisar(ahora=None):
    """Un paso del ciclo (cada 30 s): el resumen de la mañana y los avisos de la noche."""
    import cansancio
    ahora = ahora or datetime.datetime.now()
    libre = _estado["libre"]
    ofrecer = _estado["ofrecer"]
    noche = es_de_noche(ahora)
    cansancio.encender(noche and _conf().get("detectar_cansancio", True))
    if ofrecer is None or (libre is not None and not libre()):
        return
    activo = _inactivo_seg() < 120  # estás usando la computadora
    if not activo:
        return
    # Mañana
    if resumen_pendiente_hoy(ahora) and not _estado["dando_resumen"] and not noche:
        _estado["dando_resumen"] = True
        try:
            _ajuste("resumen", dia_logico(ahora).isoformat())
            texto = armar_resumen(ahora)
            print("[Ciclo: resumen del día]")
            ofrecer(texto)
        finally:
            _estado["dando_resumen"] = False
        return
    # Noche
    if not noche or not _conf().get("avisos_noche", True):
        return
    clave = dia_logico(ahora).isoformat()
    if _estado["noche"] != clave:
        _estado.update(noche=clave, avisos_noche=0, ultimo_aviso_noche=0.0, cansado_avisado=False)
    est = cansancio.estado()
    t = time.time()
    nuevo_cansancio = est["cansado"] and not _estado["cansado_avisado"]
    toca = (_estado["avisos_noche"] < 3 and t - _estado["ultimo_aviso_noche"] >= 45 * 60)
    if toca or (nuevo_cansancio and t - _estado["ultimo_aviso_noche"] >= 10 * 60):
        texto = mensaje_noche(ahora, est["cansado"], est["motivo"], _estado["avisos_noche"] == 0)
        _estado["avisos_noche"] += 1
        _estado["ultimo_aviso_noche"] = t
        _estado["cansado_avisado"] = _estado["cansado_avisado"] or est["cansado"]
        print(f"[Ciclo: aviso de noche (cansancio {est['perclos']:.0%}, {est['bostezos']} bostezos)]")
        ofrecer(texto)


def _bucle():
    while True:
        time.sleep(30)
        try:
            revisar()
        except Exception as e:
            print(f"[Ciclo: {type(e).__name__}: {str(e)[:100]}]")


def iniciar(cfg, ofrecer=None, libre=None):
    """ofrecer(texto): Jarvis lo dice y se queda escuchando. libre(): se puede hablar ahora."""
    _estado.update(cfg=cfg, ofrecer=ofrecer, libre=libre)
    if _estado["hilo"] is None and _conf().get("activo", True):
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="ciclo")
        _estado["hilo"].start()
