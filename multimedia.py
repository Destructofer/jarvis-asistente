"""YouTube y Spotify: buscar y reproducir lo que pidas por voz."""
import http.server
import json
import os
import re
import secrets
import socketserver
import threading
import time
import webbrowser
from base64 import b64encode
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen

from secreto import guardar_json, leer_json
from skills import skill

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_token = ("", 0.0)  # (token de Spotify para buscar, momento en que caduca)

# Control de reproducción real (álbumes, playlists) necesita permiso del usuario, no solo
# buscar: flujo OAuth de una vez. El redirect debe coincidir con el registrado en el
# dashboard de Spotify (el mismo que se pidió configurar: http://127.0.0.1:8888/callback).
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "user-modify-playback-state user-read-playback-state"
TOKEN_USUARIO_PATH = Path(__file__).parent / "datos" / "spotify_usuario.json"


# ---------- YouTube ----------
FILTROS = {"playlist": "EgIQAw%3D%3D", "video": "EgIQAQ%3D%3D", "cancion": "EgIQAQ%3D%3D"}


def _videos(consulta):
    """IDs de los videos reales de los resultados, en orden (sin anuncios, shorts ni listas)."""
    req = Request("https://www.youtube.com/results?search_query=" + quote_plus(consulta),
                  headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9",
                           "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    html = urlopen(req, timeout=8).read().decode("utf-8", "ignore")
    ids = []
    for vid in re.findall(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html):
        if vid not in ids:
            ids.append(vid)
    return ids


def _primer_video(consulta):
    ids = _videos(consulta)
    return ids[0] if ids else None


def _abrir(url):
    """Con tu navegador preferido (preferencias.py); si no elegiste uno, el de Windows."""
    try:
        import preferencias
        preferencias.abrir_url(url)
    except Exception:
        webbrowser.open(url)


def _abrir_y_elegir(url, tipo, posicion):
    """Abre la página y elige ahí lo que se ve (interaccion.py): "la primera playlist", "el
    segundo video que salga en el inicio"..."""
    import interaccion
    desde = time.time()
    _abrir(url)
    h = interaccion.esperar_youtube(desde)
    if h is None:
        return "Abrí YouTube, pero no encontré su ventana para elegir el video."
    return interaccion.reproducir_en(h, tipo, posicion, espera=12.0)


@skill("youtube",
       "Abre YouTube, busca o reproduce. Con consulta y modo 'reproducir' pone directo el video "
       "(o con tipo=playlist/mix/short, la playlist o mix) número 'posicion' de los resultados. "
       "SIN consulta: modo 'abrir' solo abre el inicio; modo 'reproducir' abre el inicio y pone "
       "lo primero que se vea del tipo pedido ('abre YouTube y pon la primera canción o playlist "
       "que veas'). Con 'resultados' muestra la lista de resultados. Para elegir algo de lo que "
       "YA está en pantalla ('el tercer video que aparece') usa elegir_en_pantalla.",
       {"consulta": {"type": "string", "description": "Qué buscar o reproducir (vacío = el inicio de YouTube)"},
        "modo": {"type": "string", "enum": ["reproducir", "resultados", "abrir"],
                 "description": "reproducir (por defecto con consulta), resultados, o abrir (por defecto sin consulta)"},
        "tipo": {"type": "string",
                 "description": "Qué poner: video (por defecto), cancion, playlist, mix, short, o "
                                "varios con 'o' ('cancion o playlist')"},
        "posicion": {"type": "integer", "description": "1 = el primero (por defecto), 2 = el segundo..."}},
       requeridos=[])
def youtube(consulta="", modo="", tipo="", posicion=1):
    consulta = (consulta or "").strip()
    modo = (modo or ("reproducir" if consulta else "abrir")).strip().lower()
    tipo = (tipo or "").strip().lower()
    try:
        posicion = max(-50, min(50, int(posicion or 1))) or 1
    except (TypeError, ValueError):
        posicion = 1
    eligiendo = bool(tipo) or posicion != 1
    if not consulta and eligiendo and modo != "abrir":
        # "El tercer video que aparece" con YouTube ya al frente: se elige ahí, sin otra pestaña
        import interaccion
        h = _youtube_al_frente()
        if h is not None:
            return interaccion.reproducir_en(h, tipo or "cualquiera", posicion, espera=6.0)
    if modo == "resultados" and eligiendo:
        modo = "reproducir"   # pidió "la segunda playlist de...": hay que ponerla, no solo mostrarla
    if not consulta:
        if modo == "reproducir" or tipo:
            return _abrir_y_elegir("https://www.youtube.com", tipo or "cualquiera", posicion)
        _abrir("https://www.youtube.com")
        return "Abriendo YouTube."
    resultados = "https://www.youtube.com/results?search_query=" + quote_plus(consulta)
    if modo == "resultados":
        _abrir(resultados)
        return f"Mostrando en YouTube los resultados de: {consulta}."
    if tipo and not re.fullmatch(r"(video|videos|cancion|canciones|tema|rola)?", _sin_acentos(tipo)):
        # playlist, mix, short...: se elige en la página de resultados (filtrada si se puede)
        filtro = FILTROS.get(_sin_acentos(tipo))
        return _abrir_y_elegir(resultados + (f"&sp={filtro}" if filtro else ""), tipo, posicion)
    try:
        ids = _videos(consulta)
    except (URLError, OSError):
        ids = []
    i = posicion - 1 if posicion > 0 else posicion
    if ids and -len(ids) <= i < len(ids):
        _abrir(f"https://www.youtube.com/watch?v={ids[i]}")
        return f"Reproduciendo en YouTube: {consulta}."
    if ids or posicion != 1:
        return _abrir_y_elegir(resultados, "video", posicion)
    _abrir(resultados)
    return f"Mostrando en YouTube los resultados de: {consulta}."


def _youtube_al_frente():
    import control
    h = control.ventana_activa()
    return h if h and "youtube" in control._titulo(h).lower() else None


def _sin_acentos(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


# ---------- Spotify ----------
def _pedir(req, timeout=8, reintentos=1, espera=0.6):
    """502/503/504 de Spotify suelen ser una caída de un segundo en su borde; un reintento
    corto la resuelve sola sin que el usuario tenga que volver a pedirlo. Otros errores
    (404, 401...) no se reintentan: no se van a arreglar solos."""
    for intento in range(reintentos + 1):
        try:
            return urlopen(req, timeout=timeout)
        except HTTPError as e:
            if e.code not in (502, 503, 504) or intento == reintentos:
                raise
            time.sleep(espera)


def _token_spotify():
    """Token sin login (flujo client credentials): solo sirve para buscar, no requiere Premium."""
    global _token
    if time.time() < _token[1]:
        return _token[0]
    cid, secreto = os.environ.get("SPOTIFY_CLIENT_ID", ""), os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not (cid and secreto):
        return None
    req = Request("https://accounts.spotify.com/api/token",
                  data=urlencode({"grant_type": "client_credentials"}).encode(),
                  headers={"Authorization": "Basic " + b64encode(f"{cid}:{secreto}".encode()).decode()})
    datos = json.loads(_pedir(req).read())
    _token = (datos["access_token"], time.time() + datos["expires_in"] - 60)
    return _token[0]


def _buscar_spotify(consulta, tipo):
    token = _token_spotify()
    if not token:
        return None
    url = "https://api.spotify.com/v1/search?" + urlencode({"q": consulta, "type": tipo, "limit": 1})
    datos = json.loads(_pedir(Request(url, headers={"Authorization": f"Bearer {token}"})).read())
    items = datos.get(tipo + "s", {}).get("items") or []
    if not items or not items[0]:
        return ""
    item = items[0]
    artistas = ", ".join(a["name"] for a in item.get("artists", []))
    return item["uri"], item["name"] + (f" de {artistas}" if artistas else "")


# ---------- Autorización del usuario (control real de reproducción) ----------
def _credenciales():
    return (os.environ.get("SPOTIFY_CLIENT_ID", ""), os.environ.get("SPOTIFY_CLIENT_SECRET", ""))


def _guardar_tokens(datos):
    actual = leer_json(TOKEN_USUARIO_PATH) or {}
    actual["access_token"] = datos["access_token"]
    actual["expira"] = time.time() + datos.get("expires_in", 3600) - 60
    if datos.get("refresh_token"):  # el primer intercambio trae uno; los refrescos no siempre
        actual["refresh_token"] = datos["refresh_token"]
    guardar_json(TOKEN_USUARIO_PATH, actual)  # cifrado con DPAPI (ver secreto.py)
    return actual


def _token_usuario():
    """Token con permiso para controlar la reproducción, o None si no se ha conectado la cuenta."""
    datos = leer_json(TOKEN_USUARIO_PATH)
    if not datos:
        return None
    if time.time() < datos.get("expira", 0):
        return datos["access_token"]
    cid, secreto = _credenciales()
    if not (cid and secreto and datos.get("refresh_token")):
        return None
    req = Request("https://accounts.spotify.com/api/token",
                  data=urlencode({"grant_type": "refresh_token",
                                  "refresh_token": datos["refresh_token"]}).encode(),
                  headers={"Authorization": "Basic " + b64encode(f"{cid}:{secreto}".encode()).decode()})
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
            pass  # no ensucia el registro de Genesis

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 8888), Handler) as httpd:
        httpd.timeout = 110
        httpd.handle_request()


