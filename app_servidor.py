"""El puente entre Jarvis y su app de escritorio (jarvis_app.pyw + app/).

Un servidor HTTP solo en 127.0.0.1 (nada de la red puede entrar) con una llave
(datos/app.json, que la app lee). Sirve la interfaz (app/) y
una API para personalizar a Jarvis en vivo (la llave se conserva entre reinicios para que la
ventana abierta se reconecte sola):

  GET  /api/estado                     estado, personalidad, avatar, voz
  GET  /api/eventos?t=LLAVE            eventos en vivo (Server-Sent Events): estado, volumen
                                       de la voz (mueve el orbe), lo que oyó y lo que dice
  GET  /api/personalidades             POST /api/personalidad {clave}
  GET  /api/avatares                   POST /api/avatar {nombre}
  POST /api/avatar/subir {personaje, archivo, datos (base64), categoria?}
  POST /api/avatar/categoria {personaje, archivo, categoria}
  GET  /api/avatar/vista?t=&p=&f=      la animación (para las vistas previas)
  GET  /api/voces                      POST /api/voz {...}   POST /api/voz/probar {texto}
  GET  /api/ajustes                    POST /api/ajustes {...}
  POST /api/orden {texto}              una orden escrita: la respuesta llega por /api/eventos

Seguridad: todo /api/ pide la llave (encabezado X-Jarvis-Token, o ?t= en lo que el navegador
pide solo: eventos e imágenes); solo se aceptan peticiones con Host 127.0.0.1/localhost (contra
páginas web que intenten hablarle a este servidor), y los archivos solo salen de las carpetas
de avatares. Las órdenes de la app son tuyas en la PC: sí pueden pedir confirmación.
"""
import base64
import http.server
import itertools
import json
import queue
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
CARPETA_APP = BASE / "app"
DATOS_APP = BASE / "datos" / "app.json"
PUERTO = 8766
MAX_SUBIDA = 25 * 1024 * 1024

entregar = None      # lo pone genesis.py: fn(orden) -> la mete a la cola de órdenes
hablar = None        # lo pone genesis.py: fn(texto)
_cfg = {"cfg": {}}
_estado = {"servidor": None, "llave": None, "puerto": None, "estado": "inactivo", "voces_edge": None}
_suscriptores = []   # colas de los clientes conectados a /api/eventos
_lock = threading.Lock()
_ids = itertools.count(1)

# Ajustes que la app puede cambiar (y de qué tipo)
AJUSTES = {
    "voz_activa": bool, "palabra_activacion": bool, "interrumpir_con_voz": bool,
    "hud.activo": bool, "hud.estilo": ("vaultboy", "reactor"), "hud.subtitulos": ("siempre", "expositor", "nunca"),
    "hud.tamano": int, "remoto.hablar_en_pc": bool, "para_mi.callarse": float,
    "presencia.saludar": bool, "habitos.activo": bool,
}


# ---------- Eventos en vivo ----------
def publicar(tipo, **datos):
    """Manda un evento a todas las ventanas de la app abiertas (no bloquea nunca)."""
    if tipo == "estado":
        _estado["estado"] = datos.get("estado", "inactivo")
    evento = json.dumps({"tipo": tipo, **datos}, ensure_ascii=False)
    with _lock:
        for q in list(_suscriptores):
            try:
                q.put_nowait(evento)
            except queue.Full:
                pass   # una ventana que no lee (minimizada): se le saltan eventos


def al_sonar(niveles, paso):
    """Hook de voz.py: el volumen de lo que va a sonar mueve el orbe."""
    if _suscriptores:
        publicar("voz", niveles=niveles, paso=paso)


# ---------- Datos que la app muestra ----------
def _cfg_valor(ruta, por_omision=None):
    d = _cfg["cfg"]
    for parte in ruta.split("."):
        if not isinstance(d, dict) or parte not in d:
            return por_omision
        d = d[parte]
    return d


