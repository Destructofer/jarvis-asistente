"use strict";
// La app de escritorio de Jarvis. Habla con el servidor que corre DENTRO de Jarvis
// (app_servidor.py, solo en 127.0.0.1) usando la llave de sesión que llega después del "#".

// ---------- Llave y API ----------
const LLAVE = (() => {
  const h = new URLSearchParams(location.hash.slice(1)).get("t");
  try {
    if (h) sessionStorage.setItem("jarvis-llave", h);
    history.replaceState(null, "", location.pathname);
    return h || sessionStorage.getItem("jarvis-llave") || "";
  } catch (e) { return h || ""; }
})();

async function api(ruta, cuerpo) {
  const r = await fetch(ruta, {
    method: cuerpo === undefined ? "GET" : "POST",
    headers: { "X-Jarvis-Token": LLAVE, "Content-Type": "application/json" },
    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo), cache: "no-store",
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `error ${r.status}`);
  return d;
}

const $ = (id) => document.getElementById(id);
const el = (etiqueta, clase, texto) => {
  const e = document.createElement(etiqueta);
  if (clase) e.className = clase;
  if (texto !== undefined) e.textContent = texto;   // siempre texto: nunca HTML
  return e;
};
let temporizadorToast = null;
function toast(texto, error) {
  const t = $("toast");
  t.textContent = texto;
  t.classList.toggle("error", !!error);
  t.classList.add("visible");
  clearTimeout(temporizadorToast);
  temporizadorToast = setTimeout(() => t.classList.remove("visible"), 3800);
}

// ---------- Tema ----------
const tema = matchMedia("(prefers-color-scheme: light)");
const ponerTema = () => { document.documentElement.dataset.theme = tema.matches ? "light" : "dark"; };
tema.addEventListener("change", ponerTema);
ponerTema();

// ---------- Navegación ----------
const cargas = {};
document.querySelectorAll(".nav").forEach((b) => b.addEventListener("click", () => mostrar(b.dataset.seccion)));
function mostrar(seccion) {
  document.querySelectorAll(".nav").forEach((b) => b.classList.toggle("activo", b.dataset.seccion === seccion));
  document.querySelectorAll(".seccion").forEach((s) => s.classList.toggle("visible", s.id === seccion));
  if (cargas[seccion]) cargas[seccion]().catch((e) => toast(e.message, true));
  if (seccion === "inicio") orbe.redimensionar();
}

// ---------- El orbe ----------
const ESTADOS = {
  inactivo:   { c1: [56, 225, 255], c2: [70, 90, 255],  energia: 0.22, giro: 0.18, texto: "en espera" },
  escuchando: { c1: [70, 255, 200], c2: [56, 200, 255], energia: 0.55, giro: 0.45, texto: "escuchando" },
  pensando:   { c1: [255, 178, 50], c2: [255, 112, 40], energia: 0.75, giro: 1.7,  texto: "pensando" },
  hablando:   { c1: [100, 235, 255], c2: [130, 110, 255], energia: 0.45, giro: 0.6, texto: "hablando" },
  mirando:    { c1: [200, 130, 255], c2: [60, 200, 255], energia: 0.5,  giro: 0.55, texto: "mirando" },
  error:      { c1: [255, 95, 115], c2: [255, 160, 70], energia: 0.6,  giro: 0.3,  texto: "algo falló" },
};

