"""Ventanas de Genesis: panel con botones (mantenimiento), caja para escribir una orden y
pregunta de Sí/No para confirmar.

Todas viven en UN solo hilo con UNA sola instancia de Tkinter. Antes cada ventana creaba su
propio Tk() en un hilo nuevo, y Tcl no lo tolera: al liberar una de esas instancias desde otro
hilo, el proceso entero abortaba ("Tcl_AsyncDelete: async handler deleted by the wrong
thread"), sin excepción que el reinicio automático pudiera atrapar. Ahora las funciones
públicas solo dejan un pedido en una cola y el hilo de la interfaz es el único que toca Tk.
"""
import os
import queue
import sys
import threading
from pathlib import Path


def _arreglar_tcl():
    """Algunas instalaciones de Python 3.13 en Windows (sobre todo dentro de un .venv) no
    encuentran la carpeta de Tcl/Tk y Tkinter truena con "Can't find a usable init.tcl".
    La carpeta sí existe en la instalación base (Python313\\tcl\\tcl8.6): se le indica a mano."""
    if os.environ.get("TCL_LIBRARY"):
        return
    bases = {sys.base_prefix, sys.prefix, os.path.dirname(sys.executable)}
    for base in bases:
        tcl = Path(base) / "tcl"
        if not tcl.is_dir():
            continue
        for d in sorted(tcl.glob("tcl[89]*")):
            if (d / "init.tcl").is_file():
                os.environ["TCL_LIBRARY"] = str(d)
                tk_dirs = sorted(tcl.glob("tk[89]*"))
                if tk_dirs:
                    os.environ["TK_LIBRARY"] = str(tk_dirs[0])
                return


_arreglar_tcl()
import tkinter as tk  # noqa: E402  (después de _arreglar_tcl)

FUENTE = "Segoe UI"
BG = "#1b1c22"
TEXTO = "#eceef5"
TEXTO_SUAVE = "#9497ab"
ACENTO = "#5b7fff"
ACENTO_HOVER = "#4a6ae6"
OK = "#34c17a"
ERROR = "#e0556b"
ANCHO = 380

_pedidos = queue.Queue()   # funciones a ejecutar dentro del hilo de la interfaz
_arrancado = threading.Lock()
_hilo = None


def _ui(fn):
    """Ejecuta fn(root) en el hilo de la interfaz (lo arranca la primera vez)."""
    global _hilo
    with _arrancado:
        if _hilo is None or not _hilo.is_alive():
            _hilo = threading.Thread(target=_bucle, daemon=True, name="interfaz-genesis")
            _hilo.start()
    _pedidos.put(fn)


def _bucle():
    root = tk.Tk()
    root.withdraw()  # raíz invisible: cada ventana real es un Toplevel suyo

    def atender():
        while True:
            try:
                fn = _pedidos.get_nowait()
            except queue.Empty:
                break
            try:
                fn(root)
            except Exception as e:
                print(f"[Error en la interfaz: {type(e).__name__}: {str(e)[:120]}]")
        root.after(50, atender)

    atender()
    root.mainloop()


def _en_hilo_trabajo(fn, al_terminar):
    """Corre fn() fuera de la interfaz (para no congelarla) y entrega el resultado de vuelta
    al hilo de la interfaz por la cola, que es el único que puede tocar los widgets."""
    def trabajar():
        try:
            import pythoncom  # las acciones pueden usar COM (pywinauto al cerrar apps)
            pythoncom.CoInitialize()
        except Exception:
            pass
        try:
            resultado, ok = fn(), True
        except Exception as e:
            resultado, ok = f"No pude hacerlo: {type(e).__name__}", False
        _pedidos.put(lambda _root: al_terminar(resultado, ok))
    threading.Thread(target=trabajar, daemon=True).start()


def _ventana(root, titulo):
    v = tk.Toplevel(root)
    v.title(titulo)
    v.configure(bg=BG)
    v.attributes("-topmost", True)
    v.resizable(False, False)
    return v


def _colocar(v, cont, ancho, x=None, y=70):
    v.update_idletasks()
    alto = cont.winfo_reqheight() + 8
    x = v.winfo_screenwidth() - ancho - 36 if x is None else x
    v.geometry(f"{ancho}x{alto}+{x}+{y}")


def _al_frente(v, widget=None):
    v.lift()
    v.focus_force()
    if widget is not None:
        widget.focus_force()


