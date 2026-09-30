"""Jarvis maneja el software de la demo en el navegador, como un integrante del equipo.

Controla un Chrome propio (perfil en datos/chrome-jarvis, separado de tu Chrome personal: ahí
queda la sesión iniciada de tu sistema) con Playwright, que lee la página directamente del
HTML. Comparado con dar clic "por accesibilidad" (control.clic_en):

- Encuentra el menú y los botones al instante y con precisión, aunque cambien de lugar.
- Muestra un CURSOR DE JARVIS animado (un círculo luminoso) que viaja hasta el botón y hace
  una onda al dar clic: el público ve que es Jarvis quien opera el sistema.
- Explica una pantalla SEÑALANDO: ilumina cada parte mientras habla de ella.
- "Ensaya la demo": visita cada módulo antes de la exposición, prepara la explicación y deja
  la voz generada. En escena el recorrido sale al instante y sin depender de la IA en vivo.

Configuración en config.json → demo (url, pantalla_completa, menu_selector, modulos, omitir,
login...). Ver GUIA_JARVIS.md.
"""
import json
import queue
import random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import cerebro
import conocimiento
import control
import skills
from apps import puntaje
from skills import Callado, Fallo, _norm, skill

BASE = Path(__file__).parent
PERFIL = BASE / "datos" / "chrome-jarvis"
CACHE = BASE / "datos" / "demo_cache.json"

_cfg = {}
_hablar = None      # fn(texto): habla con la voz de Jarvis (bloquea hasta terminar)
_confirmar = None   # fn(pregunta) -> bool
_estado = {"contexto": None, "pagina": None, "modulo": "", "modulos": []}

# Lo que NUNCA se pulsa en un recorrido automático (sí a mano, con confirmación)
NO_RECORRER = re.compile(r"\b(salir|cerrar sesion|log ?out|sign ?out|eliminar|borrar|pagar|"
                         r"comprar|desinstalar|delete|remove)\b")
ANUNCIOS = ["Vamos a {m}.", "Ahora, {m}.", "Pasemos a {m}.", "Veamos {m}."]


# ---------- Cursor y resaltado (se inyecta en cada página) ----------
OVERLAY_JS = r"""
(() => {
  if (window.__jarvis) return;
  const Z = 2147483647;
  const estilo = document.createElement('style');
  estilo.textContent = `
    #__jv_cursor{position:fixed;left:50vw;top:50vh;width:26px;height:26px;margin:-13px 0 0 -13px;
      border-radius:50%;border:3px solid #38e1ff;background:rgba(56,225,255,.15);
      box-shadow:0 0 14px 4px rgba(56,225,255,.75),inset 0 0 8px rgba(56,225,255,.6);
      pointer-events:none;z-index:${Z};opacity:0;
      transition:left .6s cubic-bezier(.22,.61,.36,1),top .6s cubic-bezier(.22,.61,.36,1),opacity .3s}
    #__jv_cursor.__jv_on{opacity:1}
    .__jv_onda{position:fixed;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;
      border:3px solid #38e1ff;pointer-events:none;z-index:${Z};animation:__jv_onda .6s ease-out forwards}
    @keyframes __jv_onda{to{transform:scale(3.2);opacity:0}}
    .__jv_marco{position:fixed;pointer-events:none;z-index:${Z - 1};border:3px solid #38e1ff;
      border-radius:10px;transition:all .45s ease;
      box-shadow:0 0 0 9999px rgba(3,10,20,.42),0 0 22px 6px rgba(56,225,255,.7)}
  `;
  (document.head || document.documentElement).appendChild(estilo);
  const cursor = document.createElement('div');
  cursor.id = '__jv_cursor';
  const listo = () => {
    if (!document.body) return false;
    if (!cursor.isConnected) document.body.appendChild(cursor);
    if (!estilo.isConnected) (document.head || document.documentElement).appendChild(estilo);
    return true;
  };
  let marco = null;
  window.__jarvis = {
    mover(x, y) {
      if (!listo()) return;
      cursor.classList.add('__jv_on');
      cursor.style.left = x + 'px'; cursor.style.top = y + 'px';
      try { sessionStorage.setItem('__jv_pos', JSON.stringify([x, y])); } catch (e) {}
    },
    clic() {
      if (!listo()) return;
      const o = document.createElement('div');
      o.className = '__jv_onda';
      o.style.left = cursor.style.left; o.style.top = cursor.style.top;
      document.body.appendChild(o);
      setTimeout(() => o.remove(), 700);
    },
    resaltar(x, y, w, h) {
      if (!listo()) return;
      if (!marco) { marco = document.createElement('div'); marco.className = '__jv_marco'; document.body.appendChild(marco); }
      const m = 8;
      Object.assign(marco.style, {left: (x - m) + 'px', top: (y - m) + 'px',
                                  width: (w + 2 * m) + 'px', height: (h + 2 * m) + 'px', opacity: '1'});
    },
    quitar() { if (marco) { marco.remove(); marco = null; } },
    ocultar() { cursor.classList.remove('__jv_on'); this.quitar(); }
  };
  document.addEventListener('DOMContentLoaded', () => {
    try {
      const p = JSON.parse(sessionStorage.getItem('__jv_pos') || 'null');
      if (p && listo()) {
        cursor.style.transition = 'none';
        cursor.style.left = p[0] + 'px'; cursor.style.top = p[1] + 'px';
        cursor.classList.add('__jv_on');
        requestAnimationFrame(() => requestAnimationFrame(() => { cursor.style.transition = ''; }));
      }
    } catch (e) {}
  });
})();
"""

