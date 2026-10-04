"""Los avatares animados de Jarvis: una carpeta por personaje, y cada GIF que sueltes ahí se
prepara y se usa solo.

    Documentos\\Jarvis\\Avatares\\
        Vault Boy\\        <- un personaje (sus GIF, con el nombre que quieras)
        Iron Man\\         <- otro
        ...

Al agregar un GIF (también con Jarvis corriendo), en unos segundos:
1. Se le quita el fondo (cualquier color liso; si el GIF ya es transparente, se respeta).
2. Se mide al personaje (alto, dónde quedan los pies y su centro) para que en el HUD todos se
   vean del mismo tamaño y parados en la misma línea.
3. Se decide qué acción representa (saludo, pensando, celebrando...): si el nombre del archivo
   empieza con la categoría ("celebrando_baile.gif") se usa esa; si no, Jarvis mira la
   animación con su modelo de visión y la clasifica. Si se equivoca: "Jarvis, ese GIF es de
   celebrando" (clasificar_gif) o renombra el archivo.
4. Queda listo para el HUD (hud.py) en <personaje>\\.jarvis\\: una versión ya sin fondo y
   recortada, y avatar.json con la categoría y las medidas de cada uno.

Categorías: las de acciones.py (saludo, buscando, pensando, ejecutando, ciencia, cansado,
confundido, celebrando, tecnologia, cyborg) más:
    espera       la pose quieta entre animaciones (si no hay, se usa el primer cuadro de otro)
    completado   el pulgar arriba al terminar algo
    descargando  mientras se baja un archivo
    libre        solo para el modo libre (animaciones al azar cuando no hace nada)
Una categoría puede tener varios GIF: cada vez se elige uno al azar.
"""
import json
import re
import threading
import time
from pathlib import Path

import numpy as np

import acciones
import skills
from skills import Fallo, skill

ESPECIALES = {
    "espera": "la pose quieta, de pie y sin hacer nada (entre animaciones)",
    "completado": "terminó algo con éxito (pulgar arriba, aprobación)",
    "descargando": "está guardando o descargando algo (costal, caja, bolsa)",
    "libre": "una animación cualquiera para cuando no hace nada (caminar, jugar, entretenerse)",
}
CATEGORIAS = list(acciones.CATEGORIAS) + list(ESPECIALES)
EXTENSIONES = {".gif", ".webp", ".png", ".apng"}
ALTO_FIGURA_PX = 180       # alto del personaje en la versión preparada (el HUD la escala)
MAX_CUADROS = 90           # GIF con más cuadros: se toma uno de cada dos (pesan menos)
CARPETA_REPO = Path(__file__).parent / "vaultboy"  # de donde se migran los GIF de Vault Boy

_lock = threading.RLock()
_estado = {"version": 0, "activo": None, "ultimo": None, "vigilando": False}


# ---------- Carpetas ----------
def raiz():
    try:
        from win32com.shell import shell, shellcon
        base = Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Documents))
    except Exception:
        base = Path.home() / "Documents"
    r = base / "Jarvis" / "Avatares"
    r.mkdir(parents=True, exist_ok=True)
    return r


def listar():
    return sorted(d.name for d in raiz().iterdir() if d.is_dir() and not d.name.startswith("."))


def activo():
    """El avatar elegido (preferencia 'avatar'); si no hay o ya no existe, el primero."""
    with _lock:
        if _estado["activo"] is None:
            elegido = None
            try:
                import preferencias
                elegido, _ = preferencias.obtener("avatar")
            except Exception:
                pass
            nombres = listar()
            _estado["activo"] = (elegido if elegido in nombres else
                                 "Vault Boy" if "Vault Boy" in nombres else (nombres[0] if nombres else None))
        return _estado["activo"]


def _dir(nombre):
    return raiz() / nombre


def _dir_listo(nombre):
    d = _dir(nombre) / ".jarvis"
    d.mkdir(exist_ok=True)
    return d


