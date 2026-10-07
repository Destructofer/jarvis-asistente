"""Respaldos automáticos y cifrados de Jarvis en Supabase Storage.

Una vez al día (o con "respalda ahora") arma un zip con lo que no se puede volver a descargar:
tu memoria, preferencias, pendientes y hábitos (las bases de datos, copiadas de forma segura
aunque Jarvis las esté usando), config.json y tus avatares (Documentos\\Jarvis\\Avatares). Lo
CIFRA con tu frase (AES de cryptography/Fernet, clave derivada con PBKDF2) y lo sube al bucket
privado "respaldos" de tu proyecto. Conserva los últimos 7.

Lo que no va: los modelos y la caché de voz (se vuelven a descargar), el registro, y las claves
cifradas con DPAPI (gmail.json, nube.json...: solo sirven en esta PC; en otra se conectan de nuevo).

Recuperar (con Jarvis cerrado):  .venv\\Scripts\\python respaldo.py restaurar
    Descarga el último respaldo de esta PC (o el que elijas), lo descifra con tu frase y lo pone
    en su lugar; antes guarda una copia de lo actual en datos/antes_de_restaurar_<fecha>.
"""
import base64
import datetime
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import nube
from skills import Fallo, skill

BASE = Path(__file__).parent
DATOS = BASE / "datos"
AVATARES = Path(os.path.expanduser("~")) / "Documents" / "Jarvis" / "Avatares"
MAGIA = b"JARVIS-RESPALDO-1\n"
CONSERVAR = 7
CADA_HORAS = 24
LIMITE_MB = 48            # el plan gratis de Supabase acepta archivos de hasta 50 MB
BASES = ("genesis.db", "habitos.db")
ARCHIVOS = ("apps.json", "mantenimiento.json", "interaccion.json")
NO_RESPALDAR = {"gmail.json", "nube.json", "spotify_usuario.json", "graph_token.json"}

_estado = {"hilo": None, "corriendo": False}


# ---------- Cifrado ----------
def _clave(frase, sal):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal, iterations=390_000)
    return base64.urlsafe_b64encode(kdf.derive(frase.encode("utf-8")))


def cifrar(datos, frase):
    from cryptography.fernet import Fernet
    sal = os.urandom(16)
    return MAGIA + sal + Fernet(_clave(frase, sal)).encrypt(datos)


def descifrar(blob, frase):
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIA):
        raise ValueError("no es un respaldo de Jarvis")
    sal = blob[len(MAGIA):len(MAGIA) + 16]
    try:
        return Fernet(_clave(frase, sal)).decrypt(blob[len(MAGIA) + 16:])
    except InvalidToken as e:
        raise ValueError("la frase no es la correcta") from e


# ---------- Armar el zip ----------
def _copia_segura(origen, destino):
    """Copia de una base SQLite aunque esté en uso (la API de respaldo de SQLite)."""
    fuente = sqlite3.connect(origen)
    copia = sqlite3.connect(destino)
    try:
        fuente.backup(copia)
    finally:
        copia.close()
        fuente.close()


def armar_zip(datos_dir=DATOS, avatares=AVATARES, config=BASE / "config.json"):
    """Bytes del zip con todo lo que se respalda."""
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre in BASES:
            origen = Path(datos_dir) / nombre
            if origen.exists():
                copia = Path(tmp) / nombre
                _copia_segura(origen, copia)
                z.write(copia, f"datos/{nombre}")
        for nombre in ARCHIVOS:
            origen = Path(datos_dir) / nombre
            if origen.exists() and nombre not in NO_RESPALDAR:
                z.write(origen, f"datos/{nombre}")
        if Path(config).exists():
            z.write(config, "config.json")
        if Path(avatares).is_dir():
            for d, _dirs, archivos in os.walk(avatares):
                for a in archivos:
                    ruta = Path(d) / a
                    z.write(ruta, "Avatares/" + str(ruta.relative_to(avatares)).replace("\\", "/"))
        z.writestr("respaldo.json", json.dumps({"equipo": nube.EQUIPO,
                                                "fecha": datetime.datetime.now().isoformat(timespec="seconds"),
                                                "version": 1}))
    return buf.getvalue()


# ---------- Subir, listar, borrar ----------
def _prefijo():
    return f"{nube.EQUIPO}/"


def listar(equipo=None):
    """[{name, created_at, metadata}] de los respaldos (los más nuevos primero)."""
    r = nube.peticion("POST", f"/storage/v1/object/list/{nube.BUCKET}", json={
        "prefix": f"{equipo}/" if equipo else _prefijo(), "limit": 100,
        "sortBy": {"column": "name", "order": "desc"}})
    return [o for o in r.json() if o.get("name", "").endswith(".jarvis")]


