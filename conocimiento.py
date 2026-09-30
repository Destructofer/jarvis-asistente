"""Lo que Jarvis sabe del proyecto que se expone y del equipo.

Para que responda preguntas del público y del jurado como un integrante más, necesita conocer
el proyecto como ustedes: qué problema resuelve, cómo funciona, quién hizo qué, qué sigue.
Eso se escribe en conocimiento/*.md (texto normal; ver conocimiento/proyecto.md) y el equipo
en config.json → equipo. Entra al prompt solo en modo expositor o con la demo activa, para
no gastar tokens cuando Jarvis se usa para otras cosas.
"""
import re
from pathlib import Path

CARPETA = Path(__file__).parent / "conocimiento"
_cache = {"clave": None, "texto": ""}


def _archivos():
    if not CARPETA.is_dir():
        return []
    return [a for a in sorted(CARPETA.glob("*.md"))
            if not a.name.lower().startswith(("readme", "_"))]


def _limpiar(contenido):
    """Quita los comentarios de la plantilla (<!-- ... -->) y las líneas vacías de sobra."""
    contenido = re.sub(r"<!--.*?-->", "", contenido, flags=re.S)
    lineas = [ln.rstrip() for ln in contenido.splitlines()]
    texto = "\n".join(ln for ln in lineas if ln.strip())
    # una sección que solo tiene su título no aporta nada
    return texto if re.sub(r"^#+.*$", "", texto, flags=re.M).strip() else ""


def _equipo(cfg):
    eq = cfg.get("equipo") or {}
    if not eq:
        return ""
    partes = []
    if eq.get("nombre"):
        partes.append(f"Equipo: {eq['nombre']}.")
    if eq.get("proyecto"):
        partes.append(f"Proyecto: {eq['proyecto']}.")
    integrantes = [f"{i.get('nombre', '')}" + (f" ({i['rol']})" if i.get("rol") else "")
                   for i in eq.get("integrantes", []) if i.get("nombre")]
    if integrantes:
        partes.append("Integrantes: " + ", ".join(integrantes) + ".")
    return " ".join(partes)


def texto(cfg, max_chars=None):
    max_chars = int(max_chars or cfg.get("conocimiento_max_chars", 6000))
    archivos = _archivos()
    clave = (tuple((a.name, a.stat().st_mtime) for a in archivos), _equipo(cfg), max_chars)
    if clave == _cache["clave"]:
        return _cache["texto"]
    partes = [p for p in [_equipo(cfg)] + [_limpiar(a.read_text(encoding="utf-8")) for a in archivos] if p]
    cuerpo = "\n\n".join(partes)
    if len(cuerpo) > max_chars:
        cuerpo = cuerpo[:max_chars] + " [...]"
    resultado = ("" if not cuerpo else
                 "\n\nCONOCIMIENTO DEL PROYECTO QUE SE EXPONE (formas parte de este equipo: habla "
                 "del proyecto en primera persona del plural, 'nosotros'; úsalo para responder "
                 "preguntas del público y del jurado; si algo no está aquí, dilo con honestidad "
                 "y ofrece que el expositor lo amplíe):\n" + cuerpo)
    _cache.update(clave=clave, texto=resultado)
    return resultado


def hay_conocimiento(cfg):
    return bool(texto(cfg))
