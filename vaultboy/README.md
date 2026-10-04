# Avatares de Jarvis

El ícono animado de la esquina es un **avatar**: un personaje con una animación para cada cosa
que hace Jarvis. Los avatares viven en tu PC, fuera del repositorio:

```
Documentos\Jarvis\Avatares\
    Vault Boy\        <- un personaje: sus GIF, con el nombre que quieras
    Iron Man\         <- otro
```

**Para agregar una animación, solo suelta el GIF en la carpeta del personaje** (también con
Jarvis encendido). En unos segundos `avatares.py` le quita el fondo (cualquier color liso; si ya
es transparente, lo respeta), mide al personaje para que todos se vean del mismo tamaño y
parados en la misma línea, decide qué acción representa y lo empieza a usar. Al borrar un GIF,
deja de usarlo.

**Qué acción representa:** si el nombre empieza con una categoría (`celebrando_baile.gif`,
`ejecutando_3.gif`) se usa esa; si no, Jarvis mira la animación y la clasifica. Si se equivoca:
"Jarvis, ese GIF es de celebrando" o renombra el archivo.

| Categoría | Cuándo sale |
|---|---|
| `espera` | **La pose quieta** entre animaciones (si no hay, usa el primer cuadro de otra) |
| `saludo` | Te saluda o inicia la conversación |
| `completado` | Terminó una acción o una descarga |
| `buscando` · `pensando` · `ejecutando` · `ciencia` · `tecnologia` · `cyborg` | Según lo que hace (ver `acciones.py`) |
| `cansado` | Termina algo largo o te despides |
| `confundido` | No entendió o hubo un error |
| `celebrando` | Una buena noticia o un éxito |
| `descargando` | Se está bajando un archivo |
| `libre` | Solo para el modo libre (animaciones al azar cuando no hace nada) |

Varias animaciones de la misma categoría se turnan al azar. **Sin nada que hacer**, Jarvis
alterna la pose quieta (de 4 a 7 s) con una animación, como una baraja: salen TODAS una vez
antes de repetir alguna, nunca la misma dos veces seguidas, incluidos los GIF de categorías que
casi nunca se usan para una acción. Solo se reservan los que nombres `confundido…`,
`descargando…` o `completado…`: salen únicamente cuando pasa eso, para que signifiquen algo.

**Varios personajes:** "Jarvis, usa el avatar de Iron Man" (lo recuerda como preferencia),
"¿qué avatares tienes?". Lo que prepara Jarvis queda en `<personaje>\.jarvis\` (las versiones
sin fondo y `avatar.json` con la categoría y las medidas de cada GIF; ahí se pueden corregir a
mano las medidas en `figura`).

Esta carpeta del repositorio (`vaultboy/`) solo se usa la primera vez: si tiene GIF de Vault Boy
(con las medidas de `ajustes.json`), Jarvis los pasa a `Documentos\Jarvis\Avatares\Vault Boy\`.
Los GIF no vienen en el repositorio porque Vault Boy es arte de Fallout (Bethesda) y el repo es
público. Sin ningún avatar se ve el reactor azul de siempre (`config.json → hud.estilo`).
