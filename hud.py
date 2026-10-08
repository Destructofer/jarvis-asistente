"""HUD estilo Iron Man encima de todo (incluida la presentación en pantalla completa):

- Un Vault Boy (Fallout) en una esquina que muestra qué está haciendo Jarvis: quieto en espera,
  y animado según la acción de cada respuesta ([ACCION: categoria], ver acciones.py). El borde
  y el texto de abajo llevan el color del estado (escuchando, procesando, hablando...). Las
  animaciones están en vaultboy/<categoria>.gif; config.json → hud.estilo = "reactor" vuelve al
  reactor azul de antes (también se usa si faltan las imágenes).
- Subtítulos de lo que Jarvis dice, para que el público lo lea además de oírlo.
- Una miniatura de lo que Jarvis acaba de ver por la cámara de los lentes.

Las ventanas no se pueden activar ni reciben clics (WS_EX_NOACTIVATE + WS_EX_TRANSPARENT): no le
roban el foco a PowerPoint y los clics las atraviesan. Viven en el mismo hilo de Tk que
panel.py (Tk no tolera dos hilos de interfaz; ver el docstring de panel.py).
"""
import ctypes
import math
import random
import threading
import time
from pathlib import Path

import panel


FONDO = "#010203"          # color que Windows vuelve transparente en el reactor
COLORES = {
    "inactivo": "#1d6b86", "escuchando": "#38e1ff", "pensando": "#ffb020",
    "mirando": "#b06bff", "hablando": "#38e1ff", "error": "#ff4d5e",
}
TEXTOS = {"inactivo": "en espera", "escuchando": "escuchando", "pensando": "procesando",
          "mirando": "visión", "hablando": "hablando", "error": "error"}

_cfg = {}
_s = {"estado": "inactivo", "expositor": False, "fase": 0.0, "reactor": None, "canvas": None,
      "sub": None, "sub_lbl": None, "oido_lbl": None, "img": None, "img_lbl": None,
      "foto": None, "ocultar_sub": None, "ocultar_img": None, "oido_hasta": 0.0}
_pil_vault = {}      # archivo -> [(PIL RGBA sin fondo, ms)] (se preparan en segundo plano)
_cuadros_vault = {}  # archivo -> [(PhotoImage, ms)] (Tk solo acepta crearlas en su hilo)
# Qué Vault Boy se ve: la acción de la última respuesta (hasta 'hasta' o mientras habla) o, si
# no, el que corresponde al estado (espera, pensando...). 'inicio' marca el cuadro 0 del GIF.
# Qué Vault Boy se ve y desde cuándo. 'clave' identifica lo que se está mostrando: cuando
# cambia, se sortea el archivo (variante) y la animación empieza de su primer cuadro.
_vista = {"accion": None, "hasta": 0.0, "completado_pendiente": False, "completado_hasta": 0.0,
          "clave": None, "archivo": None, "inicio": 0.0,
          "libre_n": 0, "libre_archivo": None, "libre_hasta": 0.0, "libre_pose": False,
          "pausa_hasta": 0.0}
_descarga = {"activa": False, "fin_hasta": 0.0}
POR_ESTADO = {"escuchando": "espera", "pensando": "pensando", "mirando": "cyborg",
              "error": "confundido"}
# Animaciones con significado propio: no salen en el modo libre (al azar), para que cuando
# aparezcan signifiquen algo (un error, una descarga, algo terminado)
NO_ALEATORIAS = ("confundido", "descargando", "completado")
POSE = "espera"            # la pose inicial y rígida: entre animación y animación
PAUSA_LIBRE = (4.0, 7.0)   # s de pose entre animaciones del modo libre (al azar en ese rango)
PAUSA_CAMBIO = 1.0         # s de pose al pasar de una animación a otra distinta
ALTO_FIGURA = 0.78         # alto del muñeco, como fracción de hud.tamano: igual en TODAS
DURACION_COMPLETADO = 3.6  # s del pulgar arriba al terminar una acción o una descarga


def _conf():
    return _cfg.get("hud", {}) or {}


OBSERVADORES = []   # fn(tipo, **datos): la app de escritorio (app_servidor.publicar)


