# Jarvis — guía de uso con los lentes Ray-Ban Meta

Jarvis es Genesis (el proyecto de tu amigo) con todo lo suyo intacto, incluido Teams, más
tres cosas nuevas:

1. **Control total de la PC por voz**: PowerPoint, cambiar de ventana, teclas, escribir, dar
   clic en botones por su nombre (también en tu software web) y rutinas para demos.
2. **Modo expositor**: mientras expones, Jarvis habla con el público por las bocinas de la
   laptop o del proyector, ve lo que tú ves con la cámara de los lentes y conoce el
   contenido de tus diapositivas.
3. **HUD estilo Iron Man** sobre la presentación: un reactor que muestra si escucha, piensa,
   mira o habla, subtítulos de lo que dice y la imagen de lo que acaba de ver.

## Qué se agregó

| Archivo | Para qué sirve |
|---|---|
| `control.py` | Cambiar de ventana, presionar teclas, escribir texto, cerrar la ventana activa, dar clic por texto, leer una ventana y ejecutar rutinas |
| `presentacion.py` | PowerPoint por COM (funciona aunque otra ventana tenga el foco) y lectura del contenido de cada diapositiva |
| `expositor.py` | Modo expositor: mirar, presentarse al público y hablarle al público |
| `camara.py` | De dónde sale la imagen de los lentes (OBS, ventana, celular, pantalla o una foto de prueba) |
| `vision.py` | Modelo con visión: Groq en la nube, o Ollama local si no hay internet |
| `hud.py` | Reactor, subtítulos y miniatura encima de todo, sin robar el foco ni bloquear clics |
| `diagnostico.py` | Revisa pieza por pieza que todo funcione en tu PC antes de la demo |
| `escuchar.py` | "Hey Jarvis" con openWakeWord y protección para que Jarvis no se oiga a sí mismo |
| `voz.py` | Permite elegir por qué bocinas sale la voz (público o privada) |

Todo lo demás (Teams, Spotify, recordatorios, memoria, mantenimiento) funciona igual que
antes. Los archivos siguen llamándose `genesis.py`, etc., para que puedas traer mejoras
futuras de tu amigo sin pelear con nombres.

## Instalación

```powershell
cd jarvis
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
setx GROQ_API_KEY "tu_clave"        # cerebro y visión en la nube (gratis en console.groq.com)
```

Opcional, para funcionar sin internet: `ollama pull qwen2.5:7b` (cerebro) y
`ollama pull qwen2.5vl:7b` (visión).

La voz Piper (`voces/es_MX-claude-high.onnx`) te la tiene que pasar tu amigo. Sin ella se usa
la voz de Windows.

Después corre la revisión:

```powershell
.venv\Scripts\python diagnostico.py
```

## Las dos formas de conectar los lentes

Meta no deja que una PC lea la cámara de los lentes por Bluetooth. Por eso hay dos modos, y
eliges según lo que quieras mostrar:

| | Modo A: lentes como audífonos | Modo B: lentes en videollamada |
|---|---|---|
| Cómo se conectan | Bluetooth directo a la laptop | Videollamada de WhatsApp del celular a la laptop |
| Micrófono de los lentes | Sí | Sí (con VB-Cable) o usa el de la laptop |
| Cámara de los lentes | No | Sí |
| Jarvis te habla al oído | Sí (salida privada = lentes) | No, todo sale por las bocinas |
| Dificultad | Fácil | Media (OBS + segunda cuenta de WhatsApp) |
| Ideal para | Controlar la presentación y la PC | "Jarvis, ¿qué ves?" y saludar al público |

### Modo A: Bluetooth directo (empieza por aquí)

1. Windows → Configuración → Bluetooth → Agregar dispositivo → tus Ray-Ban. Mientras estén
   conectados a la laptop se desconectan de la app de Meta en el celular; es normal.
2. Corre `python diagnostico.py audio` y copia los nombres exactos que aparezcan.
3. En `config.json`:
   - `"mic_dispositivo"`: el micrófono de los lentes (suele decir "Hands-Free").
   - `"salida_privada"`: la salida de los lentes (también "Hands-Free").
   - `"salida_publico"`: "Altavoces", "Realtek" o el nombre del proyector/TV si el audio va por HDMI.
4. `python diagnostico.py voz`: tienes que oír una frase en los lentes y otra en las bocinas.

### Modo B: cámara por videollamada (para que Jarvis vea)

La idea: los lentes transmiten su vista en una videollamada de WhatsApp, la laptop recibe esa
llamada, OBS la convierte en una "cámara" y Jarvis la usa.

1. **Una segunda cuenta de WhatsApp en la laptop.** No puedes llamarte a ti mismo. Puede ser
   un número de trabajo o WhatsApp Business con otro número, abierto en WhatsApp Desktop.
