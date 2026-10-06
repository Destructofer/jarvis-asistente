# Jarvis — guía para la demo

Jarvis es Genesis (el proyecto de tu amigo, con todo lo suyo intacto, incluido Teams) convertido
en **un integrante más del equipo** para exponer: ve al público, maneja tu software en vivo,
cambia a la presentación, responde preguntas y habla por las bocinas mientras tú expones.

Empieza por el **checklist** (desde la carpeta `jarvis`): `.\.venv\Scripts\python diagnostico.py demo`
te dice en verde/rojo qué falta.

## Qué hace ahora

| Capacidad | Cómo |
|---|---|
| Habla casi al instante | Empieza a hablar con la primera frase mientras la IA sigue escribiendo; si pensar tarda, dice "Claro." / "Veamos." al instante |
| Maneja tu software | Chrome propio con **cursor de Jarvis** (un círculo luminoso que viaja al botón) y **reflector** que ilumina lo que explica |
| Recorre todos los módulos | Anuncia cada módulo mientras da clic y lo explica señalando cada parte |
| Ensaya antes | "Ensaya la demo": prepara la explicación de cada módulo y la voz; en escena no espera a la IA |
| Ve al público | Por los lentes (videollamada) o una cámara; saluda **al instante** mientras la visión mira |
| Responde preguntas | Del proyecto (conocimiento/proyecto.md) o de cualquier tema; recuerda lo que se oyó en los últimos 45 s |
| Se le puede interrumpir | Di "Hey Jarvis" mientras habla o recorre algo: se calla y te escucha |
| Aguanta fallas | Micrófono de respaldo si se cae la llamada; si falla la nube: modelo local, Whisper en la GPU y voz local Piper |

## Puesta a punto (una vez)

```powershell
cd jarvis
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -r requirements-gpu.txt   # solo con tarjeta NVIDIA: Whisper 6x más rápido
copy config.example.json config.json
setx GROQ_API_KEY "tu_clave"            # console.groq.com
setx ELEVENLABS_API_KEY "tu_clave"      # opcional: la voz más natural
```

Cierra y abre la terminal después de `setx`. Luego:

1. **Groq en plan Developer** (console.groq.com → Billing). El plan gratis da 8.000 tokens por
   minuto y cada orden usa ~2.500: **a la segunda o tercera orden seguida se satura**. Una demo
   cuesta centavos.
2. **Modelos locales** (respaldo sin internet): `ollama pull qwen2.5:7b` (y en config.json
   `vision.local_modelo: "gemma3:4b"` o `ollama pull qwen2.5vl:7b`). Si `diagnostico.py demo`
   dice que Ollama no usa la GPU, **reinstálalo** desde ollama.com.
3. **Windows**: *Configuración → Sistema → Sonido → Más opciones de sonido → pestaña
   Comunicaciones → "No hacer nada"*. Si no, al usar el micrófono Bluetooth o la videollamada,
   Windows baja al 20 % el volumen de todo lo demás, incluida la voz de Jarvis.
4. `python diagnostico.py demo` hasta que todo salga en verde.

## Cómo conectar los lentes (elige según cómo vas a exponer)

| | A. Bluetooth directo | B. Videollamada (caminar entre el público) | C. Sin lentes |
|---|---|---|---|
| Te mueves por el salón | No (≈10 m de alcance) | **Sí, sin límite** | Con micrófono de solapa |
| Jarvis ve lo que tú ves | No | **Sí, por la cámara de los lentes** | Cámara del celular hacia el público |
| Jarvis te habla al oído | Sí | No (todo por la bocina) | No |
| Dificultad | Fácil | Media | Fácil |

### B. Videollamada: la recomendada para caminar entre el público

La idea: los lentes están conectados a tu celular como siempre; el celular hace una
videollamada de WhatsApp a la laptop; Jarvis ve el video de la llamada y oye tu voz por un
cable virtual; contesta por la bocina de la PC.

```
Lentes (cámara + micrófono) ─Bluetooth─ Celular (en tu bolsillo) ─WhatsApp─► Laptop
                                                                   ├─ video → Jarvis "ve"
                                                                   └─ audio → VB-Cable → Jarvis "oye"
Jarvis responde ─► bocina de la PC (o la del salón)
```

1. **WhatsApp Desktop en la laptop con OTRA cuenta**: no necesitas otro número; vincula la
   cuenta de un compañero (WhatsApp de su celular → Dispositivos vinculados). Ojo: sus chats
   quedan visibles en la laptop; que esa ventana nunca salga en el proyector.
2. Instala **VB-Audio Virtual Cable** (gratis).
3. Windows → Configuración → Sistema → Sonido → **Mezclador de volumen → WhatsApp → Salida:
   "CABLE Input"**. Así Jarvis oye la llamada y el salón no.