@skill("conectar_spotify",
       "Autoriza a Jarvis a controlar la reproducción de Spotify (álbumes, playlists, pausar, "
       "saltar), no solo abrir canciones sueltas. Es un trámite de una sola vez: abre el navegador "
       "para que el usuario inicie sesión y acepte. Úsala cuando el usuario pida conectar, vincular "
       "o autorizar Spotify.",
       disponible=lambda: all(_credenciales()))
def conectar_spotify():
    cid, secreto = _credenciales()
    if not (cid and secreto):
        return "Faltan las credenciales SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET."
    estado = secrets.token_urlsafe(12)
    resultado = {}
    hilo = threading.Thread(target=_esperar_codigo, args=(estado, resultado), daemon=True)
    hilo.start()
    time.sleep(0.3)  # a que el servidor local ya esté escuchando antes de abrir el navegador
    webbrowser.open("https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": estado,
    }))
    hilo.join(timeout=115)
    if not resultado.get("code"):
        return "No completé la conexión con Spotify (se agotó el tiempo o se canceló)."
    try:
        req = Request("https://accounts.spotify.com/api/token",
                      data=urlencode({"grant_type": "authorization_code", "code": resultado["code"],
                                      "redirect_uri": REDIRECT_URI}).encode(),
                      headers={"Authorization": "Basic " +
                                                b64encode(f"{cid}:{secreto}".encode()).decode()})
        _guardar_tokens(json.loads(urlopen(req, timeout=10).read()))
    except (URLError, OSError, KeyError) as e:
        return f"Spotify autorizó el acceso pero no pude guardarlo ({type(e).__name__})."
    return "Spotify conectado. Ya puedo reproducir álbumes y playlists directamente."


