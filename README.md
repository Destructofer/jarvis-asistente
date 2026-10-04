# Jarvis (basado en Genesis)

> Esta copia convierte a Genesis en un integrante más del equipo para exponer: control total
> de la PC, PowerPoint por voz, modo expositor con la cámara de los lentes Ray-Ban Meta (por
> videollamada), manejo del software de la demo en el navegador con su propio cursor, voz en
> streaming, visión y un HUD estilo Iron Man. Todo lo de Genesis (incluido Teams) sigue igual.
> **Empieza por [GUIA_JARVIS.md](GUIA_JARVIS.md)** y corre `python diagnostico.py demo`.
>
> Módulos agregados: `navegador.py` (software de la demo con Playwright), `conocimiento.py` +
> `conocimiento/` (lo que sabe del proyecto), `configuracion.py` (guardado atómico de
> config.json), `tests/` (pruebas sin micrófono ni internet). También: `documentos.py`
> (descomprimir, leer, resumir y guardar documentos), `entorno.py` (escanear el entorno y vigilar
> con la cámara), `realidad.py` (modo realidad aumentada con las manos), `acciones.py` +
> `vaultboy/` (el Vault Boy del HUD según la acción) y `descargas.py` (el HUD muestra las descargas).

# Genesis

Asistente personal de escritorio para Windows, controlado por voz, en español. Detecta la
palabra "Genesis", transcribe con Whisper, decide qué hacer con un modelo de lenguaje (en la
nube por Groq, o local con Ollama si no hay internet) y ejecuta acciones reales sobre el
equipo: abrir programas y archivos, controlar volumen, temporizadores y recordatorios,
reproducir en Spotify/YouTube, y navegar Microsoft Teams.

## Cómo está armado

- **genesis.py** — bucle principal: escucha, decide, ejecuta, responde. Cada orden es un
  `Turno`: la respuesta se dice mientras el modelo la escribe, con relleno si tarda y
  corte inmediato si lo interrumpen ("Hey Jarvis").
- **escuchar.py** — micrófono continuo (`Microfono`, con respaldo automático si el principal se
  queda mudo o se desconecta), palabra de activación (Whisper u openWakeWord), transcripción
  con filtro de alucinaciones y Whisper en la GPU si hay CUDA.
- **voz.py** — `Locucion`: frases generadas en paralelo y reproducidas en orden y por turnos
  (ElevenLabs en streaming → Edge → Piper → Windows), caché de frases y `detener()`.
- **cerebro.py** — el modelo de lenguaje: nube (Groq, en streaming, conexiones persistentes)
  si hay internet y clave configurada, con respaldos gratis en otras nubes (Google Gemini,
  Cerebras: `nubes_extra`); si nada responde, cae a un modelo local con Ollama.
- **gestos.py** / **presencia.py** — la cámara de la laptop: gestos de la mano estilo Iron Man
  (callar, escuchar, sí/no, mouse con la mano, deslizar diapositivas) y saber si estás frente
  a la PC para saludarte y tomar en cuenta cómo te ve. MediaPipe, local y gratis.
- **skills.py** — el registro de "herramientas" que el modelo puede llamar (`@skill(...)`) y
  la infraestructura común (confirmaciones para acciones riesgosas, etc.).
- **memoria.py** — recuerdos permanentes e historial, en SQLite (`datos/genesis.db`).
- **recordatorios.py** — temporizadores, recordatorios y control de música.
- **apps.py** / **archivos.py** — abrir aplicaciones instaladas y buscar/abrir archivos, con
  tolerancia a nombres mal pronunciados.
- **multimedia.py** — YouTube y Spotify (búsqueda + reproducción real vía Spotify Connect).
- **teams.py** — navega Microsoft Teams leyendo su árbol de accesibilidad (no hay una API
  para "hacer clic aquí"); enviar mensajes siempre pide confirmación con el texto exacto.