MENU_JS = r"""
(sel) => {
  const visible = el => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const texto = el => ((el.innerText || '').trim() || el.getAttribute('aria-label') ||
                       el.getAttribute('title') || '').replace(/\s+/g, ' ').trim();
  const cont = sel ? [...document.querySelectorAll(sel)] : [...document.querySelectorAll(
    'nav, [role=navigation], aside, [class*=sidebar], [class*=side-bar], [class*=menu], [id*=sidebar], [id*=menu]')];
  document.querySelectorAll('[data-jarvis-modulo]').forEach(e => e.removeAttribute('data-jarvis-modulo'));
  const vistos = new Set(); const items = [];
  for (const c of (cont.length ? cont : [document.body])) {
    for (const el of c.querySelectorAll('a[href], button, [role=menuitem], [role=tab], [role=link], [role=button]')) {
      if (items.length >= 40) break;
      if (!visible(el)) continue;
      const t = texto(el);
      if (!t || t.length > 40 || vistos.has(t.toLowerCase())) continue;
      vistos.add(t.toLowerCase());
      el.setAttribute('data-jarvis-modulo', String(items.length));
      items.push({texto: t, href: el.getAttribute('href') || ''});
    }
  }
  return items;
}
"""


# ---------- Hilo propio para Playwright (su API síncrona exige un solo hilo) ----------
class _Hilo:
    def __init__(self):
        self._q = queue.Queue()
        self._t = None
        self.pw = None
        self.error = None

    def ejecutar(self, fn, *args, limite=90, **kwargs):
        if self._t is None or not self._t.is_alive():
            self._t = threading.Thread(target=self._bucle, daemon=True, name="navegador")
            self._t.start()
        salida = queue.Queue()
        self._q.put((fn, args, kwargs, salida))
        try:
            ok, valor = salida.get(timeout=limite)
        except queue.Empty:
            raise TimeoutError("el navegador tardó demasiado en responder")
        if not ok:
            raise valor
        return valor

    def _bucle(self):
        from playwright.sync_api import sync_playwright
        try:
            self.pw = sync_playwright().start()
        except Exception as e:
            self.error = e
        while True:
            fn, args, kwargs, salida = self._q.get()
            if self.pw is None:
                salida.put((False, RuntimeError(f"Playwright no arrancó: {self.error}")))
                continue
            try:
                salida.put((True, fn(*args, **kwargs)))
            except Exception as e:
                salida.put((False, e))


_hilo = _Hilo()


# ---------- Configuración ----------
def configurar(cfg, hablar=None, confirmar=None):
    global _cfg, _hablar, _confirmar
    _cfg = cfg
    _hablar = hablar
    _confirmar = confirmar


def _demo():
    return (_cfg or skills._CFG).get("demo", {}) or {}


def _url_demo():
    return (_demo().get("url") or "").strip()


def _hay_url():
    return bool(_url_demo())


def _decir(texto):
    if texto and _hablar is not None:
        _hablar(texto)