def _avisar(tipo, **datos):
    for fn in list(OBSERVADORES):
        try:
            fn(tipo, **datos)
        except Exception:
            pass


def _usa_vault():
    """El avatar animado (o el reactor azul si se eligió, o si no hay ningún avatar todavía)."""
    if _conf().get("estilo", "vaultboy") not in ("vaultboy", "avatar"):
        return False
    try:
        _refrescar_avatar()  # barato: solo compara la versión (cambia al agregar un GIF)
    except Exception:
        return False
    return _av["pose"] is not None


def _centro_x():
    """Dónde va el muñeco dentro del lienzo: pegado a la esquina. Las escenas que tienen algo
    de ese lado se recorren solas lo necesario (ver _preparar_vault)."""
    lado = int(_conf().get("tamano", 150))
    ancho, _alto = _dims()
    hacia_esquina = lado * 0.42
    return ancho - hacia_esquina if "derecha" in _conf().get("posicion", "abajo_derecha") else hacia_esquina


def _dims():
    """(ancho, alto de la imagen) del lienzo del ícono. El Vault Boy usa uno más amplio (y
    transparente) para que quepan las escenas completas alrededor del muñeco."""
    lado = int(_conf().get("tamano", 150))
    if _usa_vault():
        return int(lado * 2.6), int(lado * 1.4)
    return lado + 70, lado


def _activo():
    return bool(_conf().get("activo", True))


def _mostrar_reactor():
    return _activo() and (_s["expositor"] or not _conf().get("solo_expositor", False))


def _subtitulos_on():
    modo = _conf().get("subtitulos", "expositor")
    return _activo() and (modo == "siempre" or (modo == "expositor" and _s["expositor"]))


# ---------- Ventanas de Windows: sin foco y sin clics ----------
def _fantasma(v):
    """Hace que la ventana no se pueda activar, no reciba clics y no salga en Alt+Tab."""
    try:
        v.update_idletasks()
        hwnd = int(v.wm_frame(), 16)
        u = ctypes.windll.user32
        GWL_EXSTYLE = -20
        extra = 0x08000000 | 0x20 | 0x80 | 0x8 | 0x80000  # NOACTIVATE|TRANSPARENT|TOOLWINDOW|TOPMOST|LAYERED
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, u.GetWindowLongW(hwnd, GWL_EXSTYLE) | extra)
    except Exception as e:
        print(f"[HUD: no pude hacer la ventana transparente a clics: {e}]")


def _encima(v):
    """Reafirma 'siempre encima' sin activar la ventana (PowerPoint en pantalla completa puede
    taparla al iniciar)."""
    try:
        hwnd = int(v.wm_frame(), 16)
        # HWND_TOPMOST=-1; SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE|SWP_SHOWWINDOW
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)
    except Exception:
        pass


def _monitor():
    """(x, y, ancho, alto) del área del monitor donde conviene dibujar: el de la presentación
    si hay una en pantalla completa (el proyector), si no el principal. hud.monitor = número
    para fijar uno."""
    try:
        import win32api
        import win32gui
        elegido = _conf().get("monitor", "auto")
        monitores = [m[0] for m in win32api.EnumDisplayMonitors()]
        h = None
        ppt = win32gui.FindWindow("screenClass", None)
        if isinstance(elegido, int) and 0 <= elegido < len(monitores):
            h = monitores[elegido]
        elif ppt:
            h = win32api.MonitorFromWindow(ppt, 2)
        if h is None:
            h = win32api.MonitorFromPoint((0, 0), 1)
        # Con una presentación en pantalla completa no hay barra de tareas: se usa todo el
        # monitor. Si no, el área de trabajo, para no tapar la barra (el reloj, los iconos).
        info = win32api.GetMonitorInfo(h)
        x0, y0, x1, y1 = info["Monitor"] if ppt else info["Work"]
        return x0, y0, x1 - x0, y1 - y0
    except Exception:
        return 0, 0, 1920, 1080