const orbe = (() => {
  const lienzo = $("orbe");
  const ctx = lienzo.getContext("2d");
  const s = {
    estado: "inactivo", c1: [...ESTADOS.inactivo.c1], c2: [...ESTADOS.inactivo.c2], energia: 0.22, giro: 0.18,
    fase: 0, nivel: 0, cola: [], toque: 0, ondas: [], t0: performance.now(),
  };
  const particulas = Array.from({ length: 70 }, () => ({
    a: Math.random() * Math.PI * 2, r: 1.15 + Math.random() * 0.75, v: 0.3 + Math.random() * 0.9,
    tam: 0.6 + Math.random() * 1.6, brillo: Math.random(),
  }));
  let ancho = 0, alto = 0, dpr = 1;

  function redimensionar() {
    const r = lienzo.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    ancho = r.width; alto = r.height;
    lienzo.width = Math.max(1, Math.round(ancho * dpr));
    lienzo.height = Math.max(1, Math.round(alto * dpr));
  }
  new ResizeObserver(redimensionar).observe(lienzo);

  const mezcla = (a, b, k) => a.map((x, i) => x + (b[i] - x) * k);
  const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

  function cuadro(ahora) {
    requestAnimationFrame(cuadro);
    if (!ancho || document.hidden || !$("inicio").classList.contains("visible")) return;
    const t = (ahora - s.t0) / 1000;
    const dt = Math.min(0.05, t - (s.ultimo || t));
    s.ultimo = t;
    const meta = ESTADOS[s.estado] || ESTADOS.inactivo;
    s.c1 = mezcla(s.c1, meta.c1, 0.05);
    s.c2 = mezcla(s.c2, meta.c2, 0.05);
    // el volumen real de la voz (llega en pedazos de 25 ms, programados en el tiempo)
    while (s.cola.length && s.cola[0][0] <= ahora) s.nivelMeta = s.cola.shift()[1];
    if (!s.cola.length && ahora - (s.ultimaVoz || 0) > 300) s.nivelMeta = 0;
    s.nivel += ((s.nivelMeta || 0) - s.nivel) * 0.35;
    s.toque *= 0.92;
    const energia = meta.energia + s.nivel * 0.9 + s.toque * 0.4;
    s.energia += (energia - s.energia) * 0.08;
    s.giro += (meta.giro - s.giro) * 0.04;
    s.fase += dt * (0.6 + s.giro * 1.4);

    const claro = document.documentElement.dataset.theme === "light";
    const w = ancho * dpr, h = alto * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cx = ancho / 2, cy = alto / 2 - 10;
    const R = Math.min(ancho, alto) * 0.2 * (1 + s.nivel * 0.12 + Math.sin(t * 1.3) * 0.015);
    ctx.globalCompositeOperation = claro ? "source-over" : "lighter";

    // halo
    const halo = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 2.8);
    halo.addColorStop(0, rgba(s.c2, claro ? 0.18 : 0.28));
    halo.addColorStop(1, rgba(s.c2, 0));
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, ancho, alto);

    // ondas al escuchar
    if (s.estado === "escuchando" && (!s.ondas.length || t - s.ondas[s.ondas.length - 1] > 1.1)) s.ondas.push(t);
    s.ondas = s.ondas.filter((o) => t - o < 2.4);
    for (const o of s.ondas) {
      const k = (t - o) / 2.4;
      ctx.strokeStyle = rgba(s.c1, (1 - k) * 0.5);
      ctx.lineWidth = 2 * (1 - k) + 0.5;
      ctx.beginPath();
      ctx.arc(cx, cy, R * (1.1 + k * 1.4), 0, Math.PI * 2);
      ctx.stroke();
    }

    // capas fluidas: el contorno se deforma con la energía
    for (let capa = 0; capa < 4; capa++) {
      ctx.beginPath();
      const pasos = 120;
      for (let i = 0; i <= pasos; i++) {
        const a = (i / pasos) * Math.PI * 2;
        let d = 0;
        for (let k = 2; k <= 6; k++) {
          d += Math.sin(a * k + s.fase * (1 + capa * 0.35) * (k % 2 ? 1 : -1) + capa * 1.7 + k) / k;
        }
        const r = R * (0.86 + capa * 0.07) * (1 + d * s.energia * 0.16);
        const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      const g = ctx.createRadialGradient(cx, cy, R * 0.1, cx, cy, R * (1.05 + capa * 0.1));
      const c = capa % 2 ? s.c2 : s.c1;
      g.addColorStop(0, rgba(c, claro ? 0.30 : 0.24));
      g.addColorStop(0.7, rgba(c, claro ? 0.16 : 0.12));
      g.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = g;
      ctx.fill();
    }

    // núcleo
    const nucleo = ctx.createRadialGradient(cx - R * 0.12, cy - R * 0.15, 0, cx, cy, R * (0.62 + s.nivel * 0.25));
    nucleo.addColorStop(0, `rgba(255,255,255,${claro ? 0.95 : 0.85})`);
    nucleo.addColorStop(0.3, rgba(s.c1, 0.6));
    nucleo.addColorStop(1, rgba(s.c2, 0));
    ctx.fillStyle = nucleo;
    ctx.beginPath();
    ctx.arc(cx, cy, R * (0.62 + s.nivel * 0.25), 0, Math.PI * 2);
    ctx.fill();

    // anillos que giran (rápido al pensar)
    ctx.lineCap = "round";
    for (let i = 0; i < 3; i++) {
      const radio = R * (1.32 + i * 0.13);
      const inicio = s.fase * (i % 2 ? -0.7 : 1) * (0.6 + i * 0.25) + i;
      ctx.strokeStyle = rgba(i % 2 ? s.c2 : s.c1, claro ? 0.55 : 0.6 - i * 0.12);
      ctx.lineWidth = 2.2 - i * 0.5;
      for (let k = 0; k < 3; k++) {
        ctx.beginPath();
        const largo = 0.5 + 0.35 * Math.sin(t * 0.7 + k + i) + s.energia * 0.4;
        ctx.arc(cx, cy, radio, inicio + k * 2.1, inicio + k * 2.1 + largo);
        ctx.stroke();
      }
    }

    // partículas en órbita
    for (const p of particulas) {
      p.a += dt * p.v * (0.25 + s.giro * 0.6);
      const r = R * (p.r + Math.sin(t * 0.8 + p.brillo * 6) * 0.04 * (1 + s.energia));
      const x = cx + Math.cos(p.a) * r, y = cy + Math.sin(p.a) * r * 0.92;
      ctx.fillStyle = rgba(p.brillo > 0.5 ? s.c1 : s.c2, (0.25 + 0.6 * Math.abs(Math.sin(t * 1.5 + p.brillo * 9))) * (claro ? 0.7 : 0.8));
      ctx.beginPath();
      ctx.arc(x, y, p.tam * (1 + s.nivel), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
  }
  requestAnimationFrame(cuadro);

  return {
    redimensionar,
    probar(e, nivel) { this.ponerEstado(e); s.nivelMeta = nivel || 0; s.ultimaVoz = performance.now() + 60000; },
    ponerEstado(e) {
      s.estado = ESTADOS[e] ? e : "inactivo";
      $("estado-texto").textContent = (ESTADOS[s.estado] || ESTADOS.inactivo).texto;
      $("estado-texto").style.color = `rgb(${ESTADOS[s.estado].c1.join(",")})`;
    },
    voz(niveles, paso) {
      const ahora = performance.now();
      s.cola = niveles.map((v, i) => [ahora + i * paso * 1000, v]);
      s.ultimaVoz = ahora + niveles.length * paso * 1000;
    },
    tocar(k = 1) { s.toque = Math.min(1.5, s.toque + k); },
  };
})();
window.jarvisOrbe = orbe;   // para probar los estados desde fuera (pruebas de la interfaz)

// ---------- Diálogo ----------
const dialogo = { ultimaEl: null, tUltima: 0, pendientes: new Map() };
function agregarBurbuja(clase, texto) {
  const b = el("div", "burbuja " + clase, texto);
  $("dialogo").appendChild(b);
  while ($("dialogo").children.length > 30) $("dialogo").firstChild.remove();
  $("dialogo").scrollTop = $("dialogo").scrollHeight;
  return b;
}
function dice(texto) {   // las frases de una misma respuesta se juntan en una burbuja
  const ahora = Date.now();
  if (dialogo.ultimaEl && ahora - dialogo.tUltima < 5000) {
    dialogo.ultimaEl.textContent += " " + texto;
  } else {
    dialogo.ultimaEl = agregarBurbuja("el", texto);
  }
  dialogo.tUltima = ahora;
}

$("forma-orden").addEventListener("submit", async (e) => {
  e.preventDefault();
  const texto = $("orden").value.trim();
  if (!texto) return;
  $("orden").value = "";
  agregarBurbuja("tu", texto);
  dialogo.ultimaEl = null;
  orbe.tocar(1);
  try {
    const r = await api("/api/orden", { texto });
    dialogo.pendientes.set(r.id, Date.now());
  } catch (err) { toast(err.message, true); }
});
$("orden").addEventListener("input", () => orbe.tocar(0.08));

// ---------- Eventos en vivo ----------
function conectar() {
  const fuente = new EventSource(`/api/eventos?t=${encodeURIComponent(LLAVE)}`);
  fuente.onopen = () => { $("punto").classList.add("vivo"); $("conexion").textContent = "conectado"; };
  fuente.onerror = () => { $("punto").classList.remove("vivo"); $("conexion").textContent = "reconectando…"; };
  fuente.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (ev.tipo === "estado") orbe.ponerEstado(ev.estado);
    else if (ev.tipo === "voz") orbe.voz(ev.niveles || [], ev.paso || 0.025);
    else if (ev.tipo === "oido") { agregarBurbuja("oido", "🎙 " + ev.texto); dialogo.ultimaEl = null; orbe.tocar(0.6); }
    else if (ev.tipo === "dice") dice(ev.texto);
    else if (ev.tipo === "respuesta") {
      dialogo.pendientes.delete(ev.id);
      if (!dialogo.ultimaEl || Date.now() - dialogo.tUltima > 8000) agregarBurbuja("el" + (ev.ok ? "" : " error"), ev.texto);
      else dialogo.ultimaEl.textContent = ev.texto;   // ya se fue mostrando frase por frase
      dialogo.ultimaEl = null;
    } else if (ev.tipo === "personalidad") { cargarEstado(); if (cargas.personalidades) cargas.personalidades(); }
    else if (ev.tipo === "avatares") { if ($("avatares").classList.contains("visible")) cargas.avatares(); }
  };
}