# ---------- Dentro del hilo del navegador ----------
def _pagina_viva():
    p = _estado["pagina"]
    try:
        return p if p is not None and not p.is_closed() else None
    except Exception:
        return None


def _abrir_en_hilo(url, pantalla_completa):
    p = _pagina_viva()
    if p is not None:
        return p
    ctx = _estado["contexto"]
    if ctx is not None:
        # Cerraron la pestaña pero la ventana sigue: otra pestaña en el mismo Chrome (abrir un
        # segundo Chrome con el mismo perfil fallaría por "perfil en uso")
        try:
            pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
            _estado["pagina"] = pagina
            pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
            _esperar_carga(pagina)
            return pagina
        except Exception:
            _estado.update(contexto=None, pagina=None)
    PERFIL.mkdir(parents=True, exist_ok=True)
    args = ["--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble", "--disable-infobars", "--start-maximized"]
    if pantalla_completa:
        args.append("--start-fullscreen")
    ctx = _hilo.pw.chromium.launch_persistent_context(
        str(PERFIL), channel=_demo().get("navegador", "chrome"),
        headless=bool(_demo().get("headless", False)), no_viewport=True,
        args=args, ignore_default_args=["--enable-automation"])
    ctx.add_init_script(OVERLAY_JS)
    ctx.on("close", lambda _c: _estado.update(contexto=None, pagina=None))
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    _estado.update(contexto=ctx, pagina=pagina)
    pagina.goto(url, wait_until="domcontentloaded", timeout=30000)
    _esperar_carga(pagina)
    return pagina


def _esperar_carga(p, extra_ms=500):
    try:
        p.wait_for_load_state("domcontentloaded", timeout=15000)
        p.wait_for_load_state("networkidle", timeout=2500)
    except Exception:
        pass  # las apps que nunca dejan de pedir datos no llegan a "networkidle"
    p.wait_for_timeout(extra_ms)
    try:
        p.evaluate(OVERLAY_JS)  # por si la página cargó antes de inyectarlo
    except Exception:
        pass


def _mover_y_clic(p, loc):
    loc.scroll_into_view_if_needed(timeout=5000)
    caja = loc.bounding_box()
    if caja:
        cx, cy = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
        p.evaluate("([x, y]) => window.__jarvis && __jarvis.mover(x, y)", [cx, cy])
        p.wait_for_timeout(650)
        p.evaluate("() => window.__jarvis && __jarvis.clic()")
    loc.click(timeout=8000)
    _esperar_carga(p, 400)


def _modulos_en_hilo():
    p = _pagina_viva()
    if p is None:
        return []
    items = p.evaluate(MENU_JS, _demo().get("menu_selector") or None) or []
    omitir = [_norm(x) for x in _demo().get("omitir", [])]
    for it in items:
        it["peligroso"] = bool(NO_RECORRER.search(_norm(it["texto"]))) or control.es_peligroso(it["texto"])
        it["omitido"] = any(o and o in _norm(it["texto"]) for o in omitir)
    return items


def _clic_modulo_en_hilo(indice):
    p = _pagina_viva()
    loc = p.locator(f'[data-jarvis-modulo="{indice}"]').first
    _mover_y_clic(p, loc)
    return p.title()


def _buscar_elemento(p, texto):
    """Locator del elemento visible cuyo texto se parece más a 'texto' (o None)."""
    if not texto:
        return None
    for loc in (p.get_by_role("heading", name=texto), p.get_by_role("button", name=texto),
                p.get_by_role("link", name=texto), p.get_by_label(texto), p.get_by_text(texto)):
        try:
            for i in range(min(loc.count(), 5)):
                candidato = loc.nth(i)
                if candidato.is_visible():
                    return candidato
        except Exception:
            continue
    return None


def _resaltar_en_hilo(texto, mover=True):
    p = _pagina_viva()
    loc = _buscar_elemento(p, texto)
    if loc is None:
        p.evaluate("() => window.__jarvis && __jarvis.quitar()")
        return False
    try:
        loc.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
        p.wait_for_timeout(450)
        caja = loc.bounding_box()
    except Exception:
        caja = None
    if not caja:
        return False
    p.evaluate("([x, y, w, h]) => window.__jarvis && __jarvis.resaltar(x, y, w, h)",
               [caja["x"], caja["y"], caja["width"], caja["height"]])
    if mover:
        p.evaluate("([x, y]) => window.__jarvis && __jarvis.mover(x, y)",
                   [caja["x"] + min(caja["width"] / 2, 60), caja["y"] + min(caja["height"] / 2, 20)])
    return True


