"""Revisa pieza por pieza que todo lo de Jarvis funcione en esta PC, antes de la demo.

    python diagnostico.py demo       -> CHECKLIST DEL DÍA DE LA DEMO (todo en verde o rojo)
    python diagnostico.py            -> revisión general (no mueve nada ni habla)
    python diagnostico.py latencia   -> mide cuánto tarda en contestar (cerebro + voz, sin sonar)
    python diagnostico.py audio      -> lista micrófonos y salidas de audio
    python diagnostico.py llamada    -> ¿llega el audio de la videollamada (cable virtual)?
    python diagnostico.py ventanas   -> ventanas abiertas (para camara.ventana_titulo)
    python diagnostico.py voz        -> habla por la salida privada y por la del público
    python diagnostico.py voces      -> lista y te deja escuchar las voces naturales de Edge
    python diagnostico.py camaras    -> prueba las cámaras 0 a 5 y guarda una foto de cada una
    python diagnostico.py vision     -> toma una foto con la fuente configurada y la describe
    python diagnostico.py hud        -> muestra el HUD pasando por todos los estados
    python diagnostico.py palabra    -> prueba "hey Jarvis" durante 30 s
    python diagnostico.py powerpoint -> revisa que PowerPoint responda por COM
"""
import json
import os
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


def _clave_usuario(nombre):
    """Variable de entorno; si la terminal se abrió antes del setx, se lee del registro."""
    if not os.environ.get(nombre):
        import configuracion
        configuracion.cargar_claves_de_windows(cfg())
    return os.environ.get(nombre, "")


def _groq(c):
    """Latencia real y límite por minuto del plan de Groq (una petición mínima)."""
    import cerebro
    nube = c.get("nube", {})
    cli = cerebro.cliente(nube.get("url"), os.environ.get(nube.get("clave_env", ""), ""), 20)
    t = time.time()
    crudo = cli.chat.completions.with_raw_response.create(
        model=nube.get("modelo"), messages=[{"role": "user", "content": "Di: listo"}], max_tokens=5)
    latencia = time.time() - t
    limite = crudo.headers.get("x-ratelimit-limit-tokens")
    return latencia, int(limite) if limite and str(limite).isdigit() else None