4. En `config.json`:
   ```json
   "mic_dispositivo": "CABLE Output",
   "mic_respaldo": "auto",
   "pausa_tras_hablar": 1.5,
   "salida_publico": "Realtek",
   "salida_privada": "Realtek",
   "camara": {"fuente": "ventana", "ventana_titulo": "(título de la ventana de la llamada)"}
   ```
   - `mic_respaldo: "auto"`: si la llamada se cae (el cable queda en silencio total ~8 s),
     Jarvis pasa **solo** al micrófono de la laptop y regresa en cuanto vuelve la llamada.
   - `pausa_tras_hablar: 1.5`: la voz de Jarvis regresa por tus lentes con ~1 s de retraso;
     así no se escucha a sí mismo.
   - El título de la ventana: con la llamada abierta corre `python diagnostico.py ventanas`.
     Si la captura sale negra, usa **OBS** (Captura de ventana → Iniciar cámara virtual) con
     `"camara": {"fuente": "webcam", "webcam_indice": N}` (N sale de `diagnostico.py camaras`).
5. En la llamada: **silencia el micrófono y apaga la cámara de la laptop**. En tu celular cambia
   a la cámara de los lentes (en las Ray-Ban Meta: presiona dos veces el botón de captura;
   confírmalo en tu modelo).
6. Pruebas: `diagnostico.py llamada` (¿llega tu voz?), `diagnostico.py vision` (¿ve?).
7. Usa **datos móviles** en el celular (no el Wi-Fi del evento) y conecta la llamada justo
   antes de exponer: el video gasta batería de los lentes.

### A. Bluetooth directo (si te quedas cerca de la laptop)

Windows → Bluetooth → Agregar dispositivo → tus Ray-Ban. `python diagnostico.py audio` y copia
los nombres: `mic_dispositivo` y `salida_privada` = los de los lentes ("Hands-Free");
`salida_publico` = la bocina. Jarvis te puede decir cosas **al oído** (salida privada).

### C. Sin lentes

Un **micrófono de solapa inalámbrico** (receptor USB) es lo más confiable para que te oiga en
un salón ruidoso. Para que vea al público: tu celular en un tripié como cámara (Android: Phone
Link; iPhone: Iriun o Camo). La cámara de la laptop normalmente te ve a ti, no al público.

## Tu software en la demo

En `config.json → demo`:

```json
"demo": {
  "url": "https://tu-sistema.com",
  "pantalla_completa": true,
  "modulos": [],
  "omitir": ["Configuración"],
  "login": {"usuario_env": "DEMO_USUARIO", "clave_env": "DEMO_CLAVE",
            "campo_usuario": "Usuario", "campo_clave": "Contraseña", "boton": "Iniciar sesión"}
}
```

- Jarvis abre **su propio Chrome** (perfil en `datos/chrome-jarvis`, aparte de tu Chrome
  personal). Inicia sesión en tu sistema **una vez** en ese Chrome y queda guardada; o pon el
  usuario de demo en variables de entorno (`setx DEMO_USUARIO ...`, `setx DEMO_CLAVE ...`) y di
  "inicia sesión en la demo" (la contraseña nunca se dice en voz alta).
- `modulos` vacío = recorre el menú tal como está (sin "Salir", "Eliminar", "Pagar"...). Para
  fijar orden y narraciones propias:
  ```json
  "modulos": ["Inventario", {"nombre": "Ventas", "decir": "Aquí registramos cada venta en segundos."}]
  ```
- Si no encuentra tu menú, pon su selector CSS en `menu_selector` (p. ej. `"aside nav"`).
- **Ensayo**: con el sistema listo, di "Jarvis, ensaya la demo". Visita cada módulo, prepara
  qué señalar y qué decir, y genera la voz (queda en `datos/demo_cache.json`). En la
  exposición el recorrido sale al instante. Repite el ensayo si cambias tu software.

## Lo que Jarvis sabe del proyecto

Llena `conocimiento/proyecto.md` (problema, cómo funciona, tecnología, equipo, preguntas
probables del jurado con sus respuestas) y `config.json → equipo` (nombre del equipo e
integrantes con su rol). Con eso responde como integrante: "nosotros desarrollamos…".

Jarvis **no inventa** datos del proyecto: si le preguntan algo que no está ahí (precios,
cifras, clientes), dice que esa pregunta te la deja a ti. Por eso vale la pena escribir las
respuestas a las preguntas típicas del jurado (costo, modelo de negocio, qué sigue).

## Modo conversación: habla de corrido, sin repetir "Jarvis"