def respaldar():
    """Arma, cifra y sube un respaldo; borra los viejos. Devuelve qué decir."""
    if not nube.configurada():
        return Fallo("Supabase no está conectado. Dime 'conecta Supabase' y te guío.")
    frase = nube.credenciales().get("frase")
    if not frase:
        return Fallo("Falta la frase para cifrar los respaldos; vuelve a conectar Supabase.")
    if _estado["corriendo"]:
        return "Ya estoy haciendo un respaldo."
    _estado["corriendo"] = True
    try:
        blob = cifrar(armar_zip(), frase)
        mb = len(blob) / 2**20
        if mb > LIMITE_MB:
            return Fallo(f"El respaldo pesa {mb:.0f} MB y Supabase gratis acepta hasta 50. "
                         "Revisa tus avatares (son lo que más pesa).")
        nombre = datetime.datetime.now().strftime("%Y-%m-%d_%H%M") + ".jarvis"
        nube.asegurar_bucket()
        nube.peticion("POST", f"/storage/v1/object/{nube.BUCKET}/{_prefijo()}{nombre}", contenido=blob,
                      encabezados={"Content-Type": "application/octet-stream", "x-upsert": "true"},
                      timeout=120)
        viejos = listar()[CONSERVAR:]
        if viejos:
            nube.peticion("DELETE", f"/storage/v1/object/{nube.BUCKET}",
                          json={"prefixes": [_prefijo() + o["name"] for o in viejos]})
        _guardar_ultimo(nombre)
        return f"Respaldo listo: {mb:.1f} MB, cifrado y guardado en Supabase."
    except nube.NubeError as e:
        return Fallo(f"No pude subir el respaldo ({e}).")
    finally:
        _estado["corriendo"] = False


def _guardar_ultimo(nombre):
    (DATOS / "ultimo_respaldo.txt").write_text(
        f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{nombre}", encoding="utf-8")


def ultimo_respaldo():
    """'hoy a las 3:10' / 'el 5 de octubre', o '' si nunca."""
    try:
        fecha = datetime.datetime.fromisoformat((DATOS / "ultimo_respaldo.txt").read_text(encoding="utf-8").split("\t")[0])
    except (OSError, ValueError):
        return ""
    if fecha.date() == datetime.date.today():
        return f"hoy a las {fecha:%H:%M}"
    return f"el {fecha.day}/{fecha.month} a las {fecha:%H:%M}"


def toca_respaldo(ahora=None):
    ahora = ahora or time.time()
    try:
        t = datetime.datetime.fromisoformat(
            (DATOS / "ultimo_respaldo.txt").read_text(encoding="utf-8").split("\t")[0]).timestamp()
    except (OSError, ValueError):
        return True
    return ahora - t >= CADA_HORAS * 3600


def _bucle():
    time.sleep(600)   # no compite con el arranque de Jarvis
    while True:
        try:
            if nube.configurada() and toca_respaldo():
                resultado = respaldar()
                print(f"[Respaldo: {resultado}]")
        except Exception as e:
            print(f"[Respaldo: {type(e).__name__}: {str(e)[:100]}]")
        time.sleep(1800)


def iniciar():
    if _estado["hilo"] is None:
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="respaldo")
        _estado["hilo"].start()


# ---------- Recuperar ----------
def restaurar(frase=None, nombre=None, equipo=None, destino=BASE):
    """Descarga, descifra y pone en su lugar el respaldo (el último si no se dice). Antes guarda
    lo actual en datos/antes_de_restaurar_<fecha>. Devuelve la ruta de esa copia."""
    frase = frase or nube.credenciales().get("frase")
    equipo = equipo or nube.EQUIPO
    if not nombre:
        lista = listar(equipo)
        if not lista:
            raise FileNotFoundError("no hay respaldos en Supabase")
        nombre = lista[0]["name"]
    blob = nube.peticion("GET", f"/storage/v1/object/{nube.BUCKET}/{equipo}/{nombre}", timeout=120).content
    datos = descifrar(blob, frase)
    destino = Path(destino)
    copia = destino / "datos" / f"antes_de_restaurar_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    copia.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for miembro in z.namelist():
            if miembro == "respaldo.json" or ".." in miembro or miembro.startswith("/"):
                continue
            if miembro.startswith("Avatares/"):
                final = AVATARES / miembro[len("Avatares/"):]
            else:
                final = destino / miembro
            if final.exists():
                respaldo_actual = copia / miembro
                respaldo_actual.parent.mkdir(parents=True, exist_ok=True)
                respaldo_actual.write_bytes(final.read_bytes())
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(z.read(miembro))
    return copia


@skill("respaldar_ahora",
       "Hace en este momento un respaldo cifrado de la memoria, preferencias, pendientes, "
       "hábitos, configuración y avatares de Jarvis en Supabase ('respáldate', 'haz un respaldo').",
       requeridos=[])
def respaldar_ahora():
    return respaldar()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restaurar":
        import getpass

        import psutil
        abierto = any("iniciar_genesis" in " ".join(p.info["cmdline"] or [])
                      for p in psutil.process_iter(["cmdline"]) if p.pid != os.getpid())
        if abierto:
            print("Cierra Jarvis primero: restaurar con Jarvis abierto podría dañar su base de datos.")
            sys.exit(1)
        frase = nube.credenciales().get("frase") or getpass.getpass("Frase de tus respaldos: ")
        equipo = sys.argv[2] if len(sys.argv) > 2 else None
        try:
            copia = restaurar(frase, equipo=equipo)
        except Exception as e:
            print(f"No se pudo restaurar: {e}")
            sys.exit(1)
        print(f"Listo. Lo que tenías antes quedó en {copia}. Ya puedes abrir Jarvis.")
    else:
        print(respaldar())