# ---------- Construcción (hilo de Tk) ----------
def _crear(root):
    import tkinter as tk
    if _s["reactor"] is not None:
        return
    r = tk.Toplevel(root)
    r.overrideredirect(True)
    r.attributes("-topmost", True)
    r.configure(bg=FONDO)
    r.attributes("-transparentcolor", FONDO)
    ancho_c, alto_c = _dims()
    c = tk.Canvas(r, width=ancho_c, height=alto_c + 26, bg=FONDO, highlightthickness=0)
    c.pack()
    _s["reactor"], _s["canvas"] = r, c

    s = tk.Toplevel(root)
    s.overrideredirect(True)
    s.attributes("-topmost", True)
    s.attributes("-alpha", 0.9)
    s.configure(bg="#081018")
    marco = tk.Frame(s, bg="#081018", padx=26, pady=12, highlightthickness=2,
                     highlightbackground="#1d6b86")
    marco.pack(fill="both", expand=True)
    oido = tk.Label(marco, text="", font=("Segoe UI", 12), fg="#7fb6c9", bg="#081018",
                    anchor="w", justify="left")
    oido.pack(fill="x")
    lbl = tk.Label(marco, text="", font=("Segoe UI Semibold", int(_conf().get("letra", 22))),
                   fg="#eaf9ff", bg="#081018", justify="center")
    lbl.pack(fill="both", expand=True)
    s.withdraw()
    _s["sub"], _s["sub_lbl"], _s["oido_lbl"] = s, lbl, oido

    i = tk.Toplevel(root)
    i.overrideredirect(True)
    i.attributes("-topmost", True)
    i.attributes("-alpha", 0.97)  # hace la ventana "layered" (necesario para _fantasma)
    i.configure(bg="#38e1ff")
    il = tk.Label(i, bg="#081018", bd=0)
    il.pack(padx=2, pady=2)
    i.withdraw()
    _s["img"], _s["img_lbl"] = i, il

    for v in (r, s, i):
        _fantasma(v)
    _colocar()
    _animar(root)
    # Quitar el fondo de todas las animaciones lleva unos segundos: se hace en otro hilo al
    # arrancar, para que la primera vez que salga cada una no haya un tirón
    threading.Thread(target=_precargar_vault, daemon=True, name="hud-vaultboy").start()


def _colocar():
    x, y, w, h = _monitor()
    ancho_r, alto_r = _dims()
    esquina = _conf().get("posicion", "abajo_derecha")
    derecha, abajo = "derecha" in esquina, "abajo" in esquina
    margen = 6 if _usa_vault() else 24  # el Vault Boy, pegado a la esquina
    rx = x + w - ancho_r - margen if derecha else x + margen
    ry = y + h - alto_r - 26 - margen if abajo else y + margen
    r = _s["reactor"]
    if r is not None:
        r.geometry(f"+{rx}+{ry}")
        if _mostrar_reactor():
            r.deiconify()
            _encima(r)
        else:
            r.withdraw()
    # La miniatura de la cámara va junto al reactor; los subtítulos usan el espacio libre que
    # queda del otro lado, para no encimarse nunca.
    reservado = ancho_r + margen
    i = _s["img"]
    if i is not None and i.state() != "withdrawn":
        i.update_idletasks()
        iw, ih = i.winfo_reqwidth(), i.winfo_reqheight()
        ix = rx - iw - 12 if derecha else rx + ancho_r + 12
        iy = y + h - ih - margen if abajo else y + margen
        i.geometry(f"+{ix}+{iy}")
        _encima(i)
        reservado += iw + 12
    s = _s["sub"]
    if s is not None and s.state() != "withdrawn":
        libre_x0 = x + margen + (0 if derecha else reservado)
        libre_ancho = w - reservado - 2 * margen
        ancho_max = max(360, min(int(w * 0.7), libre_ancho))
        _s["sub_lbl"].configure(wraplength=ancho_max - 60)
        s.update_idletasks()
        sw = min(ancho_max, max(420, s.winfo_reqwidth()))
        sh = s.winfo_reqheight()
        sx = libre_x0 + (libre_ancho - sw) // 2
        sy = y + h - sh - margen if abajo else y + margen
        s.geometry(f"{sw}x{sh}+{sx}+{sy}")
        _encima(s)