def estado():
    import avatares
    import personalidades
    clave, _ = personalidades.actual()
    return {"nombre": _cfg["cfg"].get("name", "Jarvis"), "estado": _estado["estado"],
            "personalidad": clave, "personalidad_nombre": personalidades.PERSONALIDADES[clave]["nombre"],
            "avatar": avatares.activo(), "voz": _voz_actual()}


def lista_personalidades():
    import personalidades
    clave, _ = personalidades.actual()
    return [{"clave": k, "nombre": p["nombre"], "descripcion": p["descripcion"],
             "voz_propia": bool(p.get("voz")), "voz": (p.get("voz") or {}).get("edge_voz", ""),
             "saludo": p.get("saludo", ""), "activa": k == clave}
            for k, p in personalidades.PERSONALIDADES.items()]


def cambiar_personalidad(clave):
    import personalidades
    if clave not in personalidades.PERSONALIDADES:
        raise ValueError("esa personalidad no existe")
    personalidades._guardar(clave, False)
    personalidades.aplicar_voz(_cfg["cfg"])
    saludo = personalidades.PERSONALIDADES[clave]["saludo"]
    publicar("personalidad", clave=clave, nombre=personalidades.PERSONALIDADES[clave]["nombre"])
    if hablar:
        threading.Thread(target=hablar, args=(saludo,), daemon=True).start()
    return saludo


def lista_avatares():
    import avatares
    activo = avatares.activo()
    salida = []
    for nombre in avatares.listar():
        meta = avatares._leer_meta(nombre)
        fuentes = avatares._fuentes(nombre)
        por_cat = {}
        animaciones = []
        for f in fuentes:
            e = meta.get(f.name) or {}
            cat = e.get("categoria") or "(preparando)"
            por_cat[cat] = por_cat.get(cat, 0) + 1
            animaciones.append({"archivo": f.name, "categoria": cat, "lista": bool(e.get("listo"))})
        portada = next((a["archivo"] for a in animaciones if a["categoria"] in ("saludo", "espera")),
                       animaciones[0]["archivo"] if animaciones else None)
        salida.append({"nombre": nombre, "activo": nombre == activo, "animaciones": animaciones,
                       "categorias": por_cat, "portada": portada})
    return {"avatares": salida, "categorias": list(avatares.CATEGORIAS), "carpeta": str(avatares.raiz())}


def _ruta_avatar(personaje, archivo):
    """La ruta de un archivo dentro de la carpeta del avatar (o None si intenta salirse)."""
    import avatares
    raiz = avatares.raiz().resolve()
    carpeta = (raiz / personaje).resolve()
    ruta = (carpeta / archivo).resolve()
    if raiz not in carpeta.parents or carpeta not in ruta.parents:
        return None
    return ruta


def subir_avatar(personaje, archivo, datos_b64, categoria=None):
    import avatares
    personaje = re.sub(r'[<>:"/\\|?*]', "", (personaje or "").strip())[:40]
    nombre = re.sub(r'[<>:"/\\|?*]', "", Path(archivo or "").name)[:80]
    if not personaje or not nombre:
        raise ValueError("falta el personaje o el nombre del archivo")
    if Path(nombre).suffix.lower() not in avatares.EXTENSIONES:
        raise ValueError("solo GIF, WEBP, PNG o APNG")
    datos = base64.b64decode(datos_b64 or "", validate=False)
    if not datos or len(datos) > MAX_SUBIDA:
        raise ValueError("el archivo está vacío o pesa más de 25 MB")
    destino = _ruta_avatar(personaje, nombre)
    if destino is None:
        raise ValueError("nombre de archivo no válido")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino = destino.with_name(f"{destino.stem}_{int(time.time())}{destino.suffix}")
    destino.write_bytes(datos)
    if categoria and categoria in avatares.CATEGORIAS:
        meta = avatares._leer_meta(personaje)
        meta.setdefault(destino.name, {})["categoria_manual"] = categoria
        avatares._guardar_meta(personaje, meta)

    def preparar():
        avatares.sincronizar(personaje)
        publicar("avatares")
    threading.Thread(target=preparar, daemon=True, name="preparar-avatar").start()
    return destino.name


