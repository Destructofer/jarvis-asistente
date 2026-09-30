"""HUD estilo Iron Man encima de todo (incluida la presentación en pantalla completa):

- Un "reactor" animado en una esquina que muestra qué está haciendo Jarvis: en espera,
  escuchando, pensando, mirando o hablando.
- Subtítulos de lo que Jarvis dice, para que el público lo lea además de oírlo.
- Una miniatura de lo que Jarvis acaba de ver por la cámara de los lentes.

Las ventanas no se pueden activar ni reciben clics (WS_EX_NOACTIVATE + WS_EX_TRANSPARENT): no le
roban el foco a PowerPoint y los clics las atraviesan. Viven en el mismo hilo de Tk que
panel.py (Tk no tolera dos hilos de interfaz; ver el docstring de panel.py).
"""
import ctypes
import math
import time

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


def _conf():
    return _cfg.get("hud", {}) or {}


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
        if isinstance(elegido, int) and 0 <= elegido < len(monitores):
            h = monitores[elegido]
        else:
            ppt = win32gui.FindWindow("screenClass", None)
            if ppt:
                h = win32api.MonitorFromWindow(ppt, 2)
        if h is None:
            h = win32api.MonitorFromPoint((0, 0), 1)
        x0, y0, x1, y1 = win32api.GetMonitorInfo(h)["Monitor"]
        return x0, y0, x1 - x0, y1 - y0
    except Exception:
        return 0, 0, 1920, 1080


# ---------- Construcción (hilo de Tk) ----------
def _crear(root):
    import tkinter as tk
    if _s["reactor"] is not None:
        return
    lado = int(_conf().get("tamano", 150))
    r = tk.Toplevel(root)
    r.overrideredirect(True)
    r.attributes("-topmost", True)
    r.configure(bg=FONDO)
    r.attributes("-transparentcolor", FONDO)
    c = tk.Canvas(r, width=lado + 70, height=lado + 26, bg=FONDO, highlightthickness=0)
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


def _colocar():
    x, y, w, h = _monitor()
    lado = int(_conf().get("tamano", 150))
    ancho_r = lado + 70
    esquina = _conf().get("posicion", "abajo_derecha")
    derecha, abajo = "derecha" in esquina, "abajo" in esquina
    margen = 24
    rx = x + w - ancho_r - margen if derecha else x + margen
    ry = y + h - lado - 26 - margen if abajo else y + margen
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
    if _s["oido_lbl"] is not None and _s["oido_hasta"] and time.time() > _s["oido_hasta"]:
        _s["oido_lbl"].configure(text="")
        _s["oido_hasta"] = 0.0
    root.after(40 if est != "inactivo" else 90, lambda: _animar(root))


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
    _s["estado"] = nombre


def modo_expositor(activo):
    _s["expositor"] = bool(activo)
    _ui(lambda _r: _colocar())


def oido(texto):
    """Muestra en pequeño lo que Jarvis entendió (el público ve qué se le pidió)."""
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