def _animar(root):
    c = _s["canvas"]
    if c is None:
        return
    est = _s["estado"]
    color = COLORES.get(est, COLORES["inactivo"])
    lado = int(_conf().get("tamano", 150))
    if _conf().get("estilo", "vaultboy") == "vaultboy":
        animado = _dibujar_vault(c, est, color, lado)
        if animado is not None:  # None = faltan las imágenes: se dibuja el reactor
            _limpiar_oido()
            # Animado: ~25 cuadros por segundo; quieto basta revisar ~8 veces por segundo
            root.after(40 if animado else 120, lambda: _animar(root))
            return
    cx, cy = (lado + 70) / 2, lado / 2
    vel = {"inactivo": 0.6, "escuchando": 2.2, "pensando": 6.0, "mirando": 4.0,
           "hablando": 2.8, "error": 0.0}.get(est, 1.0)
    _s["fase"] = (_s["fase"] + vel) % 360
    f = _s["fase"]
    c.delete("all")
    R = lado / 2 - 6
    # anillo exterior y marcas
    c.create_oval(cx - R, cy - R, cx + R, cy + R, outline=color, width=2)
    for k in range(24):
        a = math.radians(k * 15 + f * 0.3)
        r1, r2 = R - 6, R - (12 if k % 3 == 0 else 8)
        c.create_line(cx + r1 * math.cos(a), cy + r1 * math.sin(a),
                      cx + r2 * math.cos(a), cy + r2 * math.sin(a), fill=color, width=2)
    # arcos que giran en sentidos opuestos
    for radio, ext, sentido, ancho in ((R - 18, 100, 1, 5), (R - 18, 60, 1, 5),
                                       (R - 30, 140, -1, 3), (R - 40, 70, -1, 2)):
        inicio = (f * sentido * (1.4 if ancho < 4 else 1)) % 360
        if ext == 60:
            inicio += 180
        c.create_arc(cx - radio, cy - radio, cx + radio, cy + radio, start=inicio, extent=ext,
                     style="arc", outline=color, width=ancho)
    # núcleo que late (más fuerte al hablar)
    pulso = 1 + (0.18 * math.sin(time.time() * 9) if est == "hablando" else
                 0.06 * math.sin(time.time() * 3))
    nucleo = (R - 52) * pulso
    c.create_oval(cx - nucleo, cy - nucleo, cx + nucleo, cy + nucleo, outline=color, width=3)
    c.create_oval(cx - nucleo * 0.55, cy - nucleo * 0.55, cx + nucleo * 0.55, cy + nucleo * 0.55,
                  fill=color, outline="")
    nombre = _cfg.get("name", "Jarvis").upper()
    c.create_text(cx, lado + 12, text=f"{nombre} · {TEXTOS.get(est, est)}", fill=color,
                  font=("Consolas", 10, "bold"))
    _limpiar_oido()
    # En reposo basta ~6 cuadros por segundo: redibujar ~35 figuras 11 veces por segundo todo el
    # día le quitaba CPU a la voz y a la realidad aumentada sin que se notara la diferencia
    root.after(40 if est != "inactivo" else 160, lambda: _animar(root))


# ---------- API (se puede llamar desde cualquier hilo) ----------
def _ui(fn):
    if _activo():
        panel._ui(fn)


def iniciar(cfg):
    global _cfg
    _cfg = cfg
    _ui(_crear)
    if _s.get("reafirmando"):
        return  # main() se reinicia si algo falla: un solo ciclo de "reafirmar" basta
    _s["reafirmando"] = True

    def reafirmar(root):  # cada 3 s: vuelve a ponerse encima y al monitor correcto
        if _s["reactor"] is not None:
            _colocar()
        root.after(3000, lambda: reafirmar(root))
    _ui(reafirmar)


def estado(nombre):
    anterior, _s["estado"] = _s["estado"], nombre
    _avisar("estado", estado=nombre)
    if nombre in ("escuchando", "pensando"):
        # orden nueva: se deja de mostrar lo de la anterior
        _vista.update(accion=None, completado_pendiente=False, completado_hasta=0.0)
    elif anterior == "hablando" and nombre == "inactivo":
        if _vista["completado_pendiente"]:
            _vista["hasta"] = 0.0  # terminó de hablar: pasa directo al pulgar arriba
        else:  # que la acción siga unos segundos después de terminar de hablar
            _vista["hasta"] = max(_vista["hasta"], time.time() + 3)


