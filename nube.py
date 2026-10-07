"""Supabase: la nube de Jarvis (opcional). Guarda una copia de la memoria por significado
(semantica.py) y los respaldos cifrados (respaldo.py).

Jarvis funciona igual sin Supabase o sin internet: todo lo de cada orden se lee del SQLite local
(menos de 1 ms); la nube se sincroniza en segundo plano. Se habla directo con la API REST de
Supabase (PostgREST + Storage) con httpx, sin librerías extra.

Conectar: "Jarvis, conecta Supabase" -> ventanitas para la URL del proyecto, la clave secreta
(Settings > API Keys > secret, o la service_role de las claves "legacy") y una frase para cifrar
los respaldos. Si falta la tabla, copia supabase/esquema.sql al portapapeles y abre el SQL Editor
del proyecto para pegarlo y darle Run; Jarvis lo detecta solo.

Seguridad: la clave secreta y la frase se guardan cifradas con DPAPI (datos/nube.json, solo tu
usuario de Windows las puede leer). En Supabase la tabla tiene RLS activo sin políticas: con la
clave pública nadie puede leer nada. Los respaldos se suben ya cifrados con tu frase.
"""
import re
import socket
import threading
import time
import webbrowser
from pathlib import Path

from secreto import guardar_json, leer_json
from skills import Fallo, skill

BASE = Path(__file__).parent
CREDENCIALES = BASE / "datos" / "nube.json"
ESQUEMA = BASE / "supabase" / "esquema.sql"
BUCKET = "respaldos"
EQUIPO = re.sub(r"[^A-Za-z0-9_-]", "-", socket.gethostname())[:40] or "pc"
_cache = {"datos": None, "t": 0.0, "lista": None}
hablar = None   # lo pone genesis.py: fn(texto) para avisar cómo terminó la conexión


class NubeError(Exception):
    pass


def credenciales():
    if _cache["datos"] is None or time.time() - _cache["t"] > 60:
        _cache.update(datos=leer_json(CREDENCIALES) or {}, t=time.time())
    return _cache["datos"]


def configurada():
    d = credenciales()
    return bool(d.get("url") and d.get("clave"))


def _encabezados(clave, extra=None):
    h = {"apikey": clave}
    if clave.startswith("eyJ"):   # las claves "legacy" (JWT) también van como Bearer
        h["Authorization"] = f"Bearer {clave}"
    h.update(extra or {})
    return h


def peticion(metodo, ruta, json=None, contenido=None, encabezados=None, timeout=20, datos=None):
    """Una petición a Supabase. Devuelve la respuesta (httpx) o lanza NubeError."""
    import httpx
    d = datos or credenciales()
    if not d.get("url") or not d.get("clave"):
        raise NubeError("Supabase no está conectado")
    url = d["url"].rstrip("/") + ruta
    try:
        r = httpx.request(metodo, url, json=json, content=contenido, timeout=timeout,
                          headers=_encabezados(d["clave"], encabezados))
    except httpx.HTTPError as e:
        raise NubeError(f"sin conexión con Supabase ({type(e).__name__})") from e
    if r.status_code >= 400:
        raise NubeError(f"Supabase respondió {r.status_code}: {r.text[:160]}")
    return r


def normalizar_url(url):
    """'abcd1234' o 'abcd1234.supabase.co' -> 'https://abcd1234.supabase.co'."""
    u = "".join((url or "").split()).rstrip("/")
    u = re.sub(r"/(rest|storage|auth)/v1.*$", "", u)
    if re.fullmatch(r"[a-z0-9]{15,30}", u):
        u += ".supabase.co"
    if u and not u.startswith("http"):
        u = "https://" + u
    return u


def referencia(url):
    m = re.match(r"https://([a-z0-9]+)\.supabase\.co", url or "")
    return m.group(1) if m else ""


