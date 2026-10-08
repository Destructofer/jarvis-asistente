"""La app de escritorio de Jarvis: una ventana propia con el orbe, las personalidades, los
avatares, la voz y los ajustes (la interfaz está en app/ y la sirve Jarvis: app_servidor.py).

- Si Jarvis no está corriendo, lo arranca y espera a que esté listo.
- Una sola ventana: si ya está abierta, la trae al frente.
- python jarvis_app.pyw --acceso-directo   crea "Jarvis" en tu escritorio.
"""
import ctypes
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATOS_APP = BASE / "datos" / "app.json"
ICONO = BASE / "datos" / "jarvis.ico"
TITULO = "Jarvis"


def nombre():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("name", "Jarvis")
    except Exception:
        return "Jarvis"


def asegurar_icono():
    """El ícono del orbe (.ico con varios tamaños), dibujado aquí: no hace falta un binario en el repo."""
    if ICONO.exists():
        return ICONO
    from PIL import Image, ImageDraw, ImageFilter
    t = 256
    img = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, t - 1, t - 1), radius=58, fill=(5, 11, 17, 255))
    brillo = Image.new("RGBA", (t, t), (0, 0, 0, 0))
    ImageDraw.Draw(brillo).ellipse((28, 28, 228, 228), fill=(123, 108, 255, 120))
    img.alpha_composite(brillo.filter(ImageFilter.GaussianBlur(22)))
    d = ImageDraw.Draw(img)
    for k, a in enumerate(range(0, 360, 120)):
        d.arc((50, 50, 206, 206), a + 10, a + 90, fill=(56, 225, 255, 255), width=8)
    for r, color in ((54, (59, 76, 255)), (44, (56, 225, 255)), (22, (255, 255, 255))):
        d.ellipse((128 - r, 128 - r, 128 + r, 128 + r), fill=color + (255,))
    ICONO.parent.mkdir(parents=True, exist_ok=True)
    img.save(ICONO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ICONO


def crear_acceso_directo():
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    escritorio = Path(shell.SpecialFolders("Desktop"))
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    acceso = shell.CreateShortCut(str(escritorio / f"{nombre()}.lnk"))
    acceso.TargetPath = str(pythonw if pythonw.exists() else sys.executable)
    acceso.Arguments = f'"{BASE / "jarvis_app.pyw"}"'
    acceso.WorkingDirectory = str(BASE)
    acceso.IconLocation = str(asegurar_icono())
    acceso.Description = "Personaliza a Jarvis: personalidades, avatares, voz y su orbe"
    acceso.save()
    return escritorio / f"{nombre()}.lnk"


def _datos():
    try:
        return json.loads(DATOS_APP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _responde(d):
    """¿El Jarvis de datos/app.json está vivo y acepta su llave?"""
    if not d:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{d['puerto']}/api/estado",
                                     headers={"X-Jarvis-Token": d["llave"]})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _jarvis_corriendo():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k.OpenMutexW(0x00100000, False, "Genesis.Asistente.Instancia")   # SYNCHRONIZE
    if h:
        k.CloseHandle(h)
        return True
    return False


def conectar(espera=90):
    """Los datos de la conexión con Jarvis; si no está corriendo, lo arranca."""
    d = _datos()
    if _responde(d):
        return d
    if not _jarvis_corriendo():
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), str(BASE / "iniciar_genesis.pyw")],
                         cwd=str(BASE), creationflags=0x00000008)
    fin = time.time() + espera
    while time.time() < fin:
        time.sleep(1)
        d = _datos()
        if _responde(d):
            return d
    return None


def _traer_al_frente():
    u = ctypes.windll.user32
    h = u.FindWindowW(None, nombre())
    if h:
        u.ShowWindow(h, 9)   # SW_RESTORE
        u.SetForegroundWindow(h)


def main():
    if "--acceso-directo" in sys.argv:
        print(f"Acceso directo creado: {crear_acceso_directo()}")
        return
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW(None, False, "Jarvis.App.Ventana")
    if ctypes.get_last_error() == 183:   # ya hay una ventana abierta
        _traer_al_frente()
        return

    import webview
    ventana = webview.create_window(nombre(), html=_cargando(), width=1180, height=780,
                                    min_size=(720, 520), background_color="#050b11")

    def al_mostrarse():
        try:   # el ícono del orbe en la barra de tareas
            from System.Drawing import Icon
            ventana.native.Icon = Icon(str(asegurar_icono()))
        except Exception:
            pass
        d = conectar()
        if d is None:
            ventana.load_html(_cargando("No pude conectarme con Jarvis. Revisa que esté abierto "
                                        "(el ícono azul junto al reloj) y vuelve a abrir la app."))
            return
        ventana.load_url(f"http://127.0.0.1:{d['puerto']}/#t={d['llave']}")
    ventana.events.shown += al_mostrarse
    webview.start(private_mode=True)


def _cargando(texto="Conectando con Jarvis…"):
    return ("<html><body style='margin:0;height:100vh;display:grid;place-items:center;background:#050b11;"
            "color:#8eaebf;font:15px Segoe UI,sans-serif'><div style='text-align:center'>"
            "<div style='width:70px;height:70px;margin:0 auto 18px;border-radius:50%;"
            "background:radial-gradient(circle at 40% 35%,#fff,#38e1ff 45%,#3b4cff);"
            "box-shadow:0 0 40px #38e1ff'></div>" + texto + "</div></body></html>")


if __name__ == "__main__":
    main()