def descarga(activa):
    """La llama descargas.py: mientras se baja algo se ve el costal; al terminar, pulgar arriba."""
    if _descarga["activa"] and not activa:
        _descarga["fin_hasta"] = time.time() + DURACION_COMPLETADO
    _descarga["activa"] = bool(activa)


def descarga_terminada():
    """Una descarga tan rápida que no alcanzó a verse en curso: solo el pulgar arriba."""
    _descarga["fin_hasta"] = time.time() + DURACION_COMPLETADO


def modo_expositor(activo):
    _s["expositor"] = bool(activo)
    _ui(lambda _r: _colocar())


def oido(texto):
    """Muestra en pequeño lo que Jarvis entendió (el público ve qué se le pidió)."""
    if texto:
        _avisar("oido", texto=texto)
    if not _subtitulos_on() or not texto:
        return

    def hacer(_r):
        if _s["oido_lbl"] is None:
            return
        _s["oido_lbl"].configure(text=f"» {texto[:90]}")
        _s["oido_hasta"] = time.time() + 6
        if _s["sub"].state() == "withdrawn":
            _s["sub_lbl"].configure(text="")
            _s["sub"].deiconify()
        _colocar()
    _ui(hacer)


def subtitulo(texto):
    if texto:
        _avisar("dice", texto=texto)
    if not _subtitulos_on() or not texto:
        return

    def hacer(root):
        if _s["sub"] is None:
            return
        if _s["ocultar_sub"]:
            root.after_cancel(_s["ocultar_sub"])
            _s["ocultar_sub"] = None
        _s["sub_lbl"].configure(text=texto)
        _s["sub"].deiconify()
        _colocar()
    _ui(hacer)


def fin_subtitulo(retraso_ms=2500):
    def hacer(root):
        if _s["sub"] is None:
            return

        def ocultar():
            _s["ocultar_sub"] = None
            _s["sub"].withdraw()
            _s["oido_lbl"].configure(text="")
        if _s["ocultar_sub"]:
            root.after_cancel(_s["ocultar_sub"])
        _s["ocultar_sub"] = root.after(retraso_ms, ocultar)
    _ui(hacer)


def mostrar_imagen(ruta, segundos=9):
    if not _activo() or not _conf().get("mostrar_vista", True):
        return

    def hacer(root):
        if _s["img"] is None:
            return
        try:
            from PIL import Image, ImageTk
            im = Image.open(ruta)
            im.thumbnail((int(_conf().get("ancho_vista", 280)), 400))
            _s["foto"] = ImageTk.PhotoImage(im)  # hay que guardar la referencia o Tk la borra
            _s["img_lbl"].configure(image=_s["foto"])
        except Exception as e:
            print(f"[HUD: no pude mostrar la vista: {e}]")
            return
        if _s["ocultar_img"]:
            root.after_cancel(_s["ocultar_img"])
        _s["img"].deiconify()
        _colocar()
        _s["ocultar_img"] = root.after(segundos * 1000, _s["img"].withdraw)
    _ui(hacer)


# ---------- Vault Boy ----------
def _limpiar_oido():
    if _s["oido_lbl"] is not None and _s["oido_hasta"] and time.time() > _s["oido_hasta"]:
        _s["oido_lbl"].configure(text="")
        _s["oido_hasta"] = 0.0


# ---------- El avatar (avatares.py) ----------
# Las animaciones vienen de Documentos\Jarvis\Avatares\<personaje>\ ya preparadas por
# avatares.py (sin fondo, medidas y clasificadas). Aquí solo se colocan y se dibujan.
_av = {"version": None, "por_cat": {}, "todas": [], "pose": None, "figura": {}}


def _refrescar_avatar():
    """Relee el avatar si cambió (un GIF nuevo, otro personaje): descarta lo ya cargado."""
    import avatares
    v = (avatares.version(), avatares.activo())
    if v == _av["version"]:
        return
    por_cat, figura = {}, {}
    for cat in avatares.CATEGORIAS:
        lista = avatares.entradas(cat)
        por_cat[cat] = [r for r, _f in lista]
        figura.update({r: f for r, f in lista})
    pose = avatares.pose()
    if pose:
        figura[pose[0]] = pose[1]
    _av.update(version=v, por_cat=por_cat, figura=figura, pose=pose[0] if pose else None,
               todas=[r for lista in por_cat.values() for r in lista])
    _pil_vault.clear()
    _cuadros_vault.clear()
    _vista.update(clave=None, archivo=None, libre_archivo=None)


