"""Revisa pieza por pieza que todo lo de Jarvis funcione en esta PC, antes de la demo.

    python diagnostico.py            -> revisión completa (no mueve nada ni habla)
    python diagnostico.py audio      -> lista micrófonos y salidas de audio
    python diagnostico.py voz        -> habla por la salida privada y por la del público
    python diagnostico.py voces      -> lista y te deja escuchar las voces naturales de Edge
    python diagnostico.py camaras    -> prueba las cámaras 0 a 5 y guarda una foto de cada una
    python diagnostico.py vision     -> toma una foto con la fuente configurada y la describe
    python diagnostico.py hud        -> muestra el HUD pasando por todos los estados
    python diagnostico.py palabra    -> prueba "hey Jarvis" durante 30 s
    python diagnostico.py powerpoint -> revisa que PowerPoint responda por COM
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
DATOS = BASE / "datos"
DATOS.mkdir(exist_ok=True)


def ok(msg):
    print(f"  [OK]  {msg}")


def mal(msg):
    print(f"  [!!]  {msg}")


def cfg():
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        mal("No existe config.json: copia config.example.json a config.json")
        sys.exit(1)


def audio():
    print("\n== Audio ==")
    import escuchar
    import voz
    c = cfg()
    print("  Micrófonos:")
    for i, n in escuchar.listar_dispositivos():
        print(f"     {i}) {n}")
    print("  Salidas:")
    for i, n in voz.listar_salidas():
        print(f"     {i}) {n}")
    for clave in ("salida_privada", "salida_publico"):
        nombre = c.get(clave, "")
        idx = voz.resolver_salida(nombre)
        if not nombre:
            ok(f"{clave}: predeterminada de Windows")
        elif idx is None:
            mal(f"{clave}: '{nombre}' no aparece en la lista")
        else:
            ok(f"{clave}: '{nombre}' -> salida {idx}")
    mic = c.get("mic_dispositivo", "")
    ok(f"micrófono: {mic or 'predeterminado de Windows'} -> {escuchar.resolver_dispositivo(mic)}")


def probar_voz():
    print("\n== Voz ==")
    import voz
    c = cfg()
    voz.configurar(c)
    for clave, frase in (("salida_privada", "Esta es mi voz privada, solo para usted, señor."),
                         ("salida_publico", "Y esta es mi voz para el público.")):
        print(f"  Hablando por {clave} ({c.get(clave) or 'predeterminada'})...")
        voz.hablar(frase, c.get("voz_velocidad", 180), c.get("voz_nombre", ""),
                   c.get("elevenlabs_voz", ""), c.get(clave, ""))
    ok("¿Escuchaste las dos frases por donde esperabas?")


def camaras():
    print("\n== Cámaras (0 a 5) ==")
    try:
        import cv2
    except ImportError:
        mal("Falta opencv-python: pip install opencv-python")
        return
    for i in range(6):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print(f"     {i}) no existe")
            continue
        frame = None
        for _ in range(15):
            listo, f = cap.read()
            if listo:
                frame = f
            time.sleep(0.05)
        cap.release()
        if frame is None:
            mal(f"{i}) se abre pero no da imagen")
            continue
        ruta = DATOS / f"diag_camara_{i}.jpg"
        cv2.imwrite(str(ruta), frame)
        ok(f"{i}) {frame.shape[1]}x{frame.shape[0]} -> foto en {ruta}")
    print("  Abre las fotos: la que muestre la videollamada de los lentes es tu webcam_indice.")


def vision():
    print("\n== Visión ==")
    import camara
    import cerebro  # noqa: F401
    import vision as v
    c = cfg()
    fuente = c.get("camara", {}).get("fuente", "webcam")
    print(f"  Capturando de la fuente '{fuente}'...")
    try:
        img = camara.capturar(c)
    except camara.CamaraError as e:
        mal(str(e))
        return
    ok(f"imagen {img.size[0]}x{img.size[1]} guardada en {camara.ULTIMA_PATH}")
    t = time.time()
    try:
        texto = v.ver(c, img, "Describe en 2 frases lo que ves.",
                      "Eres Jarvis, un asistente elegante que habla español.")
        ok(f"({time.time() - t:.1f} s) {texto}")
    except RuntimeError as e:
        mal(str(e))


def probar_hud():
    print("\n== HUD (20 s) ==")
    import hud
    c = cfg()
    c.setdefault("hud", {})["activo"] = True
    hud.iniciar(c)
    hud.modo_expositor(True)
    for est in ("inactivo", "escuchando", "pensando", "mirando", "hablando"):
        print(f"  estado: {est}")
        hud.estado(est)
        if est == "hablando":
            hud.oido("preséntate con el público")
            hud.subtitulo("Buenas tardes a todos. Soy Jarvis, y hoy acompaño a Chris en su exposición.")
        time.sleep(3.5)
    hud.fin_subtitulo(500)
    hud.estado("inactivo")
    time.sleep(3)
    ok("¿Viste el reactor en la esquina y los subtítulos abajo?")


def palabra():
    print("\n== Palabra de activación (30 s) ==")
    print("  Di 'hey Jarvis' varias veces, normal, como le hablarías. Cada medio segundo verás:")
    print("  volumen de tu voz | puntaje del detector (se activa al llegar a la sensibilidad)\n")
    import numpy as np
    import sounddevice as sd

    import escuchar
    c = cfg()
    escuchar.DISPOSITIVO = escuchar.resolver_dispositivo(c.get("mic_dispositivo"))
    nombre = sd.query_devices(escuchar.DISPOSITIVO, "input")["name"]
    print(f"  Micrófono: {nombre}")
    ganancia = float(c.get("mic_ganancia", 1.0))
    sens = float(c.get("oww_sensibilidad", 0.5))
    det = escuchar._cargar_oww(c.get("oww_modelo", "hey_jarvis"))
    fin = time.time() + 30
    pico_voz, pico_punt, detecciones = 0.0, 0.0, 0
    nivel_max = punt_max = 0.0
    cuenta = 0
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=1280,
                        device=escuchar.DISPOSITIVO) as st:
        while time.time() < fin:
            data, _ = st.read(1280)
            nivel = float(np.sqrt(np.mean(data ** 2)))
            p = max(det.predict((np.clip(data[:, 0] * ganancia, -1, 1) * 32767).astype(np.int16)).values())
            nivel_max, punt_max = max(nivel_max, nivel), max(punt_max, p)
            pico_voz, pico_punt = max(pico_voz, nivel), max(pico_punt, p)
            cuenta += 1
            if cuenta % 6 == 0:  # ~0.5 s
                marca = "  <- ¡DETECTADO!" if punt_max >= sens else ""
                if punt_max >= sens:
                    detecciones += 1
                    det.reset()
                print(f"     volumen {nivel_max:.4f} {'#' * min(40, int(nivel_max * 400)):<40} "
                      f"puntaje {punt_max:.2f}{marca}")
                nivel_max = punt_max = 0.0
    print(f"\n  Volumen máximo de tu voz: {pico_voz:.4f}   Puntaje máximo: {pico_punt:.2f}   "
          f"Detecciones: {detecciones}")
    if pico_voz * ganancia < 0.05:
        sugerida = min(20.0, round(0.12 / max(pico_voz, 0.001), 1))
        mal(f"Tu voz llega muy bajita. Pon en config.json: \"mic_ganancia\": {sugerida}  y repite esta prueba.")
    elif detecciones == 0 and pico_punt >= 0.2:
        mal(f"Casi te detecta. Baja la sensibilidad en config.json: \"oww_sensibilidad\": {max(0.2, round(pico_punt - 0.05, 2))}")
    elif detecciones == 0:
        mal("No reconoce la frase. Prueba decir 'hey Jarvis' en inglés ('jei yárvis'), o usa el motor "
            "de Whisper: \"motor_activacion\": \"whisper\" y di solo 'Jarvis'.")
    else:
        ok("La palabra de activación funciona.")


def voces():
    """Lista las voces neuronales de Edge en español (y las multilingües, que también hablan
    español) y te deja escucharlas. Uso:
        python diagnostico.py voces                      -> lista y reproduce una muestra de varias
        python diagnostico.py voces es-MX-JorgeNeural    -> escucha solo esa
    """
    import asyncio

    import voz
    c = cfg()
    voz.configurar(c)
    frase = ("Buenas tardes, señor. Todos los sistemas están en línea. Cuando usted lo indique, "
             "comenzamos la presentación.")
    elegida = sys.argv[2] if len(sys.argv) > 2 else ""
    if elegida:
        print(f"\n  Escuchando {elegida}...")
        c["edge_voz"] = elegida
        voz.configurar(c)
        voz._hablar_edge(frase, voz.resolver_salida(c.get("salida_privada", "")))
        ok(f"Si te gustó, pon en config.json:  \"edge_voz\": \"{elegida}\"")
        return
    try:
        import edge_tts
        todas = asyncio.run(edge_tts.list_voices())
    except Exception as e:
        mal(f"No pude conectarme al servicio de voces de Microsoft ({type(e).__name__}). ¿Hay internet?")
        return
    esp = [v for v in todas if v["Locale"].startswith("es-")]
    multi = [v for v in todas if "Multilingual" in v["ShortName"]]
    print("\n== Voces en español ==")
    for v in sorted(esp, key=lambda v: (v["Locale"] not in ("es-MX", "es-US", "es-ES"), v["ShortName"])):
        print(f"     {v['ShortName']:<34} {v['Gender']}")
    print("\n== Multilingües (hablan español con otro acento) ==")
    for v in multi:
        print(f"     {v['ShortName']:<34} {v['Gender']}")
    muestra = [n for n in ("es-MX-JorgeNeural", "es-MX-DaliaNeural", "es-ES-AlvaroNeural",
                           "es-US-AlonsoNeural", "en-US-AndrewMultilingualNeural")
               if any(v["ShortName"] == n for v in todas)]
    print("\n  Te pongo una muestra de algunas:")
    for n in muestra:
        print(f"     ▶ {n}")
        c["edge_voz"] = n
        voz.configurar(c)
        try:
            voz._hablar_edge(frase, voz.resolver_salida(c.get("salida_privada", "")))
        except Exception as e:
            mal(f"{n}: {type(e).__name__}")
        time.sleep(0.5)
    print("\n  Para escuchar cualquiera de la lista:  python diagnostico.py voces NOMBRE")
    print("  Para ajustar: \"edge_velocidad\" (\"+10%\" más rápido) y \"edge_tono\" (\"-5Hz\" más grave)")


def powerpoint():
    print("\n== PowerPoint ==")
    import presentacion
    app = presentacion._app()
    if app is None:
        mal("PowerPoint no está abierto (o no responde por COM). Ábrelo con una presentación y repite.")
        return
    ok(f"PowerPoint responde: {app.Presentations.Count} presentación(es) abierta(s)")
    diapos = presentacion.contenido(forzar=True)
    ok(f"Leí {len(diapos)} diapositivas de la activa")
    for d in diapos[:3]:
        print(f"     {d['numero']}) {d['titulo'] or '(sin título)'}")
    print(f"  En pantalla completa: {presentacion.en_curso()}  Posición: {presentacion.posicion()}")


def general():
    print("== Jarvis: revisión general ==")
    c = cfg()
    import os
    for clave in ("GROQ_API_KEY",):
        (ok if os.environ.get(clave) else mal)(f"{clave} {'configurada' if os.environ.get(clave) else 'no configurada (solo modo local)'}")
    try:
        import ollama
        modelos = [m.model for m in ollama.list().models]
        ok(f"Ollama responde. Modelos: {', '.join(modelos) or 'ninguno'}")
        for m in (c.get("model"), c.get("vision", {}).get("local_modelo")):
            if m and not any(x.startswith(m) for x in modelos):
                mal(f"Falta el modelo local '{m}': ollama pull {m}")
    except Exception as e:
        mal(f"Ollama no responde ({type(e).__name__}); sin internet no habrá cerebro")
    for mod in ("openwakeword", "cv2", "pptx", "faster_whisper", "piper", "edge_tts"):
        try:
            __import__(mod)
            ok(f"módulo {mod}")
        except ImportError:
            mal(f"falta el módulo {mod} (pip install -r requirements.txt)")
    voces = list((BASE / "voces").glob("*.onnx"))
    (ok if voces else mal)(f"voz Piper: {voces[0].name if voces else 'no hay (se usará la voz de Windows)'}")
    audio()
    powerpoint()


if __name__ == "__main__":
    pruebas = {"audio": audio, "voz": probar_voz, "voces": voces, "camaras": camaras, "vision": vision,
               "hud": probar_hud, "palabra": palabra, "powerpoint": powerpoint}
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    import skills
    skills.configurar(cfg())
    pruebas.get(arg, general)()
    import os
    os._exit(0)  # el hilo de Tk del HUD puede trabar el cierre normal
