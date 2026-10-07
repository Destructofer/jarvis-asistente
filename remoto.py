"""Control remoto desde el teléfono, por Supabase.

Cómo funciona:
1. "Jarvis, conecta mi teléfono": se crea una llave secreta de 256 bits para ese teléfono (en
   Supabase solo queda su huella SHA-256) y se muestra un código QR con la dirección de la página
   de control. La llave va DESPUÉS del "#" de la dirección: esa parte nunca sale del teléfono
   (los navegadores no la mandan a ningún servidor). La página la guarda y la borra de la barra.
2. Desde el teléfono, en cualquier lugar: escribes o dictas una orden ("¿qué pendientes tengo?",
   "pon música", "resumen del día"). La página llama a remoto_enviar(llave, texto) en Supabase.
3. Jarvis revisa cada 2 s, toma la orden, la procesa igual que una orden escrita en la PC (atajos,
   modelo, herramientas, bitácora) y deja la respuesta; la página la muestra.

Seguridad (supabase/remoto.sql): las tablas no se pueden leer ni escribir con la llave pública;
el teléfono solo puede usar dos funciones que exigen su llave. Desde el teléfono no se hace nada
que pida confirmación (apagar, borrar, cerrar sin guardar): eso solo en la PC.
"Jarvis, desconecta mis teléfonos" revoca todas las llaves al instante.

Página: https://abraham-src.github.io/jarvis-control/ (repo público SIN secretos; config.json →
remoto.pagina). Las respuestas no suenan en la PC (remoto.hablar_en_pc = false): tal vez no
estás ahí.
"""
import hashlib
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from urllib.parse import quote

import nube
from skills import Fallo, skill

PAGINA = "https://abraham-src.github.io/jarvis-control/"
CADA_SEG = 2.0           # cada cuánto se revisan órdenes nuevas
LATIDO_SEG = 15          # cada cuánto se dice "aquí estoy" (el teléfono muestra "en línea")
SIN_TELEFONOS_SEG = 20   # sin teléfonos vinculados, se revisa menos seguido
GUARDAR_DIAS = 7         # las órdenes viejas se borran

entregar = None          # lo pone genesis.py: fn(orden) — la mete a la cola de órdenes
hablar = None            # lo pone genesis.py: fn(texto)
_cfg = {"cfg": {}}
_estado = {"hilo": None, "telefonos": None, "t_telefonos": 0.0, "t_latido": 0.0, "t_limpieza": 0.0,
           "procesando": set()}
_despertar = threading.Event()   # vincular un teléfono despierta al vigilante al instante


def _conf():
    return (_cfg["cfg"].get("remoto") or {})


def activo():
    return bool(_conf().get("activo", True)) and nube.configurada()


def huella(llave):
    return hashlib.sha256(llave.encode("utf-8")).hexdigest()


def direccion(llave, url=None, publica=None, pagina=None):
    """La dirección del QR: la página y, después del '#', lo que el teléfono necesita."""
    d = nube.credenciales()
    url = url or d.get("url", "")
    publica = publica or nube.clave_publica()
    pagina = pagina or _conf().get("pagina", PAGINA)
    return f"{pagina}#u={quote(url, safe='')}&k={quote(publica, safe='')}&t={quote(llave, safe='')}"


# ---------- Supabase ----------
def _telefonos(forzar=False):
    ahora = time.time()
    if forzar or _estado["telefonos"] is None or ahora - _estado["t_telefonos"] > SIN_TELEFONOS_SEG:
        r = nube.peticion("GET", f"/rest/v1/telefonos?equipo=eq.{nube.EQUIPO}&select=id,nombre,creado,usado")
        _estado.update(telefonos=r.json(), t_telefonos=ahora)
    return _estado["telefonos"]


def vincular(nombre="Mi teléfono"):
    """Crea la llave de un teléfono nuevo. Devuelve la llave (solo existe en este momento)."""
    llave = secrets.token_urlsafe(32)
    nube.peticion("POST", "/rest/v1/telefonos", json={"equipo": nube.EQUIPO, "nombre": nombre[:60],
                                                       "huella": huella(llave)},
                  encabezados={"Prefer": "return=minimal"})
    _estado["telefonos"] = None
    _estado["t_latido"] = 0.0   # que el teléfono lo vea "en línea" desde el primer momento
    _despertar.set()
    return llave