def _archivos_vault(categoria):
    """Las animaciones de esa categoría (si hay varias, se elige una al azar)."""
    return list(_av["por_cat"].get(categoria, []))


def _preparar_vault(ruta):
    """Cuadros escalados para que el PERSONAJE mida siempre lo mismo y quede parado en la misma
    línea (no la escena: en el laboratorio o la mina hay objetos alrededor), con la posición en
    el lienzo. Sin Tk: sirve en otro hilo. Devuelve {"x", "y", "cuadros", "dura"}."""
    clave = str(ruta)
    if clave in _pil_vault:
        return _pil_vault[clave]
    from PIL import Image, ImageSequence
    lado = int(_conf().get("tamano", 150))
    ancho_c, alto_c = _dims()
    base = alto_c - int(lado * 0.2)         # línea de los pies (deja aire para lo que va más abajo)
    cuadros = [(fr.convert("RGBA").copy(), max(20, int(fr.info.get("duration", 60) or 60)))
               for fr in ImageSequence.Iterator(Image.open(ruta))]
    fig = _av["figura"].get(ruta) or {}
    alto = float(fig.get("alto") or cuadros[0][0].height)
    pies = float(fig.get("pies") or cuadros[0][0].height)
    centro = float(fig.get("centro") or cuadros[0][0].width / 2)
    k = (lado * ALTO_FIGURA) / max(1.0, alto)
    caja = (0, 0) + cuadros[0][0].size   # ya viene recortada al dibujo
    ox, oy = _centro_x() - centro * k, base - pies * k  # dónde cae el (0, 0) del GIF en el lienzo
    # El personaje va pegado a la esquina; si la escena tiene algo de ese lado (el cyborg, la
    # puerta, las rocas), la animación entera se recorre lo justo para que quepa
    if (caja[2] - caja[0]) * k <= ancho_c - 4:
        ox -= max(0.0, ox + caja[2] * k - (ancho_c - 2))
        ox += max(0.0, 2 - (ox + caja[0] * k))
    caja = (max(caja[0], int(-ox / k)), max(caja[1], int(-oy / k)),
            min(caja[2], int((ancho_c - ox) / k) + 1), min(caja[3], int((alto_c - oy) / k) + 1))
    lista = []
    for f, ms in cuadros:
        f = f.crop(caja)
        f = f.resize((max(1, int(f.width * k)), max(1, int(f.height * k))), Image.LANCZOS)
        # transparencia de sí o no: con la ventana de color-clave de Windows, un borde a medio
        # transparentar se vería como una orilla negra
        f.putalpha(f.getchannel("A").point(lambda a: 255 if a > 128 else 0))
        lista.append((f, ms))
    datos = {"x": int(ox + caja[0] * k), "y": int(oy + caja[1] * k), "cuadros": lista,
             "dura": sum(ms for _, ms in lista) / 1000}
    _pil_vault[clave] = datos
    return datos


def _precargar_vault():
    try:
        _refrescar_avatar()
    except Exception as e:
        print(f"[HUD: no pude leer el avatar: {e}]")
        return
    for ruta in list(_av["todas"]) + ([_av["pose"]] if _av["pose"] else []):
        try:
            _preparar_vault(ruta)
        except Exception as e:
            print(f"[HUD: no pude preparar {Path(ruta).name}: {e}]")


def _cargar_vault(ruta):
    """{"x", "y", "cuadros": [(PhotoImage, ms)]} de una animación (solo en el hilo de Tk)."""
    if ruta is None:
        return None
    clave = str(ruta)
    if clave not in _cuadros_vault:
        try:
            from PIL import ImageTk
            datos = _preparar_vault(ruta)
            _cuadros_vault[clave] = dict(
                datos, cuadros=[(ImageTk.PhotoImage(f), ms) for f, ms in datos["cuadros"]])
            datos["cuadros"] = []  # ya está en Tk: la copia en PIL solo gastaba memoria
        except Exception as e:
            print(f"[HUD: no pude cargar {Path(ruta).name}: {e}]")
            _cuadros_vault[clave] = None
    return _cuadros_vault[clave]


