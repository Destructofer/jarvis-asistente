"""Tu Gmail, ordenado por lo que importa: "¿tengo correos importantes?" y en el resumen de la
mañana (ciclo.py).

Separa los correos de las últimas horas en categorías y los dice de MÁS a MENOS importante:
    1. Salud: hospitales, clínicas, laboratorios, IMSS/ISSSTE, citas y resultados.
    2. Bancos: BBVA, Banorte, Santander, Nu, Mercado Pago... (alertas y cargos primero).
    3. Inversiones: GBM, Cetes Directo, Bursanet, Actinver, Fintual, Bitso...
    4. Empleo: OCC, Computrabajo, LinkedIn, Indeed... (vacantes, entrevistas).
    5. Escuela: tu tecnológico, Teams, Classroom.
Dentro de cada una: lo urgente (seguridad, cargo, cita, vence...) y lo no leído primero.

Conexión: IMAP con una "contraseña de aplicación" de Google (no tu contraseña: se crea en
myaccount.google.com/apppasswords con la verificación en dos pasos activada, solo sirve para
leer el correo y se puede revocar). Se guarda cifrada con DPAPI (secreto.py) en
datos/gmail.json. Solo LEE: nunca envía, borra ni marca como leído (usa BODY.PEEK).
"""
import email
import email.header
import email.utils
import imaplib
import re
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import skills
from secreto import guardar_json, leer_json
from skills import Fallo, skill

BASE = Path(__file__).parent
CREDENCIALES = BASE / "datos" / "gmail.json"
SERVIDOR = "imap.gmail.com"

# (clave, nombre para decir, pistas en el remitente, pistas en el asunto). En orden de importancia.
CATEGORIAS = [
    ("salud", "salud",
     ("hospital", "clinica", "clínica", "medic", "médic", "laboratorio", "salud digna", "chopo",
      "imss", "issste", "cruz roja", "star medica", "starmedica", "angeles", "christus", "medicasur",
      "medica sur", "abchospital", "farmacia", "fahorro", "similares", "doctoralia", "consultorio",
      "dental", "gnp", "metlife", "axa", "seguros monterrey"),
     ("cita médica", "cita medica", "resultados", "estudios", "laboratorio", "receta", "consulta",
      "hospital", "urgencias", "vacuna", "póliza de gastos médicos", "gastos medicos")),
    ("banco", "bancos",
     ("bbva", "banorte", "santander", "banamex", "citibanamex", "hsbc", "scotiabank", "inbursa",
      "banregio", "bajio", "bajío", "bancoazteca", "banco azteca", "bancoppel", "nu.com.mx", "nu méxico",
      "nubank", "hey banco", "heybanco", "stori", "klar", "mercadopago", "mercado pago", "spin",
      "uala", "ualá", "albo", "vexi", "didi", "rappicard", "americanexpress", "american express",
      "amex", "paypal", "banco"),
     ("estado de cuenta", "cargo", "compra", "transferencia", "spei", "tarjeta", "token",
      "acceso a tu banca", "depósito", "deposito", "pago recibido", "domiciliación", "movimiento")),
    ("inversiones", "inversiones",
     ("gbm", "cetesdirecto", "cetes directo", "bursanet", "actinver", "kuspit", "fintual", "flink",
      "hapi", "monex", "vector", "valmex", "bitso", "binance", "skandia", "principal"),
     ("portafolio", "rendimiento", "inversión", "inversion", "fondo", "dividendo", "bolsa",
      "acciones", "cetes", "estado de cuenta de inversión")),
    ("empleo", "empleo",
     ("occ", "occmundial", "computrabajo", "linkedin", "indeed", "bumeran", "glassdoor",
      "talenteca", "jooble", "getonbrd", "workana", "hireline"),
     ("vacante", "postulación", "postulacion", "entrevista", "oferta de trabajo", "oferta laboral",
      "reclutamiento", "proceso de selección", "empleo", "candidato")),
    ("escuela", "escuela",
     ("tesci", "tecnologico", "tecnológico", "edu.mx", "classroom", "teams", "universidad",
      "moodle", "ceneval"),
     ("tarea", "calificación", "calificaciones", "examen", "clase", "inscripción", "reinscripción",
      "beca", "entrega")),
]
URGENTE = re.compile(r"\b(urgente|alerta|seguridad|sospech|no reconocid|fraude|bloque|vence|vencimiento|"
                     r"ultimo dia|último día|hoy|cita|resultados|entrevista|aprobad|rechazad|cargo)\w*",
                     re.I)


