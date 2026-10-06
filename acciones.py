"""Clasifica cada respuesta de Jarvis en una "acción" estilo Vault Boy de Fallout.

Cada respuesta termina con [ACCION: categoria]. La pone el modelo (REGLA va en el prompt) y,
cuando la respuesta no pasó por el modelo (atajos, o el camino rápido en que el resultado de una
herramienta se dice tal cual), se deduce de la herramienta que se usó. La etiqueta queda en el
registro y el HUD muestra la animación de vaultboy/<categoria>.gif; NUNCA se dice en voz alta.
"""
import re
import unicodedata

CATEGORIAS = {
    "saludo": "el usuario te saluda, inicias la conversación o estás en espera",
    "buscando": "buscas información, navegas o consultas datos",
    "pensando": "analizas, reflexionas o calculas algo complejo",
    "ejecutando": "realizas una tarea técnica, programas o ejecutas comandos",
    "ciencia": "haces análisis técnicos, cálculos científicos o creas algo",
    "cansado": "terminas una tarea larga o el usuario se despide",
    "confundido": "no entiendes algo o hubo un error",
    "celebrando": "el usuario te da una buena noticia o logras un éxito",
    "tecnologia": "usas APIs, automatizas o interactúas con otros sistemas",
    "cyborg": "interactúas con otras IAs o sistemas externos",
}

REGLA = ("\n\nAl final de CADA respuesta añade exactamente [ACCION: categoria], con UNA sola de "
         "estas categorías según lo que hiciste: "
         + "; ".join(f"{c} ({d})" for c, d in CATEGORIAS.items()) + ".")

# Si la respuesta no trae etiqueta: la herramienta que se usó decide la categoría
POR_HERRAMIENTA = {
    "buscando": {"buscar_web", "buscar_archivo", "hora_fecha", "abrir_web", "consultar_memoria", "listar_avisos",
                 "listar_rutinas", "listar_microfonos", "teams_leer_pantalla", "teams_listar_clases",
                 "teams_buscar_archivos_clase", "leer_ventana", "youtube", "desplazar",
                 "teams_desplazar", "explicar_pantalla", "listar_en_pantalla", "mis_habitos",
                 "listar_personalidades",
                 "que_suena"},
    "pensando": {"analizar_documento", "teams_analizar_tarea", "explicar_diapositiva",
                 "recorrer_y_explicar", "pregunta_del_publico"},
    "ciencia": {"escanear_entorno", "crear_documento", "info_sistema", "revisar_equipo",
                "captura_pantalla"},
    "ejecutando": {"abrir_app", "cerrar_app", "enfocar_ventana", "presionar_teclas", "escribir_texto",
                   "clic_en", "cerrar_ventana_activa", "volumen", "rutina", "abrir_archivo",
                   "abrir_carpeta", "descomprimir", "abrir_presentacion", "presentacion",
                   "limpiar_temporales", "vaciar_papelera", "apagar_equipo", "reiniciar_equipo",
                   "cancelar_apagado", "bloquear_pantalla", "wifi", "cambiar_microfono",
                   "temporizador", "recordatorio", "cancelar_avisos", "recordar", "olvidar",
                   "olvidar_todo", "abrir_sistema", "ir_a_modulo", "volver_atras", "llenar_campo",
                   "iniciar_sesion_demo", "resaltar", "recorrer_modulos", "ensayar_demo",
                   "mostrar_presentacion", "preparar_exposicion", "elegir_en_pantalla",
                   "controlar_reproduccion", "escribir_en", "saltar_anuncios",
                   "cambiar_personalidad", "sugerencias_habitos", "olvidar_habitos",
                   "sugerencia_rechazada"},
    "tecnologia": {"spotify", "musica", "conectar_spotify", "teams_abrir", "teams_click",
                   "teams_enviar_mensaje", "conectar_archivos_teams", "modo_realidad",
                   "modo_expositor", "gestos"},
    "cyborg": {"mirar", "vigilar_camara", "presentarse_al_publico", "hablar_al_publico",
               "mirar_usuario", "vista_usuario", "observar_publico"},
}
_HERRAMIENTA_A_CATEGORIA = {h: c for c, hs in POR_HERRAMIENTA.items() for h in hs}

_ETIQUETA = re.compile(r"\[\s*acci[oó]n\s*:\s*([^\]]{1,30})\]", re.I)
_ERROR = re.compile(r"\b(no pude|no logr[eé]|error|no encontr[eé]|no entend[ií]|no te escuch[eé]|"
                    r"no conozco|no existe|fall[oó])\b", re.I)


def _norm(texto):
    texto = unicodedata.normalize("NFD", texto.lower().strip())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def separar(texto):
    """'Listo, abrí Spotify. [ACCION: ejecutando]' -> ('Listo, abrí Spotify.', 'ejecutando').
    Quita TODAS las etiquetas (a veces el modelo pone dos o la pone a la mitad). La categoría es
    None si no venía ninguna válida."""
    categoria = None
    for m in _ETIQUETA.finditer(texto or ""):
        c = _norm(m.group(1))
        if c in CATEGORIAS:
            categoria = c
    limpio = _ETIQUETA.sub("", texto or "")
    return re.sub(r"\s{2,}", " ", limpio).strip(), categoria


def deducir(herramientas, texto=""):
    """Categoría para una respuesta sin etiqueta: por la última herramienta conocida que se
    usó; si no hubo, 'confundido' si suena a error y 'saludo' (conversación) si no."""
    for nombre in reversed(list(herramientas or [])):
        if nombre in _HERRAMIENTA_A_CATEGORIA:
            categoria = _HERRAMIENTA_A_CATEGORIA[nombre]
            return "confundido" if _ERROR.search(texto or "") else categoria
    return "confundido" if _ERROR.search(texto or "") else "saludo"


def clasificar(texto, herramientas=None):
    """(texto limpio, categoría) para cualquier respuesta."""
    limpio, categoria = separar(texto)
    return limpio, categoria or deducir(herramientas, limpio)


def es_inicio_etiqueta(texto):
    """True si el texto es el comienzo de una etiqueta cortada ("[ACC", "[Acción: ejec")."""
    t = _norm(texto).replace(" ", "")
    return "[accion:".startswith(t[:8]) or t.startswith("[accion")