def demo():
    """Checklist del día de la demo: cada pieza en verde (OK) o rojo (!!) con qué hacer."""
    print("== Jarvis: checklist de la demo ==")
    c = cfg()
    import skills
    skills.configurar(c)
    rojos = []

    def mal_(msg):
        rojos.append(msg)
        mal(msg)

    print("\n-- Cerebro --")
    if _clave_usuario(c.get("nube", {}).get("clave_env", "GROQ_API_KEY")):
        try:
            lat, limite = _groq(c)
            (ok if lat < 2 else mal_)(f"Groq responde en {lat:.1f}s")
            if limite is not None and limite <= 20000:
                mal_(f"Plan gratis de Groq ({limite:,} tokens por minuto): en la demo se satura a la "
                     "2ª-3ª orden. Actívale el plan Developer en console.groq.com (una demo cuesta centavos).")
            elif limite:
                ok(f"Límite de Groq: {limite:,} tokens por minuto")
        except Exception as e:
            mal_(f"Groq no responde: {type(e).__name__}: {str(e)[:100]}")
    else:
        mal_("Falta GROQ_API_KEY (setx GROQ_API_KEY \"tu_clave\" y abre otra terminal)")
    try:
        import ollama
        modelos = [m.model for m in ollama.list().models]
        for m in (c.get("model"), c.get("vision", {}).get("local_modelo")):
            (ok if m and any(x.startswith(m) for x in modelos) else mal_)(
                f"Modelo local {m}" + ("" if any(x.startswith(str(m)) for x in modelos) else f": falta, ollama pull {m}"))
        log = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
        if log.exists():
            ultimas = [ln for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()
                       if "inference compute" in ln]
            if ultimas and "library=cpu" in ultimas[-1]:
                mal_("Ollama no está usando la GPU (el modelo local será MUY lento). Reinstálalo "
                     "desde ollama.com: la actualización de julio dejó la instalación incompleta.")
            elif ultimas:
                ok("Ollama usa la GPU")
    except Exception as e:
        mal_(f"Ollama no responde ({type(e).__name__}): sin internet no habría cerebro")

    print("\n-- Voz --")
    import voz
    voz.configurar(c)
    if c.get("elevenlabs_voz") and _clave_usuario("ELEVENLABS_API_KEY"):
        try:
            t = time.time()
            primero = next(voz._eleven_trozos("Prueba.", c["elevenlabs_voz"], os.environ["ELEVENLABS_API_KEY"]))
            (ok if len(primero) else mal_)(f"ElevenLabs responde en {time.time() - t:.1f}s")
        except Exception as e:
            mal_(f"ElevenLabs falla: {type(e).__name__}: {str(e)[:100]} (se usará Edge, más lento)")
    (ok if voz._modelo_piper() else mal_)("Voz local Piper (respaldo sin internet)"
                                          + ("" if voz._modelo_piper() else ": falta voces/*.onnx"))

    print("\n-- Oídos --")
    try:
        import ctranslate2
        (ok if ctranslate2.get_cuda_device_count() else mal)("GPU para Whisper"
                                                             if ctranslate2.get_cuda_device_count() else
                                                             "Sin GPU para Whisper (funciona en CPU, más lento)")
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Multimedia\Audio") as k:
            ducking = winreg.QueryValueEx(k, "UserDuckingPreference")[0]
    except OSError:
        ducking = 1  # el valor por defecto de Windows: bajar 80 %
    if ducking != 3:
        mal_("Windows baja el volumen de todo durante llamadas/micrófono Bluetooth: Más opciones de "
             "sonido → Comunicaciones → 'No hacer nada' (si no, la voz de Jarvis se oye al 20 %).")
    else:
        ok("Windows no baja el volumen en llamadas")
    audio()

    print("\n-- Exposición --")
    powerpoint()
    import conocimiento
    (ok if len(conocimiento.texto(c)) > 800 else mal_)(
        "Conocimiento del proyecto" + ("" if len(conocimiento.texto(c)) > 800 else
                                       ": llena conocimiento/proyecto.md para que responda al jurado"))
    d = c.get("demo", {}) or {}
    if not d.get("url"):
        mal_("Falta demo.url en config.json (la dirección de tu software)")
    else:
        try:
            from urllib.request import urlopen
            urlopen(d["url"], timeout=8).close()
            ok(f"Tu software responde: {d['url']}")
        except Exception as e:
            mal_(f"No abre {d['url']}: {type(e).__name__}")
        try:
            import navegador  # noqa: F401
            ok("Playwright listo para manejar el navegador")
        except ImportError:
            mal_("Falta Playwright: .venv\\Scripts\\pip install playwright")
        cache = DATOS / "demo_cache.json"
        if cache.exists():
            datos = json.loads(cache.read_text(encoding="utf-8"))
            ok(f"Demo ensayada el {datos.get('fecha')}: {len(datos.get('modulos', {}))} módulos listos")
        else:
            mal_("La demo no está ensayada: di 'Jarvis, ensaya la demo' con el sistema abierto")
    if c.get("mantenimiento_activo", True):
        ok("Mantenimiento: se calla solo durante la exposición")
    print("\n" + ("TODO LISTO. ¡Éxito en la demo!" if not rojos else
                  f"{len(rojos)} cosa(s) por resolver (arriba, en [!!])."))