def categoria_avatar(personaje, archivo, categoria):
    import avatares
    if categoria not in avatares.CATEGORIAS:
        raise ValueError("categoría no válida")
    meta = avatares._leer_meta(personaje)
    if archivo not in meta:
        raise ValueError("esa animación todavía no está lista")
    meta[archivo]["categoria"] = meta[archivo]["categoria_manual"] = categoria
    avatares._guardar_meta(personaje, meta)
    avatares._estado["version"] += 1


def _voz_actual():
    import voz
    conf = voz._CONF if voz._CONF is not None else _cfg["cfg"]
    return {k: conf.get(k) for k in ("voz_motor", "edge_voz", "edge_velocidad", "edge_tono", "voz_nombre", "voz_activa")}


def lista_voces():
    import personalidades
    import voz
    if _estado["voces_edge"] is None:
        try:
            import asyncio

            import edge_tts
            todas = asyncio.run(edge_tts.list_voices())
            _estado["voces_edge"] = sorted(
                ({"id": v["ShortName"], "nombre": v["ShortName"].split("-")[-1].replace("Neural", ""),
                  "region": v["Locale"], "genero": "mujer" if v.get("Gender") == "Female" else "hombre"}
                 for v in todas if v["Locale"].startswith("es-")), key=lambda v: (v["region"] != "es-MX", v["region"], v["nombre"]))
        except Exception as e:
            print(f"[App: no pude traer las voces de Edge ({type(e).__name__})]")
            _estado["voces_edge"] = []
    piper = sorted(p.stem for p in voz.VOCES_DIR.glob("*.onnx"))
    clave, _ = personalidades.actual()
    propia = personalidades.PERSONALIDADES[clave].get("voz") or {}
    base = personalidades._estado.get("original_voz") or {}
    return {"edge": _estado["voces_edge"], "piper": piper, "actual": _voz_actual(),
            "base": {k: base.get(k) for k in ("edge_voz", "edge_velocidad", "edge_tono")},
            "personalidad_con_voz": personalidades.PERSONALIDADES[clave]["nombre"] if propia else None}


def cambiar_voz(cambios):
    """Guarda la voz BASE (la de la personalidad clásica y las que no traen voz propia)."""
    import configuracion
    import personalidades
    import voz
    permitidos = {"voz_motor": ("auto", "edge", "elevenlabs", "windows"), "edge_voz": str,
                  "edge_velocidad": str, "edge_tono": str, "voz_nombre": str, "voz_activa": bool}
    limpio = {}
    for k, v in (cambios or {}).items():
        regla = permitidos.get(k)
        if regla is None:
            continue
        if isinstance(regla, tuple):
            if v not in regla:
                raise ValueError(f"{k} no válido")
        elif regla is bool:
            v = bool(v)
        elif not isinstance(v, str) or len(v) > 60:
            raise ValueError(f"{k} no válido")
        if k == "edge_velocidad" and not re.fullmatch(r"[+-]\d{1,2}%", v):
            raise ValueError("velocidad no válida")
        if k == "edge_tono" and not re.fullmatch(r"[+-]\d{1,2}Hz", v):
            raise ValueError("tono no válido")
        limpio[k] = v
    configuracion.guardar_campos(limpio)
    _cfg["cfg"].update(limpio)
    base = personalidades._estado.get("original_voz")
    if base is not None:
        base.update({k: v for k, v in limpio.items() if k in base})
    conf = voz._CONF if voz._CONF is not None else _cfg["cfg"]
    conf.update({k: v for k, v in limpio.items() if k not in ("edge_voz", "edge_velocidad", "edge_tono")})
    personalidades.aplicar_voz(_cfg["cfg"])   # la personalidad activa decide si usa la suya
    return _voz_actual()


_prueba = threading.Lock()


