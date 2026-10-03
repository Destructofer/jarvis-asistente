"""Gestos, presencia y nubes gratis: la lógica sin cámara ni internet (datos inventados)."""
import os
import queue
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cerebro  # noqa: E402
import genesis  # noqa: E402
import gestos  # noqa: E402
import presencia  # noqa: E402
import vision  # noqa: E402


def mano(cx=0.5, cy=0.5, abierta=True, pellizco=False):
    """21 puntos de una mano de frente centrada en (cx, cy)."""
    p = [(cx, cy + 0.15)] * 21  # muñeca
    for i, dx in ((5, -0.06), (9, -0.02), (13, 0.02), (17, 0.06)):
        p[i] = (cx + dx, cy)                                   # nudillos
        p[i + 1] = (cx + dx, cy - 0.05)                        # articulación media
        p[i + 3] = (cx + dx, cy - (0.12 if abierta else 0.0))  # punta
    p[4] = (cx - 0.06, cy - 0.11) if pellizco else (cx - 0.12, cy)  # pulgar
    return p


class Gestos(unittest.TestCase):
    def test_sostener_dispara_una_vez(self):
        d = gestos.Detector()
        eventos = [d.actualizar(t / 15, "Open_Palm", 0.9, mano()) for t in range(30)]
        self.assertEqual([e for e in eventos if e], ["palma"])

    def test_gesto_pasajero_no_dispara(self):
        d = gestos.Detector()
        eventos = [d.actualizar(t / 15, "Thumb_Up", 0.9, mano()) for t in range(4)]  # ~0.27 s
        eventos.append(d.actualizar(5 / 15, None, 0.0, None))
        self.assertFalse(any(eventos))

    def test_mano_en_movimiento_no_es_gesto(self):
        d = gestos.Detector()
        eventos = [d.actualizar(t / 15, "Victory", 0.9, mano(cx=0.3 + 0.012 * t, cy=0.4 + 0.012 * t))
                   for t in range(20)]
        self.assertNotIn("victoria", eventos)

    def test_soltar_y_repetir(self):
        d = gestos.Detector()
        primero = [d.actualizar(t / 15, "Thumb_Up", 0.9, mano()) for t in range(12)]
        hueco = [d.actualizar(1 + t / 15, None, 0.0, None) for t in range(10)]
        segundo = [d.actualizar(2 + t / 15, "Thumb_Up", 0.9, mano()) for t in range(12)]
        self.assertEqual([e for e in primero + hueco + segundo if e], ["pulgar_arriba", "pulgar_arriba"])

    def test_cuadro_mal_reconocido_no_repite(self):
        d = gestos.Detector()
        eventos = [d.actualizar(t / 15, "Open_Palm", 0.9, mano()) for t in range(10)]
        eventos.append(d.actualizar(10 / 15, None, 0.2, mano()))  # un cuadro dudoso
        eventos += [d.actualizar((11 + t) / 15, "Open_Palm", 0.9, mano()) for t in range(10)]
        self.assertEqual([e for e in eventos if e], ["palma"])

    def test_deslizar(self):
        d = gestos.Detector()
        izq = [d.actualizar(t / 15, None, 0.0, mano(cx=0.75 - 0.06 * t)) for t in range(8)]
        self.assertIn("deslizar_izquierda", izq)
        d = gestos.Detector()
        der = [d.actualizar(t / 15, None, 0.0, mano(cx=0.25 + 0.06 * t)) for t in range(8)]
        self.assertIn("deslizar_derecha", der)

    def test_deslizar_con_puno_no_cuenta(self):
        d = gestos.Detector()
        eventos = [d.actualizar(t / 15, None, 0.0, mano(cx=0.75 - 0.06 * t, abierta=False))
                   for t in range(8)]
        self.assertFalse(any(eventos))

    def test_mano_lejana_no_cuenta(self):
        self.assertTrue(gestos.cerca(mano()))                       # 0.15 de alto: la tuya
        lejana = [(0.5 + (x - 0.5) / 4, 0.5 + (y - 0.5) / 4) for x, y in mano()]
        self.assertFalse(gestos.cerca(lejana))                       # alguien del público
        self.assertFalse(gestos.cerca(None))

    def test_raton(self):
        r = gestos.Raton(1920, 1080, {"suavizado_mouse": 1.0})
        (accion,) = r.actualizar(mano(cx=0.5, cy=0.5))
        self.assertEqual(accion[0], "mover")
        self.assertAlmostEqual(accion[1], 1919 * (0.44 - 0.2) / 0.6, delta=2)  # punta del índice
        self.assertEqual(r.actualizar(mano(cx=0.5, cy=0.5, pellizco=True)), [("clic",)])
        self.assertEqual(r.actualizar(mano(cx=0.5, cy=0.5, pellizco=True)), [])  # no repite el clic

    def test_acciones_configurables(self):
        hechas = []
        gestos.hooks.update({"callar": lambda: hechas.append("callar"),
                             "orden": lambda t: hechas.append(("orden", t))})
        cfg = {"gestos": {"acciones": {"puno": "orden:pausa la música"}}}
        with unittest.mock.patch.object(gestos, "_cfg", return_value=cfg), \
                unittest.mock.patch.object(gestos, "_avisar"):
            gestos.ejecutar("palma")
            gestos.ejecutar("puno")
        self.assertEqual(hechas, ["callar", ("orden", "pausa la música")])

    def test_pulgar_contesta_confirmacion(self):
        self.assertFalse(genesis.confirmar_por_gesto(True))  # nada que confirmar
        cola = queue.Queue()
        genesis._esperando_si_no["cola"] = cola
        try:
            self.assertTrue(genesis.confirmar_por_gesto(False))
            self.assertEqual(cola.get_nowait(), ("gesto", False))
        finally:
            genesis._esperando_si_no["cola"] = None


