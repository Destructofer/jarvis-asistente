# Vault Boy del HUD

El ícono de Jarvis en la esquina de la pantalla es un Vault Boy sin fondo (solo el muñeco). Qué
animación se ve, por prioridad:

1. **La acción de la respuesta**, mientras Jarvis habla y unos segundos más. Jarvis termina cada
   respuesta con `[ACCION: categoria]` (ver `acciones.py`).
2. **`completado.gif` (pulgar arriba)** al terminar una acción que hizo con una herramienta y
   salió bien (abrir una app, buscar, guardar un documento...).
3. **El estado**: `pensando` mientras procesa una orden, `cyborg` al mirar por la cámara,
   `confundido` si hay un error, `espera` mientras te escucha.
4. **`descargando.gif` (el costal)** mientras se baja cualquier archivo a tu carpeta de Descargas
   (navegadores, torrents, gestores de descargas), y el pulgar arriba cuando termina
   (`descargas.py`).
5. **Modo libre**: sin nada que hacer, Jarvis no se queda quieto: alterna la pose inicial y
   rígida (`espera.gif`, de 2.5 a 5 s) con alguna de sus animaciones al azar, nunca dos
   animaciones de corrido. `confundido`, `descargando` y `completado` no salen al azar, para que
   signifiquen algo cuando aparecen. Los GIF que empiezan con `libre_` (como
   `libre_caminando.gif`) solo salen en el modo libre.

Al pasar de una animación a otra distinta, siempre hay un momento (0.6 s) de pose rígida.

Los GIF no vienen en el repositorio: Vault Boy es arte de Fallout (Bethesda) y el repo es
público. Cada quien pone los suyos aquí con estos nombres (si falta alguno, no se muestra nada
para ese caso):

| Archivo | Cuándo sale |
|---|---|
| `espera.gif` | **Predeterminado**: descansando (puede ser una imagen fija) |
| `saludo.gif` | Te saluda, inicia la conversación o está en espera |
| `completado.gif` | Terminó una acción o una descarga |
| `buscando.gif` | Busca información, navega o consulta datos |
| `descargando.gif` | Se está bajando un archivo |
| `pensando.gif` | Analiza, reflexiona o calcula algo complejo |
| `ejecutando.gif` | Realiza una tarea técnica o ejecuta comandos |
| `ciencia.gif` | Análisis técnicos, cálculos científicos o crea algo |
| `cansado.gif` | Termina una tarea larga o te despides |
| `confundido.gif` | No entiende algo o hubo un error |
| `celebrando.gif` | Buena noticia o un éxito |
| `tecnologia.gif` | Usa APIs, automatiza o interactúa con otros sistemas |
| `cyborg.gif` | Interactúa con otras IAs o sistemas externos |

**Variantes:** una categoría puede tener varias animaciones (`ejecutando.gif`,
`ejecutando_2.gif`, `ejecutando_3.gif`...) y cada vez se elige una al azar.

**Mismo tamaño:** `ajustes.json` dice dónde está el muñeco en cada GIF (alto de la coronilla a
los pies, altura de los pies y centro). El HUD escala cada animación para que el muñeco mida
siempre lo mismo y quede parado en la misma línea; los objetos de cada escena se acomodan
alrededor. Si un GIF no está en `ajustes.json`, se calcula solo a partir del dibujo.

**Sin fondo:** el HUD quita solo el fondo al cargar (en segundo plano al arrancar) y deja
únicamente el muñeco. Funciona con el estilo de estos GIF: dibujo claro sobre un fondo liso
oscuro (como el gris #333333 original), de unos 200 px de alto.

Opciones en `config.json → hud`: `estilo` (`"vaultboy"`, o `"reactor"` para el reactor azul de
antes, que también se usa si falta `espera.gif`), `vault_aleatorio` (el modo libre) y
`vigilar_descargas` (el costal). El tamaño lo da `tamano`.