def desvincular_todos():
    nube.peticion("DELETE", f"/rest/v1/telefonos?equipo=eq.{nube.EQUIPO}",
                  encabezados={"Prefer": "return=minimal"})
    _estado["telefonos"] = []


def pendientes():
    r = nube.peticion("GET", f"/rest/v1/ordenes_remotas?equipo=eq.{nube.EQUIPO}&estado=eq.pendiente"
                             "&select=id,texto,creado&order=id.asc&limit=5")
    return r.json()


def tomar(orden_id):
    """Pasa la orden a 'procesando' solo si seguía pendiente (que no se procese dos veces)."""
    r = nube.peticion("PATCH", f"/rest/v1/ordenes_remotas?id=eq.{int(orden_id)}&estado=eq.pendiente",
                      json={"estado": "procesando"}, encabezados={"Prefer": "return=representation"})
    return bool(r.json())


def responder(orden_id, texto, ok=True):
    try:
        nube.peticion("PATCH", f"/rest/v1/ordenes_remotas?id=eq.{int(orden_id)}",
                      json={"estado": "hecha" if ok else "error", "respuesta": (texto or "")[:4000],
                            "respondido": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                      encabezados={"Prefer": "return=minimal"})
    except nube.NubeError as e:
        print(f"[Remoto: no pude mandar la respuesta ({e})]")
    finally:
        _estado["procesando"].discard(orden_id)


def _latido():
    nube.peticion("POST", "/rest/v1/equipos_vivos?on_conflict=equipo",
                  json={"equipo": nube.EQUIPO, "visto": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
                  encabezados={"Prefer": "resolution=merge-duplicates,return=minimal"})


def _limpiar():
    limite = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - GUARDAR_DIAS * 86400))
    nube.peticion("DELETE", f"/rest/v1/ordenes_remotas?equipo=eq.{nube.EQUIPO}&creado=lt.{limite}",
                  encabezados={"Prefer": "return=minimal"})
    # Lo que quedó "procesando" de una sesión que se cerró de golpe: que no se quede colgado
    nube.peticion("PATCH", f"/rest/v1/ordenes_remotas?equipo=eq.{nube.EQUIPO}&estado=eq.procesando",
                  json={"estado": "error", "respuesta": "Jarvis se reinició antes de terminar; mándala otra vez."},
                  encabezados={"Prefer": "return=minimal"})


# ---------- El vigilante ----------
def revisar_una_vez():
    """Una vuelta: latido, limpieza y órdenes nuevas. Devuelve cuántas órdenes entregó."""
    ahora = time.time()
    if not _telefonos():
        return 0
    if ahora - _estado["t_latido"] >= LATIDO_SEG:
        _latido()
        _estado["t_latido"] = ahora
    if ahora - _estado["t_limpieza"] >= 3600:
        _limpiar()
        _estado["t_limpieza"] = ahora
    entregadas = 0
    for o in pendientes():
        if o["id"] in _estado["procesando"] or not tomar(o["id"]):
            continue
        _estado["procesando"].add(o["id"])
        print(f"[Remoto: orden desde el teléfono: {o['texto'][:80]}]")
        if entregar is None:
            responder(o["id"], "Jarvis todavía está arrancando; mándala en un momento.", ok=False)
        else:
            entregar(o)
            entregadas += 1
    return entregadas


def _bucle():
    errores = 0
    while True:
        espera = float(_conf().get("cada_seg", CADA_SEG))
        try:
            if activo():
                revisar_una_vez()
                if not _telefonos():
                    espera = SIN_TELEFONOS_SEG
            else:
                espera = SIN_TELEFONOS_SEG
            errores = 0
        except Exception as e:
            errores += 1
            if errores in (1, 10):
                print(f"[Remoto: no pude revisar el teléfono ({type(e).__name__}: {str(e)[:100]})]")
            espera = min(60, espera * 2 ** min(errores, 5))   # sin internet: cada vez más espaciado
        _despertar.wait(espera)
        _despertar.clear()