async function cargarEstado() {
  const e = await api("/api/estado");
  $("marca-nombre").textContent = e.nombre;
  document.title = e.nombre;
  $("persona-texto").textContent = `${e.personalidad_nombre} · ${e.avatar || "sin avatar"}`;
  orbe.ponerEstado(e.estado);
}

// ---------- Personalidades ----------
cargas.personalidades = async () => {
  const lista = await api("/api/personalidades");
  const cont = $("lista-personalidades");
  cont.replaceChildren();
  for (const p of lista) {
    const t = el("button", "tarjeta" + (p.activa ? " activa" : ""));
    t.type = "button";
    t.append(el("h3", "", p.nombre), el("p", "", p.descripcion));
    const etiquetas = el("div", "etiquetas");
    if (p.activa) etiquetas.append(el("span", "etiqueta acento", "Activa"));
    if (p.voz_propia) etiquetas.append(el("span", "etiqueta", "Voz propia" + (p.voz ? ` · ${p.voz.split("-").pop().replace("Neural", "")}` : "")));
    t.append(etiquetas);
    t.addEventListener("click", async () => {
      if (p.activa) return;
      try {
        const r = await api("/api/personalidad", { clave: p.clave });
        toast(`${p.nombre}: «${r.saludo}»`);
        orbe.tocar(1.2);
        await cargas.personalidades();
        cargarEstado();
      } catch (e) { toast(e.message, true); }
    });
    cont.append(t);
  }
};