def _quitar_resaltado_en_hilo():
    p = _pagina_viva()
    if p is not None:
        p.evaluate("() => window.__jarvis && __jarvis.quitar()")


def _leer_en_hilo(maximo=3500):
    p = _pagina_viva()
    if p is None:
        return {}
    raiz = p.locator("main, [role=main]").first
    try:
        if raiz.count() == 0:
            raiz = p.locator("body")
        arbol = raiz.aria_snapshot(timeout=8000)
    except Exception:
        arbol = p.locator("body").inner_text(timeout=8000)
    lineas = [ln for ln in arbol.splitlines() if ln.strip() and not re.match(r"^\s*- (img|generic)\s*$", ln)]
    arbol = "\n".join(lineas)
    return {"titulo": p.title(), "url": p.url, "contenido": arbol[:maximo]}


def _llenar_en_hilo(campo, valor):
    p = _pagina_viva()
    for loc in (p.get_by_label(campo), p.get_by_placeholder(campo),
                p.get_by_role("textbox", name=campo)):
        try:
            if loc.count() and loc.first.is_visible():
                objetivo = loc.first
                _mover_y_clic(p, objetivo)
                objetivo.fill("")
                objetivo.press_sequentially(str(valor), delay=35)  # que se vea escribir
                return True
        except Exception:
            continue
    return False


def _clic_texto_en_hilo(texto):
    p = _pagina_viva()
    loc = _buscar_elemento(p, texto)
    if loc is None:
        return False
    _mover_y_clic(p, loc)
    return True


def _atras_en_hilo():
    p = _pagina_viva()
    p.go_back(wait_until="domcontentloaded", timeout=15000)
    _esperar_carga(p, 300)
    return p.title()


def _al_frente_en_hilo():
    p = _pagina_viva()
    if p is None:
        return ""
    p.bring_to_front()
    return p.title()


# ---------- Utilidades (hilo principal) ----------
def activo():
    """¿Está abierto el sistema de la demo? (sin arrancar Playwright solo por preguntar)"""
    if _hilo._t is None or not _hilo._t.is_alive() or _estado["pagina"] is None:
        return False
    try:
        return _hilo.ejecutar(lambda: _pagina_viva() is not None, limite=5)
    except Exception:
        return False


def _traer_ventana_al_frente(titulo):
    """Playwright activa la pestaña, pero la ventana de Chrome puede seguir detrás de la
    presentación: se trae al frente como cualquier otra ventana."""
    if not titulo:
        return
    h, _t, p = control.buscar_ventana(titulo)
    if h is not None and p >= 0.5:
        control.traer_al_frente(h)


def abrir_sistema():
    url = _url_demo()
    if not url:
        raise RuntimeError("falta demo.url en config.json")
    pantalla_completa = bool(_demo().get("pantalla_completa", True))
    _hilo.ejecutar(_abrir_en_hilo, url, pantalla_completa, limite=60)
    titulo = _hilo.ejecutar(_al_frente_en_hilo)
    _traer_ventana_al_frente(titulo)
    return titulo


def _cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"modulos": {}}


def _guardar_cache(datos):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CACHE)


def _config_modulo(nombre):
    """Lo que el equipo escribió en config.json → demo.modulos para ese módulo (o {})."""
    for m in _demo().get("modulos", []) or []:
        if isinstance(m, dict) and puntaje(nombre, m.get("nombre", "")) >= 0.7:
            return m
        if isinstance(m, str) and puntaje(nombre, m) >= 0.7:
            return {"nombre": m}
    return {}


def _mejor_modulo(nombre, items):
    candidatos = [(puntaje(nombre, it["texto"]), i, it) for i, it in enumerate(items)]
    candidatos.sort(key=lambda c: c[0], reverse=True)
    if candidatos and candidatos[0][0] >= 0.55:
        return candidatos[0][1], candidatos[0][2]
    return None, None