def _decodificar(valor):
    partes = []
    for texto, cod in email.header.decode_header(valor or ""):
        if isinstance(texto, bytes):
            texto = texto.decode(cod or "utf-8", "replace")
        partes.append(texto)
    return " ".join(" ".join(partes).split())


def clasificar(remitente, asunto):
    """(clave, posición) de la categoría, o (None, 99) si no es de las importantes."""
    r, a = skills._norm(remitente), skills._norm(asunto)
    for i, (clave, _n, en_remitente, en_asunto) in enumerate(CATEGORIAS):
        if any(skills._norm(p) in r for p in en_remitente):
            return clave, i
    for i, (clave, _n, _r, en_asunto) in enumerate(CATEGORIAS):
        if clave != "escuela" and any(skills._norm(p) in a for p in en_asunto):
            return clave, i
    return None, 99


def ordenar(correos):
    """Los importantes, de más a menos: categoría, urgente, no leído y más reciente."""
    importantes = []
    for c in correos:
        clave, pos = clasificar(c["de"], c["asunto"])
        if clave is None:
            continue
        urgente = bool(URGENTE.search(c["asunto"]))
        importantes.append(dict(c, categoria=clave, _orden=(pos, not urgente, c["leido"], -c["ts"])))
    return sorted(importantes, key=lambda c: c["_orden"])


def _nombre_remitente(de):
    nombre, direccion = email.utils.parseaddr(de)
    nombre = _decodificar(nombre).strip('"') if nombre else ""
    if not nombre:
        nombre = direccion.split("@")[-1].split(".")[0].capitalize()
    return nombre[:40]