// ---------- Avatares ----------
const vista = (personaje, archivo) =>
  `/api/avatar/vista?t=${encodeURIComponent(LLAVE)}&p=${encodeURIComponent(personaje)}&f=${encodeURIComponent(archivo)}`;
let datosAvatares = null;

cargas.avatares = async () => {
  datosAvatares = await api("/api/avatares");
  const cont = $("lista-avatares");
  cont.replaceChildren();
  $("personajes").replaceChildren(...datosAvatares.avatares.map((a) => { const o = el("option"); o.value = a.nombre; return o; }));
  const sel = $("subir-categoria");
  if (sel.options.length <= 1) for (const c of datosAvatares.categorias) { const o = el("option", "", c); o.value = c; sel.append(o); }
  $("carpeta-avatares").textContent = `También puedes copiar GIF directo a ${datosAvatares.carpeta}\\<personaje>\\ y Jarvis los toma solo.`;
  if (!datosAvatares.avatares.length) cont.append(el("p", "nota", "Todavía no hay avatares: sube tus GIF abajo."));
  for (const a of datosAvatares.avatares) {
    const t = el("div", "tarjeta" + (a.activo ? " activa" : ""));
    if (a.portada) { const img = el("img"); img.src = vista(a.nombre, a.portada); img.alt = a.nombre; img.loading = "lazy"; t.append(img); }
    t.append(el("h3", "", a.nombre), el("p", "", `${a.animaciones.length} animaciones`));
    const etiquetas = el("div", "etiquetas");
    if (a.activo) etiquetas.append(el("span", "etiqueta acento", "En uso"));
    for (const [c, n] of Object.entries(a.categorias).slice(0, 6)) etiquetas.append(el("span", "etiqueta", `${c} ${n}`));
    t.append(etiquetas);
    const fila = el("div", "fila");
    const ver = el("button", "secundario", "Ver animaciones");
    ver.addEventListener("click", (e) => { e.stopPropagation(); detalleAvatar(a); });
    fila.append(ver);
    if (!a.activo) {
      const usar = el("button", "primario", "Usar");
      usar.addEventListener("click", async (e) => {
        e.stopPropagation();
        try { const r = await api("/api/avatar", { nombre: a.nombre }); toast(r.mensaje); cargas.avatares(); cargarEstado(); }
        catch (err) { toast(err.message, true); }
      });
      fila.append(usar);
    }
    t.append(fila);
    cont.append(t);
  }
};