def _modulos_del_recorrido(items):
    """Módulos a recorrer, en orden: los de config.json (demo.modulos) si hay; si no, los del
    menú, sin los peligrosos (salir, eliminar...) ni los omitidos."""
    pedidos = _demo().get("modulos") or []
    if pedidos:
        orden = []
        for m in pedidos:
            nombre = m.get("nombre") if isinstance(m, dict) else str(m)
            i, it = _mejor_modulo(nombre, items)
            if it is not None:
                orden.append((i, it))
        return orden
    return [(i, it) for i, it in enumerate(items) if not it["peligroso"] and not it["omitido"]]


def _plan_explicacion(lectura, enfoque="", pasos=3):
    """Pide al modelo un guion corto: qué señalar y qué decir de cada cosa."""
    saber = conocimiento.texto(_cfg or skills._CFG, 1500)
    pedido = (
        f"Pantalla del sistema que se está mostrando: '{lectura.get('titulo', '')}' ({lectura.get('url', '')}).\n"
        f"Contenido (árbol de accesibilidad):\n{lectura.get('contenido', '')}\n{saber}\n\n"
        f"Explícasela al público en {pasos} pasos cortos, señalando elementos de la pantalla."
        + (f" Enfoque: {enfoque}." if enfoque else "") +
        " Responde SOLO con JSON: {\"pasos\": [{\"elemento\": \"texto EXACTO de un título, botón, "
        "tabla o sección visible (o \\\"\\\" para la pantalla en general)\", \"decir\": \"1 o 2 frases "
        "naturales para decir en voz alta\"}]}. El primer paso dice qué es esta pantalla y para qué "
        "sirve. No leas números ni filas una por una; cuenta el valor para el usuario. Habla como "
        "integrante del equipo que hizo el sistema, en español de México, sin markdown.")
    r = cerebro.chat(_cfg or skills._CFG, [
        {"role": "system", "content": "Eres Jarvis, la inteligencia artificial del equipo, explicando su software en una exposición. Respondes solo JSON válido."},
        {"role": "user", "content": pedido}], [], 0.4)
    texto = r.get("content") or ""
    m = re.search(r"\{.*\}", texto, re.S)
    try:
        datos = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        datos = {}
    salida = [{"elemento": str(p.get("elemento", "")).strip(), "decir": str(p.get("decir", "")).strip()}
              for p in datos.get("pasos", []) if isinstance(p, dict) and p.get("decir")]
    if not salida and texto.strip() and not texto.strip().startswith("{"):
        salida = [{"elemento": "", "decir": texto.strip()}]
    return salida[:6]


def _exponer(pasos):
    """Dice cada paso mientras resalta el elemento del que habla. False si lo interrumpieron."""
    for paso in pasos:
        if skills.INTERRUPCION.is_set():
            return False
        try:
            if paso.get("elemento"):
                _hilo.ejecutar(_resaltar_en_hilo, paso["elemento"], limite=15)
            else:
                _hilo.ejecutar(_quitar_resaltado_en_hilo, limite=5)
        except Exception as e:
            print(f"[No pude señalar '{paso.get('elemento')}': {type(e).__name__}]")
        _decir(paso["decir"])
    try:
        _hilo.ejecutar(_quitar_resaltado_en_hilo, limite=5)
    except Exception:
        pass
    return True


def _asegurar_abierto():
    if not activo():
        abrir_sistema()


# ---------- Skills ----------
@skill("abrir_sistema",
       "Abre (o trae al frente) el software/página web de la demo en el navegador de Jarvis: "
       "'muéstrales el sistema', 'abre la plataforma', 'vamos al software'.",
       requeridos=[], disponible=_hay_url)
def skill_abrir_sistema():
    try:
        titulo = abrir_sistema()
    except Exception as e:
        return Fallo(f"No pude abrir el sistema de la demo: {type(e).__name__}: {str(e)[:120]}")
    return Callado(f"Sistema en pantalla: {titulo}.")


@skill("ir_a_modulo",
       "Da clic en una opción o módulo del menú del sistema de la demo ('ve a Inventario', "
       "'abre el módulo de ventas'), mostrando el cursor de Jarvis.",
       {"nombre": {"type": "string", "description": "Nombre aproximado del módulo u opción del menú"}},
       disponible=_hay_url)