def estado_tabla(datos=None):
    """'lista' | 'falta_esquema' | 'clave_mala' | 'sin_red'."""
    try:
        peticion("GET", "/rest/v1/recuerdos?select=id&limit=1", datos=datos, timeout=12)
        return "lista"
    except NubeError as e:
        texto = str(e)
        if "401" in texto or "403" in texto or "Invalid API key" in texto:
            return "clave_mala"
        if "404" in texto or "PGRST205" in texto or "does not exist" in texto:
            return "falta_esquema"
        return "sin_red"


def asegurar_bucket(datos=None):
    """El bucket privado de los respaldos (lo crea si no existe)."""
    try:
        peticion("GET", f"/storage/v1/bucket/{BUCKET}", datos=datos)
    except NubeError:
        peticion("POST", "/storage/v1/bucket", datos=datos,
                 json={"id": BUCKET, "name": BUCKET, "public": False, "file_size_limit": 50 * 1024 * 1024})


def _esperar_esquema(datos, avisar):
    """Tras abrir el SQL Editor: revisa cada 10 s (hasta 10 min) si ya corriste el esquema."""
    fin = time.time() + 600
    while time.time() < fin:
        time.sleep(10)
        if estado_tabla(datos) == "lista":
            avisar("Listo, ya está la tabla en Supabase. Tu memoria y tus respaldos ya se guardan en la nube.")
            _cache["lista"] = True
            return
    avisar("No vi la tabla en Supabase. Cuando corras el esquema, dime 'conecta Supabase' otra vez.")


def conectar(url, clave, frase, avisar=print, abrir_editor=True):
    """Valida, prueba y guarda. Devuelve (ok, qué decir)."""
    import pyperclip
    url = normalizar_url(url)
    clave = (clave or "").strip()
    frase = (frase or "").strip()
    if not referencia(url):
        return False, "Esa URL no parece de un proyecto de Supabase (es como https://xxxx.supabase.co)."
    if clave.startswith("sb_publishable") or (clave.startswith("eyJ") and '"anon"' in _jwt_rol(clave)):
        return False, ("Esa es la clave pública (publishable/anon). Necesito la secreta: Settings > "
                       "API Keys > secret (o service_role en las legacy).")
    if len(frase) < 8:
        return False, "La frase para cifrar tus respaldos debe tener al menos 8 caracteres."
    datos = {"url": url, "clave": clave, "frase": frase}
    estado = estado_tabla(datos)
    if estado == "clave_mala":
        return False, "Supabase no aceptó esa clave. Revisa que sea la secreta del mismo proyecto."
    if estado == "sin_red":
        return False, "No pude comunicarme con Supabase; revisa la URL y el internet."
    guardar_json(CREDENCIALES, datos)
    _cache.update(datos=datos, t=time.time())
    try:
        asegurar_bucket(datos)
    except NubeError as e:
        print(f"[Nube: no pude crear el bucket de respaldos: {e}]")
    if estado == "falta_esquema":
        try:
            pyperclip.copy(ESQUEMA.read_text(encoding="utf-8"))
        except Exception:
            pass
        if abrir_editor:
            webbrowser.open(f"https://supabase.com/dashboard/project/{referencia(url)}/sql/new")
            threading.Thread(target=_esperar_esquema, args=(datos, avisar), daemon=True).start()
        return True, ("Conectado, pero falta crear la tabla. Te abrí el SQL Editor de Supabase y te "
                      "copié el esquema: pégalo con Control V y dale Run. Yo me doy cuenta solo.")
    _cache["lista"] = True
    return True, "Listo, Supabase quedó conectado: tu memoria y tus respaldos ya se guardan en la nube."


# ---------- Configuración automática (Management API de Supabase) ----------
API = "https://api.supabase.com/v1"
NOMBRE_PROYECTO = "jarvis"
REGION = "us-east-1"   # la más cercana a México de las del plan gratis, ~60 ms


def _api(token, metodo, ruta, **kw):
    import httpx
    r = httpx.request(metodo, API + ruta, headers={"Authorization": f"Bearer {token}"},
                      timeout=kw.pop("timeout", 60), **kw)
    if r.status_code >= 400:
        raise NubeError(f"{r.status_code}: {r.text[:200]}")
    return r.json() if r.content else None