def iniciar(cfg):
    _cfg["cfg"] = cfg
    if _estado["hilo"] is None or not _estado["hilo"].is_alive():
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="remoto")
        _estado["hilo"].start()


# ---------- Código QR ----------
def _mostrar_qr(texto):
    """Abre el QR en el visor de imágenes y lo borra a los 3 minutos (lleva la llave)."""
    import qrcode
    img = qrcode.make(texto, box_size=10, border=3)
    ruta = os.path.join(tempfile.gettempdir(), f"jarvis-telefono-{secrets.token_hex(4)}.png")
    img.save(ruta)
    os.startfile(ruta)

    def borrar():
        time.sleep(180)
        try:
            os.remove(ruta)
        except OSError:
            pass
    threading.Thread(target=borrar, daemon=True).start()


def _vincular_y_mostrar():
    llave = vincular()
    _mostrar_qr(direccion(llave))
    return ("Listo: te abrí un código QR. Escanéalo con la cámara de tu teléfono y guarda la página "
            "en tu pantalla de inicio. El código se borra en 3 minutos.")


@skill("conectar_telefono",
       "Vincula tu teléfono para darle órdenes a Jarvis desde cualquier lugar (por Supabase): "
       "muestra un código QR para escanear. 'Conecta mi teléfono', 'quiero controlarte desde el celular'.",
       terminal=True, disponible=nube.configurada)
def conectar_telefono():
    if not nube.configurada():
        return Fallo("Primero hay que conectar Supabase ('conecta Supabase').")
    try:
        nube.asegurar_esquemas_extra()
    except Exception as e:
        print(f"[Remoto: no pude revisar las tablas ({type(e).__name__}: {str(e)[:100]})]")
    if nube.clave_publica():
        return _vincular_y_mostrar()
    # La página necesita la llave pública del proyecto (no es secreta): se pide una sola vez
    import panel
    avisar = hablar or print
    webbrowser.open(nube.pagina_llaves())

    def con_llave(texto):
        try:
            nube.guardar_clave_publica(texto)
            avisar(_vincular_y_mostrar())
        except Exception as e:
            avisar(f"No pude vincular el teléfono: {e}")
    panel.pedir_texto("Llave pública de Supabase",
                      "Te abrí Supabase en API Keys. Copia la 'Publishable key' (empieza con "
                      "sb_publishable_) y pégala aquí. No es secreta; solo se pide una vez.",
                      con_llave, lambda: None)
    return ("Te abrí Supabase: copia la llave pública (Publishable key) y pégala en la ventanita. "
            "Después te muestro el código QR para tu teléfono.")


@skill("desconectar_telefonos",
       "Revoca el acceso de todos los teléfonos vinculados a Jarvis: 'desconecta mi teléfono', "
       "'perdí mi celular'.", riesgo="confirmar",
       pregunta="¿Desconecto todos los teléfonos vinculados?", terminal=True, disponible=nube.configurada)
def desconectar_telefonos():
    desvincular_todos()
    return "Listo: ningún teléfono puede darme órdenes. Para volver a usarlo, dime 'conecta mi teléfono'."


@skill("estado_telefono",
       "Dice si hay teléfonos vinculados y cuándo se usaron: '¿mi teléfono está conectado?'.",
       terminal=True, disponible=nube.configurada)
def estado_telefono():
    tels = _telefonos(forzar=True)
    if not tels:
        return "No hay teléfonos vinculados. Dime 'conecta mi teléfono'."
    usado = [t for t in tels if t.get("usado")]
    return (f"Tengo {len(tels)} teléfono{'s' if len(tels) != 1 else ''} vinculado{'s' if len(tels) != 1 else ''}"
            + (f"; el último lo usaste el {usado[-1]['usado'][:10]}." if usado else "; todavía no me ha mandado nada."))
