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
       "cifrados: abre ventanitas para la URL del proyecto, la clave secreta y una frase para "
       "cifrar los respaldos. Úsala con 'conecta Supabase', 'vincula la nube'.",
       requeridos=[])
def conectar_supabase():
    import panel
    avisar = hablar or print

    def con_url(url):
        def con_clave(clave):
            def con_frase(frase):
                ok, aviso = conectar(url, clave, frase, avisar)
                avisar(aviso)
            panel.pedir_texto("Frase para cifrar tus respaldos",
                              "Inventa una frase (8+ caracteres) y guárdala: la necesitarás para "
                              "recuperar tus respaldos en otra computadora.", con_frase, lambda: None,
                              oculto=True)
        panel.pedir_texto("Clave secreta de Supabase",
                          "Settings > API Keys > secret (sb_secret_...) o service_role (legacy). "
                          "Se guarda cifrada en tu PC.", con_clave, lambda: None, oculto=True)
    panel.pedir_texto("Conectar Supabase", "La URL de tu proyecto (https://xxxx.supabase.co):",
                      con_url, lambda: None)
    return ("Te abrí la ventanita: pon la URL de tu proyecto de Supabase, luego la clave secreta "
            "(Settings, API Keys) y una frase para cifrar tus respaldos.")


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