- **graph.py** — lee de verdad el contenido de archivos Word/PDF de las clases (Microsoft
  Graph API): busca en todos los canales de una clase, incluidos los subcanales donde un
  profesor separa guías y materiales, y junta todo lo relacionado con una tarea para poder
  explicar qué pide. Nunca resuelve ni entrega la tarea — ver la nota de `config.json` y el
  docstring del módulo.
- **apps.py** también cierra apps/ventanas (`cerrar_app`) y **mantenimiento.py** vigila el
  equipo (caché, RAM, disco) y ofrece arreglarlo en un panel emergente (`panel.py`).
- **iniciar_genesis.pyw** / **autoinicio.py** — arranque sin consola con icono en la bandeja,
  y arranque automático con Windows.

Cada skill nueva se registra con `@skill(...)` en el archivo que le corresponda y se activa
en `config.json → skills`.

## Requisitos

- Windows 10/11 (usa `winsound`, `pywinauto`, `ctypes` — no es multiplataforma).
- Python 3.11+.
- [Ollama](https://ollama.com) si se quiere modo local/offline (`ollama pull qwen2.5:7b`).

## Instalación

```powershell
git clone <url-del-repo>
cd Genesis
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
```

Edita `config.json` a tu gusto (tu nombre, tus apps instaladas en `apps`, etc.).

### Claves necesarias (variables de entorno de Windows)

Ninguna clave va en `config.json`; todo se lee de variables de entorno con `setx`:

```powershell
setx GROQ_API_KEY "tu_clave"                  # modelo en la nube (groq.com)
setx SPOTIFY_CLIENT_ID "tu_id"                # developer.spotify.com/dashboard
setx SPOTIFY_CLIENT_SECRET "tu_secreto"
setx ELEVENLABS_API_KEY "tu_clave"            # opcional, voz más natural
setx GRAPH_CLIENT_ID "tu_id"                  # opcional, para leer archivos de Teams
setx GRAPH_CLIENT_SECRET "tu_secreto"
```

Para Spotify, el Redirect URI de tu app en el dashboard debe ser exactamente
`http://127.0.0.1:8888/callback`. Sin `GROQ_API_KEY`, Genesis funciona igual pero solo en
modo local (Ollama).

Para leer archivos de Teams (`graph.py`), registra una app en
[portal.azure.com](https://portal.azure.com) → Microsoft Entra ID → Registros de aplicaciones
→ Nuevo registro, plataforma "Web", con el Redirect URI exacto `http://127.0.0.1:8890/callback`,
y crea un secreto en "Certificados y secretos". Esto usa la cuenta de la escuela, y algunos
planteles bloquean el consentimiento de apps propias para estudiantes — si al decir "conecta
mis archivos de Teams" la autorización falla o pide aprobación de un administrador, esa es la
causa; Teams sigue funcionando igual por navegación (`teams.py`), solo sin leer contenido de
archivos.

Cierra y vuelve a abrir la terminal después de `setx` para que tome las variables nuevas.

### Voz (opcional)

`voces/` no viene en el repositorio (ver `voces/README.md`): sin esos archivos, Genesis usa
automáticamente la voz de Windows como respaldo. No es necesario para que todo lo demás
funcione.

## Ejecutar

```powershell
.venv\Scripts\python genesis.py          # con consola, para ver los logs mientras se prueba
.venv\Scripts\pythonw iniciar_genesis.pyw # sin consola, con icono en la bandeja
python autoinicio.py instalar             # arranca con Windows + vigilante que lo relanza si se cae
```

El vigilante es una tarea programada (`JarvisVigilante`) que cada 5 minutos intenta lanzar
Jarvis; si ya está corriendo no hace nada, y si lo cerraste con "Salir" en la bandeja lo
respeta hasta que lo abras a mano. `python autoinicio.py quitar` quita las dos cosas. (Si lo
instalaste antes de esta versión, corre `python autoinicio.py instalar` otra vez.)

Pruebas: `.venv\Scripts\python -m unittest discover -s tests -v`.

En la bandeja, **"Escribir una orden"** (o doble clic en el icono) abre la ventana para
escribir sin decir "Genesis" — útil si el micrófono no está oyendo o prefieres no hablar.

Con consola: si `palabra_activacion` es `true` en `config.json`, di "Genesis" y espera el
pitido; si es `false`, se puede escribir por teclado.

## Seguridad

Las acciones que pueden afectar algo importante (apagar el equipo, Wi-Fi, borrar memoria,
vaciar la papelera, cambiar el micrófono, forzar el cierre de una app, enviar un mensaje en
Teams, pulsar botones como "Salir" o "Entregar" en Teams) siempre piden confirmación antes de
ejecutarse — por voz o con los botones Sí/No de la ventana, lo que llegue primero. Revisa
`skills.py` (`riesgo="confirmar"`) y el patrón de `pedir_confirmacion` en `memoria.py` y
`teams.py` antes de añadir una skill nueva que actúe sobre algo de otra persona.

Texto de terceros: lo que Genesis lee de Teams (mensajes, documentos) lo escribieron otras
personas y podría traer instrucciones escondidas. Las skills que traen ese texto llevan
`externo=True` y su resultado se marca como "solo datos"; mientras ese contenido siga en la
conversación, cualquier skill `sensible=True` (cerrar apps, abrir webs) pide confirmación.
Al añadir una skill nueva, márcala con esas banderas si corresponde.

Procesos del sistema (`explorer`, `dwm`, el propio Genesis...) nunca se cierran: ver
`BLOQUEADAS` y `bloqueado()` en `apps.py`.

## Privacidad

- Los tokens de Spotify y Microsoft se guardan cifrados con DPAPI de Windows
  (`secreto.py`): solo tu usuario de Windows en ese equipo puede leerlos.
- Con el modo en la nube, lo que le dices a Genesis — el audio de tus órdenes (transcripción),
  el texto, las imágenes de la cámara y el contenido de documentos y mensajes de Teams que le
  pidas leer — se envía a Groq para procesarlo, y lo que responde se envía a ElevenLabs (o a
  Microsoft Edge) para generar la voz. Si no quieres que algo salga del equipo, usa
  `"modo": "offline"`, `"stt": {"modo": "local"}` y `"voz_motor": "windows"` (o Piper).
- Los gestos y la detección de si estás frente a la PC se calculan en el equipo (MediaPipe);
  solo el saludo, los vistazos y "mírame" envían una foto pequeña al modelo de visión.
- Los últimos ~45 s de audio del micrófono se guardan solo en memoria (para "responde la
  pregunta que me hicieron"); nunca se escriben a disco.
- La conversación se guarda en `datos/genesis.db` (últimos 500 mensajes) y en
  `datos/genesis.log`. Ninguno de los dos se sube a git.

## Pendiente / por dónde seguir

- `teams_enviar_mensaje` busca el cuadro de texto por posición y solo escribe si comprueba
  que el cursor quedó dentro; no se ha probado enviando a un chat real. Si dice que no logró
  poner el cursor, revisar `_campo_mensaje` y `_foco_en` en `teams.py`.
- `graph.py` (lectura de archivos de Teams) no se pudo probar de punta a punta en desarrollo:
  el flujo OAuth necesita que una persona complete el login en el navegador, y no cubre
  PowerPoint (solo `.docx`, `.pdf`, `.txt`, `.md`). Si falla, revisar primero si el tenant de
  la escuela bloqueó el consentimiento (ver sección de instalación).
- Integración con WhatsApp: no empezada.
- Límite importante, a propósito: Genesis analiza y explica tareas, pero nunca las resuelve
  ni las entrega por el usuario (ver `config.json → personality`). No cambiar ese
  comportamiento sin pensarlo dos veces — es una línea de honestidad académica, no un
  descuido.