function detalleAvatar(a) {
  $("detalle-avatar").hidden = false;
  $("detalle-nombre").textContent = a.nombre;
  const cont = $("animaciones");
  cont.replaceChildren();
  for (const an of a.animaciones) {
    const caja = el("div", "anim");
    const img = el("img"); img.src = vista(a.nombre, an.archivo); img.alt = an.archivo; img.loading = "lazy";
    const sel = el("select");
    sel.setAttribute("aria-label", `Acción de ${an.archivo}`);
    for (const c of datosAvatares.categorias) { const o = el("option", "", c); o.value = c; o.selected = c === an.categoria; sel.append(o); }
    if (!an.lista) { sel.disabled = true; }
    sel.addEventListener("change", async () => {
      try { await api("/api/avatar/categoria", { personaje: a.nombre, archivo: an.archivo, categoria: sel.value }); toast(`${an.archivo} ahora es «${sel.value}»`); }
      catch (err) { toast(err.message, true); }
    });
    caja.append(img, el("small", "", an.archivo + (an.lista ? "" : " · preparando…")), sel);
    cont.append(caja);
  }
  $("detalle-avatar").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("cerrar-detalle").addEventListener("click", () => { $("detalle-avatar").hidden = true; });

// subir GIF: arrastrar o elegir
const zona = $("zona");
["dragenter", "dragover"].forEach((t) => zona.addEventListener(t, (e) => { e.preventDefault(); zona.classList.add("encima"); }));
["dragleave", "drop"].forEach((t) => zona.addEventListener(t, (e) => { e.preventDefault(); zona.classList.remove("encima"); }));
zona.addEventListener("drop", (e) => subir([...e.dataTransfer.files]));
$("archivos").addEventListener("change", (e) => { subir([...e.target.files]); e.target.value = ""; });

function leerBase64(archivo) {
  return new Promise((ok, mal) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || "");
    r.onerror = () => mal(new Error("no pude leer el archivo"));
    r.readAsDataURL(archivo);
  });
}
async function subir(archivos) {
  const personaje = $("subir-personaje").value.trim();
  if (!personaje) { toast("Primero escribe para qué personaje son (o el nombre de uno nuevo).", true); $("subir-personaje").focus(); return; }
  for (const a of archivos) {
    const linea = el("div", "", `${a.name}: subiendo…`);
    $("progreso").append(linea);
    try {
      if (a.size > 25 * 1024 * 1024) throw new Error("pesa más de 25 MB");
      const r = await api("/api/avatar/subir", { personaje, archivo: a.name, datos: await leerBase64(a), categoria: $("subir-categoria").value || null });
      linea.textContent = `${r.archivo}: listo · Jarvis le está quitando el fondo`;
    } catch (err) { linea.textContent = `${a.name}: ${err.message}`; }
  }
  cargas.avatares();
}