def _meta_ruta(nombre):
    return _dir_listo(nombre) / "avatar.json"


def _leer_meta(nombre):
    try:
        return json.loads(_meta_ruta(nombre).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_meta(nombre, meta):
    tmp = _meta_ruta(nombre).with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_meta_ruta(nombre))


def _fuentes(nombre):
    d = _dir(nombre)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES
                  and not p.name.startswith("."))


# ---------- Quitar el fondo ----------
def sin_fondo(img):
    """RGBA con el fondo transparente y solo el personaje.

    Sirve para cualquier fondo liso (gris, blanco, de color): el color del fondo es el que
    predomina en el borde. En dibujos de líneas (Vault Boy) las líneas son del MISMO color que
    el fondo, así que no se borra todo ese color: solo lo que está fuera de la figura (conectado
    al borde) y los huecos grandes; las líneas de adentro se conservan. Si el GIF ya venía
    transparente, se respeta tal cual."""
    import cv2
    rgba = np.asarray(img.convert("RGBA"))
    alfa = rgba[..., 3]
    borde = np.concatenate([alfa[0], alfa[-1], alfa[:, 0], alfa[:, -1]])
    if (borde < 128).mean() > 0.5:  # ya trae transparencia
        return _binario(img.convert("RGBA"))
    rgb = rgba[..., :3].astype(np.int16)
    orilla = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    fondo_color = np.median(orilla, axis=0)
    parecido = (np.abs(rgb - fondo_color).max(axis=2) <= 22).astype(np.uint8)
    k3, k5 = np.ones((3, 3), np.uint8), np.ones((5, 5), np.uint8)
    candidato = (cv2.dilate(1 - parecido, k3) == 0).astype(np.uint8)  # dilatar sella las líneas
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(candidato, connectivity=4)
    alto, ancho = parecido.shape
    fondo = np.zeros((alto, ancho), bool)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        comp = etiquetas == i
        toca_borde = x == 0 or y == 0 or x + w >= ancho or y + h >= alto
        if toca_borde or (area > 40 and cv2.erode(comp.astype(np.uint8), k5).any()):
            fondo |= comp
    # el anillo de 1 px que dejó la dilatación alrededor de la figura, si es color fondo, fuera
    fondo |= cv2.dilate(fondo.astype(np.uint8), k3).astype(bool) & (parecido == 1)
    from PIL import Image
    return Image.fromarray(np.dstack([rgba[..., :3], np.where(fondo, 0, 255).astype(np.uint8)]), "RGBA")


def _binario(img):
    """Transparencia de sí o no: la ventana del HUD (color-clave de Windows) no tiene medios
    tonos y un borde a medio transparentar se vería como una orilla negra."""
    img.putalpha(img.getchannel("A").point(lambda a: 255 if a > 128 else 0))
    return img


# ---------- Medir al personaje ----------
def _medir(img):
    """(alto, pies, centro) del personaje en un cuadro sin fondo: el trazo más grande (en las
    escenas con objetos, el personaje suele ser lo más grande; si no, se corrige a mano en
    avatar.json → figura)."""
    import cv2
    alfa = (np.asarray(img.getchannel("A")) > 0).astype(np.uint8)
    n, _e, stats, _c = cv2.connectedComponentsWithStats(alfa, connectivity=8)
    if n <= 1:
        w, h = img.size
        return float(h), float(h), w / 2
    i = 1 + int(np.argmax(stats[1:, 4]))
    x, y, w, h, _a = stats[i]
    return float(h), float(y + h), x + w / 2


# ---------- Clasificar ----------
def categoria_por_nombre(stem):
    """'celebrando_baile' -> 'celebrando'; 'libre_caminando' -> 'libre'; None si no empieza
    con una categoría."""
    t = skills._norm(stem).replace(" ", "_")
    for c in sorted(CATEGORIAS, key=len, reverse=True):
        if t == c or t.startswith(c + "_") or re.match(rf"{c}\d+$", t):
            return c
    return None