Solo la **primera** vez dices "Jarvis" (y puedes decir la orden de corrido: "Jarvis, abre la
presentación", sin esperar el pitido). Después de que contesta, **sigue escuchando 20 segundos**
(12 en modo expositor) sin su nombre ni pitido: el reactor del HUD se queda encendido mientras
tanto. Cada respuesta vuelve a abrir la ventana, así que la plática sigue sola.

- "Gracias" o "eso es todo" la cierra ("A sus órdenes."). Si te quedas callado, se cierra sola.
- En **modo expositor** distingue lo que le dices a él de lo que le dices al público:
  "¿puedes explicar el de ventas?", "muéstrales…", "¿y tú qué opinas?" → contesta;
  "como pueden ver…", "gracias por venir", "les voy a mostrar…" → se queda callado.
  Si quieres asegurarte, di su nombre: "Jarvis, …" siempre es para él.
- Duración: `config.json → conversacion_seg` y `conversacion_seg_expositor` (0 = apagado).

## Jarvis tiene criterio propio

Jarvis no habla como asistente sino como un compañero más (`config.json → personality` y
`personality_expositor`; `{presentador}` se cambia por tu nombre):

- **Opina de verdad**: ante "¿qué opinas?", "¿cuál es mejor?", "¿qué le mejorarías?" toma una
  postura con su razón, puede no estar de acuerdo contigo (con respeto) y a veces te devuelve
  una pregunta. En esas preguntas **razona más a fondo** (`razonamiento_opinion: "medium"`) y
  dice "Buena pregunta." mientras piensa; las órdenes siguen en modo rápido.
- **Honesto**: reconoce debilidades del proyecto; sus ideas propias las dice como suyas ("yo
  propondría...") y nunca inventa datos ni planes del equipo.
- Nada de frases de asistente ("¿en qué más puedo ayudarte?"); te llama por tu nombre.

## Cómo piensa Jarvis (`cognicion.py`)

El modelo de lenguaje es solo el motor; la forma de pensar está en Jarvis y es igual en línea y
sin internet. Todos los cerebros son de **pesos abiertos** (nada de ChatGPT ni Gemini):

- **Cuánto pensar.** Una orden directa ("abre Spotify") va rápido. Lo que merece pensarse
  (planear, decidir, comparar, explicar por qué, resolver, "¿qué me conviene?") se razona a
  fondo: en la nube con razonamiento alto (~1 s más). Medido: con razonamiento bajo armó un
  horario sin sentido; con alto, uno coherente.
- **Cómo pensar.** Entiende qué necesitas de verdad, usa el contexto (la hora, la ventana que
  tienes abierta, lo que sabe de ti), advierte antes de algo riesgoso, no dice que hizo algo
  sin confirmarlo y te ofrece el siguiente paso cuando es claramente útil.
- **Si algo falla, busca otra vía** (otra herramienta, otro nombre, preguntarte lo que falte)
  en vez de solo decir el error.
- **Calcula, no adivina.** Para cuentas usa `calcular` y para fechas `calendario` ("¿qué día
  cae el 15?", "¿cuántos días faltan para Navidad?"). Sin ellas, ningún modelo atinaba el día
  de la semana.
- **Aprende de ti.** Cuando hablas de ti ("me encanta el café de olla", "estudio sistemas")
  guarda el dato solo, en segundo plano, sin contraseñas, cuentas, teléfonos ni salud, y solo
  de TUS palabras (nunca de documentos o pantallas). "¿Qué sabes de mí?" para verlo; "olvida
  que..." para borrarlo. `config.json → cognicion.aprender: false` lo apaga.

**Sin internet** (`modo: offline` o si se cae la red) todo sigue en la laptop: Whisper en la
GPU, el cerebro y la visión con `qwen3.5:4b` (cabe entero en una GPU de 6 GB: ~0.6 s por orden,
~2 s por foto) y la voz con Piper. Medido con las órdenes de prueba: 7/7 correctas. Razona
menos fino que la nube en planes complejos (es un modelo chico); `model_profundo` permite usar
uno más grande solo para pensar a fondo si tienes una GPU con más memoria.

## Memoria y aprendizaje (`preferencias.py`, `memoria.py`)

Jarvis aprende cómo quieres que haga las cosas y lo sigue haciendo, también después de
reiniciarlo, hasta que le digas otra cosa:

| Dices | Qué aprende | Desde ese momento |
|---|---|---|
| "De ahora en adelante usa Opera como navegador" | navegador = Opera | Las páginas, búsquedas y "abre el navegador" van a Opera |
| "Para la música prefiero YouTube" | música = YouTube | "Pon música de…" usa YouTube |
| "Usa DuckDuckGo para buscar" | buscador = DuckDuckGo | Las búsquedas usan ese buscador |
| "Mi editor de código es VS Code" | código = VS Code | "Abre mi editor de código" abre ese |
| "Siempre que te pida un resumen, guárdalo en Word" | una instrucción permanente | La sigue cada vez que aplica |
| "Ya no uses Opera, vuelve a Chrome" | cambia la preferencia | Usa Chrome |
| "¿Qué preferencias tienes de mí?" / "Olvida la regla de los resúmenes" | — | Te las dice / la borra |

Las preferencias de apps las aplica el código (no dependen de que el modelo se acuerde); las
instrucciones van al contexto del modelo en cada orden. También **recuerda las conversaciones**
(los últimos 5000 mensajes, `memoria.max_mensajes`): "¿de qué hablamos ayer?", "¿qué te dije
del examen?". Fijar una preferencia o instrucción pide confirmación si en la conversación entró
texto de terceros (Teams, páginas, documentos), para que nadie te las siembre.

## Jarvis te ve y entiende tus manos (estilo Iron Man)

Usa la cámara de la laptop (`config.json → camara.usuario_indice`, normalmente 0). Lo que tiene
que ser instantáneo (tus manos y si estás frente a la PC) se calcula **en la laptop** con
MediaPipe: es gratis, funciona sin internet y tarda ~35 ms por cuadro.

| Gesto (sostenlo ~medio segundo, mano quieta) | Qué hace |
|---|---|
| ✋ palma abierta | Jarvis se calla al instante |
| ☝ índice arriba | te escucha sin que digas "Jarvis" (suena el bip) |
| 👍 / 👎 | contesta "sí" / "no" cuando te pide confirmar algo |
| ✌ victoria | te mira y te dice algo, como un compañero que voltea a verte |
| 🤟 rock | **modo mouse**: el índice mueve el cursor y juntar pulgar e índice hace clic; otro 🤟 lo apaga |
| 👋 deslizar a la izquierda / derecha | siguiente / anterior diapositiva (sin presentación: cambia de ventana) |

- Todo se cambia en `gestos.acciones`. Un gesto puede lanzar una orden de voz
  (`"puno": "orden:pausa la música"`) o teclas (`"teclas:ctrl+s"`); `""` lo desactiva.
- **Ensáyalo**: `.\.venv\Scripts\python diagnostico.py gestos` abre tu cámara y muestra qué gesto ve y
  qué haría (sin ejecutarlo).
- **Te saluda** al encenderlo y cuando vuelves tras 5 min fuera (`presencia.saludar_tras_min`),
  con algo que note si viene al caso, y se queda escuchando tu respuesta.
- Mientras platican, cada 2 min echa un vistazo (`presencia.describir_cada_seg`, 0 = nunca) y
  lo toma en cuenta al contestar ("te veo desvelado: ¿lo dejamos para mañana?").
- Dile "mírame", "¿cómo me veo?", "¿qué tengo en la mano?" o "¿qué te parece esto?" mostrándole
  algo. "Deja de verme" apaga la cámara; "ya puedes verme" la vuelve a encender.

## Cerebros gratis de respaldo (cuando Groq llega a su límite)

El plan gratis de Groq se satura con varias preguntas seguidas. Ahora Jarvis pasa solo a otra
nube gratis en vez de caer al modelo local (que tarda ~26 s):

1. **Google Gemini** (gratis, sin caducidad, también con visión): saca la clave en
   https://aistudio.google.com/apikey y en PowerShell `setx GEMINI_API_KEY "tu-clave"`.
2. **Cerebras** (opcional; muy rápido, pero su prueba gratis dura 30 días): clave en
   https://cloud.cerebras.ai y `setx CEREBRAS_API_KEY "tu-clave"`.

Abre una terminal nueva y revisa con `.\.venv\Scripts\python diagnostico.py nubes`. Si prefieres
Gemini antes que Groq: `"nube": {"preferir_extras": true}`.

## Jarvis observa al público (y no te confunde con órdenes)

En modo expositor, Jarvis mira por la cámara cada ~8 segundos (`config.json → observador`):

- **Si alguien parece tener una duda** (gesto de confusión que se sostiene en dos miradas
  seguidas, o uno muy claro), espera a que **hagas una pausa** y pregunta con tacto, sobre lo
  que estabas explicando: "¿Te quedó alguna duda sobre cómo se agenda la cita?". Si platicas
  de frente con una persona le habla de tú; si es el público, en plural. Nunca menciona caras
  ni pone a nadie en evidencia, y no vuelve a hacerlo antes de 60 s. La respuesta de esa
  persona la escucha **sin que nadie diga "Jarvis"**.
- **Si le explicas algo a alguien**, Jarvis sabe que no es para él (por cómo lo dices y porque
  la cámara ve que platicas de frente con alguien) y se queda callado. **Solo si dijiste un dato
  equivocado o faltó algo clave** de lo que está en `conocimiento/proyecto.md`, te complementa
  con tacto **en tu siguiente pausa** ("Si me permites, el costo es de 99 pesos al mes"), como
  máximo una vez cada 45 s (`complementar.cada_seg`). Si sigues hablando, se lo guarda.
- "Jarvis, deja de observar al público" / "observa al público" lo apaga y lo prende.

Todo esto depende de que la cámara esté configurada y de que `conocimiento/proyecto.md` tenga
los datos reales: sin ellos no tiene con qué complementarte (y no inventa).

## Documentos: descomprimir, resumir y guardar

| Dices | Qué pasa |
|---|---|
| "Jarvis, descomprime el zip de la práctica" | Lo extrae junto al archivo (.zip, .rar, .7z, .tar) y dice qué traía, numerado |
| "…resume el segundo" / "…analiza el Excel de ventas" | Lee el archivo y da el resumen en 2-4 frases |
| "…¿cuáles son los puntos clave del PDF?" / "…¿de qué trata?" | Puntos clave (datos, fechas, entregables) o la idea general |
| "…¿qué mes tuvo más ganancia según el Excel?" | Responde usando solo el documento |
| "…resúmelo y ponlo en un bloc de notas" / "…en un Word" | Crea el archivo en Documentos\Jarvis y lo abre |
| "…anota en un bloc de notas: comprar focos" | Escribe tus notas |

Lee PDF (también escaneados, con visión), Word, Excel, PowerPoint, texto, HTML, EPUB, formatos
viejos de Office (con Office instalado), imágenes, una carpeta o un zip completo. Los documentos
largos se analizan por partes. Todo en `documentos.py`.

## La cámara para tareas del entorno

| Dices | Qué pasa |
|---|---|
| "Jarvis, escanea el cuarto y dime qué hay" | Describe el lugar, los objetos y dónde están (con "haz un barrido", 3 fotos) |
| "…¿dónde dejé mis llaves?" / "…¿cuántas sillas hay?" | Busca y dice junto a qué está / cuenta |
| "…lee esta hoja y pásala a un bloc de notas" | Transcribe y guarda |
| "…¿qué es este componente?" / "…revisa si hay algo peligroso" | Identifica / revisa riesgos |
| "…vigila la puerta y avísame si alguien entra" | Vigila en segundo plano; solo consulta a la IA cuando algo se mueve |

En `entorno.py`. Usa la misma fuente de cámara que "¿qué ves?" (`camara.fuente`).

## El día y la noche (`ciclo.py`)

**En la mañana**, la primera vez que te ve (o que usas la computadora) te da el resumen del día:
saludo y hora, el **clima** de donde estás y el pronóstico (con consejos: paraguas, abrígate,
bloqueador), lo que te **quedó pendiente** de antes y lo de hoy, tus recordatorios, los chats de
**WhatsApp** sin responder de las últimas 24 h (nombre del chat y último mensaje) y tus **correos
importantes** de más a menos: salud, bancos, inversiones, empleo y escuela. También: "dame mi
resumen", "¿cómo está el clima?", "¿qué tengo pendiente?".

**En la noche** (desde `ciclo.hora_noche`, 23:00), si sigues en la computadora te dice la hora y,
si la cámara te ve **cansado** (ojos que se cierran seguido o bostezos, medido en la PC con
MediaPipe, sin mandar fotos), te propone descansar. Lo que le digas que quedó pendiente ("me
quedó pendiente el reporte") se anota y te lo dice a la mañana siguiente.

Conexiones:
- **Ubicación:** la de Windows (Configuración > Privacidad > Ubicación activada). Sin ella, por IP.
- **Gmail:** "conecta mi Gmail": te abre la página de Google para crear una *contraseña de
  aplicación* (requiere la verificación en dos pasos) y una ventanita para pegarla. Solo lee;
  se guarda cifrada (`datos/gmail.json`).
- **WhatsApp:** la app de escritorio con la sesión iniciada. Jarvis solo lee la lista de chats
  (nunca abre uno, para no marcarlo como leído).

## Personalidades (`personalidades.py`)

Como los modos de voz de Grok en un Tesla, pero con sabor mexicano: "Jarvis, ponte en modo
mirrey", "háblame como abuelita", "cambia tu personalidad a norteño", "vuelve a ser normal",
"¿qué personalidades tienes?". La que elijas se queda **para siempre** (también al reiniciar)
hasta que pidas otra, y cambia el tono, las palabras y la voz:

| Personalidad | Cómo es |
|---|---|
| Jarvis clásico | elegante, seguro, humor fino (la de siempre) |
| Mirrey | "mi rey", "papá", "neta", "lo que le sigue", "simple is nice" |
| Godín | "licenciado", "quedo atento", "ya falta poco para salir" |
| Fresa | "o sea", "qué oso", "obvi", spanglish |
| Chavorruco | "qué hongo, carnal", "de pelos", referencias de los 90 |
| Abuelita | "mijo, ¿ya comiste?", dichos, ternura (voz de Dalia, lenta) |
| Norteño | "fierro, pariente", "arre", "compa" |
| Coach, Terapeuta, Narrador, Discutidor, Sin filtro, Profesor, Tutor de inglés, Zen, Niños | los modos de Grok, en español |

La personalidad cambia **cómo** lo dice, nunca lo que hace: las órdenes se ejecutan igual, no
inventa y los riesgos o errores los dice claro. En plena exposición usa la clásica (salvo
"ponte en modo coach también en la exposición").

## Hábitos (`habitos.py`)

Jarvis nota tus rutinas (todo local, en `datos/habitos.db`): cada minuto que usas la computadora
anota qué app tienes al frente y si suena música de fondo, y también lo que le pides y a qué
hora. Con eso te ofrece lo que sueles hacer:

- "Oye, Abraham, cuando estás en VS Code sueles tener música. ¿Te pongo lo de siempre?" → "sí".
- "Normalmente a esta hora abres Spotify. ¿Lo abro?"

Solo ofrece algo que hiciste en 3 días distintos o más, nunca más de una vez cada 30 minutos, ni
mientras expones; si le dices que no dos veces, deja de ofrecerlo ahí una semana. "¿Qué hábitos
has aprendido de mí?", "deja de sugerirme cosas", "olvida mis hábitos".

## Platicarle una situación (`cognicion.py`)

Si le cuentas algo ("fíjate que mi jefe me quiere cambiar de área y no sé qué hacer", "estoy
estresado con la escuela"), Jarvis lo piensa a fondo y contesta como una persona: entiende lo
importante, analiza opciones y riesgos, te da su opinión con su razón y, si falta un dato
clave, te lo pregunta. Con la personalidad que tenga activa.

## Usar páginas y apps como tú (`interaccion.py`)

Jarvis ve lo que hay en la ventana **en el orden en que tú lo ves** y sabe qué es cada cosa:

- "Abre YouTube y pon la primera canción o playlist que veas" · "pon la segunda playlist de
  música para estudiar" · "reproduce el tercer video que aparece" · "pon el mix de trap que sale ahí".
- "Abre el primer resultado" (en Google, Bing...) · "¿qué videos hay?" (te los dice numerados)
  y luego "pon el cuarto".
- "Busca rock en español en esta página" · "escribe hola en el chat": escribe en el buscador o
  campo sin tener que darle clic.
- Al instante, sin pasar por el modelo: "pausa", "reanuda", "siguiente canción", "canción
  anterior", "adelanta 30 segundos", "regresa un minuto", "pantalla completa", "pon subtítulos",
  "salta el anuncio" y "¿qué canción es esta?".
- **Anuncios:** los de YouTube que se pueden omitir se omiten solos en cuanto aparece el botón
  ("ya no quites los anuncios" lo apaga; queda guardado en `datos/interaccion.json`).

Cómo: por accesibilidad (UI Automation) cada enlace de Chrome, Edge u Opera trae su URL real,
así que se distingue con certeza un video, una playlist, un mix, un short, un canal o un
resultado de búsqueda; la posición en pantalla da el orden. Pausar, siguiente, anterior y
adelantar usan los **controles multimedia de Windows** (el panel que sale al subir el volumen):
funcionan con YouTube, Spotify o cualquier reproductor, sin el foco ni el ratón, aunque YouTube
haya escondido sus controles. Pantalla completa, subtítulos y velocidad usan los atajos de
YouTube. Lo peligroso (comprar, borrar, enviar...) se confirma antes de pulsarlo.

## Modo realidad aumentada (tipo Vision Pro)

"Jarvis, activa el modo realidad aumentada": la pantalla completa se vuelve la webcam y encima
flotan tus ventanas abiertas **en vivo** y los iconos de YouTube, Spotify y Steam. Con las manos:

- Pellizco corto (pulgar con índice) sobre una ventana o icono → se abre grande e interactiva.
- **Se usa como un escritorio normal.** Cualquier ventana que se vea grande se usa directo (tocarla
  la enfoca y le da clic ahí mismo, sin reacomodar nada): toque = clic, dos toques = doble clic
  (abrir archivos y carpetas), **pulgar con dedo medio = clic derecho** (menú contextual),
  pellizcar y deslizar = scroll, mantener ½ s y mover = arrastrar.
- **Mover archivos y carpetas:** mantén el pellizco sobre el archivo y llévalo a otra ventana (se
  pinta de naranja): al soltar cae ahí, como con el ratón. Con el botón **Mosaico** (arriba)
  tienes hasta 4 ventanas grandes y en vivo a la vez; las ventanas reales también se acomodan
  lado a lado (para que se lean y se pueda soltar en ellas) y al salir vuelven a como estaban,
  aunque Jarvis se cierre de golpe.
- **Escribir:** al tocar un campo de texto aparece un **teclado virtual** (también con el botón
  Teclado): ñ, acentos (´ y la vocal), Mayús, Borrar sostenido, Enter, Tab, flechas, Copiar y
  Pegar; arriba dice a qué ventana va y lo último que escribiste. El teclado físico y dictarle a
  Jarvis ("escribe lofi hip hop") también funcionan.
- Barra de título: arrastrar mueve el panel; Reducir, Escritorio y X (la X y Salir hay que mantenerlas).
- Salir: botón Salir, decirlo o mantener Esc.

Todo reacciona a la mano: el anillo del cursor se cierra mientras juntas los dedos y se ilumina
sobre lo que se puede tocar; cada toque deja una onda. Al agarrar un panel se levanta (sombra); al
arrastrarlo se inclina hacia donde va, se balancea como colgado de la mano y su borde de adelante
brilla; si lo sueltas en movimiento sigue un poco y se acomoda con un rebote (sin salirse de la
pantalla). Sobre "Llevar al escritorio" la ventana se encoge hacia tu mano. Los iconos del dock
son elásticos (se estiran hacia la mano y regresan) y rebotan mientras su app abre. Un scroll
rápido sigue solo y se frena, como en el celular. Las ventanas aparecen creciendo y al cerrarse
se desvanecen. Si la cámara va lenta (poca luz), la interfaz sigue a 30 fps igual.

Usa el mismo lector de cámara que los gestos y la presencia, y mientras está activo **los gestos
✋👋🤟 se pausan** (las manos son de la realidad aumentada). En `realidad.py`; las ventanas en
vivo usan Windows Graphics Capture (`windows-capture`).

## El avatar del HUD (`avatares.py`)

Cada respuesta termina con `[ACCION: categoria]` (nunca se dice en voz alta) y el ícono de la
esquina es un personaje animado y sin fondo que cambia según la acción. Los personajes viven en
**`Documentos\Jarvis\Avatares\<personaje>\`**: para agregar una animación, suelta el GIF en esa
carpeta (también con Jarvis encendido) y en segundos le quita el fondo, lo mide para que todos
se vean del mismo tamaño, decide qué acción representa (por el nombre del archivo o mirándolo)
y lo empieza a usar. Puedes tener varios personajes: "Jarvis, usa el avatar de Iron Man". Si
clasifica mal un GIF: "Jarvis, ese GIF es de celebrando". Detalles y categorías en
`vaultboy/README.md`. Sin avatares se ve el reactor azul de siempre (`hud.estilo`).

## Qué le puedes decir

| Dices | Qué pasa |
|---|---|
| "Hey Jarvis, prepárate para la exposición" | Modo expositor, conexiones, tu software y la presentación listos; revisa cámara y micrófono: "Todos los sistemas en línea" |
| "…preséntate con el público" | Saluda **al instante** (sin pasar por la IA), mira al público y comenta algo de lo que ve |
| "…pon la presentación" / "inicia la presentación" | PowerPoint en pantalla completa |
| "…siguiente" / "regresa" / "ve a la diapositiva 7" | Instantáneo, sin IA y sin hablar |
| "…muéstrales el sistema" | Cambia a tu software (instantáneo) |
| "…regresa a la presentación" | Vuelve a PowerPoint en pantalla completa (instantáneo) |
| "…explora todos los módulos" / "dales un tour" | Recorrido completo, anunciando y explicando cada módulo |
| "…ve a Inventario" | Da clic en ese módulo con su cursor |
| "…explícales esta pantalla" | Explica señalando cada parte |
| "…señala la gráfica de ventas" | Ilumina ese elemento mientras tú hablas |
| "…en Cliente escribe Ana López" | Llena el campo, letra por letra |
| "…¿qué ves?" / "¿cuánta gente hay?" | Mira por los lentes y responde |
| "…responde la pregunta que me hicieron" | Recupera lo que se oyó antes y la contesta |
| "…¿algo que agregar?" | Aporta algo con lo que sabe del proyecto y lo que ha visto |
| "Hey Jarvis" (mientras habla) | Se calla al instante y te escucha |
| "…modo expositor" / "ya terminé de exponer" | Voz por las bocinas del público / de vuelta a ti |

## Guion sugerido (el momento "wow")

0. **Antes de que entre el público**: "Hey Jarvis, prepárate para la exposición".
1. **Entrada**: tú saludas y dices "Hey Jarvis, preséntate con el público". Jarvis saluda al
   instante, comenta algo real de lo que ve y te cede la palabra.
2. **Contexto**: "Jarvis, pon la presentación". Avanzas con "siguiente" (o el clicker).
3. **El software**: "Jarvis, muéstrales el sistema y dales un tour". El público ve el cursor
   luminoso navegar solo y a Jarvis explicar cada módulo señalando.
4. **Detalle**: "Jarvis, explícales esta pantalla" o "señala…" mientras tú cuentas la historia.
5. **Preguntas**: alguien pregunta; tú dices "Jarvis, ¿escuchaste? Respóndele".
6. **Cierre**: "Jarvis, ¿algo que agregar?" y regresas a la presentación.

Consejos: dirígete a Jarvis por su nombre y dale pie como a un compañero; mira al público
cuando él habla; si se extiende, "Hey Jarvis, gracias" lo corta. **Ensaya el guion completo
tres veces en condiciones parecidas** (ruido, proyector, bocina).

## Velocidad

Cada orden imprime `[Tiempos ...]` y `[Desglose: modelo 0.7s → frase 0.8s → voz lista 1.2s
(elevenlabs) → suena 1.2s]`. Medido en esta laptop: **~1.2 s** desde que terminas de hablar
hasta que Jarvis empieza (con Groq y ElevenLabs de buenas); las diapositivas y cambios de
ventana son instantáneos. `python diagnostico.py latencia` lo mide sin sonar.

Si el desglose dice `(edge)` o `(windows)`, ElevenLabs falló y usó el respaldo (más lento);
si "modelo" pasa de 2 s, Groq está saturado (plan gratis) o la red está mal.

**Con tarjeta NVIDIA** (`pip install -r requirements-gpu.txt`) Whisper corre en la GPU:
`small` transcribe una orden en ~0.25 s (en la CPU ~2.8 s). Entonces la MISMA transcripción
sirve para oír "Jarvis" y como la orden (no se transcribe dos veces ni se va a Groq) y se le
puede cortar diciendo solo **"Jarvis, ya…"** mientras habla (sin GPU, solo con "Hey Jarvis").
La GPU se prueba una vez al arrancar en un proceso aparte: si faltan las DLL de CUDA, sigue en
la CPU (antes ese caso congelaba a Jarvis y no volvía a oír nada). Edge también habla en
streaming: empieza con el primer pedazo de audio. Medido con Edge y GPU: **1.1-2.0 s** desde
que terminas de hablar hasta su primera palabra.

**Videos y música sonando:** Jarvis mide cuánto sonido sale por las bocinas (`pycaw`). Si una
frase coincidió con audio de la computadora, no la toma como seguimiento de la conversación ni
acepta un nombre que solo "se parece" a Jarvis: antes le contestaba al video durante minutos.

## Checklist del día

| Antes de empezar | Listo |
|---|---|
| `python diagnostico.py demo`: todo en [OK] | ☐ |
| Laptop, celular y lentes cargados | ☐ |
| Videollamada conectada, cámara de los lentes activa, micrófono de la laptop silenciado | ☐ |
| `diagnostico.py llamada` y `diagnostico.py vision` responden | ☐ |
| Demo ensayada hoy ("ensaya la demo") y sesión iniciada en el Chrome de Jarvis | ☐ |
| Presentación abierta una vez | ☐ |
| Prueba de voz en la bocina del salón | ☐ |
| Internet estable (o hotspot de respaldo) | ☐ |
| Avisar al público que los lentes tienen cámara | ☐ |

**Plan B en vivo**: el clicker y las flechas siguen funcionando; "Escribir una orden" en el
icono de la bandeja si el micrófono falla; si se cae la llamada, Jarvis pasa solo al
micrófono de la laptop; si se cae internet, sigue con el modelo local y la voz Piper.

## Privacidad y respeto al público

- Jarvis no identifica personas ni comenta rasgos físicos del público (regla fija en
  `vision.py`). A ti sí te puede comentar lo que le preguntes (con tacto, nada sensible).
- Gestos y "¿estás frente a la PC?" se calculan en la laptop: ese video no sale de ella. Solo
  el saludo, los vistazos y "mírame" mandan una foto pequeña al modelo de visión.
- Con la nube: el audio de tus órdenes, el texto y las imágenes van a Groq; lo que Jarvis dice
  va a ElevenLabs (o Microsoft Edge) para generar la voz. Con `"modo": "offline"` y
  `"voz_motor": "windows"` nada sale de la laptop.
- En la laptop: `datos/genesis.db` (historial), `datos/ultima_vista.jpg` (la última foto) y,
  en modo bandeja, `datos/genesis.log`. Lo de los últimos 45 s del micrófono vive solo en
  memoria. Nada de `datos/` se sube a git.

## Pruebas

`.venv\Scripts\python -m unittest discover -s tests -v`: 38 pruebas que no necesitan micrófono
ni internet (atajos, confirmaciones, voz, micrófono de respaldo, respuestas en streaming) más el
recorrido del navegador contra una app de prueba con Chrome oculto.