// ---------- Voz ----------
let datosVoz = null;
const numero = (txt, sufijo) => parseInt(String(txt || "").replace(sufijo, ""), 10) || 0;
const conSigno = (n, sufijo) => `${n >= 0 ? "+" : ""}${n}${sufijo}`;

cargas.voz = async () => {
  datosVoz = await api("/api/voces");
  const a = datosVoz.actual, base = datosVoz.base || {};
  $("voz-activa").checked = a.voz_activa !== false;
  $("voz-motor").value = a.voz_motor || "auto";
  const sel = $("voz-edge");
  sel.replaceChildren();
  const grupos = {};
  for (const v of datosVoz.edge) {
    if (!grupos[v.region]) { grupos[v.region] = el("optgroup"); grupos[v.region].label = v.region; sel.append(grupos[v.region]); }
    const o = el("option", "", `${v.nombre} (${v.genero})`); o.value = v.id; grupos[v.region].append(o);
  }
  if (!datosVoz.edge.length) { const o = el("option", "", "Sin conexión: no pude traer las voces"); o.value = base.edge_voz || a.edge_voz || ""; sel.append(o); }
  sel.value = base.edge_voz || a.edge_voz || "es-MX-JorgeNeural";
  $("voz-vel").value = numero(base.edge_velocidad || a.edge_velocidad, "%");
  $("voz-tono").value = numero(base.edge_tono || a.edge_tono, "Hz");
  pintarValores();
  const aviso = $("aviso-voz");
  aviso.hidden = !datosVoz.personalidad_con_voz;
  aviso.textContent = datosVoz.personalidad_con_voz
    ? `La personalidad «${datosVoz.personalidad_con_voz}» usa su propia voz. Lo que elijas aquí se usa con las demás.` : "";
};
function pintarValores() {
  $("vel-valor").textContent = conSigno(+$("voz-vel").value, "%");
  $("tono-valor").textContent = conSigno(+$("voz-tono").value, " Hz");
}
$("voz-vel").addEventListener("input", pintarValores);
$("voz-tono").addEventListener("input", pintarValores);
const vozEnPantalla = () => ({
  voz_motor: $("voz-motor").value, edge_voz: $("voz-edge").value,
  edge_velocidad: conSigno(+$("voz-vel").value, "%"), edge_tono: conSigno(+$("voz-tono").value, "Hz"),
});
async function guardarVoz() {
  await api("/api/voz", { voz_activa: $("voz-activa").checked, ...vozEnPantalla() });
}
$("guardar-voz").addEventListener("click", async () => {
  try { await guardarVoz(); toast("Voz guardada."); } catch (e) { toast(e.message, true); }
});
$("probar-voz").addEventListener("click", async () => {
  try { await api("/api/voz/probar", { texto: $("voz-prueba").value, voz: vozEnPantalla() }); toast("Escucha… (todavía no se guarda)"); }
  catch (e) { toast(e.message, true); }
});