def probar_voz(texto, prueba=None):
    """Dice el texto con la voz indicada (sin guardarla): se pone un momento y se regresa."""
    import voz
    texto = (texto or "").strip()[:200] or "Hola, así sueno ahora."
    prueba = {k: v for k, v in (prueba or {}).items()
              if k in ("edge_voz", "edge_velocidad", "edge_tono", "voz_motor") and isinstance(v, str) and len(v) <= 60}
    if "edge_velocidad" in prueba and not re.fullmatch(r"[+-]\d{1,2}%", prueba["edge_velocidad"]):
        raise ValueError("velocidad no válida")
    if "edge_tono" in prueba and not re.fullmatch(r"[+-]\d{1,2}Hz", prueba["edge_tono"]):
        raise ValueError("tono no válido")

    def decir():
        with _prueba:
            conf = voz._CONF if voz._CONF is not None else _cfg["cfg"]
            antes = {k: conf.get(k) for k in prueba}
            conf.update(prueba)
            try:
                voz.hablar(texto)
            finally:
                for k, v in antes.items():
                    if v is None:
                        conf.pop(k, None)
                    else:
                        conf[k] = v
    threading.Thread(target=decir, daemon=True).start()


# Lo que vale cada ajuste si no está en config.json (lo mismo que usa cada módulo)
POR_OMISION = {"voz_activa": True, "palabra_activacion": True, "interrumpir_con_voz": True, "hud.activo": True,
               "hud.estilo": "vaultboy", "hud.subtitulos": "expositor", "hud.tamano": 150,
               "remoto.hablar_en_pc": False, "para_mi.callarse": 0.05, "presencia.saludar": True,
               "habitos.activo": True}


def ajustes():
    return {k: _cfg_valor(k, POR_OMISION.get(k)) for k in AJUSTES}


def cambiar_ajustes(cambios):
    import configuracion
    import json as _json
    datos = configuracion.cargar()
    for k, v in (cambios or {}).items():
        regla = AJUSTES.get(k)
        if regla is None:
            continue
        if isinstance(regla, tuple):
            if v not in regla:
                raise ValueError(f"{k} no válido")
        elif regla is bool:
            v = bool(v)
        elif regla is int:
            v = int(v)
        elif regla is float:
            v = float(v)
        partes = k.split(".")
        for d in (datos, _cfg["cfg"]):
            nodo = d
            for p in partes[:-1]:
                nodo = nodo.setdefault(p, {})
            nodo[partes[-1]] = v
    configuracion.guardar(datos)
    _json.dumps(datos)   # que siga siendo JSON válido
    return ajustes()


def orden(texto):
    texto = (texto or "").strip()[:500]
    if not texto:
        raise ValueError("la orden está vacía")
    if entregar is None:
        raise RuntimeError("Jarvis todavía está arrancando")
    oid = f"app-{next(_ids)}"

    def responder(respuesta, ok=True):
        publicar("respuesta", id=oid, texto=respuesta, ok=ok)
    entregar({"id": oid, "texto": texto, "origen": "app", "hablar": True, "responder": responder})
    publicar("orden", id=oid, texto=texto)
    return oid


