"""Pruebas del navegador de la demo contra una app web de prueba, con Chrome oculto.

Se saltan solas si no están Playwright o Chrome. Tardan ~15 s (abren un Chrome sin ventana).
"""
import functools
import http.server
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import skills  # noqa: E402

try:
    import navegador  # noqa: E402
    HAY_PLAYWRIGHT = True
except ImportError:
    HAY_PLAYWRIGHT = False

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
MENU = [("index.html", "Inicio"), ("inventario.html", "Inventario"), ("ventas.html", "Ventas"),
        ("salir.html", "Cerrar sesión")]
PAGINAS = {
    "index.html": ("Panel principal", "<section><h2>Resumen del día</h2><p>Ventas: 42.</p></section>"),
    "inventario.html": ("Inventario", "<section><h2>Productos en existencia</h2><table><tr><th>Producto</th>"
                        "</tr><tr><td>Laptop</td></tr></table><button>Agregar producto</button></section>"),
    "ventas.html": ("Ventas", "<label>Cliente <input name='cliente'></label><button>Registrar venta</button>"),
    "salir.html": ("Sesión cerrada", "<p>No debería verse en un recorrido.</p>"),
}


class Puras(unittest.TestCase):
    @unittest.skipUnless(HAY_PLAYWRIGHT, "sin Playwright")
    def test_elige_el_modulo_por_parecido(self):
        items = [{"texto": t, "peligroso": False, "omitido": False} for _a, t in MENU]
        self.assertEqual(navegador._mejor_modulo("inventarios", items)[1]["texto"], "Inventario")
        self.assertEqual(navegador._mejor_modulo("las ventas", items)[1]["texto"], "Ventas")
        self.assertIsNone(navegador._mejor_modulo("recursos humanos", items)[1])

    @unittest.skipUnless(HAY_PLAYWRIGHT, "sin Playwright")
    def test_recorrido_respeta_config_y_omite_peligrosos(self):
        items = [{"texto": t, "peligroso": t == "Cerrar sesión", "omitido": False} for _a, t in MENU]
        skills.configurar({"demo": {"url": "x", "modulos": []}})
        navegador.configurar({"demo": {"url": "x", "modulos": []}})
        self.assertNotIn("Cerrar sesión", [it["texto"] for _i, it in navegador._modulos_del_recorrido(items)])
        navegador.configurar({"demo": {"url": "x", "modulos": ["Ventas", {"nombre": "Inventario"}]}})
        self.assertEqual([it["texto"] for _i, it in navegador._modulos_del_recorrido(items)],
                         ["Ventas", "Inventario"])


@unittest.skipUnless(HAY_PLAYWRIGHT and CHROME.exists(), "sin Playwright o Chrome")
class ConChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="jarvis_app_"))
        for archivo, (titulo, cuerpo) in PAGINAS.items():
            menu = "".join(f"<a href='{a}'>{t}</a>" for a, t in MENU)
            (cls.dir / archivo).write_text(
                f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>SIMU · {titulo}</title>"
                f"</head><body><aside><nav>{menu}</nav></aside><main><h1>{titulo}</h1>{cuerpo}</main>"
                "</body></html>", encoding="utf-8")
        manejador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(cls.dir))
        manejador.func.log_message = lambda *a: None
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cfg = {"demo": {"url": f"http://127.0.0.1:{cls.srv.server_port}/index.html", "headless": True,
                        "pantalla_completa": False}}
        skills.configurar(cfg)
        navegador.PERFIL = cls.dir / "perfil"
        navegador.CACHE = cls.dir / "cache.json"
        cls.dicho = []
        navegador.configurar(cfg, hablar=cls.dicho.append, confirmar=lambda q: False)
        navegador._plan_explicacion = lambda lectura, enfoque="", pasos=3: [
            {"elemento": "", "decir": f"Pantalla {lectura['titulo']}."}]

    @classmethod
    def tearDownClass(cls):
        try:
            navegador._hilo.ejecutar(lambda: navegador._estado["contexto"] and navegador._estado["contexto"].close())
        except Exception:
            pass
        cls.srv.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_flujo_completo(self):
        self.assertNotIsInstance(navegador.skill_abrir_sistema(), skills.Fallo)
        mods = navegador._hilo.ejecutar(navegador._modulos_en_hilo)
        self.assertEqual([m["texto"] for m in mods], [t for _a, t in MENU])
        self.assertTrue(next(m for m in mods if m["texto"] == "Cerrar sesión")["peligroso"])

        self.assertEqual(navegador.ir_a_modulo("inventarios"), "Estamos en Inventario.")
        self.assertTrue(navegador._hilo.ejecutar(navegador._resaltar_en_hilo, "Productos en existencia"))
        marco = navegador._hilo.ejecutar(lambda: navegador._pagina_viva().evaluate(
            "() => !!document.querySelector('.__jv_marco')"))
        self.assertTrue(marco)

        navegador.ir_a_modulo("Ventas")
        navegador.llenar_campo("Cliente", "Ana López")
        valor = navegador._hilo.ejecutar(lambda: navegador._pagina_viva().input_value("input[name=cliente]"))
        self.assertEqual(valor, "Ana López")

        self.dicho.clear()
        self.assertEqual(str(navegador.recorrer_modulos()), "Recorrido completado.")
        self.assertFalse(any("Sesión cerrada" in d for d in self.dicho), self.dicho)
        self.assertIn("Pantalla SIMU · Ventas.", self.dicho)


if __name__ == "__main__":
    unittest.main()