// ---------- Ajustes ----------
const AJUSTES = [
  ["voz_activa", "Hablar en voz alta", "bool"],
  ["palabra_activacion", "Despertar al decir «Jarvis»", "bool", "Aplica al reiniciar Jarvis"],
  ["interrumpir_con_voz", "Poder interrumpirlo hablando", "bool", "Aplica al reiniciar Jarvis"],
  ["presencia.saludar", "Saludarte cuando te ve llegar", "bool"],
  ["habitos.activo", "Aprender tus hábitos y sugerirte cosas", "bool"],
  ["mantenimiento_activo", "Mantenimiento automático del equipo", "bool", "Borra temporales y cachés viejos, sin avisos"],
  ["mantenimiento_procesos", "Regular procesos que no se usan", "bool", "Recorta su memoria y baja la prioridad de lo que alenta de fondo; nunca cierra nada"],
  ["hud.activo", "Mostrar el personaje en la esquina", "bool", "Aplica al reiniciar Jarvis"],
  ["hud.estilo", "Estilo de la esquina", [["vaultboy", "Avatar animado"], ["reactor", "Reactor"]]],
  ["hud.subtitulos", "Subtítulos de lo que dice", [["siempre", "Siempre"], ["expositor", "Solo al exponer"], ["nunca", "Nunca"]]],
  ["hud.tamano", "Tamaño del personaje", "rango", "", 80, 220, 5],
  ["remoto.hablar_en_pc", "Decir en la PC lo que pides desde el teléfono", "bool"],
  ["para_mi.callarse", "Qué tan fácil se calla con el ruido de fondo", "rango", "0 = nunca se calla solo", 0, 0.2, 0.01],
];
cargas.ajustes = async () => {
  const valores = await api("/api/ajustes");
  const cont = $("lista-ajustes");
  cont.replaceChildren();
  for (const [clave, texto, tipo, ayuda, min, max, paso] of AJUSTES) {
    const fila = el("div", "ajuste");
    const etiqueta = el("div", "", texto);
    if (ayuda) etiqueta.append(el("small", "", ayuda));
    let control;
    const guardar = async (v) => {
      try { await api("/api/ajustes", { [clave]: v }); toast("Guardado."); } catch (e) { toast(e.message, true); }
    };
    if (tipo === "bool") {
      control = el("label", "interruptor");
      const c = el("input"); c.type = "checkbox"; c.checked = !!valores[clave];
      c.addEventListener("change", () => guardar(c.checked));
      control.append(c, el("span"));
    } else if (tipo === "rango") {
      control = el("input"); control.type = "range"; control.min = min; control.max = max; control.step = paso;
      control.value = valores[clave] ?? min; control.style.maxWidth = "220px";
      control.addEventListener("change", () => guardar(+control.value));
    } else {
      control = el("select");
      for (const [v, t] of tipo) { const o = el("option", "", t); o.value = v; o.selected = valores[clave] === v; control.append(o); }
      control.addEventListener("change", () => guardar(control.value));
    }
    fila.append(etiqueta, control);
    cont.append(fila);
  }
};

// ---------- Arranque ----------
(async () => {
  if (!LLAVE) { toast("Abre la app desde el acceso directo de Jarvis.", true); return; }
  try { await cargarEstado(); } catch (e) { toast("No pude hablar con Jarvis: " + e.message, true); }
  conectar();
})();