def ir_a_modulo(nombre):
    try:
        _asegurar_abierto()
        items = _hilo.ejecutar(_modulos_en_hilo)
        i, it = _mejor_modulo(nombre, items)
        if it is None:
            # quizá no está en el menú: un botón o enlace de la página con ese texto
            if _hilo.ejecutar(_clic_texto_en_hilo, nombre):
                _estado["modulo"] = nombre
                return f"Listo, en {nombre}."
            disponibles = ", ".join(x["texto"] for x in items[:15]) or "ninguno"
            return Fallo(f"No encontré '{nombre}' en el menú. Veo: {disponibles}.")
        if it["peligroso"] and (_confirmar is None or not _confirmar(f"¿Pulso '{it['texto']}'?")):
            return Fallo(f"No pulsé '{it['texto']}'.")
        _hilo.ejecutar(_clic_modulo_en_hilo, i)
        _estado["modulo"] = it["texto"]
        return f"Estamos en {it['texto']}."
    except Exception as e:
        return Fallo(f"No pude ir a '{nombre}': {type(e).__name__}: {str(e)[:120]}")


@skill("explicar_pantalla",
       "Explica al público la pantalla actual del sistema de la demo SEÑALANDO cada parte "
       "(la ilumina mientras habla de ella): 'explícales esta pantalla', 'qué estamos viendo'.",
       {"enfoque": {"type": "string", "description": "Opcional: en qué fijarse o a quién va dirigido"}},
       requeridos=[], disponible=_hay_url)
def explicar_pantalla(enfoque=""):
    try:
        _asegurar_abierto()
        lectura = _hilo.ejecutar(_leer_en_hilo)
        guardado = _cache()["modulos"].get(_norm(_estado.get("modulo") or ""), {})
        pasos = (guardado.get("pasos") if guardado and not enfoque
                 and guardado.get("url") == lectura.get("url") else None) or _plan_explicacion(lectura, enfoque)
        if not pasos:
            return Fallo("No logré preparar la explicación de esta pantalla.")
        completo = _exponer(pasos)
        return Callado("Explicación terminada." if completo else "Explicación interrumpida.")
    except Exception as e:
        return Fallo(f"No pude explicar la pantalla: {type(e).__name__}: {str(e)[:120]}")


@skill("recorrer_modulos",
       "Recorre los módulos del menú del sistema de la demo uno por uno: anuncia a dónde va, da "
       "clic con el cursor de Jarvis y explica cada pantalla señalando. 'Explora todos los "
       "módulos', 'dales un tour por el sistema', 'muéstrales cada opción del menú'.",
       {"cuantos": {"type": "integer", "description": "Opcional: máximo de módulos a recorrer"}},
       requeridos=[], disponible=_hay_url)
def recorrer_modulos(cuantos=0):
    try:
        _asegurar_abierto()
        items = _hilo.ejecutar(_modulos_en_hilo)
    except Exception as e:
        return Fallo(f"No pude abrir el sistema: {type(e).__name__}: {str(e)[:120]}")
    orden = _modulos_del_recorrido(items)
    if cuantos and int(cuantos) > 0:
        orden = orden[:int(cuantos)]
    if not orden:
        return Fallo("No encontré módulos en el menú del sistema. Revisa demo.menu_selector en config.json.")
    cache = _cache()["modulos"]
    _decir(_demo().get("intro_recorrido") or "Les muestro el sistema, módulo por módulo.")
    anterior = None
    for n, (_i, it) in enumerate(orden, 1):
        if skills.INTERRUPCION.is_set():
            return Callado("Recorrido detenido.")
        nombre = it["texto"]
        conf = _config_modulo(nombre)
        guardado = cache.get(_norm(nombre), {})
        anuncio = guardado.get("anuncio") or random.choice([a for a in ANUNCIOS if a != anterior]).format(m=nombre)
        anterior = anuncio
        # El anuncio suena MIENTRAS el cursor viaja y da clic (como alguien que dice "vamos a
        # inventario" mientras lo abre), no antes.
        voz_hilo = threading.Thread(target=_decir, args=(anuncio,), daemon=True)
        voz_hilo.start()
        try:
            items = _hilo.ejecutar(_modulos_en_hilo)  # el menú se re-marca en cada página
            i, actual = _mejor_modulo(nombre, items)
            if actual is None:
                voz_hilo.join()
                continue
            _hilo.ejecutar(_clic_modulo_en_hilo, i)
            _estado["modulo"] = nombre
        except Exception as e:
            print(f"[Recorrido: no pude abrir '{nombre}': {type(e).__name__}: {str(e)[:100]}]")
            voz_hilo.join()
            continue
        voz_hilo.join()
        pasos = ([{"elemento": "", "decir": conf["decir"]}] if conf.get("decir") else
                 guardado.get("pasos") or _plan_explicacion(_hilo.ejecutar(_leer_en_hilo),
                                                           conf.get("enfoque", ""), pasos=2))
        if not _exponer(pasos):
            return Callado("Recorrido detenido.")
    cierre = _demo().get("cierre_recorrido") or "Y ese es el recorrido completo por el sistema."
    _decir(cierre)
    return Callado("Recorrido completado.")