def _duracion(archivo):
    """Segundos que dura una vuelta de la animación (si ya está preparada)."""
    datos = _pil_vault.get(str(archivo)) if archivo else None
    return datos["dura"] if datos else 4.0


def _pose():
    return _av["pose"]


_baraja = []  # animaciones del modo libre que faltan por salir en esta vuelta


def _libres():
    """Todo lo que puede salir en el modo libre: cualquier GIF del avatar (también los de
    categorías que casi nunca se usan para una acción, y las poses de 'espera' además de la
    principal). Solo se reservan los que nombraste con una categoría de significado propio
    (confundido.gif, descargando.gif, completado.gif): esos salen únicamente cuando pasa eso.
    Los que Jarvis clasificó solo en esas categorías también salen entre ratos."""
    import avatares
    return [a for cat, lista in _av["por_cat"].items() for a in lista
            if a != _pose() and not (cat in NO_ALEATORIAS
                                     and avatares.categoria_por_nombre(Path(a).stem) == cat)]


def _sacar_de_la_baraja(ultimo):
    """Como una baraja: salen todas una vez, en orden al azar, antes de repetir alguna (con
    random.choice cada vez, unas se repetían y otras casi no salían)."""
    disponibles = set(_libres())
    _baraja[:] = [a for a in _baraja if a in disponibles]  # se borró un GIF o cambió el avatar
    if not _baraja:
        _baraja.extend(disponibles)
        random.shuffle(_baraja)
        if len(_baraja) > 1 and _baraja[-1] == ultimo:  # que no repita la que acaba de salir
            _baraja.insert(0, _baraja.pop())
    return _baraja.pop() if _baraja else None


def _siguiente_libre(ahora):
    """Modo libre (Jarvis sin nada que hacer): alterna la pose rígida unos segundos con una
    animación al azar, para que se vea vivo pero sin encadenar animaciones de corrido."""
    v = _vista
    if not v["libre_pose"]:  # después de una animación (o al empezar): la pose
        archivo, dura = _pose(), random.uniform(*PAUSA_LIBRE)
    else:
        archivo = _sacar_de_la_baraja(v["libre_archivo"])
        if archivo is None:
            archivo, dura = _pose(), random.uniform(*PAUSA_LIBRE)
        else:
            vuelta = _duracion(archivo)
            dura = min(7.0, max(2.5, vuelta * (2 if vuelta < 2.5 else 1)))
    v["libre_pose"] = archivo == _pose()
    v["libre_n"] += 1
    v["libre_archivo"], v["libre_hasta"] = archivo, ahora + dura


def _que_mostrar(est, ahora):
    """(clave, categoría, archivo fijo o None), por prioridad: la acción de la respuesta, el
    pulgar arriba al terminar, el estado (procesando, mirando, error), una descarga en curso,
    y si no hay nada de eso, el modo libre."""
    v = _vista
    if v["accion"]:
        if est == "hablando" or ahora < v["hasta"]:
            return f"accion:{v['accion']}", v["accion"], None
        if v["completado_pendiente"]:  # terminó lo que hizo: pulgar arriba
            v["completado_pendiente"] = False
            v["completado_hasta"] = ahora + DURACION_COMPLETADO
        v["accion"] = None
        v["libre_pose"] = False  # al volver al modo libre, primero la pose
    if ahora < v["completado_hasta"]:
        return "completado", "completado", None
    if est in POR_ESTADO:
        return f"estado:{est}", POR_ESTADO[est], (_pose() if POR_ESTADO[est] == POSE else None)
    if _descarga["activa"]:
        return "descargando", "descargando", None
    if ahora < _descarga["fin_hasta"]:
        return "descarga-lista", "completado", None
    if not _conf().get("vault_aleatorio", True):
        return "pose", POSE, _pose()
    if v["libre_archivo"] is None or ahora >= v["libre_hasta"]:
        _siguiente_libre(ahora)
    return f"libre:{v['libre_n']}", POSE, v["libre_archivo"]