# ---------- Reproducción ----------
def _dispositivo(token):
    """ID del dispositivo activo de Spotify, o el primero disponible; None si no hay ninguno."""
    req = Request("https://api.spotify.com/v1/me/player/devices",
                  headers={"Authorization": f"Bearer {token}"})
    dispositivos = json.loads(urlopen(req, timeout=8).read()).get("devices", [])
    if not dispositivos:
        return None
    return next((d for d in dispositivos if d.get("is_active")), dispositivos[0])["id"]


def _asegurar_dispositivo(token):
    dispositivo = _dispositivo(token)
    if dispositivo:
        return dispositivo
    try:
        os.startfile("spotify:")  # no había ningún Spotify abierto: se lanza y se espera
    except OSError:
        return None
    for _ in range(6):
        time.sleep(1)
        dispositivo = _dispositivo(token)
        if dispositivo:
            return dispositivo
    return None


def _reproducir_en_dispositivo(token, uri, tipo, dispositivo):
    cuerpo = json.dumps({"uris": [uri]} if tipo == "track" else {"context_uri": uri}).encode()
    req = Request(f"https://api.spotify.com/v1/me/player/play?device_id={dispositivo}",
                  data=cuerpo, method="PUT",
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    urlopen(req, timeout=8)  # sin contenido si funcionó; lanza HTTPError si falla


@skill("spotify",
       "Reproduce en Spotify una canción, artista, álbum o playlist. Ejemplos: 'reproduce Monaco de "
       "Bad Bunny en Spotify', 'pon música de Queen'. Abre la app de Spotify si no está abierta.",
       {"consulta": {"type": "string", "description": "Canción, artista, álbum o playlist con "
                                                      "el artista si se dijo, p. ej. 'Monaco Bad Bunny'"},
        "tipo": {"type": "string", "enum": ["track", "artist", "album", "playlist"],
                 "description": "track (canción, por defecto), artist, album o playlist"}},
       requeridos=["consulta"])
def spotify(consulta, tipo="track"):
    if tipo not in ("track", "artist", "album", "playlist"):
        tipo = "track"
    try:
        hallado = _buscar_spotify(consulta, tipo)
    except (URLError, OSError, KeyError, ValueError) as e:
        os.startfile(f"spotify:search:{quote(consulta, safe='')}")
        return (f"No pude consultar Spotify ({type(e).__name__}); abrí la búsqueda de "
                f"'{consulta}' para que la elijas.")
    if hallado is None:
        os.startfile(f"spotify:search:{quote(consulta, safe='')}")
        return (f"Abrí la búsqueda de '{consulta}' en Spotify, pero no puedo reproducirla sola: "
                "faltan las credenciales SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET.")
    if hallado == "":
        return f"No encontré '{consulta}' en Spotify."
    uri, nombre = hallado

    token = _token_usuario()
    if token:
        dispositivo = _asegurar_dispositivo(token)
        if dispositivo:
            try:
                _reproducir_en_dispositivo(token, uri, tipo, dispositivo)
                return f"Reproduciendo {nombre} en Spotify."
            except (HTTPError, URLError, OSError) as e:
                print(f"[No pude reproducir por la API de Spotify: {type(e).__name__}]")

    # Sin cuenta conectada o sin dispositivo disponible: se abre el enlace tal cual. Una
    # canción sola arranca sola al abrirla; álbumes y playlists solo navegan, hay que darle play.
    try:
        os.startfile(uri)
    except OSError:
        _, tipo_uri, id_ = uri.split(":")
        _abrir(f"https://open.spotify.com/{tipo_uri}/{id_}")
    if tipo == "track":
        return f"Reproduciendo {nombre} en Spotify."
    aviso = "" if token else " Di 'conecta mi Spotify' una vez para que la reproduzca sola."
    return f"Abrí {nombre} en Spotify; dale play.{aviso}"


if __name__ == "__main__":
    print("primer video:", _primer_video("bad bunny monaco"))
    print("spotify:", _buscar_spotify("monaco bad bunny", "track"))