def _clave_secreta(llaves):
    """La clave secreta del proyecto: la nueva (sb_secret_...) o la service_role (legacy)."""
    for k in llaves:
        if k.get("type") == "secret" and k.get("api_key"):
            return k["api_key"]
    for k in llaves:
        if k.get("name") == "service_role" and k.get("api_key"):
            return k["api_key"]
    return ""


def configurar_automatico(token, frase, avisar=print, espera_seg=420):
    """Con un token de acceso de la cuenta (no se guarda): crea o reutiliza el proyecto
    "jarvis", corre el esquema, toma la clave secreta y deja todo conectado.
    Devuelve (ok, qué decir)."""
    import secrets
    token = "".join((token or "").split())
    frase = (frase or "").strip()
    if not token.startswith("sbp_"):
        return False, ("Eso no parece un token de acceso de Supabase (empieza con sbp_). Lo generas en "
                       "Account, Access Tokens.")
    if len(frase) < 8:
        return False, "La frase para cifrar tus respaldos debe tener al menos 8 caracteres."
    try:
        proyectos = _api(token, "GET", "/projects")
    except NubeError as e:
        return False, ("Supabase no aceptó el token." if "401" in str(e) else f"No pude hablar con Supabase ({e}).")
    proyecto = next((x for x in proyectos if x.get("name") == NOMBRE_PROYECTO), None)
    if proyecto is None:
        organizaciones = _api(token, "GET", "/organizations")
        if not organizaciones:
            # Cuenta nueva: todavía no tiene organización (el contenedor de los proyectos). Se
            # crea una, en el plan gratis.
            try:
                organizaciones = [_api(token, "POST", "/organizations", json={"name": "Jarvis"})]
            except NubeError as e:
                return False, ("Tu cuenta de Supabase no tiene organización y no pude crearla "
                               f"({e}). Entra una vez a supabase.com/dashboard y créala ahí.")
        org = organizaciones[0]
        clave_bd = secrets.token_urlsafe(24)   # contraseña de la base: solo para emergencias
        avisar("Estoy creando tu proyecto de Supabase; tarda uno o dos minutos.")
        try:
            proyecto = _api(token, "POST", "/projects", json={
                "name": NOMBRE_PROYECTO, "organization_id": org.get("id") or org.get("slug"),
                "db_pass": clave_bd, "region": REGION})
        except NubeError as e:
            if "limit" in str(e).lower() or "maximum" in str(e).lower():
                return False, ("Tu cuenta gratis ya tiene el máximo de proyectos activos (2). Pausa o "
                               "borra uno en supabase.com, o dime cuál usar.")
            return False, f"Supabase no me dejó crear el proyecto ({e})."
        guardar_json(BASE / "datos" / "supabase_bd.json", {"ref": proyecto.get("id") or proyecto.get("ref"),
                                                          "db_pass": clave_bd})
    ref = proyecto.get("id") or proyecto.get("ref")
    # Esperar a que esté listo
    fin = time.time() + espera_seg
    while True:
        estado = (_api(token, "GET", f"/projects/{ref}") or {}).get("status", "")
        if estado == "ACTIVE_HEALTHY":
            break
        if estado in ("INACTIVE", "PAUSED"):
            try:
                _api(token, "POST", f"/projects/{ref}/restore")
            except NubeError:
                pass
        if time.time() > fin:
            return False, "El proyecto de Supabase no terminó de arrancar; vuelve a intentarlo en unos minutos."
        time.sleep(8)
    # Esquema (idempotente) y claves
    _api(token, "POST", f"/projects/{ref}/database/query",
         json={"query": ESQUEMA.read_text(encoding="utf-8")}, timeout=120)
    llaves = _api(token, "GET", f"/projects/{ref}/api-keys?reveal=true")
    clave = _clave_secreta(llaves or [])
    if not clave:
        return False, "Creé el proyecto pero no encontré su clave secreta; dime y lo reviso."
    url = f"https://{ref}.supabase.co"
    datos = {"url": url, "clave": clave, "frase": frase}
    fin = time.time() + 90   # la API REST tarda unos segundos en ver la tabla nueva
    while estado_tabla(datos) != "lista":
        if time.time() > fin:
            return False, "La tabla no aparece todavía en la API de Supabase; vuelve a intentarlo en un rato."
        time.sleep(5)
    guardar_json(CREDENCIALES, datos)
    _cache.update(datos=datos, t=time.time(), lista=True)
    asegurar_bucket(datos)
    return True, ("Listo: tu proyecto jarvis de Supabase quedó conectado. Ya guardo una copia de tu "
                  "memoria y un respaldo cifrado al día. Guarda bien tu frase.")