def latencia():
    """Cuánto tarda Jarvis en empezar a contestar: cerebro (primera frase) + voz (primer audio)."""
    print("== Latencia (sin sonar nada) ==")
    c = cfg()
    for nombre in (c.get("nube", {}).get("clave_env", "GROQ_API_KEY"), "ELEVENLABS_API_KEY"):
        _clave_usuario(nombre)
    import cerebro
    import genesis
    import skills
    import voz
    skills.configurar(c)
    voz.configurar(c)
    cerebro.precalentar(c)
    voz.mantener_caliente()
    sistema = {"role": "system", "content": genesis._prompt(c)}
    for pregunta in ("¿Para qué sirve un sistema de inventarios? Una frase.",
                     "Dile al público en una frase qué es la inteligencia artificial."):
        t0 = time.time()
        marcas = {}

        def al_texto(f, _m=marcas, _t0=t0):
            _m.setdefault("token", time.time() - _t0)
            _m["texto"] = _m.get("texto", "") + f
            if "frase" not in _m and any(x in _m["texto"] for x in ".!?"):
                _m["frase"] = time.time() - _t0
        try:
            cerebro.chat(c, [sistema, {"role": "user", "content": pregunta}],
                         skills.schemas(genesis.elegir_herramientas(pregunta, [])), 0.2, al_texto)
        except Exception as e:
            mal(f"cerebro: {type(e).__name__}: {str(e)[:100]}")
            continue
        clip = voz._Clip((marcas.get("texto") or "Listo.").split(".")[0] + ".")
        t1 = time.time()
        voz._generar(clip, c.get("elevenlabs_voz", ""))
        voz_seg = (clip.t_primer_audio or time.time()) - t1
        total = marcas.get("frase", 0) + voz_seg
        (ok if total < 2 else mal)(f"primera frase del cerebro {marcas.get('frase', 0):.1f}s + primer audio "
                                   f"({clip.motor}) {voz_seg:.1f}s = ~{total:.1f}s hasta que empieza a hablar")


def ventanas():
    """Ventanas visibles con su programa y tamaño: el título de la videollamada va en
    config.json → camara.ventana_titulo (fuente "ventana")."""
    print("== Ventanas abiertas ==")
    import control
    import win32gui
    for h in control._todas_las_ventanas():
        x0, y0, x1, y1 = win32gui.GetWindowRect(h)
        print(f"  {control._titulo(h)[:60]:<60} {control._proceso(h):<22} {x1 - x0}x{y1 - y0}")


def llamada():
    """¿Llega audio por el micrófono configurado (p. ej. CABLE Output de la videollamada)?"""
    print("== Audio de la llamada (15 s): habla por los lentes ==")
    import numpy as np
    import sounddevice as sd

    import escuchar
    c = cfg()
    indice = escuchar.resolver_dispositivo(c.get("mic_dispositivo"))
    print(f"  Micrófono: {escuchar._nombre_dispositivo(indice)}")
    fin, ceros, voz_max = time.time() + 15, 0, 0.0
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=1600, device=indice) as st:
        while time.time() < fin:
            data, _ = st.read(1600)
            nivel = float(np.sqrt(np.mean(data ** 2)))
            voz_max = max(voz_max, nivel)
            ceros += float(np.max(np.abs(data))) < escuchar.SILENCIO_DIGITAL
            print(f"\r  nivel {nivel:.4f} {'#' * min(40, int(nivel * 400)):<40}", end="", flush=True)
    print()
    if ceros > 100:
        mal("Llega silencio total: ¿la llamada está conectada y la salida de WhatsApp va a 'CABLE Input'?")
    elif voz_max < 0.01:
        mal("Llega audio pero muy bajo: sube el volumen de WhatsApp en el mezclador de Windows.")
    else:
        ok("El audio de la llamada llega bien.")


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
               "hud": probar_hud, "palabra": palabra, "powerpoint": powerpoint, "demo": demo,
               "latencia": latencia, "ventanas": ventanas, "llamada": llamada}
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    import skills
    skills.configurar(cfg())
    pruebas.get(arg, general)()
    sys.stdout.flush()  # os._exit no vacía la salida: sin esto, redirigida a un archivo se perdía
    os._exit(0)  # el hilo de Tk del HUD puede trabar el cierre normal