def _clasificar_con_vision(cuadros):
    """Mira 4 momentos de la animación (en una sola imagen) y elige la categoría."""
    import vision
    from PIL import Image
    elegidos = [cuadros[int(k * (len(cuadros) - 1) / 3)].convert("RGBA") for k in range(4)]
    w = max(c.width for c in elegidos)
    h = max(c.height for c in elegidos)
    hoja = Image.new("RGB", (w * 4, h), (128, 128, 128))
    for k, c in enumerate(elegidos):
        hoja.paste(c, (k * w, 0), c)
    hoja.thumbnail((1200, 400))
    opciones = {**acciones.CATEGORIAS, **ESPECIALES}
    lista = "\n".join(f"- {c}: {d}" for c, d in opciones.items())
    respuesta = vision.ver(
        skills._CFG, hoja,
        "Son 4 momentos seguidos de una animación de un personaje (un avatar). ¿Qué categoría "
        f"describe mejor lo que hace?\n{lista}\nResponde SOLO con el nombre de la categoría.",
        "Clasificas animaciones para el avatar de un asistente. Respondes con una sola palabra.",
        max_tokens=20)
    t = skills._norm(respuesta)
    for c in sorted(CATEGORIAS, key=len, reverse=True):
        if c in t.replace(" ", "_"):
            return c
    return "libre"


# ---------- Preparar un GIF ----------
def _cuadros(ruta):
    from PIL import Image, ImageSequence
    im = Image.open(ruta)
    total = getattr(im, "n_frames", 1)
    cuadros, ms = [], []
    for i, fr in enumerate(ImageSequence.Iterator(im)):
        d = max(20, int(fr.info.get("duration", 80) or 80))
        if total > MAX_CUADROS and i % 2:
            ms[-1] += d
            continue
        cuadros.append(fr.convert("RGBA").copy())  # copy(): si no, todos terminan siendo el último
        ms.append(d)
    return cuadros, ms


def preparar(nombre, ruta, meta, manual=None):
    """Quita el fondo, mide, recorta y guarda la versión lista. Devuelve la entrada para
    avatar.json. manual: (alto, pies, centro) medidos a mano en el archivo original."""
    cuadros, ms = _cuadros(ruta)
    sin = [sin_fondo(c) for c in cuadros]
    alto, pies, centro = manual or _medir(sin[0])
    caja = None
    for c in sin:
        b = c.getchannel("A").getbbox()
        if b:
            caja = b if caja is None else (min(caja[0], b[0]), min(caja[1], b[1]),
                                           max(caja[2], b[2]), max(caja[3], b[3]))
    caja = caja or (0, 0) + sin[0].size
    k = ALTO_FIGURA_PX / max(1.0, alto)
    from PIL import Image
    listos = []
    for c in sin:
        c = c.crop(caja)
        c = c.resize((max(1, int(c.width * k)), max(1, int(c.height * k))), Image.LANCZOS)
        listos.append(_binario(c))
    destino = _dir_listo(nombre) / f"{ruta.stem}.webp"
    listos[0].save(destino, save_all=True, append_images=listos[1:], duration=ms, loop=0,
                   lossless=True, method=3)
    categoria = (meta.get(ruta.name, {}).get("categoria_manual") or categoria_por_nombre(ruta.stem)
                 or _clasificar_con_vision(cuadros))
    return {
        "categoria": categoria,
        "categoria_manual": meta.get(ruta.name, {}).get("categoria_manual"),
        "listo": destino.name,
        "figura": {"alto": round(alto * k, 1), "pies": round((pies - caja[1]) * k, 1),
                   "centro": round((centro - caja[0]) * k, 1)},
        "origen": {"tam": ruta.stat().st_size, "mtime": ruta.stat().st_mtime},
        "manual": list(manual) if manual else None,
    }