def _dibujar_vault(c, est, color, lado):
    """Dibuja el Vault Boy que toca en el lugar del reactor, sin fondo. Devuelve True si está
    animado, False si es una imagen quieta, o None si no hay imágenes (se dibuja el reactor)."""
    if not _usa_vault():
        return None
    _refrescar_avatar()  # un GIF nuevo o el cambio de personaje se ven sin reiniciar
    ahora = time.time()
    clave, cat, fijo = _que_mostrar(est, ahora)
    if clave != _vista["clave"]:
        opciones = _archivos_vault(cat) or [_pose()]
        nuevo = fijo or random.choice(opciones)
        # De una animación a otra distinta: un momento de pose rígida entre las dos
        if (_vista["archivo"] not in (None, _pose()) and nuevo not in (None, _pose())
                and nuevo != _vista["archivo"]):
            _vista["pausa_hasta"] = ahora + PAUSA_CAMBIO
        _vista["clave"], _vista["archivo"] = clave, nuevo
        _vista["inicio"] = max(ahora, _vista["pausa_hasta"])
    archivo = _pose() if ahora < _vista["pausa_hasta"] else _vista["archivo"]
    datos = _cargar_vault(archivo) or _cargar_vault(_pose())
    if not datos or not datos["cuadros"]:
        return None
    cuadros = datos["cuadros"]
    if len(cuadros) > 1:  # cuadro según el tiempo transcurrido (respeta la duración de cada uno)
        t = max(0.0, ahora - _vista["inicio"]) * 1000 % sum(ms for _, ms in cuadros)
        for foto, ms in cuadros:
            if t < ms:
                break
            t -= ms
    else:
        foto = cuadros[0][0]
    ancho, alto = _dims()
    texto = f"{_cfg.get('name', 'Jarvis').upper()} · {TEXTOS.get(est, est)}"
    # Los elementos del lienzo se crean UNA vez y solo se actualiza lo que cambió: borrar y
    # recrear todo 25 veces por segundo (aunque fuera la misma imagen) era lo que más procesador
    # gastaba Jarvis en reposo
    items = _s.get("vb_items")
    if not items or not c.find_withtag(items[0]):
        c.delete("all")
        cx = min(max(_centro_x(), 82), ancho - 82)  # el texto, debajo del muñeco
        items = (c.create_image(datos["x"], datos["y"], image=foto, anchor="nw"),
                 # sombra: el texto no tiene recuadro detrás y debe leerse sobre cualquier fondo
                 c.create_text(cx + 1, alto + 13, text=texto, fill="#000000", font=("Consolas", 9, "bold")),
                 c.create_text(cx, alto + 12, text=texto, fill=color, font=("Consolas", 9, "bold")))
        _s["vb_items"], _s["vb_ultimo"] = items, (foto, datos["x"], datos["y"], texto, color)
    else:
        ultimo = _s.get("vb_ultimo") or (None,) * 5
        if (foto, datos["x"], datos["y"]) != ultimo[:3]:
            if (datos["x"], datos["y"]) != ultimo[1:3]:
                c.coords(items[0], datos["x"], datos["y"])
            c.itemconfigure(items[0], image=foto)
        if (texto, color) != ultimo[3:]:
            c.itemconfigure(items[1], text=texto)
            c.itemconfigure(items[2], text=texto, fill=color)
        _s["vb_ultimo"] = (foto, datos["x"], datos["y"], texto, color)
    return len(cuadros) > 1 or ahora < _vista["pausa_hasta"]


def accion(categoria, segundos=None, completado=False):
    """Pone el Vault Boy de esa acción: se ve mientras Jarvis habla y unos segundos más (o los
    segundos indicados). Con completado=True (se hizo algo con una herramienta y salió bien),
    al terminar sale el pulgar arriba. Después vuelve al modo libre."""
    if not categoria:
        return
    _avisar("accion", categoria=categoria)
    _vista.update(accion=categoria, hasta=time.time() + (segundos or 5),
                  completado_pendiente=bool(completado), completado_hasta=0.0)
    _vista["clave"] = None  # aunque se repita la categoría, que vuelva a sortear la variante
