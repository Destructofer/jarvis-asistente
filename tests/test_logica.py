"""Pruebas de la lógica de Jarvis que no necesitan micrófono, bocinas ni internet.

    .venv\\Scripts\\python -m unittest discover -s tests -v

Cubren los errores que ya pasaron en la vida real (atajos que bloqueaban la PC, "Adiós" que no
cerraba, confirmaciones en cadena, fallos de rutina mal detectados...) para que no regresen.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import archivos  # noqa: E402
import cerebro  # noqa: E402
import configuracion  # noqa: E402
import control  # noqa: E402
import escuchar  # noqa: E402
import genesis  # noqa: E402
import graph  # noqa: E402
import mantenimiento  # noqa: E402
import presentacion  # noqa: E402
import skills  # noqa: E402
import voz  # noqa: E402


class Registro(unittest.TestCase):
    def test_cada_skill_apunta_a_su_funcion(self):
        """Un @skill pegado a la función equivocada (pasó al insertar una función entre el
        decorador y la suya) solo se notaba al usarla en vivo."""
        import expositor  # noqa: F401
        import graph  # noqa: F401
        import multimedia  # noqa: F401
        import teams  # noqa: F401
        mal = [n for n, s in skills._SKILLS.items()
               if s["fn"].__name__ not in (n, "skill_" + n)]
        self.assertEqual(mal, [])

    def test_parametros_coinciden_con_la_funcion(self):
        import inspect
        for n, s in skills._SKILLS.items():
            firma = inspect.signature(s["fn"]).parameters
            for p in s["params"]:
                self.assertIn(p, firma, f"{n}: el parámetro '{p}' no existe en la función")


class Atajos(unittest.TestCase):
    def test_frases_largas_ya_no_disparan_atajos(self):
        for frase in ["¿Por qué se bloquea el equipo tan seguido?",
                      "Recuérdame a qué hora es la junta con el profe",
                      "Mándale un pantallazo a Juan por WhatsApp",
                      "¿Qué día es el examen de cálculo?",
                      "¿Qué hora es en Tokio?",
                      "¿Cómo está el equipo de México?"]:
            self.assertIsNone(genesis.buscar_atajo(frase), frase)

    def test_frases_exactas_si_disparan(self):
        self.assertEqual(genesis.buscar_atajo("Jarvis, ¿qué hora es?"), "hora_fecha")
        self.assertEqual(genesis.buscar_atajo("Dime qué hora es, por favor"), "hora_fecha")
        self.assertEqual(genesis.buscar_atajo("Toma una captura de pantalla"), "captura_pantalla")
        self.assertEqual(genesis.buscar_atajo("Bloquea la pantalla"), "bloquear_pantalla")

    def test_presentarse_es_instantaneo(self):
        for frase in ["Jarvis, preséntate con el público", "Preséntate, por favor", "Saluda a todos"]:
            self.assertEqual(genesis.atajo_sistema(frase), ("presentarse_al_publico", {}), frase)
        self.assertIsNone(genesis.atajo_sistema("preséntate con el público y luego abre Chrome"))

    def test_salir_con_acento(self):
        for frase in ["Adiós.", "adios", "Hasta luego.", "Jarvis, adiós"]:
            self.assertIn(genesis._limpia_orden(frase), genesis.SALIDAS, frase)


class Presentacion(unittest.TestCase):
    def setUp(self):
        self._orig = (presentacion.en_curso, presentacion._presentacion_activa)
        presentacion.en_curso = lambda: True
        presentacion._presentacion_activa = lambda: None

    def tearDown(self):
        presentacion.en_curso, presentacion._presentacion_activa = self._orig

    def test_atajos_de_diapositivas(self):
        casos = {
            "siguiente": ("presentacion", {"accion": "siguiente"}),
            "Jarvis, siguiente por favor": ("presentacion", {"accion": "siguiente"}),
            "ve a la diapositiva siete": ("presentacion", {"accion": "ir", "numero": 7}),
            "avanza tres": ("presentacion", {"accion": "siguiente", "numero": 3}),
            "regresa": ("presentacion", {"accion": "anterior"}),
            "quita la pantalla negra": ("presentacion", {"accion": "reanudar"}),
        }
        for frase, esperado in casos.items():
            self.assertEqual(genesis.atajo_presentacion(frase), esperado, frase)

    def test_regresar_a_la_presentacion_la_trae_al_frente(self):
        self.assertEqual(genesis.atajo_presentacion("Jarvis, regresa a la presentación"),
                         ("mostrar_presentacion", {}))

    def test_sin_presentacion_decide_el_modelo(self):
        presentacion.en_curso = lambda: False
        self.assertIsNone(genesis.atajo_presentacion("siguiente"))

    def test_abierta_sin_pantalla_completa_responde_clara(self):
        """Antes iba al modelo, que una vez corrió una rutina por un 'siguiente'."""
        presentacion.en_curso = lambda: False
        presentacion._presentacion_activa = lambda: object()
        self.assertEqual(genesis.atajo_presentacion("siguiente"),
                         ("presentacion", {"accion": "siguiente"}))


class Confirmacion(unittest.TestCase):
    def test_si_en_mexicano(self):
        for frase in ["Sí.", "Simón", "Va", "Órale", "Sale", "Claro que sí", "Por supuesto"]:
            self.assertTrue(skills.es_afirmativo(frase), frase)

    def test_ante_la_duda_no(self):
        for frase in ["No.", "Nel", "Sí, no espera", "", "mmm"]:
            self.assertFalse(skills.es_afirmativo(frase), frase)


class BotonesPeligrosos(unittest.TestCase):
    def test_piden_confirmacion(self):
        for boton in ["Eliminar", "Entregar", "Enviar", "Send", "Submit", "Publicar", "Sign out",
                      "Log out", "Logout", "Cerrar sesión", "Transferir", "Aceptar y pagar",
                      "Unirse ahora", "Eliminarlos", "Comprar"]:
            self.assertTrue(control.es_peligroso(boton), boton)

    def test_no_molestan_con_botones_normales(self):
        for boton in ["Descendente", "Llamadas", "Envíos", "Entregas", "Postres", "Compras",
                      "Iniciar sesión", "Guardar", "Siguiente", "Panel de ventas", "Borrador"]:
            self.assertFalse(control.es_peligroso(boton), boton)


class Rutinas(unittest.TestCase):
    def test_fallos_bien_detectados(self):
        self.assertFalse(control.fallo("No había ningún apagado programado."))
        self.assertTrue(control.fallo("Encontré 'Chrome' pero Windows no me dejó traerla al frente."))
        self.assertTrue(control.fallo("'Word' no se cerró todavía; puede estar preguntando"))
        self.assertTrue(control.fallo("No encontré nada parecido a 'Entrar' en Chrome."))
        self.assertTrue(control.fallo(skills.Fallo("cualquier cosa")))
        self.assertFalse(control.fallo("Listo, en Chrome."))

    def test_rutina_se_detiene_en_el_paso_que_falla(self):
        cfg = {"rutinas": {"demo": [{"abrir_web": {"url": "x"}}, {"clic_en": {"texto": "y"}},
                                    {"decir": "no debe llegar"}]}}
        skills.configurar(cfg)
        llamados = []
        control.ejecutor = lambda n, a: (llamados.append(n), skills.Fallo("No encontré 'y'")
                                         if n == "clic_en" else "ok")[1]
        control.hablar = lambda t: llamados.append("decir")
        r = control.rutina("demo")
        self.assertIsInstance(r, skills.Fallo)
        self.assertEqual(llamados, ["abrir_web", "clic_en"])

    def test_continuar_si_falla(self):
        cfg = {"rutinas": {"demo": [{"clic_en": {"texto": "y"}, "continuar_si_falla": True},
                                    {"abrir_web": {"url": "x"}}]}}
        skills.configurar(cfg)
        llamados = []
        control.ejecutor = lambda n, a: (llamados.append(n), skills.Fallo("no"))[1]
        control.hablar = None
        control.rutina("demo")
        self.assertEqual(llamados, ["clic_en", "abrir_web"])


class ProteccionTerceros(unittest.TestCase):
    def test_lo_que_pidio_el_usuario_no_se_pregunta(self):
        self.assertTrue(genesis._pedido_por_usuario({"texto": "Iniciar sesión"},
                                                    "dale clic a iniciar sesión"))
        self.assertTrue(genesis._pedido_por_usuario({"url": "youtube.com"}, "abre youtube"))
        self.assertTrue(genesis._pedido_por_usuario({"texto": "admin@ejemplo.com", "enter": True},
                                                    "escribe admin arroba ejemplo punto com"))

    def test_lo_que_no_pidio_si(self):
        self.assertFalse(genesis._pedido_por_usuario({"url": "sitio-malo.ru/robar"},
                                                     "¿qué dice el último mensaje?"))
        self.assertFalse(genesis._pedido_por_usuario({"nombre": "powerpoint"},
                                                     "lee la ventana"))

    def test_descripcion_legible(self):
        self.assertEqual(genesis._describir_accion("clic_en", {"texto": "Enviar"}), "pulsar «Enviar»")
        self.assertNotIn("{", genesis._describir_accion("abrir_web", {"url": "x.com"}))


class Archivos(unittest.TestCase):
    def test_lista_blanca(self):
        for nombre in ["informe.pdf", "foto.JPG", "clase.pptx", "notas.txt", "cancion.mp3"]:
            self.assertTrue(archivos.se_puede_abrir(nombre), nombre)
        for nombre in ["instalar.py", "atajo.url", "panel.cpl", "consola.msc", "virus.exe",
                       "script.vbe", "sin_extension"]:
            self.assertFalse(archivos.se_puede_abrir(nombre), nombre)


class Transcripcion(unittest.TestCase):
    def test_alucinaciones_de_whisper(self):
        for t in ["Subtítulos realizados por la comunidad de Amara.org", "¡Gracias por ver el video!",
                  "Suscríbete", "", "  "]:
            self.assertTrue(escuchar._es_alucinacion(t), t)
        for t in ["Siguiente diapositiva", "Abre Chrome", "Gracias, Jarvis"]:
            self.assertFalse(escuchar._es_alucinacion(t), t)

    def test_segmentos_dudosos(self):
        self.assertFalse(escuchar._segmento_valido(0.9, -1.2, 1.1, "men la tu chapa"))
        self.assertFalse(escuchar._segmento_valido(0.1, -0.3, 3.0, "sí sí sí sí sí sí"))
        self.assertTrue(escuchar._segmento_valido(0.05, -0.3, 1.2, "abre la presentación"))

    def test_palabra_de_activacion(self):
        p = ["jarvis", "yarvis"]
        self.assertEqual(escuchar.quitar_activacion("Hey Jarvis, siguiente diapositiva", p),
                         "siguiente diapositiva")
        self.assertEqual(genesis._despues_de_palabra("Oye, Jarvis, abre Chrome", p), "abre Chrome")
        self.assertEqual(genesis._despues_de_palabra("bla bla yarvis siguiente", p), "siguiente")
        self.assertEqual(genesis._despues_de_palabra("sin la palabra", p), "")
        # el nombre al final: antes parecía que solo lo habían llamado
        self.assertEqual(genesis._despues_de_palabra("Oye, abre Chrome, Jarvis", p), "abre Chrome")

    def test_nombre_por_parecido(self):
        p = ["jarvis", "yarvis"]
        self.assertTrue(escuchar._buscar_nombre("yaervis abre spotify", p))
        self.assertTrue(escuchar._buscar_nombre("jarbis pon musica", p))
        self.assertIsNone(escuchar._buscar_nombre("travis scott es un rapero", p))
        self.assertIsNone(escuchar._buscar_nombre("abre los archivos del jardin", p))

    def test_umbral_sobre_el_ruido(self):
        escuchar.NIVELES.clear()
        escuchar.NIVELES.extend([0.02, 0.03, 0.04] * 50)  # ruido de un cuarto real
        self.assertGreater(escuchar.umbral_actual(0.004), 0.04)
        escuchar.NIVELES.clear()

    def test_etiqueta_de_accion_nunca_se_dice(self):
        class Loc:
            texto = ""
            def agregar(self, t): self.texto += t
            def cerrar(self): pass
        for pedazos in (["Listo, abrí Spotify. ", "[ACC", "ION: ejecu", "tando]"],
                        ["Todo bien ", "[ACCI"], ["Cuesta [en dólares] 17. [ACCION: buscando]"]):
            turno = genesis.Turno({"voz_activa": True})
            loc = Loc()
            turno._nueva = lambda loc=loc: loc
            for p in pedazos:
                turno.agregar(p)
            turno.cerrar_ronda()
            self.assertNotIn("ACC", loc.texto.upper())
        self.assertIn("[en dólares]", loc.texto)  # los corchetes normales sí se dicen


class Voz(unittest.TestCase):
    def test_partir_frases(self):
        frases = voz.partir_frases("Hola. Soy Jarvis, el asistente del equipo. Hoy les mostramos el sistema.")
        self.assertTrue(all(len(f) >= 20 for f in frases[:-1]))
        self.assertEqual(" ".join(frases).replace("  ", " "),
                         "Hola. Soy Jarvis, el asistente del equipo. Hoy les mostramos el sistema.")

    def test_limpia_simbolos(self):
        self.assertEqual(voz._limpiar("**Hola** mundo `x`"), "Hola mundo x")

    def test_locucion_en_orden_y_se_puede_cortar(self):
        dichas = []

        class Salida:
            def start(self): pass
            def write(self, d): time.sleep(len(d) / voz.TASA / 20)
            def stop(self): pass
            def abort(self): pass
            def close(self): pass

        def generar(clip, *_a, **_k):
            clip.poner(np.zeros(voz.TASA // 5, np.int16))
            clip.terminar()

        orig_abrir, orig_generar = voz.Locucion._abrir, voz._generar
        voz.Locucion._abrir = lambda self, d: Salida()
        voz._generar = generar
        try:
            loc = voz.Locucion(al_frase=dichas.append)
            for trozo in ["Buenas tardes a todos. ", "Soy Jarvis y hoy ", "les muestro el sistema. ", "Fin."]:
                loc.agregar(trozo)
            loc.cerrar()
            self.assertTrue(loc.esperar(10))
            self.assertEqual(dichas[0], "Buenas tardes a todos.")
            self.assertIn("les muestro el sistema.", " ".join(dichas))
            # corte: lo creado antes de detener() se calla, lo de después suena
            larga = voz.Locucion()
            larga.agregar("Una frase larga que se va a cortar. " * 5)
            voz.detener()
            larga.cerrar()
            larga.esperar(10)
            despues = voz.Locucion(al_frase=dichas.append)
            despues.agregar("Esto sí suena después del corte.")
            despues.cerrar()
            despues.esperar(10)
            self.assertEqual(dichas[-1], "Esto sí suena después del corte.")
        finally:
            voz.Locucion._abrir, voz._generar = orig_abrir, orig_generar


class Respuestas(unittest.TestCase):
    """responder() + Turno con un cerebro falso y una salida de audio falsa."""

    def setUp(self):
        class Salida:
            def start(self): pass
            def write(self, d): pass
            def stop(self): pass
            def abort(self): pass
            def close(self): pass

        def generar(clip, *_a, **_k):
            clip.poner(np.zeros(240, np.int16))
            clip.terminar()

        self.orig = (voz.Locucion._abrir, voz._generar, cerebro.chat, genesis.hud.subtitulo,
                     genesis.hud.estado)
        voz.Locucion._abrir = lambda self, d: Salida()
        voz._generar = generar
        self.dichas = []
        genesis.hud.subtitulo = self.dichas.append
        genesis.hud.estado = lambda *_a: None
        self.cfg = {"voz_activa": True, "respuesta_streaming": True, "skills": {}}
        skills.configurar(self.cfg)

    def tearDown(self):
        (voz.Locucion._abrir, voz._generar, cerebro.chat, genesis.hud.subtitulo,
         genesis.hud.estado) = self.orig

    def _cerebro(self, respuestas):
        llamadas = []

        def chat(cfg, history, tools, temperatura=0.2, al_texto=None, **_k):
            texto = respuestas[len(llamadas)]
            llamadas.append(texto)
            if al_texto:
                for trozo in texto.split(" "):
                    al_texto(trozo + " ")
            return {"content": texto, "tool_calls": [], "origen": "falso"}
        cerebro.chat = chat
        return llamadas

    def test_reintenta_si_llega_en_otro_idioma(self):
        llamadas = self._cerebro(["这是 una respuesta rota.", "Esta es la respuesta buena, en español."])
        turno = genesis.Turno(self.cfg, "¿qué es esto?")
        r = genesis.responder(self.cfg, [{"role": "system", "content": "s"},
                                         {"role": "user", "content": "¿qué es esto?"}], turno=turno)
        turno.cerrar()
        turno.esperar()
        self.assertEqual(len(llamadas), 2)
        self.assertIsInstance(r, genesis.YaDicho)
        self.assertEqual(self.dichas, ["Esta es la respuesta buena, en español."])

    def test_interrumpido_no_vuelve_a_preguntar(self):
        llamadas = self._cerebro(["No debería pedirse."])
        turno = genesis.Turno(self.cfg, "hola")
        turno.interrumpido = True
        with self.assertRaises(genesis.Interrumpido):
            genesis.responder(self.cfg, [{"role": "system", "content": "s"}], turno=turno)
        self.assertEqual(llamadas, [])

    def test_streaming_por_frases(self):
        self._cerebro(["Claro que sí. El sistema tiene tres módulos principales. Y todos funcionan en la nube."])
        turno = genesis.Turno(self.cfg, "explica")
        genesis.responder(self.cfg, [{"role": "system", "content": "s"}], turno=turno)
        turno.cerrar()
        turno.esperar()
        self.assertGreaterEqual(len(self.dichas), 2)
        self.assertEqual(" ".join(self.dichas),
                         "Claro que sí. El sistema tiene tres módulos principales. Y todos funcionan en la nube.")


class Conversacion(Respuestas):
    """Modo conversación: tras contestar sigue escuchando sin "Jarvis"; lo que no era para él
    (le hablaste al público) no se contesta."""

    def _cerebro_trozos(self, trozos):
        def chat(cfg, history, tools, temperatura=0.2, al_texto=None, **_k):
            for t in trozos:
                if al_texto:
                    al_texto(t)
            return {"content": "".join(trozos), "tool_calls": [], "origen": "falso"}
        cerebro.chat = chat

    def _responder(self):
        turno = genesis.Turno(self.cfg, "y luego les muestro", seguimiento=True)
        r = genesis.responder(self.cfg, [{"role": "system", "content": "s"}], turno=turno)
        turno.cerrar()
        turno.esperar()
        return r

    def test_no_era_para_el_se_calla(self):
        self._cerebro_trozos(["<ign", "orar>"])  # la marca puede llegar partida
        self.assertIsInstance(self._responder(), genesis.Ignorado)
        self.assertEqual(self.dichas, [])

    def test_si_era_para_el_contesta(self):
        self._cerebro_trozos(["Cl", "aro, ahí ", "va la respuesta."])
        r = self._responder()
        self.assertNotIsInstance(r, genesis.Ignorado)
        self.assertEqual(self.dichas, ["Claro, ahí va la respuesta."])

    def test_respuesta_muy_corta_no_se_pierde(self):
        self._cerebro_trozos(["Sí."])
        self._responder()
        self.assertEqual(self.dichas, ["Sí."])

    def test_ventana_escucha_sin_palabra(self):
        orig = (escuchar.grabar, escuchar.transcribir, genesis.hud.estado)
        frases = iter(["¿Y cuántos módulos tiene?", "Jarvis, abre Chrome"])
        escuchar.grabar = lambda *a, **k: np.zeros(1600, np.float32)
        escuchar.transcribir = lambda *a, **k: next(frases)
        genesis.hud.estado = lambda *a: None
        cfg = {"palabra_activacion": True, "conversacion_seg": 20,
               "palabras_activacion": ["jarvis"]}
        try:
            genesis.abrir_conversacion(cfg)
            self.assertEqual(genesis.obtener_entrada(cfg), ("¿Y cuántos módulos tiene?", False))
            self.assertTrue(genesis._entrada["seguimiento"])
            genesis.abrir_conversacion(cfg)
            self.assertEqual(genesis.obtener_entrada(cfg), ("abre Chrome", False))
            self.assertFalse(genesis._entrada["seguimiento"])  # dijo su nombre: sí era para él
        finally:
            escuchar.grabar, escuchar.transcribir, genesis.hud.estado = orig
            genesis.cerrar_conversacion()

    def test_clasificar_sin_ia(self):
        para_jarvis = ["¿puedes explicar el de ventas?", "ahora muéstrales cómo se agenda una cita",
                       "oye, ¿y tú cuántas personas ves en la sala?", "abre la presentación",
                       "¿qué opinas de esa pregunta?", "explícales el módulo de reportes",
                       "y ve a inventario"]
        para_publico = ["como pueden ver, compañeros, esto nos ahorró mucho tiempo",
                        "gracias por venir, empecemos con el problema que resolvemos",
                        "les voy a mostrar la siguiente parte del sistema",
                        "nosotros lo construimos en dos días", "bienvenidos a nuestra presentación"]
        dudosas = ["y esto lo construimos en solo dos días durante el hackathon", "¿y qué día es hoy?"]
        for f in para_jarvis:
            self.assertEqual(genesis.clasificar_seguimiento(f), "jarvis", f)
        for f in para_publico:
            self.assertEqual(genesis.clasificar_seguimiento(f), "publico", f)
        for f in dudosas:
            self.assertEqual(genesis.clasificar_seguimiento(f), "duda", f)

    def test_cierre_y_desactivado(self):
        self.assertIn(genesis._limpia_orden("Gracias, Jarvis"), genesis.CIERRE)
        genesis.abrir_conversacion({"palabra_activacion": True, "conversacion_seg": 0})
        self.assertFalse(genesis.en_conversacion())


class Observador(Respuestas):
    """Observa al público: dudas, pausas del expositor, complementos."""

    def test_lee_lo_que_ve(self):
        import observador
        o = observador.parsear('Aquí va: {"escena": "Sala con 30 personas", "personas": "30", '
                               '"platicando_de_frente": "true", "duda": true, "confianza_duda": 0.8, '
                               '"atencion": "Alta"} fin')
        self.assertEqual((o["personas"], o["platicando_de_frente"], o["duda"], o["atencion"]),
                         (30, True, True, "alta"))
        self.assertIsNone(observador.parsear("no es json"))

    def test_cuando_intervenir(self):
        import observador
        conf = {"umbral_duda": 0.7, "pausa_entre_intervenciones_seg": 60, "ventana_seg": 25}
        ahora = 1000.0
        duda = lambda c: {"duda": True, "confianza_duda": c}  # noqa: E731
        nada = {"duda": False, "confianza_duda": 0.0}
        self.assertFalse(observador.debe_intervenir([(ahora, duda(0.75))], ahora, 0, conf))  # gesto suelto
        self.assertTrue(observador.debe_intervenir([(ahora - 8, duda(0.7)), (ahora, duda(0.75))], ahora, 0, conf))
        self.assertTrue(observador.debe_intervenir([(ahora, duda(0.9))], ahora, 0, conf))   # muy claro
        self.assertFalse(observador.debe_intervenir([(ahora, duda(0.9))], ahora, ahora - 30, conf))  # muy seguido
        self.assertFalse(observador.debe_intervenir([(ahora - 8, duda(0.9)), (ahora, nada)], ahora, 0, conf))
        self.assertFalse(observador.debe_intervenir([(ahora - 60, duda(0.95))], ahora, 0, conf))  # ya viejo

    def test_contexto_de_la_camara(self):
        import observador
        observador._historial.clear()
        observador._historial.append((time.time(), {"platicando_de_frente": True}))
        self.assertIn("de frente", observador.contexto_marca())
        observador._historial.clear()
        self.assertEqual(observador.contexto_marca(), "")

    def test_detecta_pausas(self):
        mic = escuchar.MIC
        orig = list(mic._anillo)
        try:
            ahora = time.time()
            mic._anillo.clear()
            for i in range(25):  # 2 s de silencio
                mic._anillo.append((ahora - 2 + i * escuchar.SEG_BLOQUE, np.zeros(escuchar.BLOQUE, np.float32)))
            self.assertTrue(escuchar.en_pausa(1.3, umbral=0.004))
            mic._anillo.append((ahora, np.full(escuchar.BLOQUE, 0.05, np.float32)))  # habló
            self.assertFalse(escuchar.en_pausa(1.3, umbral=0.004))
        finally:
            mic._anillo.clear()
            mic._anillo.extend(orig)

    def test_no_se_graba_a_si_mismo(self):
        import queue as q
        sub = q.Queue()
        for _ in range(30):
            sub.put((time.time(), np.full(escuchar.BLOQUE, 0.05, np.float32)))
        voz.HABLANDO.set()
        try:
            self.assertIsNone(escuchar._grabar_de_sub(sub, 0.004, 0.3, 5, espera_seg=0.5))
        finally:
            voz.HABLANDO.clear()

    def test_complemento_se_guarda_para_la_pausa(self):
        def chat(cfg, history, tools, temperatura=0.2, al_texto=None, **_k):
            for t in ["<compl", "ementar> Si me permites agregar, ", "también funciona sin internet."]:
                al_texto(t)
            return {"content": "", "tool_calls": [], "origen": "falso"}
        cerebro.chat = chat
        turno = genesis.Turno(self.cfg, "como pueden ver", seguimiento=True)
        r = genesis.responder(self.cfg, [{"role": "system", "content": "s"}], turno=turno)
        turno.cerrar()
        turno.esperar()
        self.assertIsInstance(r, genesis.Complemento)
        self.assertEqual(str(r), "Si me permites agregar, también funciona sin internet.")
        self.assertEqual(self.dichas, [])  # nada se dijo todavía

    def test_intervenir_escucha_la_respuesta(self):
        orig = genesis.decir
        genesis.decir = lambda *a, **k: None
        try:
            genesis._notas.clear()
            genesis.intervenir({"palabra_activacion": True, "conversacion_seg": 20}, "¿Te quedó alguna duda?")
            self.assertEqual(genesis._notas[-1]["content"], "¿Te quedó alguna duda?")
            self.assertTrue(genesis.en_conversacion())
            self.assertTrue(escuchar.CONVERSAR.is_set())
        finally:
            genesis.decir = orig
            genesis._notas.clear()
            genesis.cerrar_conversacion()
            escuchar.CONVERSAR.clear()


class Confirmaciones(Respuestas):
    """Lo que pasó en la prueba real: tras leer la pantalla quiso pulsar «EMBIO 2.0», le dijeron
    que no y lo volvió a pedir una y otra vez, tragándose las órdenes siguientes."""

    def test_si_no_o_ninguna(self):
        self.assertTrue(skills.respuesta_si_no("Sí, hazlo"))
        self.assertFalse(skills.respuesta_si_no("No sé..."))
        self.assertIsNone(skills.respuesta_si_no("Dime qué estamos viendo"))
        self.assertIsNone(skills.respuesta_si_no(""))

    def test_un_no_termina_la_orden(self):
        llamadas = []

        def chat(cfg, history, tools, temperatura=0.2, al_texto=None, **_k):
            llamadas.append(1)  # el modelo insiste con lo mismo
            return {"content": "", "tool_calls": [{"id": f"c{len(llamadas)}", "name": "clic_en",
                                                   "args": {"texto": "EMBIO 2.0"}}], "origen": "falso"}
        cerebro.chat = chat
        orig = (genesis._preguntar, genesis.ejecutar_herramienta)
        preguntas = []
        genesis._preguntar = lambda cfg, p: (preguntas.append(p), False)[1]
        genesis.ejecutar_herramienta = lambda cfg, n, a: "Leí la ventana." if n == "leer_ventana" else "ok"
        try:
            # primero lee la pantalla (contenido externo), luego quiere pulsar algo que no pediste
            history = [{"role": "system", "content": "s"},
                       {"role": "tool", "id": "x", "name": "leer_ventana", "content": "..."}]
            turno = genesis.Turno(self.cfg, "dime qué estamos viendo")
            orig_ext = skills.es_externo
            skills.es_externo = lambda n: n in ("leer_ventana", "clic_en")
            try:
                r = genesis.responder(self.cfg, history, turno=turno, texto_usuario="dime qué estamos viendo")
            finally:
                skills.es_externo = orig_ext
            turno.cerrar()
            turno.esperar()
            self.assertLessEqual(len(preguntas), 1)  # a lo mucho UNA pregunta, nunca 8
            self.assertLessEqual(len(llamadas), 2)
            self.assertIn("no lo hago", str(r).lower())
        finally:
            genesis._preguntar, genesis.ejecutar_herramienta = orig

    def test_otra_orden_en_vez_de_contestar(self):
        orig = genesis._preguntar

        def preguntar(cfg, p):
            genesis._orden_pendiente.append("Dime qué estamos viendo")
            return False
        genesis._preguntar = preguntar
        try:
            self.assertFalse(genesis.confirmar({}, "¿Lo hago?"))
            self.assertTrue(genesis._confirmacion["negada"])
            self.assertEqual(genesis.obtener_entrada({"palabra_activacion": True}),
                             ("Dime qué estamos viendo", False))
        finally:
            genesis._preguntar = orig
            genesis._orden_pendiente.clear()


class Criterio(unittest.TestCase):
    """Cuándo Jarvis razona a fondo (opinión) y cuándo contesta rápido (orden)."""

    def test_pide_opinion(self):
        for f in ["¿Qué opinas de usar los lentes?", "¿qué le mejorarías al sistema?",
                  "¿cuál es mejor, React o Vue?", "¿por qué falló?", "¿cómo lo ves?",
                  "¿estás de acuerdo con el jurado?", "¿crees que funcione?"]:
            self.assertTrue(genesis.pide_opinion(f), f)
        for f in ["abre la calculadora", "siguiente", "ve a inventario", "¿qué hora es?"]:
            self.assertFalse(genesis.pide_opinion(f), f)

    def test_espera_sugerida(self):
        self.assertEqual(cerebro.espera_sugerida(Exception("Please try again in 2.3s.")), 2.3)
        self.assertAlmostEqual(cerebro.espera_sugerida(Exception("try again in 850ms")), 0.85)
        self.assertIsNone(cerebro.espera_sugerida(Exception("otro error")))

    def test_presentador_en_la_personalidad(self):
        cfg = {"personality": "Trabajas con {presentador}.", "expositor": {"presentador": "Chris"}}
        self.assertEqual(genesis._personalidad(cfg, False), "Trabajas con Chris.")


class Historial(unittest.TestCase):
    def test_compactar_no_deja_herramientas_huerfanas(self):
        h = [{"role": "system", "content": "s"}]
        for i in range(25):
            h += [{"role": "user", "content": f"u{i}"},
                  {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "name": "x", "args": {}}]},
                  {"role": "tool", "id": f"c{i}", "name": "x", "content": "r" * 1000}]
        genesis._compactar(h)
        self.assertLessEqual(len(h) - 1, genesis.MAX_MENSAJES)
        self.assertEqual(h[1]["role"], "user")
        self.assertTrue(all(len(m["content"]) <= genesis.MAX_TOOL_VIEJO + 20 for m in h if m["role"] == "tool"))


class Cerebro(unittest.TestCase):
    def test_argumentos_y_host(self):
        self.assertEqual(cerebro._args('{"a": 1}'), {"a": 1})
        self.assertEqual(cerebro._args("no es json"), {})
        self.assertEqual(cerebro._args("[1, 2]"), {})
        self.assertEqual(cerebro.host_de("https://api.groq.com/openai/v1"), "api.groq.com")

    def test_error_de_red(self):
        class APIConnectionError(Exception):
            pass
        self.assertTrue(cerebro.es_error_de_red(APIConnectionError()))
        self.assertTrue(cerebro.es_error_de_red(TimeoutError()))
        self.assertFalse(cerebro.es_error_de_red(ValueError()))


class Graph(unittest.TestCase):
    def test_reparto_respeta_el_tope(self):
        for n in range(1, 7):
            topes = graph._repartir(graph.MAX_TOTAL, n)
            self.assertLessEqual(sum(topes), graph.MAX_TOTAL + 800 * n)
            self.assertEqual(topes, sorted(topes, reverse=True))

    def test_docx_incluye_tablas(self):
        import io

        from docx import Document
        d = Document()
        d.add_paragraph("Actividad 1.4: programa")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Criterio", "Puntos"
        t.cell(1, 0).text, t.cell(1, 1).text = "Funciona", "50"
        buf = io.BytesIO()
        d.save(buf)
        texto = graph._texto_docx(buf.getvalue())
        self.assertIn("Actividad 1.4", texto)
        self.assertIn("Criterio | Puntos", texto)
        self.assertIn("Funciona | 50", texto)


class Mantenimiento(unittest.TestCase):
    def test_solo_borra_temporales_viejos(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            viejo, nuevo = base / "viejo.tmp", base / "nuevo.tmp"
            viejo.write_bytes(b"x" * 100)
            nuevo.write_bytes(b"y" * 100)
            hace_una_semana = time.time() - 7 * 86400
            os.utime(viejo, (hace_una_semana, hace_una_semana))
            antes = mantenimiento.TEMP_DIRS
            mantenimiento.TEMP_DIRS = [base]
            try:
                mantenimiento._limpiar_temporales_real()
            finally:
                mantenimiento.TEMP_DIRS = antes
            self.assertFalse(viejo.exists())
            self.assertTrue(nuevo.exists())


class Configuracion(unittest.TestCase):
    def test_guardar_campos_no_pierde_lo_demas(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "config.json"
            configuracion.guardar({"a": 1, "b": "ñ"}, ruta)
            configuracion.guardar_campos({"b": "otra"}, ruta)
            self.assertEqual(configuracion.cargar(ruta), {"a": 1, "b": "otra"})
            self.assertFalse(ruta.with_suffix(".json.tmp").exists())


class Microfono(unittest.TestCase):
    """El cambio automático a respaldo cuando el cable de la videollamada se queda mudo."""

    class Flujo:
        def __init__(self):
            self.active = True

        def start(self): pass
        def abort(self): self.active = False
        def close(self): pass

    def test_pasa_a_respaldo_y_regresa(self):
        mic = escuchar.Microfono()
        mic.silencio_respaldo = 0.3
        p = escuchar._Fuente("principal", 1)
        r = escuchar._Fuente("respaldo", 2)
        p.stream, r.stream = self.Flujo(), self.Flujo()
        mic._fuentes = {"principal": p, "respaldo": r}
        ruido = np.full((escuchar.BLOQUE, 1), 0.01, np.float32)
        ceros = np.zeros((escuchar.BLOQUE, 1), np.float32)
        mic._llega(p, ruido)
        mic._llega(r, ruido)
        mic._revisar()
        self.assertEqual(mic.activa, "principal")
        time.sleep(0.4)          # la llamada se cae: el cable manda puros ceros
        mic._llega(p, ceros)
        mic._llega(r, ruido)
        mic._revisar()
        self.assertEqual(mic.activa, "respaldo")
        mic._llega(p, ruido)     # vuelve la llamada
        mic._revisar()
        self.assertEqual(mic.activa, "principal")

    def test_suscriptores_reciben_solo_lo_activo(self):
        mic = escuchar.Microfono()
        mic._corriendo = True  # sin abrir dispositivos de verdad
        p = escuchar._Fuente("principal", 1)
        r = escuchar._Fuente("respaldo", 2)
        mic._fuentes = {"principal": p, "respaldo": r}
        q = mic.suscribir()
        mic._llega(p, np.full((escuchar.BLOQUE, 1), 0.5, np.float32))
        mic._llega(r, np.full((escuchar.BLOQUE, 1), 0.9, np.float32))
        _t, bloque = q.get_nowait()
        self.assertAlmostEqual(float(bloque[0]), 0.5)
        self.assertTrue(q.empty())
        self.assertEqual(len(mic.reciente(5)), escuchar.BLOQUE)


class Cognicion(unittest.TestCase):
    """La parte pensante: cuánto pensar, calcular con exactitud y qué aprender del usuario."""

    def test_nivel_de_pensamiento(self):
        import cognicion
        self.assertEqual(cognicion.nivel("abre spotify"), "rapido")
        self.assertEqual(cognicion.nivel("sube el volumen a 60"), "rapido")
        self.assertEqual(cognicion.nivel("¿cómo organizo mi día si tengo examen?"), "profundo")
        self.assertEqual(cognicion.nivel("¿qué me conviene, la laptop o la tablet?"), "profundo")
        self.assertEqual(cognicion.nivel("¿quién ganó el mundial pasado?"), "normal")

    def test_calcular_exacto_y_seguro(self):
        import cognicion
        self.assertTrue(cognicion.calcular("200 - (3*45 + 2*12.50)").endswith("= 40"))
        self.assertTrue(cognicion.calcular("1500 * 16%").endswith("= 240"))
        self.assertIsInstance(cognicion.calcular("10/0"), skills.Fallo)
        self.assertIsInstance(cognicion.calcular('__import__("os").system("dir")'), skills.Fallo)
        self.assertIsInstance(cognicion.calcular("9**999"), skills.Fallo)

    def test_calendario(self):
        import cognicion
        self.assertIn("jueves", cognicion.calendario("dia_de_la_semana", "2026-10-15"))
        self.assertIn("82 días", cognicion.calendario("dias_entre", "2026-10-04", "2026-12-25"))
        self.assertIn("miércoles 14 de octubre", cognicion.calendario("sumar_dias", "2026-10-04", dias=10))

    def test_aprende_solo_lo_util_y_seguro(self):
        import cognicion
        import memoria
        guardados = []
        viejo = (memoria.listar_hechos, memoria.agregar_hecho)
        memoria.listar_hechos = lambda: [{"texto": "Le gusta el rock"}]
        memoria.agregar_hecho = guardados.append
        try:
            nuevos = cognicion._guardar(["Le gusta el rock.", "Su contraseña es hola123",
                                         "Su tarjeta es 4152 3135 0000 1234", "Estudia ingeniería"])
        finally:
            memoria.listar_hechos, memoria.agregar_hecho = viejo
        self.assertEqual(nuevos, ["Estudia ingeniería"])  # sin repetidos ni datos sensibles
        self.assertTrue(cognicion.SOBRE_SI.search(skills._norm("me encanta el café de olla")))
        self.assertFalse(cognicion.SOBRE_SI.search(skills._norm("abre spotify")))


class MemoriaYPreferencias(unittest.TestCase):
    """Lo que Jarvis aprende y sigue usando: preferencias, instrucciones y conversaciones."""

    def setUp(self):
        import memoria
        self._dir = tempfile.TemporaryDirectory()
        self._db = memoria.DB_PATH
        memoria.DB_PATH = Path(self._dir.name) / "prueba.db"

    def tearDown(self):
        import memoria
        memoria.DB_PATH = self._db
        self._dir.cleanup()

    def test_categorias(self):
        import preferencias as pr
        self.assertEqual(pr.categoria_de("el navegador"), "navegador")
        self.assertEqual(pr.categoria_de("mi browser"), "navegador")
        self.assertEqual(pr.categoria_de("mi editor de código"), "codigo")
        self.assertEqual(pr.categoria_de("el buscador"), "buscador")

    def test_preferencia_se_usa_y_se_cambia(self):
        import apps
        import preferencias as pr
        abiertos, viejo = [], (apps.buscar_app, os.startfile)
        apps.buscar_app = lambda n: ("Opera GX", r"C:\Opera\opera.exe", 0.9) if "opera" in n.lower() \
            else ("Google Chrome", r"C:\Chrome\chrome.exe", 0.9)
        os.startfile = lambda ruta, *a: abiertos.append((ruta, a[1] if len(a) > 1 else None))
        try:
            self.assertIn("Opera", pr.fijar_preferencia("navegador", "opera"))
            pr.abrir_url("https://ejemplo.com")
            self.assertEqual(abiertos[-1], (r"C:\Opera\opera.exe", "https://ejemplo.com"))
            self.assertEqual(pr.app_para("el navegador")[0], "Opera GX")
            self.assertIn("cambié", pr.fijar_preferencia("navegador", "chrome"))
            pr.abrir_url("https://ejemplo.com")
            self.assertEqual(abiertos[-1][0], r"C:\Chrome\chrome.exe")
            self.assertEqual(len(pr.todas()), 1)  # se reemplaza, no se acumula
        finally:
            apps.buscar_app, os.startfile = viejo

    def test_buscador_y_servicios(self):
        import preferencias as pr
        self.assertIn("google.com", pr.url_busqueda("pozole"))
        pr.fijar_preferencia("buscador", "DuckDuckGo")
        self.assertIn("duckduckgo.com", pr.url_busqueda("pozole"))
        pr.fijar_preferencia("música", "YouTube")
        self.assertIn("música: YouTube", pr.contexto().replace("musica", "música"))
        self.assertIsInstance(pr.fijar_preferencia("buscador", "Altavista 3000"), skills.Fallo)

    def test_reglas_sin_duplicar_y_olvidar(self):
        import preferencias as pr
        pr.aprender_regla("Siempre que te pida un resumen, guárdalo en Word")
        pr.aprender_regla("siempre que te pida un resumen guárdalo en Word por favor")
        self.assertEqual(len(pr.reglas()), 1)
        self.assertIn("INSTRUCCIONES PERMANENTES", pr.contexto())
        self.assertIsInstance(pr.aprender_regla("mi contraseña del banco es 1234"), skills.Fallo)
        pr.olvidar_preferencia("resumen word")
        self.assertEqual(pr.reglas(), [])

    def test_recordar_conversacion(self):
        import memoria
        memoria.guardar_mensaje("user", "Busca en internet recetas de pozole")
        memoria.guardar_mensaje("assistant", "Buscando recetas de pozole.")
        memoria.guardar_mensaje("user", "¿De qué hablamos del pozole?")
        memoria.guardar_mensaje("assistant", "No recuerdo haber hablado de pozole.")
        r = memoria.recordar_conversacion(tema="pozole", cuando="hace rato")
        self.assertIn("recetas de pozole", r)
        self.assertNotIn("De qué hablamos", r)  # las preguntas de memoria no tapan lo importante


class Avatares(unittest.TestCase):
    """Los GIF del avatar: categoría por nombre, quitar cualquier fondo liso y medir."""

    def test_categoria_por_nombre(self):
        import avatares
        self.assertEqual(avatares.categoria_por_nombre("celebrando_baile"), "celebrando")
        self.assertEqual(avatares.categoria_por_nombre("ejecutando_3"), "ejecutando")
        self.assertEqual(avatares.categoria_por_nombre("libre_caminando"), "libre")
        self.assertEqual(avatares.categoria_por_nombre("Espera"), "espera")
        self.assertIsNone(avatares.categoria_por_nombre("cartas"))

    def _personaje(self, fondo):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (120, 160), fondo)
        d = ImageDraw.Draw(im)
        d.ellipse((45, 10, 75, 40), fill=(240, 200, 150))
        d.rectangle((48, 42, 72, 110), fill=(200, 40, 40))
        d.rectangle((20, 120, 40, 150), fill=(90, 90, 90))  # un objeto más chico al lado
        return im

    def test_quita_fondos_de_color(self):
        import avatares
        for fondo in ((170, 210, 245), (255, 255, 255), (51, 51, 51)):
            a = avatares.sin_fondo(self._personaje(fondo)).getchannel("A")
            self.assertEqual(a.getpixel((2, 2)), 0, fondo)       # el fondo, transparente
            self.assertEqual(a.getpixel((60, 80)), 255, fondo)   # el cuerpo, entero

    def test_respeta_gif_transparente(self):
        from PIL import Image
        import avatares
        im = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
        im.paste((10, 200, 10, 255), (15, 15, 35, 35))
        a = avatares.sin_fondo(im).getchannel("A")
        self.assertEqual((a.getpixel((2, 2)), a.getpixel((25, 25))), (0, 255))

    def test_mide_al_personaje_no_al_objeto(self):
        import avatares
        alto, pies, centro = avatares._medir(avatares.sin_fondo(self._personaje((170, 210, 245))))
        self.assertGreater(alto, 90)          # cabeza a cuerpo (unidos), no el objeto de 30 px
        self.assertAlmostEqual(centro, 60, delta=6)


if __name__ == "__main__":
    unittest.main()