class Manija:
    """Para cerrar desde fuera una ventana abierta (p. ej. la orden llegó por voz)."""

    def __init__(self):
        self._cerrar = threading.Event()

    def cerrar(self):
        self._cerrar.set()


def _vigilar_cierre(v, manija, al_cerrar):
    def revisar():
        if not v.winfo_exists():
            return
        if manija._cerrar.is_set():
            al_cerrar()
            return
        v.after(100, revisar)
    revisar()


# ---------- Panel con botones ----------
def mostrar(titulo, resumen, opciones, on_cerrar=None):
    """opciones: lista de {"etiqueta": str, "accion": callable() -> str}. Cada botón corre su
    acción aparte y muestra el resultado sin cerrar la ventana."""
    _ui(lambda root: _panel(root, titulo, resumen, opciones, on_cerrar))


def _panel(root, titulo, resumen, opciones, on_cerrar):
    v = _ventana(root, titulo)
    cont = tk.Frame(v, bg=BG, padx=22, pady=20)
    cont.pack(fill="both", expand=True)
    tk.Label(cont, text=titulo, font=(FUENTE, 14, "bold"), fg=TEXTO, bg=BG,
             anchor="w", justify="left", wraplength=ANCHO - 44).pack(fill="x")
    tk.Label(cont, text=resumen, font=(FUENTE, 10), fg=TEXTO_SUAVE, bg=BG,
             anchor="w", justify="left", wraplength=ANCHO - 44).pack(fill="x", pady=(6, 18))

    def click(opcion, boton, lbl):
        boton.configure(state="disabled", bg="#33354a", cursor="arrow")
        lbl.configure(text="Un momento…", fg=TEXTO_SUAVE)

        def terminado(resultado, ok):
            if not v.winfo_exists():
                return
            boton.configure(bg=OK if ok else ERROR)
            lbl.configure(text=resultado, fg=TEXTO)
            _colocar(v, cont, ANCHO)

        _en_hilo_trabajo(opcion["accion"], terminado)

    for opcion in opciones:
        fila = tk.Frame(cont, bg=BG)
        fila.pack(fill="x", pady=5)
        b = tk.Button(fila, text=opcion["etiqueta"], font=(FUENTE, 10), fg="white", bg=ACENTO,
                      activebackground=ACENTO_HOVER, activeforeground="white", relief="flat",
                      bd=0, padx=14, pady=10, cursor="hand2", anchor="w")
        b.pack(fill="x")
        lbl = tk.Label(fila, text="", font=(FUENTE, 9), bg=BG, anchor="w", justify="left",
                       wraplength=ANCHO - 44)
        lbl.pack(fill="x", pady=(4, 0))
        b.configure(command=lambda o=opcion, btn=b, l=lbl: click(o, btn, l))
        b.bind("<Enter>", lambda _e, btn=b: btn.configure(bg=ACENTO_HOVER)
               if btn["state"] != "disabled" else None)
        b.bind("<Leave>", lambda _e, btn=b: btn.configure(bg=ACENTO)
               if btn["state"] != "disabled" else None)

    def cerrar():
        if on_cerrar:
            try:
                on_cerrar()
            except Exception:
                pass
        v.destroy()

    tk.Button(cont, text="Ignorar por ahora", font=(FUENTE, 9), fg=TEXTO_SUAVE, bg=BG,
              activebackground=BG, activeforeground=TEXTO, relief="flat", bd=0, cursor="hand2",
              command=cerrar).pack(pady=(16, 0))
    v.protocol("WM_DELETE_WINDOW", cerrar)
    _colocar(v, cont, ANCHO)


# ---------- Caja para escribir una orden ----------
def pedir_texto(titulo, pista, al_enviar, al_cancelar, oculto=False):
    """Enter envía (al_enviar(texto)); Esc o la X cancelan (al_cancelar()). Devuelve una Manija.
    oculto=True: lo que se escribe se ve como puntos (contraseñas)."""
    m = Manija()
    _ui(lambda root: _caja_texto(root, m, titulo, pista, al_enviar, al_cancelar, oculto))
    return m