2. **Llamada:** desde tu celular (con los lentes conectados) haz videollamada a esa cuenta y
   contesta en la laptop. En la llamada, cambia a la cámara de los lentes: en las Ray-Ban es
   presionar dos veces el botón de captura. Confirma en tus lentes cómo se hace.
3. **OBS Studio (gratis):** Fuente → Captura de ventana → WhatsApp. Recórtala para que solo
   se vea el video. Luego presiona "Iniciar cámara virtual". OBS captura la ventana aunque
   PowerPoint quede encima en pantalla completa.
4. Corre `python diagnostico.py camaras`. Guarda una foto por cámara en `datos/`; la que
   muestre la videollamada es tu `camara.webcam_indice`.
5. `python diagnostico.py vision`: Jarvis describe lo que ve.
6. **Audio:** en la llamada de WhatsApp silencia el micrófono de la laptop. Para que Jarvis
   oiga el micrófono de los lentes:
   - Instala VB-Audio Virtual Cable (gratis).
   - Windows → Configuración → Sonido → Mezclador de volumen → WhatsApp → Salida:
     "CABLE Input".
   - En `config.json`: `"mic_dispositivo": "CABLE Output"`.

   Si no quieres hacer esto, usa el micrófono de la laptop: `"mic_dispositivo": ""`.

Si no quieres usar OBS, pon `"camara": {"fuente": "ventana", "ventana_titulo": "WhatsApp"}`.
Captura la ventana directamente, pero la ventana no puede estar minimizada y algunas apps salen
en negro. OBS es más confiable.

Para el futuro: si haces una app de celular con el SDK oficial de Meta (Wearables Device
Access Toolkit, todavía en vista previa), la fuente `"http"` ya recibe fotos JPEG por
`POST http://IP-de-tu-laptop:8765/frame`, con el encabezado `X-Token` si pones
`camara.http_token`.

## La palabra de activación

| Motor (`motor_activacion`) | Qué dices | Ventajas |
|---|---|---|
| `openwakeword` (por defecto) | "Hey Jarvis" (en inglés, "jei yárvis") | Casi no usa CPU aunque hables toda la exposición, reacciona rápido y puedes decir la orden de corrido: "hey Jarvis, siguiente diapositiva" |
| `whisper` (el de Genesis) | "Jarvis" | Entiende la palabra en español, pero transcribe todo lo que dices y usa más CPU |

Si "hey Jarvis" no te detecta, baja `oww_sensibilidad` a 0.35. Si se activa solo, súbela a
0.6. `python diagnostico.py palabra` muestra el puntaje en vivo.

## Qué le puedes decir

| Dices | Qué pasa |
|---|---|
| "Hey Jarvis, abre mi presentación de residencias" | Busca el .pptx, lo abre en PowerPoint y lee su contenido |
| "…inicia la presentación" | Pantalla completa |
| "…siguiente" / "regresa" / "ve a la diapositiva 7" / "avanza tres" | Instantáneo, sin pasar por la IA y sin decir nada en voz alta |
| "…pantalla negra" / "quita la pantalla negra" | Para pausar y captar la atención |
| "…modo expositor" | Desde ahí Jarvis habla por las bocinas para el público |
| "…preséntate con el público" | Mira al público por los lentes, saluda, comenta el ambiente y presenta el tema |
| "…¿qué ves?" / "describe lo que tengo en la mano" / "lee ese letrero" | Mira por la cámara y responde |
| "…explica esta diapositiva al público" | Usa el texto y tus notas del expositor |
| "…¿qué opinas de la pregunta que me hicieron sobre la seguridad?" | Responde apoyándose en tu presentación |
| "…cambia a Chrome" / "regresa a PowerPoint" | Trae al frente una ventana ya abierta |
| "…dale clic a Iniciar sesión" | Busca el botón por su nombre en la ventana activa |
| "…escribe admin@ejemplo.com" / "presiona enter" / "control más s" | Teclado |
| "…corre la demo de mi software" | Ejecuta la rutina guardada, paso por paso |
| "…cierra esta ventana" | Cierra la ventana que está al frente |
| "…termina la presentación" / "ya terminé de exponer" | Sale de pantalla completa y apaga el modo expositor |

Todo lo de antes sigue funcionando: Teams, Spotify, YouTube, recordatorios, volumen, buscar
archivos, memoria.

## Velocidad y límites de Groq

El plan gratis de Groq da **8,000 tokens por minuto por modelo**. Jarvis ya está ajustado para
eso:

| Ajuste | Qué hace |
|---|---|
| Herramientas por orden | Solo manda al cerebro las herramientas relacionadas con lo que pediste (de ~8,500 a ~2,500 tokens) |
| Modelos de respaldo | Si `gpt-oss-120b` está saturado, usa `gpt-oss-20b` al instante (`nube.respaldos`) |
| Sin reintentos lentos | Ya no espera 45 s cuando el servicio está lleno: pasa al siguiente |
| `nube.razonamiento: "low"` | El modelo "piensa" menos antes de contestar: responde más rápido |
| `silencio_seg` | Cuánto silencio espera para saber que terminaste de hablar (0.6 recomendado) |