@skill("resaltar",
       "Señala (ilumina) un elemento de la pantalla del sistema de la demo por su texto, "
       "mientras el expositor habla de él: 'señala la gráfica de ventas', 'resalta el botón Guardar'.",
       {"texto": {"type": "string", "description": "Texto visible del elemento"}},
       disponible=_hay_url)
def resaltar(texto):
    try:
        if not _hilo.ejecutar(_resaltar_en_hilo, texto, limite=15):
            return Fallo(f"No encontré '{texto}' en la pantalla.")

        def quitar_despues():
            time.sleep(float(_demo().get("resaltar_seg", 6)))
            try:
                _hilo.ejecutar(_quitar_resaltado_en_hilo, limite=5)
            except Exception:
                pass
        threading.Thread(target=quitar_despues, daemon=True).start()
        return Callado(f"Señalando '{texto}'.")
    except Exception as e:
        return Fallo(f"No pude señalar '{texto}': {type(e).__name__}")


@skill("llenar_campo",
       "Escribe un valor en un campo de un formulario del sistema de la demo, buscándolo por su "
       "etiqueta o texto de ayuda ('en Usuario escribe demo', 'pon 50 en Cantidad').",
       {"campo": {"type": "string", "description": "Etiqueta o texto de ayuda del campo"},
        "valor": {"type": "string", "description": "Lo que se escribe"}},
       sensible=True, disponible=_hay_url)
def llenar_campo(campo, valor):
    try:
        _asegurar_abierto()
        if _hilo.ejecutar(_llenar_en_hilo, campo, valor):
            return Callado(f"Escribí en {campo}.")
        return Fallo(f"No encontré el campo '{campo}'.")
    except Exception as e:
        return Fallo(f"No pude escribir en '{campo}': {type(e).__name__}")


def _hay_login():
    login = _demo().get("login") or {}
    import os
    return _hay_url() and bool(login.get("usuario_env") and os.environ.get(login["usuario_env"])
                               and login.get("clave_env") and os.environ.get(login["clave_env"]))


@skill("iniciar_sesion_demo",
       "Inicia sesión en el sistema de la demo con la cuenta de demostración configurada "
       "(usuario y contraseña en variables de entorno, nunca en voz alta).",
       requeridos=[], disponible=_hay_login)
def iniciar_sesion_demo():
    import os
    login = _demo().get("login") or {}
    try:
        _asegurar_abierto()
        ok = (_hilo.ejecutar(_llenar_en_hilo, login.get("campo_usuario", "Usuario"), os.environ[login["usuario_env"]])
              and _hilo.ejecutar(_llenar_en_hilo, login.get("campo_clave", "Contraseña"), os.environ[login["clave_env"]])
              and _hilo.ejecutar(_clic_texto_en_hilo, login.get("boton", "Iniciar sesión")))
        return Callado("Sesión iniciada.") if ok else Fallo("No encontré el formulario de inicio de sesión.")
    except Exception as e:
        return Fallo(f"No pude iniciar sesión: {type(e).__name__}")


@skill("volver_atras", "Regresa a la pantalla anterior en el sistema de la demo.",
       requeridos=[], disponible=_hay_url)
def volver_atras():
    try:
        return Callado(f"De vuelta en {_hilo.ejecutar(_atras_en_hilo)}.")
    except Exception as e:
        return Fallo(f"No pude regresar: {type(e).__name__}")