def _caja_texto(root, m, titulo, pista, al_enviar, al_cancelar, oculto=False):
    ancho = 520
    v = _ventana(root, titulo)
    cont = tk.Frame(v, bg=BG, padx=20, pady=16)
    cont.pack(fill="both", expand=True)
    tk.Label(cont, text=titulo, font=(FUENTE, 12, "bold"), fg=TEXTO, bg=BG, anchor="w").pack(fill="x")
    tk.Label(cont, text=pista, font=(FUENTE, 9), fg=TEXTO_SUAVE, bg=BG, anchor="w").pack(fill="x", pady=(2, 10))
    entrada = tk.Entry(cont, font=(FUENTE, 12), bg="#262833", fg=TEXTO, insertbackground=TEXTO,
                       relief="flat", highlightthickness=1, highlightbackground="#3a3d52",
                       highlightcolor=ACENTO, show="•" if oculto else "")
    entrada.pack(fill="x", ipady=8)
    terminado = [False]

    def enviar(_e=None):
        texto = entrada.get().strip()
        if not texto or terminado[0]:
            return
        terminado[0] = True
        al_enviar(texto)
        v.destroy()

    def cancelar(_e=None):
        if not terminado[0]:
            terminado[0] = True
            al_cancelar()
        v.destroy()

    def cerrar_externo():
        terminado[0] = True
        v.destroy()

    entrada.bind("<Return>", enviar)
    v.bind("<Escape>", cancelar)
    v.protocol("WM_DELETE_WINDOW", cancelar)
    _colocar(v, cont, ancho, x=(v.winfo_screenwidth() - ancho) // 2, y=140)
    v.after(60, lambda: _al_frente(v, entrada))
    _vigilar_cierre(v, m, cerrar_externo)


# ---------- Pregunta de Sí / No ----------
def preguntar(titulo, pregunta, al_responder):
    """al_responder(True/False) con el botón pulsado; la X o Esc cuentan como No. Devuelve una
    Manija (se cierra desde fuera si la respuesta llegó por voz)."""
    m = Manija()
    _ui(lambda root: _si_no(root, m, titulo, pregunta, al_responder))
    return m


def _si_no(root, m, titulo, pregunta, al_responder):
    ancho = 440
    v = _ventana(root, titulo)
    cont = tk.Frame(v, bg=BG, padx=20, pady=16)
    cont.pack(fill="both", expand=True)
    tk.Label(cont, text=titulo, font=(FUENTE, 12, "bold"), fg=TEXTO, bg=BG, anchor="w").pack(fill="x")
    tk.Label(cont, text=pregunta, font=(FUENTE, 10), fg=TEXTO, bg=BG, anchor="w", justify="left",
             wraplength=ancho - 40).pack(fill="x", pady=(6, 4))
    tk.Label(cont, text="Responde con un botón o di sí / no", font=(FUENTE, 9), fg=TEXTO_SUAVE,
             bg=BG, anchor="w").pack(fill="x", pady=(0, 12))
    fila = tk.Frame(cont, bg=BG)
    fila.pack(fill="x")
    terminado = [False]

    def responder(valor):
        if not terminado[0]:
            terminado[0] = True
            al_responder(valor)
        v.destroy()

    def cerrar_externo():
        terminado[0] = True
        v.destroy()

    si = tk.Button(fila, text="Sí", font=(FUENTE, 10, "bold"), fg="white", bg=ACENTO,
                   activebackground=ACENTO_HOVER, activeforeground="white", relief="flat", bd=0,
                   padx=22, pady=8, cursor="hand2", command=lambda: responder(True))
    si.pack(side="left")
    tk.Button(fila, text="No", font=(FUENTE, 10), fg=TEXTO, bg="#33354a",
              activebackground="#3d4057", activeforeground=TEXTO, relief="flat", bd=0,
              padx=22, pady=8, cursor="hand2", command=lambda: responder(False)).pack(side="left", padx=(10, 0))
    v.bind("<Escape>", lambda _e: responder(False))
    v.protocol("WM_DELETE_WINDOW", lambda: responder(False))
    _colocar(v, cont, ancho, x=(v.winfo_screenwidth() - ancho) // 2, y=140)
    v.after(60, lambda: _al_frente(v))
    _vigilar_cierre(v, m, cerrar_externo)


if __name__ == "__main__":
    import time

    mostrar("Genesis detectó algo",
            "Tu equipo tiene 3.2 GB en archivos temporales y 1.1 GB en la papelera.",
            [{"etiqueta": "Limpiar temporales (3.2 GB)",
              "accion": lambda: (time.sleep(1.2), "Listo: liberé 3.2 GB.")[1]}])
    time.sleep(30)