**Para el día del hackathon:** en console.groq.com activa el plan **Developer** (pago por uso;
una demo cuesta centavos). Los límites suben mucho y desaparece el riesgo de saturarse en
plena exposición.

Opcional: también puedes agregar Claude como cerebro de respaldo en `nubes_extra`:

```json
"nubes_extra": [
  {"url": "https://api.anthropic.com/v1/", "modelo": "claude-haiku-4-5",
   "clave_env": "ANTHROPIC_API_KEY", "timeout": 20}
]
```

## Recorrer y explicar una página

"Jarvis, explica la página mientras la bajas" (o "dales un tour por el sistema"): Jarvis
escanea la página de arriba abajo (hasta 3 pantallas), manda todo junto al modelo de visión y
luego la expone por partes, bajando la página solo entre una parte y otra. Funciona con tu
software en Chrome, un PDF o un documento.

## Rutinas: la demo que sale igual siempre

Para el momento "wow" con tu software no dependas de que la IA improvise los clics. Guarda los
pasos en `config.json → rutinas`:

```json
"demo de mi software": [
  {"decir": "Permítanme mostrarles el sistema en vivo."},
  {"abrir_web": {"url": "https://tu-sistema.com"}},
  {"esperar": 4},
  {"clic_en": {"texto": "Iniciar sesión"}},
  {"esperar": 2},
  {"decir": "Y listo: tiempo real, sin tocar el teclado."}
]
```

Cada paso es cualquier herramienta de Jarvis con sus parámetros, más dos especiales:
`"esperar"` (segundos) y `"decir"` (frase). Si un paso falla, la rutina se detiene y te dice
en cuál. Para saber cómo se llaman los botones, di "lee la ventana" con tu software abierto.

## El HUD

| Opción en `config.json → hud` | Qué hace |
|---|---|
| `activo` | Enciende o apaga todo el HUD |
| `solo_expositor` | `true` = el reactor solo aparece en modo expositor |
| `subtitulos` | `"expositor"` (por defecto), `"siempre"` o `"nunca"` |
| `monitor` | `"auto"` = el monitor donde está la presentación (el proyector); o un número |
| `posicion` | `abajo_derecha`, `abajo_izquierda`, `arriba_derecha`, `arriba_izquierda` |
| `mostrar_vista` | Muestra unos segundos lo que Jarvis vio por los lentes |

`python diagnostico.py hud` te lo muestra pasando por todos los estados.

## Checklist del día de la demo

| Antes de empezar | Listo |
|---|---|
| Laptop cargada, lentes cargados (el micrófono abierto gasta batería) | ☐ |
| `python diagnostico.py`: todo en [OK] | ☐ |
| Presentación abierta una vez (así Jarvis ya leyó su contenido) | ☐ |
| Modo B: llamada de WhatsApp conectada, OBS con cámara virtual iniciada | ☐ |
| Prueba de voz por las bocinas del salón (`diagnostico.py voz`) | ☐ |
| La rutina de la demo probada completa una vez en ese mismo lugar | ☐ |
| Internet estable (o los modelos de Ollama ya descargados) | ☐ |
| Avisarle al público que los lentes tienen cámara | ☐ |

Plan B si algo falla en vivo: el clicker o las flechas siguen funcionando, porque Jarvis
devuelve el foco a PowerPoint después de cualquier ventanita. El icono de la bandeja tiene
"Escribir una orden" por si el micrófono falla.

## Privacidad y respeto al público

Jarvis tiene una regla fija en el código (`vision.py`): no identifica personas ni comenta
rasgos físicos, edad o apariencia de nadie. Habla del grupo en general y del lugar. Con
Groq, las imágenes se procesan en su nube; si prefieres que nada salga de la laptop, usa
`"vision": {"modo": "offline"}` con Ollama.

## Qué se probó y qué no

Se probó fuera de Windows, simulando las partes de Windows:

- Detección de "hey Jarvis" y grabación de la orden seguida, con voz sintetizada: detecta en
  todas las pruebas y no se activa con frases en español sin la palabra.
- Atajos de diapositivas, teclas, rutinas, órdenes de varios pasos y enrutado de voz
  privada/pública.
- Cámara (archivo, recorte y receptor HTTP con token), petición de visión y decodificación de
  audio.
- Lectura de .pptx y dibujo del HUD.

No se pudo probar en Windows real: PowerPoint por COM, los clics por accesibilidad en Chrome,
la salida por dispositivo con los lentes y el HUD transparente sobre PowerPoint. Para eso está
`diagnostico.py`. Si algo falla, manda la captura y la parte de `datos/genesis.log`.