# ---------- El servidor ----------
class _Manejador(http.server.BaseHTTPRequestHandler):
    server_version = "Jarvis"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _llave_ok(self, consulta):
        llave = self.headers.get("X-Jarvis-Token") or (consulta.get("t") or [""])[0]
        return bool(llave) and secrets.compare_digest(llave, _estado["llave"] or "")

    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _archivo(self, ruta, tipo=None):
        if not ruta or not ruta.is_file():
            return self._json(404, {"error": "no existe"})
        tipos = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon", ".apng": "image/apng"}
        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo or tipos.get(ruta.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(datos)

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_SUBIDA * 1.4:
            raise ValueError("demasiado grande")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        consulta = urllib.parse.parse_qs(url.query)
        if not self._host_ok():
            return self._json(403, {"error": "host"})
        if not url.path.startswith("/api/"):
            ruta = (CARPETA_APP / (url.path.lstrip("/") or "index.html")).resolve()
            if CARPETA_APP.resolve() not in ruta.parents:
                return self._json(404, {"error": "no existe"})
            return self._archivo(ruta)
        if url.path == "/api/ping":
            return self._json(200, {"ok": True})
        if not self._llave_ok(consulta):
            return self._json(401, {"error": "llave"})
        try:
            if url.path == "/api/estado":
                return self._json(200, estado())
            if url.path == "/api/personalidades":
                return self._json(200, lista_personalidades())
            if url.path == "/api/avatares":
                return self._json(200, lista_avatares())
            if url.path == "/api/avatar/vista":
                import avatares
                p, f = (consulta.get("p") or [""])[0], (consulta.get("f") or [""])[0]
                e = avatares._leer_meta(p).get(f) or {}
                lista = _ruta_avatar(p, f".jarvis/{e['listo']}") if e.get("listo") else None
                return self._archivo(lista if lista and lista.is_file() else _ruta_avatar(p, f))
            if url.path == "/api/voces":
                return self._json(200, lista_voces())
            if url.path == "/api/ajustes":
                return self._json(200, ajustes())
            if url.path == "/api/eventos":
                return self._eventos()
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {str(e)[:150]}"})
        return self._json(404, {"error": "no existe"})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._host_ok() or not self._llave_ok({}):
            return self._json(401, {"error": "llave"})
        try:
            d = self._cuerpo()
            if url.path == "/api/personalidad":
                return self._json(200, {"saludo": cambiar_personalidad(d.get("clave"))})
            if url.path == "/api/avatar":
                import avatares
                r = avatares.cambiar_avatar(d.get("nombre", ""))
                publicar("avatares")
                return self._json(200, {"mensaje": str(r)})
            if url.path == "/api/avatar/subir":
                return self._json(200, {"archivo": subir_avatar(d.get("personaje"), d.get("archivo"),
                                                                d.get("datos"), d.get("categoria"))})
            if url.path == "/api/avatar/categoria":
                categoria_avatar(d.get("personaje"), d.get("archivo"), d.get("categoria"))
                return self._json(200, {"ok": True})
            if url.path == "/api/voz":
                return self._json(200, cambiar_voz(d))
            if url.path == "/api/voz/probar":
                probar_voz(d.get("texto"), d.get("voz"))
                return self._json(200, {"ok": True})
            if url.path == "/api/ajustes":
                return self._json(200, cambiar_ajustes(d))
            if url.path == "/api/orden":
                return self._json(200, {"id": orden(d.get("texto"))})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            return self._json(500, {"error": f"{type(e).__name__}: {str(e)[:150]}"})
        return self._json(404, {"error": "no existe"})

    def _eventos(self):
        q = queue.Queue(maxsize=400)
        with _lock:
            _suscriptores.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'tipo': 'estado', 'estado': _estado['estado']})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    evento = q.get(timeout=15)
                    self.wfile.write(f"data: {evento}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": sigo aqui\n\n")   # que la conexión no se cierre sola
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with _lock:
                if q in _suscriptores:
                    _suscriptores.remove(q)


def iniciar(cfg):
    """Arranca el servidor (una vez) y deja la llave y el puerto en datos/app.json."""
    _cfg["cfg"] = cfg
    if _estado["servidor"] is not None:
        return
    puerto = int((cfg.get("app") or {}).get("puerto", PUERTO))
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", puerto), _Manejador)
    except OSError:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Manejador)   # ocupado: otro libre
    srv.daemon_threads = True
    # La llave se conserva entre reinicios: así una ventana de la app abierta se reconecta sola
    try:
        llave = json.loads(DATOS_APP.read_text(encoding="utf-8")).get("llave", "")
    except (OSError, ValueError):
        llave = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", llave or ""):
        llave = secrets.token_urlsafe(24)
    _estado.update(servidor=srv, llave=llave, puerto=srv.server_address[1])
    DATOS_APP.parent.mkdir(parents=True, exist_ok=True)
    DATOS_APP.write_text(json.dumps({"puerto": _estado["puerto"], "llave": _estado["llave"]}), encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True, name="app-servidor").start()
    print(f"[App: lista en http://127.0.0.1:{_estado['puerto']}/]")