def leer_correos(horas=24, maximo=60):
    """[{de, asunto, ts, leido}] de la bandeja de entrada de las últimas `horas`."""
    datos = leer_json(CREDENCIALES)
    if not datos:
        raise PermissionError("Gmail no está conectado")
    m = imaplib.IMAP4_SSL(SERVIDOR, timeout=15)
    try:
        m.login(datos["correo"], datos["clave"])
        m.select("INBOX", readonly=True)   # solo lectura: no cambia nada
        desde = (datetime.now() - timedelta(hours=horas)).strftime("%d-%b-%Y")
        _ok, ids = m.search(None, f'(SINCE "{desde}")')
        ids = ids[0].split()[-maximo:]
        correos = []
        limite = time.time() - horas * 3600
        for i in reversed(ids):
            _ok, d = m.fetch(i, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            cabecera = email.message_from_bytes(d[0][1])
            banderas = d[0][0].decode("utf-8", "replace")
            try:
                ts = email.utils.parsedate_to_datetime(cabecera.get("Date")).timestamp()
            except Exception:
                ts = time.time()
            if ts < limite:
                continue
            correos.append({"de": _decodificar(cabecera.get("From")),
                            "asunto": _decodificar(cabecera.get("Subject")) or "(sin asunto)",
                            "ts": ts, "leido": "\\Seen" in banderas})
        return correos
    finally:
        try:
            m.logout()
        except Exception:
            pass


def resumen_correos(horas=24, maximo_dichos=6):
    """Texto para decir: los importantes de más a menos, o '' si no hay / no está conectado."""
    try:
        importantes = ordenar(leer_correos(horas))
    except PermissionError:
        return ""
    except Exception as e:
        print(f"[Correo: {type(e).__name__}: {str(e)[:80]}]")
        return ""
    if not importantes:
        return "En tu correo no llegó nada importante."
    nombres = {c[0]: c[1] for c in CATEGORIAS}
    frases = []
    for c in importantes[:maximo_dichos]:
        frases.append(f"de {nombres[c['categoria']]}, {_nombre_remitente(c['de'])}: "
                      f"{_limpiar_asunto(c['asunto'])}")
    resto = len(importantes) - len(frases)
    total = len(importantes)
    return (f"En tu correo hay {total} importante{'s' if total != 1 else ''}: " + "; ".join(frases)
            + (f"; y {resto} más." if resto > 0 else "."))


def _limpiar_asunto(asunto):
    a = re.sub(r"^(re|fw|fwd|rv)\s*:\s*", "", asunto, flags=re.I)
    a = re.sub(r"[^\w\s,.$%¿?¡!:/()-]", " ", a)
    a = " ".join(a.split())
    return a[:90]


# ---------- Conectar ----------
def probar(correo, clave):
    m = imaplib.IMAP4_SSL(SERVIDOR, timeout=15)
    try:
        m.login(correo, clave)
        return True
    finally:
        try:
            m.logout()
        except Exception:
            pass


def guardar(correo, clave):
    guardar_json(CREDENCIALES, {"correo": correo.strip(), "clave": clave.replace(" ", "").strip()})


hablar = None   # lo pone genesis.py: fn(texto) para avisar cómo terminó la conexión


@skill("conectar_gmail",
       "Conecta el Gmail del usuario (solo lectura) para que Jarvis le diga sus correos "
       "importantes: abre la página de Google para crear una contraseña de aplicación y una "
       "ventanita donde la pega. Úsala con 'conecta mi Gmail', 'vincula mi correo'.",
       requeridos=[])
def conectar_gmail():
    import panel
    webbrowser.open("https://myaccount.google.com/apppasswords")

    def con_correo(correo):
        def con_clave(clave):
            try:
                probar(correo, clave.replace(" ", ""))
            except Exception as e:
                aviso = ("Google no aceptó esa contraseña de aplicación. Revisa que sea la de 16 "
                         "letras y vuelve a decirme 'conecta mi Gmail'.")
                print(f"[Gmail: {type(e).__name__}: {str(e)[:80]}]")
            else:
                guardar(correo, clave)
                aviso = "Listo, tu Gmail quedó conectado. Ya puedo decirte tus correos importantes."
            if hablar:
                hablar(aviso)
        panel.pedir_texto("Contraseña de aplicación de Google",
                          "Pégala aquí (16 letras). Se guarda cifrada y solo sirve para LEER tu correo.",
                          con_clave, lambda: None, oculto=True)
    panel.pedir_texto("Conectar Gmail", "Tu correo de Gmail:", con_correo, lambda: None)
    return ("Te abrí la página de Google para crear una contraseña de aplicación (necesitas la "
            "verificación en dos pasos). Crea una para Jarvis y pégala en la ventanita.")


@skill("correos_importantes",
       "Dice los correos importantes de Gmail de las últimas horas, separados y ordenados de más "
       "a menos importante: salud (hospitales, citas, resultados), bancos, inversiones (GBM...), "
       "empleo (OCC, Computrabajo...) y escuela: '¿tengo correos importantes?', '¿qué me llegó al "
       "correo?', '¿me escribió el banco?'.",
       {"horas": {"type": "integer", "description": "De cuántas horas para atrás (24 por defecto)"}},
       requeridos=[], terminal=False, externo=True)
def correos_importantes(horas=24):
    if not leer_json(CREDENCIALES):
        return Fallo("Tu Gmail no está conectado. Dime 'conecta mi Gmail' y te guío.")
    texto = resumen_correos(int(horas or 24), maximo_dichos=10)
    return texto or Fallo("No pude leer tu correo ahorita (revisa el internet).")