def _jwt_rol(clave):
    import base64
    import json
    try:
        carga = clave.split(".")[1]
        return json.dumps(json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4))))
    except Exception:
        return ""


def lista():
    """¿Se puede usar ya la tabla de recuerdos? (cacheado; se revisa cada 5 min si no)."""
    if not configurada():
        return False
    if _cache["lista"] is None or (not _cache["lista"] and time.time() - _cache.get("t_lista", 0) > 300):
        _cache["lista"] = estado_tabla() == "lista"
        _cache["t_lista"] = time.time()
    return bool(_cache["lista"])


@skill("conectar_supabase",
       "Conecta Supabase (la nube de Jarvis) para guardar una copia de su memoria y respaldos "
       "cifrados: abre la página para generar un token de acceso (se entra con GitHub) y Jarvis "
       "crea y configura solo el proyecto. Úsala con 'conecta Supabase', 'vincula la nube'.",
       requeridos=[])
def conectar_supabase():
    import panel
    avisar = hablar or print
    webbrowser.open("https://supabase.com/dashboard/account/tokens")

    def con_token(token):
        def con_frase(frase):
            def trabajar():
                try:
                    ok, aviso = configurar_automatico(token, frase, avisar)
                except Exception as e:
                    aviso = f"Algo falló al configurar Supabase ({type(e).__name__}: {str(e)[:120]})."
                avisar(aviso)
                if aviso.startswith("Listo"):
                    import respaldo
                    threading.Thread(target=lambda: avisar(str(respaldo.respaldar())), daemon=True).start()
            threading.Thread(target=trabajar, daemon=True, name="configurar-supabase").start()
        panel.pedir_texto("Frase para cifrar tus respaldos",
                          "Inventa una frase (8+ caracteres) y GUÁRDALA: sin ella no se pueden recuperar "
                          "tus respaldos en otra computadora.", con_frase, lambda: None, oculto=True)
    panel.pedir_texto("Token de acceso de Supabase",
                      "Entra con GitHub, ve a Access Tokens > Generate new token, ponle Jarvis y pégalo "
                      "aquí (empieza con sbp_). Solo se usa para configurar; no se guarda.",
                      con_token, lambda: None, oculto=True)
    return ("Te abrí Supabase: entra con tu GitHub, genera un token de acceso llamado Jarvis y pégalo "
            "en la ventanita. Después pon una frase para cifrar tus respaldos y yo hago lo demás.")


@skill("estado_nube",
       "Dice si Supabase está conectado y cuándo fue el último respaldo ('¿está conectada la "
       "nube?', '¿cuándo fue el último respaldo?').", requeridos=[], terminal=True)
def estado_nube():
    if not configurada():
        return "Supabase no está conectado. Dime 'conecta Supabase' y te guío."
    estado = estado_tabla()
    if estado == "clave_mala":
        return Fallo("Supabase ya no acepta la clave guardada; vuelve a conectarlo.")
    if estado == "sin_red":
        return "Supabase está conectado, pero ahorita no tengo conexión con él."
    import respaldo
    ultimo = respaldo.ultimo_respaldo()
    tabla = "lista" if estado == "lista" else "sin crear (falta correr el esquema)"
    return (f"Supabase está conectado; la tabla de memoria está {tabla}. "
            + (f"El último respaldo fue {ultimo}." if ultimo else "Todavía no hay respaldos."))