def sincronizar(nombre=None, avisar=True):
    """Prepara los GIF nuevos o cambiados del avatar y olvida los que se borraron. True si
    cambió algo (el HUD vuelve a cargar)."""
    nombre = nombre or activo()
    if not nombre:
        return False
    with _lock:
        meta = _leer_meta(nombre)
        fuentes = {p.name: p for p in _fuentes(nombre)}
        cambio = False
        for viejo in [n for n in meta if n not in fuentes]:
            listo = _dir_listo(nombre) / meta[viejo].get("listo", "")
            if listo.is_file():
                listo.unlink()
            del meta[viejo]
            cambio = True
        for archivo, ruta in fuentes.items():
            e = meta.get(archivo)
            st = ruta.stat()
            if e and e.get("origen") == {"tam": st.st_size, "mtime": st.st_mtime} \
                    and (_dir_listo(nombre) / e.get("listo", "")).is_file():
                continue
            try:
                t = time.time()
                manual = tuple(e["manual"]) if e and e.get("manual") else None
                meta[archivo] = preparar(nombre, ruta, meta, manual)
                cambio = True
                _estado["ultimo"] = (nombre, archivo)
                if avisar:
                    print(f"[Avatar {nombre}: preparé {archivo} como «{meta[archivo]['categoria']}» "
                          f"({time.time() - t:.1f} s)]")
            except Exception as ex:
                print(f"[Avatar {nombre}: no pude preparar {archivo}: {type(ex).__name__}: {str(ex)[:100]}]")
        if cambio:
            _guardar_meta(nombre, meta)
            _estado["version"] += 1
        return cambio


# ---------- Lo que usa el HUD ----------
def version():
    """Cambia cada vez que algo del avatar cambió (el HUD descarta lo que tenía cargado)."""
    return _estado["version"]


def entradas(categoria=None):
    """[(ruta lista .webp, figura)] del avatar activo, de una categoría o todas."""
    nombre = activo()
    if not nombre:
        return []
    meta = _leer_meta(nombre)
    d = _dir_listo(nombre)
    return [(d / e["listo"], e["figura"]) for _a, e in sorted(meta.items())
            if (categoria is None or e.get("categoria") == categoria) and (d / e["listo"]).is_file()]


def categoria_de(ruta):
    for _a, e in _leer_meta(activo()).items():
        if e.get("listo") == Path(ruta).name:
            return e.get("categoria")
    return None


def pose():
    """La pose quieta: un GIF 'espera'; si el avatar no tiene, el primer cuadro de otro."""
    espera = entradas("espera")
    if espera:
        return espera[0]
    todas = entradas()
    if not todas:
        return None
    ruta, figura = todas[0]
    quieta = ruta.with_name("_pose.webp")
    if not quieta.is_file() or quieta.stat().st_mtime < ruta.stat().st_mtime:
        from PIL import Image
        Image.open(ruta).convert("RGBA").save(quieta, lossless=True)
    return quieta, figura


