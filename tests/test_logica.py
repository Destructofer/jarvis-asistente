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

    def test_salir_con_acento(self):
        for frase in ["Adiós.", "adios", "Hasta luego.", "Jarvis, adiós"]:
            self.assertIn(genesis._limpia_orden(frase), genesis.SALIDAS, frase)


class Presentacion(unittest.TestCase):
    def setUp(self):
        self._en_curso = presentacion.en_curso
        presentacion.en_curso = lambda: True

    def tearDown(self):
        presentacion.en_curso = self._en_curso

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

        def chat(cfg, history, tools, temperatura=0.2, al_texto=None):
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


if __name__ == "__main__":
    unittest.main()