@skill("ensayar_demo",
       "Ensaya la demo ANTES de exponer: visita cada módulo del sistema, prepara la explicación "
       "de cada pantalla y deja lista la voz, para que en el recorrido en vivo todo salga al "
       "instante. 'Ensaya la demo', 'prepara el recorrido'.",
       requeridos=[], terminal=False, disponible=_hay_url)
def ensayar_demo():
    import voz
    try:
        _asegurar_abierto()
        items = _hilo.ejecutar(_modulos_en_hilo)
    except Exception as e:
        return Fallo(f"No pude abrir el sistema: {type(e).__name__}: {str(e)[:120]}")
    orden = _modulos_del_recorrido(items)
    if not orden:
        return Fallo("No encontré módulos en el menú. Revisa demo.menu_selector en config.json.")
    inicio = _hilo.ejecutar(lambda: _pagina_viva().url)
    datos = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "url": _url_demo(), "modulos": {}}
    frases = [_demo().get("intro_recorrido") or "Les muestro el sistema, módulo por módulo.",
              _demo().get("cierre_recorrido") or "Y ese es el recorrido completo por el sistema."]
    hechos = []
    for n, (_i, it) in enumerate(orden):
        nombre = it["texto"]
        try:
            items = _hilo.ejecutar(_modulos_en_hilo)
            i, actual = _mejor_modulo(nombre, items)
            if actual is None:
                continue
            _hilo.ejecutar(_clic_modulo_en_hilo, i)
            lectura = _hilo.ejecutar(_leer_en_hilo)
            conf = _config_modulo(nombre)
            pasos = ([{"elemento": "", "decir": conf["decir"]}] if conf.get("decir")
                     else _con_paciencia(lambda: _plan_explicacion(lectura, conf.get("enfoque", ""), pasos=2)))
            anuncio = ANUNCIOS[n % len(ANUNCIOS)].format(m=nombre)
            datos["modulos"][_norm(nombre)] = {"nombre": nombre, "url": lectura.get("url"),
                                               "anuncio": anuncio, "pasos": pasos,
                                               "resumen": " ".join(p["decir"] for p in pasos)[:400]}
            frases += [anuncio] + [p["decir"] for p in pasos]
            hechos.append(nombre)
            print(f"[Ensayo] {nombre}: {len(pasos)} pasos")
        except Exception as e:
            print(f"[Ensayo] {nombre}: falló ({type(e).__name__}: {str(e)[:100]})")
    _guardar_cache(datos)
    try:
        _hilo.ejecutar(lambda: _pagina_viva().goto(inicio, wait_until="domcontentloaded"))
    except Exception:
        pass
    n_voz = voz.precalentar(frases)
    return (f"Ensayo listo: {len(hechos)} módulos preparados ({', '.join(hechos)}), "
            f"{n_voz} frases de voz generadas. El recorrido en vivo ya no depende de la IA.")


def _con_paciencia(fn, intentos=4):
    """Durante el ensayo no hay prisa: si el plan gratis de Groq llega a su límite por minuto,
    se espera y se reintenta en vez de fallar."""
    for i in range(intentos):
        try:
            return fn()
        except cerebro.SinCerebro:
            if i == intentos - 1:
                raise
            print("[Ensayo: la IA está saturada; espero 20 s y sigo]")
            time.sleep(20)
    return []


def contexto():
    """Para el prompt: qué sistema está en pantalla y lo que se ensayó de cada módulo."""
    if not _hay_url():
        return ""
    try:
        abierto = activo()
    except Exception:
        abierto = False
    cache = _cache().get("modulos", {})
    if not abierto and not cache:
        return ""
    partes = ["\n\nSISTEMA DE LA DEMO (el software del equipo, en el navegador de Jarvis):"]
    if abierto:
        partes.append(f" está abierto" + (f", en el módulo '{_estado['modulo']}'" if _estado.get("modulo") else "") + ".")
    else:
        partes.append(" todavía no está abierto (abrir_sistema).")
    if cache:
        partes.append(" Módulos y lo que muestra cada uno: " + " | ".join(
            f"{m['nombre']}: {m.get('resumen', '')[:160]}" for m in list(cache.values())[:12]))
    partes.append(" Para navegarlo usa ir_a_modulo, explicar_pantalla, recorrer_modulos y resaltar "
                  "(no clic_en).")
    return "".join(partes)