# ---------- Migración y vigilancia ----------
def migrar_vaultboy():
    """La primera vez: los GIF de vaultboy/ (con sus medidas a mano de ajustes.json) pasan a
    Documentos\\Jarvis\\Avatares\\Vault Boy\\."""
    destino = _dir("Vault Boy")
    if destino.is_dir() and _fuentes("Vault Boy"):
        return False
    origen = sorted(CARPETA_REPO.glob("*.gif"))
    if not origen:
        return False
    import shutil
    destino.mkdir(parents=True, exist_ok=True)
    try:
        ajustes = json.loads((CARPETA_REPO / "ajustes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ajustes = {}
    meta = {}
    for ruta in origen:
        shutil.copy2(ruta, destino / ruta.name)
        a = ajustes.get(ruta.name)
        if a:
            meta[ruta.name] = {"manual": [a["alto"], a["pies"], a["centro"]]}
    _guardar_meta("Vault Boy", meta)
    print(f"[Avatares: pasé {len(origen)} animaciones de Vault Boy a {destino}]")
    return True


def _vigilar():
    while True:
        try:
            sincronizar(activo())
        except Exception as e:
            print(f"[Avatares: error revisando la carpeta: {type(e).__name__}: {str(e)[:100]}]")
        time.sleep(3)


def iniciar():
    """Al arrancar: migra Vault Boy si hace falta, prepara lo pendiente y vigila la carpeta."""
    with _lock:
        if _estado["vigilando"]:
            return
        _estado["vigilando"] = True
    try:
        migrar_vaultboy()
    except Exception as e:
        print(f"[Avatares: no pude migrar Vault Boy: {e}]")
    threading.Thread(target=_vigilar, daemon=True, name="avatares").start()


# ---------- Skills ----------
@skill("cambiar_avatar",
       "Cambia el personaje animado de Jarvis (su avatar en la esquina) por otro de sus carpetas "
       "de avatares, y lo recuerda. Úsala con 'usa el avatar de X', 'cambia tu personaje a X', "
       "'ponte el de X'.",
       {"nombre": {"type": "string", "description": "Nombre del avatar (la carpeta)"}})
def cambiar_avatar(nombre):
    nombres = listar()
    if not nombres:
        return Fallo(f"No hay avatares en {raiz()}. Crea una carpeta con el nombre del personaje y "
                     "pon ahí sus GIF.")
    from difflib import SequenceMatcher
    mejor = max(nombres, key=lambda n: SequenceMatcher(None, skills._norm(n), skills._norm(nombre)).ratio())
    if SequenceMatcher(None, skills._norm(mejor), skills._norm(nombre)).ratio() < 0.5:
        return Fallo(f"No tengo un avatar parecido a '{nombre}'. Tengo: {', '.join(nombres)}.")
    with _lock:
        _estado["activo"] = mejor
        _estado["version"] += 1
    try:
        import memoria
        import preferencias
        preferencias._q("INSERT OR REPLACE INTO preferencias (categoria, valor, destino, actualizado) "
                        "VALUES ('avatar', ?, NULL, ?)", (mejor, memoria._ahora()), escribir=True)
    except Exception:
        pass
    threading.Thread(target=sincronizar, args=(mejor,), daemon=True).start()
    return f"Listo, ahora soy {mejor}."


@skill("listar_avatares", "Dice qué avatares (personajes animados) tiene Jarvis y cuál usa.",
       terminal=False)
def listar_avatares():
    nombres = listar()
    if not nombres:
        return f"No hay avatares todavía. Ponlos en {raiz()} (una carpeta por personaje)."
    return (f"Tengo {len(nombres)} avatares: {', '.join(nombres)}. Ahora uso {activo()}. "
            f"Están en {raiz()}.")


@skill("clasificar_gif",
       "Corrige qué acción representa un GIF del avatar (por ejemplo cuando Jarvis lo clasificó "
       "mal). Sin 'archivo' corrige el último que se agregó. Categorías: " + ", ".join(CATEGORIAS) + ".",
       {"categoria": {"type": "string", "enum": CATEGORIAS},
        "archivo": {"type": "string", "description": "Nombre del GIF (opcional: el último agregado)"}},
       requeridos=["categoria"])
def clasificar_gif(categoria, archivo=""):
    nombre = activo()
    meta = _leer_meta(nombre)
    if not meta:
        return Fallo("El avatar no tiene GIF preparados todavía.")
    if archivo:
        from difflib import SequenceMatcher
        elegido = max(meta, key=lambda a: SequenceMatcher(None, skills._norm(Path(a).stem),
                                                          skills._norm(archivo)).ratio())
    elif _estado["ultimo"] and _estado["ultimo"][0] == nombre and _estado["ultimo"][1] in meta:
        elegido = _estado["ultimo"][1]
    else:
        elegido = max(meta, key=lambda a: (meta[a].get("origen") or {}).get("mtime", 0))
    with _lock:
        meta[elegido]["categoria"] = meta[elegido]["categoria_manual"] = categoria
        _guardar_meta(nombre, meta)
        _estado["version"] += 1
    return f"Listo, {elegido} ahora es de «{categoria}»."
