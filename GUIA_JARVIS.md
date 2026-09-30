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

- Jarvis no identifica personas ni comenta rasgos físicos (regla fija en `vision.py`).
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
