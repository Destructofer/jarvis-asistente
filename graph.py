"""Microsoft Graph: lee archivos (Word, PDF) de las clases de Teams para poder analizar tareas
de verdad, no solo lo que se alcanza a leer en pantalla.

Muchos profesores separan guías, materiales y tareas en distintos canales de la misma clase;
teams.py (navegación por accesibilidad) no puede "abrir" un Word o un PDF y leer su contenido
real -sobre todo los PDF, casi nunca traen texto accesible en el visor-, así que esto usa la
API oficial de Microsoft para bajar el archivo y extraer el texto directamente.

Requiere una app registrada en Azure (portal.azure.com → Microsoft Entra ID → Registros de
aplicaciones, plataforma "Web", redirect URI exacto http://127.0.0.1:8890/callback), con
GRAPH_CLIENT_ID y GRAPH_CLIENT_SECRET como variables de entorno — mismo patrón que Spotify.
La primera vez hay que decir "conecta mis archivos de Teams" para autorizar (una sola vez).

Ojo: esto SOLO lee y resume. No genera ni entrega tareas resueltas — ver genesis.py/config.json
para las instrucciones que le dan ese límite al modelo.
"""
import http.server
import io
import json
import os
import re
import secrets
import socketserver
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from apps import puntaje
from secreto import guardar_json, leer_json
from skills import skill

# 127.0.0.1 y no "localhost": el servidor local escucha en IPv4, y en algunos equipos
# "localhost" resuelve primero a IPv6 (::1), donde no hay nadie esperando.
REDIRECT_URI = "http://127.0.0.1:8890/callback"
SCOPES = ("offline_access User.Read Files.Read Sites.Read.All "
         "Team.ReadBasic.All Channel.ReadBasic.All EduAssignments.ReadBasic EduRoster.ReadBasic")
AUTORIZAR_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN_PATH = Path(__file__).parent / "datos" / "graph_usuario.json"
LEGIBLES = (".docx", ".pdf", ".txt", ".md")
MAX_TEXTO = 6000  # caracteres por documento que se pasan al modelo, para no disparar el contexto
# Tope para TODO lo que devuelve teams_analizar_tarea junto. Antes eran hasta 6 documentos x
# 6.000 = 36.000 caracteres (~9-10 mil tokens): el plan gratis de Groq permite ~8.000 tokens
# por minuto, así que la petición fallaba siempre. El reparto da más espacio a los primeros
# (los más relacionados con la tarea).
MAX_TOTAL = 14000
UMBRAL_CLASE = 0.35   # nombres de clase suelen ser largos y formales; se dicen más cortos
UMBRAL_ARCHIVO = 0.3  # "encontrar todo lo relacionado" pide ser generoso, no exacto


# ---------- Autorización (una sola vez) ----------
def _credenciales():
    return (os.environ.get("GRAPH_CLIENT_ID", ""), os.environ.get("GRAPH_CLIENT_SECRET", ""))


def _hay_credenciales():
    return all(_credenciales())


def _conectado():
    """Hay credenciales y ya se autorizó una vez (hay token guardado). Sin esto, las skills de
    lectura no se le ofrecen al modelo: antes las elegía, fallaba y le pedía al usuario
    "conectar archivos" en vez de simplemente navegar Teams por pantalla."""
    return _hay_credenciales() and TOKEN_PATH.exists()


def _guardar_tokens(datos):
    actual = leer_json(TOKEN_PATH) or {}
    actual["access_token"] = datos["access_token"]
    actual["expira"] = time.time() + datos.get("expires_in", 3600) - 60
    if datos.get("refresh_token"):
        actual["refresh_token"] = datos["refresh_token"]
    guardar_json(TOKEN_PATH, actual)  # cifrado con DPAPI (ver secreto.py)
    return actual