class Presencia(unittest.TestCase):
    def test_llegar_e_irse(self):
        p = presencia.Presencia({"llegar_seg": 1, "ausencia_seg": 10})
        self.assertIsNone(p.actualizar(0, True))
        self.assertEqual(p.actualizar(1.2, True), ("llego", None))  # primera vez
        self.assertIsNone(p.actualizar(5, False))                    # volteó un momento
        self.assertIsNone(p.actualizar(6, True))
        self.assertEqual(p.actualizar(17, False), ("se_fue", None))
        p.actualizar(400, True)
        self.assertEqual(p.actualizar(401.5, True), ("llego", 395.5))  # desde que lo vio por última vez

    def test_cuando_saludar(self):
        conf = {"saludar_tras_min": 5, "pausa_saludos_min": 20}
        self.assertTrue(presencia.debe_saludar(None, 10_000, 0, conf))      # al encender
        self.assertFalse(presencia.debe_saludar(60, 10_000, 0, conf))       # salió 1 minuto
        self.assertTrue(presencia.debe_saludar(600, 10_000, 0, conf))       # volvió tras 10 min
        self.assertFalse(presencia.debe_saludar(600, 10_000, 9_500, conf))  # ya saludó hace poco
        self.assertFalse(presencia.debe_saludar(None, 10_000, 0, {"saludar": False}))


class NubesGratis(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["GROQ_API_KEY"] = "x"
        os.environ.pop("GEMINI_API_KEY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def cfg(self, preferir=False):
        return {"nube": {"url": "https://api.groq.com/openai/v1", "modelo": "openai/gpt-oss-120b",
                         "clave_env": "GROQ_API_KEY", "respaldos": [], "preferir_extras": preferir},
                "nubes_extra": [{"url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                                 "modelo": "gemini-3.1-flash-lite", "clave_env": "GEMINI_API_KEY"}]}

    def test_sin_clave_se_salta(self):
        self.assertEqual([p["modelo"] for p in cerebro._proveedores(self.cfg())], ["openai/gpt-oss-120b"])

    def test_orden(self):
        os.environ["GEMINI_API_KEY"] = "y"
        self.assertEqual([p["modelo"] for p in cerebro._proveedores(self.cfg())],
                         ["openai/gpt-oss-120b", "gemini-3.1-flash-lite"])
        self.assertEqual([p["modelo"] for p in cerebro._proveedores(self.cfg(True))],
                         ["gemini-3.1-flash-lite", "openai/gpt-oss-120b"])

    def test_firma_de_gemini(self):
        historia = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "name": "volumen", "args": {"nivel": 3}, "firma": "SIG"},
            {"id": "b", "name": "hora_fecha", "args": {}}]}]
        groq = cerebro._a_openai(historia)
        self.assertNotIn("extra_content", groq[0]["tool_calls"][0])
        gem = cerebro._a_openai(historia, gemini=True)
        self.assertEqual(gem[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"], "SIG")
        self.assertEqual(gem[0]["tool_calls"][1]["extra_content"]["google"]["thought_signature"],
                         "skip_thought_signature_validator")

    def test_razonamiento(self):
        self.assertTrue(cerebro.acepta_razonamiento("gemini-3.1-flash-lite"))
        self.assertTrue(cerebro.acepta_razonamiento("openai/gpt-oss-120b"))
        self.assertFalse(cerebro.acepta_razonamiento("qwen/qwen3.8-27b"))

    def test_vision_con_respaldo(self):
        conf = {"nube": {"url": "u", "modelo": "qwen", "clave_env": "GROQ_API_KEY"},
                "respaldos": [{"url": "g", "modelo": "gemini-3.1-flash-lite", "clave_env": "GEMINI_API_KEY"}]}
        self.assertEqual([n["modelo"] for n in vision._proveedores(conf)], ["qwen"])
        os.environ["GEMINI_API_KEY"] = "y"
        llamadas = []

        def falso(conf, nube, *a):
            llamadas.append(nube["modelo"])
            if nube["modelo"] == "qwen":
                raise RuntimeError("429 rate limit")
            return "Veo una taza."
        with unittest.mock.patch.object(vision, "_nube_varias", falso):
            self.assertEqual(vision._en_la_nube(conf, "s", "i", ["b64"]), "Veo una taza.")
        self.assertEqual(llamadas, ["qwen", "gemini-3.1-flash-lite"])


class Herramientas(unittest.TestCase):
    def test_mirarme_trae_la_camara_de_la_pc(self):
        for frase in ["mírame", "¿cómo me veo?", "¿qué tengo en la mano?", "activa el modo mouse"]:
            nombres = genesis.elegir_herramientas(frase, [])
            self.assertTrue({"mirar_usuario", "gestos"} & nombres, frase)
        self.assertNotIn("mirar_usuario", genesis.elegir_herramientas("háblame de un humano", []))


if __name__ == "__main__":
    unittest.main()