def _token():
    datos = leer_json(TOKEN_PATH)
    if not datos:
        return None
    if time.time() < datos.get("expira", 0):
        return datos["access_token"]
    cid, secreto = _credenciales()
    if not (cid and secreto and datos.get("refresh_token")):
        return None
    req = Request(TOKEN_URL, data=urlencode({
        "grant_type": "refresh_token", "refresh_token": datos["refresh_token"],
        "client_id": cid, "client_secret": secreto, "scope": SCOPES,
    }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        nuevos = json.loads(urlopen(req, timeout=8).read())
    except (URLError, OSError, KeyError):
        return None
    return _guardar_tokens(nuevos)["access_token"]


def _esperar_codigo(estado, resultado):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("state", [None])[0] == estado:
                resultado["code"] = qs.get("code", [None])[0]
            ok = bool(resultado.get("code"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Listo, ya puedes cerrar esta pestaña." if ok else "Algo falló; vuelve a pedírselo a Jarvis."
            self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{msg}</h2></body>"
                             "</html>".encode("utf-8"))

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 8890), Handler) as httpd:
        httpd.timeout = 150
        httpd.handle_request()


@skill("conectar_archivos_teams",
       "Autoriza a Jarvis a leer las clases, canales y archivos (Word, PDF) de Microsoft "
       "Teams. Trámite de una sola vez: abre el navegador para iniciar sesión con la cuenta "
       "de la escuela y aceptar. Úsala cuando el usuario pida conectar, vincular o autorizar "
       "el acceso a los archivos o materiales de Teams.",
       disponible=_hay_credenciales)
def conectar_archivos_teams():
    cid, secreto = _credenciales()
    if not (cid and secreto):
        return "Faltan las credenciales GRAPH_CLIENT_ID y GRAPH_CLIENT_SECRET."
    estado = secrets.token_urlsafe(12)
    resultado = {}
    hilo = threading.Thread(target=_esperar_codigo, args=(estado, resultado), daemon=True)
    hilo.start()
    time.sleep(0.3)
    webbrowser.open(AUTORIZAR_URL + "?" + urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": estado, "response_mode": "query",
    }))
    hilo.join(timeout=155)
    if not resultado.get("code"):
        return "No completé la conexión (se agotó el tiempo o se canceló)."
    try:
        req = Request(TOKEN_URL, data=urlencode({
            "grant_type": "authorization_code", "code": resultado["code"],
            "redirect_uri": REDIRECT_URI, "client_id": cid, "client_secret": secreto,
            "scope": SCOPES,
        }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
        _guardar_tokens(json.loads(urlopen(req, timeout=10).read()))
    except (URLError, OSError, KeyError) as e:
        return f"Autorizó el acceso pero no pude guardarlo ({type(e).__name__})."
    return "Listo, ya puedo leer tus clases y archivos de Teams."


# ---------- Llamadas a Graph ----------
def _pedir(req, timeout=15, reintentos=1, espera=0.6):
    for intento in range(reintentos + 1):
        try:
            return urlopen(req, timeout=timeout)
        except HTTPError as e:
            if e.code not in (502, 503, 504) or intento == reintentos:
                raise
            time.sleep(espera)


def _get(ruta, token):
    """GET que sigue todas las páginas (@odata.nextLink). Graph devuelve como mucho ~200
    elementos por página; sin esto, en una clase con muchos archivos se perdían en silencio."""
    url = ruta if ruta.startswith("http") else GRAPH + ruta
    datos = json.loads(_pedir(Request(url, headers={"Authorization": f"Bearer {token}"})).read())
    valores = datos.get("value")
    siguiente = datos.get("@odata.nextLink")
    while valores is not None and siguiente:
        pagina = json.loads(_pedir(Request(siguiente, headers={"Authorization": f"Bearer {token}"})).read())
        valores += pagina.get("value", [])
        siguiente = pagina.get("@odata.nextLink")
    return datos


def _clases(token):
    return [{"id": t["id"], "nombre": t["displayName"]}
           for t in _get("/me/joinedTeams", token).get("value", [])]


def _relacionado(consulta, nombre):
    """Como puntaje(), pero para encontrar TODO lo de una misma actividad entre archivos con
    títulos distintos: una tarea y su guía casi nunca se llaman parecido ("1.4 Programa" vs
    "Guía actividad 1.4"), pero suelen compartir el código de la actividad. Si lo comparten,
    se toman como relacionados aunque el resto del nombre no se parezca en nada."""
    p = puntaje(consulta, nombre)
    codigos_q = set(re.findall(r"\d+(?:\.\d+)*", consulta))
    codigos_n = set(re.findall(r"\d+(?:\.\d+)*", nombre))
    if codigos_q & codigos_n:
        p = max(p, 0.55)
    return p


def _mejor_clase(nombre_dicho, clases):
    mejor, mejor_p = None, 0.0
    for c in clases:
        p = puntaje(nombre_dicho, c["nombre"])
        if p > mejor_p:
            mejor, mejor_p = c, p
    return mejor if mejor_p >= UMBRAL_CLASE else None


def _canales(team_id, token):
    return _get(f"/teams/{team_id}/channels", token).get("value", [])


def _listar_archivos_recursivo(drive_id, item_id, token, ruta, profundidad=0, maximo=4):
    if profundidad > maximo:
        return []
    try:
        datos = _get(f"/drives/{drive_id}/items/{item_id}/children", token)
    except HTTPError:
        return []
    archivos = []
    for it in datos.get("value", []):
        nombre = it["name"]
        if "folder" in it:
            archivos += _listar_archivos_recursivo(drive_id, it["id"], token,
                                                    ruta + nombre + "/", profundidad + 1, maximo)
        elif "file" in it:
            archivos.append({"nombre": nombre, "ruta": ruta + nombre, "drive_id": drive_id,
                             "item_id": it["id"]})
    return archivos


_cache_archivos = {}  # id de clase -> (momento, lista de archivos)
CACHE_SEG = 600


def _archivos_de_un_canal(clase, canal, token):
    try:
        carpeta = _get(f"/teams/{clase['id']}/channels/{canal['id']}/filesFolder", token)
        drive_id = carpeta["parentReference"]["driveId"]
    except (HTTPError, KeyError):
        return []  # canal privado o sin carpeta de archivos
    return _listar_archivos_recursivo(drive_id, carpeta["id"], token, f"{canal['displayName']}/")


def _archivos_de_clase(clase, token):
    """Todos los archivos de todos los canales de la clase (incluye subcanales: cada uno es un
    canal más, con su propia carpeta de archivos). Los canales se recorren en paralelo (de uno
    en uno podía tardar minutos) y el resultado se guarda 10 min para no repetirlo en cada
    pregunta sobre la misma clase."""
    guardado = _cache_archivos.get(clase["id"])
    if guardado and time.time() - guardado[0] < CACHE_SEG:
        return guardado[1]
    from concurrent.futures import ThreadPoolExecutor
    canales = _canales(clase["id"], token)
    with ThreadPoolExecutor(max_workers=6) as ex:
        listas = list(ex.map(lambda c: _archivos_de_un_canal(clase, c, token), canales))
    archivos = [a for lista in listas for a in lista]
    _cache_archivos[clase["id"]] = (time.time(), archivos)
    return archivos


def _texto_docx(contenido):
    """Párrafos Y tablas, en el orden en que aparecen. Document.paragraphs se salta las
    tablas, y en las guías de tareas ahí suelen ir la rúbrica y los criterios de evaluación."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(io.BytesIO(contenido))
    lineas = []
    for bloque in doc.element.body.iterchildren():
        etiqueta = bloque.tag.rsplit("}", 1)[-1]
        if etiqueta == "p":
            t = Paragraph(bloque, doc).text.strip()
            if t:
                lineas.append(t)
        elif etiqueta == "tbl":
            for fila in Table(bloque, doc).rows:
                celdas = []
                for c in fila.cells:
                    t = " ".join(c.text.split())
                    if t and (not celdas or celdas[-1] != t):  # celdas combinadas se repiten
                        celdas.append(t)
                if celdas:
                    lineas.append(" | ".join(celdas))
    return "\n".join(lineas)


def _extraer_texto(nombre, contenido, maximo=MAX_TEXTO):
    ext = Path(nombre).suffix.lower()
    try:
        if ext == ".docx":
            texto = _texto_docx(contenido)
        elif ext == ".pdf":
            from pypdf import PdfReader
            texto = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(contenido)).pages)
        else:  # .txt, .md
            texto = contenido.decode("utf-8", errors="ignore")
    except Exception as e:
        return f"[No pude leer '{nombre}': {type(e).__name__}]"
    texto = texto.strip() or "[Sin texto extraíble (¿documento escaneado como imagen?)]"
    return texto[:maximo] + ("\n[...documento recortado, sigue más...]" if len(texto) > maximo else "")


def _leer_archivo(archivo, token, maximo=MAX_TEXTO):
    ext = Path(archivo["nombre"]).suffix.lower()
    if ext not in LEGIBLES:
        return f"[{archivo['nombre']}: formato .{ext.lstrip('.')} todavía no lo leo]"
    req = Request(f"{GRAPH}/drives/{archivo['drive_id']}/items/{archivo['item_id']}/content",
                  headers={"Authorization": f"Bearer {token}"})
    contenido = _pedir(req, timeout=25).read()
    return _extraer_texto(archivo["nombre"], contenido, maximo)


def _repartir(total, n):
    """Caracteres para cada uno de n documentos: más para los primeros (los más relacionados)."""
    pesos = [1.0 / (i + 1) ** 0.5 for i in range(n)]
    suma = sum(pesos)
    return [max(800, min(MAX_TEXTO, int(total * p / suma))) for p in pesos]


def _sin_conexion():
    return "No he conectado el acceso a los archivos de Teams; di 'conecta mis archivos de Teams' primero."


# ---------- Skills ----------
@skill("teams_listar_clases", "Lista las clases o equipos de Microsoft Teams a los que perteneces.",
       terminal=False, disponible=_conectado)
def teams_listar_clases():
    token = _token()
    if not token:
        return _sin_conexion()
    clases = _clases(token)
    if not clases:
        return "No encontré ninguna clase."
    return "Clases: " + " | ".join(c["nombre"] for c in clases)


@skill("teams_buscar_archivos_clase",
       "Busca archivos por nombre o tema en todos los canales de una clase de Teams, incluidos "
       "los subcanales donde el profesor separa guías, tareas y materiales aparte. Úsala para "
       "ver qué hay disponible antes de leerlo con teams_analizar_tarea.",
       {"clase": {"type": "string", "description": "Nombre aproximado de la clase"},
        "consulta": {"type": "string",
                     "description": "Nombre o tema a buscar; vacío lista todos los archivos"}},
       requeridos=["clase"], terminal=False, externo=True, disponible=_conectado)
def teams_buscar_archivos_clase(clase, consulta=""):
    token = _token()
    if not token:
        return _sin_conexion()
    hallada = _mejor_clase(clase, _clases(token))
    if not hallada:
        return f"No encontré una clase parecida a '{clase}'."
    archivos = _archivos_de_clase(hallada, token)
    if not archivos:
        return f"No encontré archivos en '{hallada['nombre']}'."
    if consulta.strip():
        archivos = sorted(archivos, key=lambda a: _relacionado(consulta, a["nombre"]), reverse=True)
        filtrados = [a for a in archivos if _relacionado(consulta, a["nombre"]) >= UMBRAL_ARCHIVO]
        archivos = (filtrados or archivos)[:12]
    else:
        archivos = archivos[:25]
    return f"Archivos en {hallada['nombre']}: " + " | ".join(a["ruta"] for a in archivos)


@skill("teams_analizar_tarea",
       "Reúne y lee todo el material relacionado con una tarea de una clase de Teams: busca en "
       "todos los canales (incluidos los subcanales donde el profesor separa guías y archivos "
       "aparte) los documentos cuyo nombre coincide con la tarea, y devuelve el contenido real "
       "de cada uno para poder explicar con precisión qué se pide. No resuelve ni entrega la "
       "tarea, solo reúne y lee el material para analizarlo.",
       {"clase": {"type": "string", "description": "Nombre aproximado de la clase"},
        "tarea": {"type": "string", "description": "Nombre de la tarea o actividad a analizar"}},
       requeridos=["clase", "tarea"], terminal=False, externo=True, disponible=_conectado)
def teams_analizar_tarea(clase, tarea):
    token = _token()
    if not token:
        return _sin_conexion()
    hallada = _mejor_clase(clase, _clases(token))
    if not hallada:
        return f"No encontré una clase parecida a '{clase}'."
    archivos = _archivos_de_clase(hallada, token)
    relacionados = sorted(archivos, key=lambda a: _relacionado(tarea, a["nombre"]), reverse=True)
    relacionados = [a for a in relacionados if _relacionado(tarea, a["nombre"]) >= UMBRAL_ARCHIVO][:6]
    if not relacionados:
        disponibles = " | ".join(a["ruta"] for a in archivos[:15])
        return (f"No encontré archivos en '{hallada['nombre']}' relacionados con '{tarea}'. "
               f"Esto sí hay disponible: {disponibles or '(sin archivos)'}")
    topes = _repartir(MAX_TOTAL, len(relacionados))
    partes = [f"--- {a['ruta']} ---\n{_leer_archivo(a, token, tope)}"
              for a, tope in zip(relacionados, topes)]
    return "\n\n".join(partes)


if __name__ == "__main__":
    tok = _token()
    print("token:", "conectado" if tok else "no conectado")
    if tok:
        for c in _clases(tok):
            print(" -", c["nombre"])
