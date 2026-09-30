"""Buscar y abrir archivos y carpetas del equipo por nombre aproximado."""
import os
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

import psutil

from apps import fonetica
from skills import _norm, skill

CARPETAS_USUARIO = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
IGNORAR = {"node_modules", "__pycache__", "venv", "site-packages", "AppData", "Windows",
           "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin",
           "System Volume Information", "Recovery"}
# Nunca se abren desde una búsqueda: un nombre mal oído no debe ejecutar programas
PELIGROSAS = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".com",
              ".lnk", ".reg", ".jar", ".dll", ".hta", ".wsf"}
TIPOS = {
    "documento": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".odt", ".csv"},
    "imagen": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic"},
    "video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"},
    "musica": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"},
}
STOP = {"el", "la", "los", "las", "de", "del", "un", "una", "archivo", "documento", "carpeta",
        "mi", "mis", "que", "se", "llama", "llamado", "en", "con", "y"}
VIGENCIA = 600  # segundos que se reutiliza el índice antes de volver a recorrer el disco

_indices = {}   # ámbito -> (momento, [entradas])
_ultimos = []   # resultados de la última búsqueda, para "abre el segundo"
_lock = threading.Lock()


def _raices(ambito):
    home = Path.home()
    bases = [home]
    if os.environ.get("OneDrive"):
        bases.append(Path(os.environ["OneDrive"]))
    if ambito == "usuario":
        return [b / c for b in bases for c in CARPETAS_USUARIO if (b / c).exists()]
    raices = [home]
    for p in psutil.disk_partitions(all=False):
        if "fixed" in p.opts and Path(p.mountpoint) != Path(home.anchor):
            raices.append(Path(p.mountpoint))
    raices.append(Path(home.anchor))
    return raices


def _recorrer(raices, limite_seg):
    fin = time.time() + limite_seg
    vistos, entradas = set(), []
    pila = [(str(r), 0) for r in raices]
    while pila and time.time() < fin:
        ruta, prof = pila.pop()
        try:
            with os.scandir(ruta) as it:
                for e in it:
                    if e.name.startswith(".") or e.name in IGNORAR or e.path in vistos:
                        continue
                    vistos.add(e.path)
                    try:
                        es_dir = e.is_dir(follow_symlinks=False)
                        mt = e.stat().st_mtime
                    except OSError:
                        continue
                    stem = e.name if es_dir else os.path.splitext(e.name)[0]
                    entradas.append((e.name, e.path, es_dir, mt, fonetica(stem)))
                    if es_dir and prof < 8:
                        pila.append((e.path, prof + 1))
        except OSError:
            continue
    return entradas


def _indice(ambito, forzar=False):
    with _lock:
        t, ent = _indices.get(ambito, (0, []))
        if forzar or not ent or time.time() - t > VIGENCIA:
            ent = _recorrer(_raices(ambito), 25 if ambito == "equipo" else 15)
            _indices[ambito] = (time.time(), ent)
        return ent


def iniciar():
    """Indexa tus carpetas en segundo plano para que la primera búsqueda sea instantánea."""
    threading.Thread(target=_indice, args=("usuario",), daemon=True, name="indice-archivos").start()


# ---------- Búsqueda ----------
def _tokens(consulta):
    return [fonetica(t) for t in _norm(consulta).split() if t not in STOP]


def _buscar(consulta, tipo, ambito):
    tokens = [t for t in _tokens(consulta) if t]
    if not tokens:
        return []
    junto = "".join(tokens)
    if tipo not in TIPOS and tipo != "carpeta":
        tipo = "cualquiera"
    exts = TIPOS.get(tipo)
    entradas = [e for e in _indice(ambito)
                if tipo == "cualquiera"
                or (tipo == "carpeta" and e[2])
                or (exts and not e[2] and os.path.splitext(e[0])[1].lower() in exts)]

    def puntuar(exacto):
        res = []
        for nombre, ruta, es_dir, mt, fon in entradas:
            ns = fon.replace(" ", "")
            if not ns:
                continue
            if all(t in ns for t in tokens):
                res.append((0.6 + 0.4 * len(junto) / len(ns), mt, nombre, ruta, es_dir))
            elif not exacto and len(junto) >= 4:
                sm = SequenceMatcher(None, junto, ns)
                if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6:
                    r = sm.ratio()
                    if r >= 0.62:  # tolera bastante lo que Whisper puede desfigurar
                        res.append((r * 0.9, mt, nombre, ruta, es_dir))
        return res

    res = puntuar(True) or puntuar(False)
    res.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return res


def _describir(r):
    _, _, nombre, ruta, es_dir = r
    return f"{nombre}{' (carpeta)' if es_dir else ''} en {Path(ruta).parent.name or Path(ruta).anchor}"


def _buscar_con_reintento(consulta, tipo, ambito):
    res = _buscar(consulta, tipo, ambito)
    if not res:  # quizá es un archivo recién creado: se vuelve a recorrer el disco una vez
        _indice(ambito, forzar=True)
        res = _buscar(consulta, tipo, ambito)
    return res


# ---------- Skills ----------
@skill("buscar_archivo",
       "Busca archivos o carpetas en el equipo por nombre aproximado (informes, fotos, canciones, "
       "documentos). Devuelve las mejores coincidencias numeradas. Por defecto busca en Escritorio, "
       "Documentos, Descargas, Imágenes, Música y Vídeos.",
       {"consulta": {"type": "string", "description": "Palabras del nombre del archivo"},
        "tipo": {"type": "string",
                 "enum": ["cualquiera", "documento", "imagen", "video", "musica", "carpeta"],
                 "description": "Tipo de elemento (opcional)"},
        "todo_el_equipo": {"type": "boolean",
                           "description": "true para buscar en todos los discos (más lento)"}},
       requeridos=["consulta"], terminal=False)
def buscar_archivo(consulta, tipo="cualquiera", todo_el_equipo=False):
    global _ultimos
    res = _buscar_con_reintento(consulta, tipo, "equipo" if todo_el_equipo else "usuario")
    if not res:
        extra = "" if todo_el_equipo else " Puedo buscar en todo el equipo si quieres."
        return f"No encontré nada parecido a '{consulta}'.{extra}"
    _ultimos = res[:5]
    lista = " | ".join(f"{i}) {_describir(r)}" for i, r in enumerate(_ultimos, 1))
    return f"Encontré {len(res)}. Mejores: {lista}"


@skill("abrir_archivo",
       "Abre un archivo o carpeta del equipo. Con 'consulta' lo busca por nombre aproximado y abre la "
       "mejor coincidencia; con 'numero' abre el resultado de la última búsqueda (1 = el primero). "
       "No abre programas ejecutables.",
       {"consulta": {"type": "string", "description": "Nombre aproximado del archivo"},
        "numero": {"type": "integer", "description": "Número del resultado de la última búsqueda"},
        "tipo": {"type": "string",
                 "enum": ["cualquiera", "documento", "imagen", "video", "musica", "carpeta"],
                 "description": "Tipo de elemento (opcional)"}},
       requeridos=[])
def abrir_archivo(consulta="", numero=0, tipo="cualquiera"):
    global _ultimos
    numero = int(numero or 0)
    if numero:
        if not 1 <= numero <= len(_ultimos):
            return "No tengo ese resultado; haz primero una búsqueda."
        elegido, otros = _ultimos[numero - 1], 0
    else:
        if not consulta.strip():
            return "Dime qué archivo quieres abrir."
        res = _buscar_con_reintento(consulta, tipo, "usuario")
        if not res:
            return f"No encontré nada parecido a '{consulta}'. Puedo buscar en todo el equipo."
        _ultimos, elegido, otros = res[:5], res[0], len(res) - 1

    _, _, nombre, ruta, es_dir = elegido
    if not es_dir and os.path.splitext(nombre)[1].lower() in PELIGROSAS:
        return f"'{nombre}' es un ejecutable o acceso directo; no lo abro desde una búsqueda."
    os.startfile(ruta)
    aviso = f" Había {otros} coincidencias más; abrí la mejor." if otros else ""
    return f"Abriendo {_describir(elegido)}.{aviso}"


if __name__ == "__main__":
    t = time.time()
    n = len(_indice("usuario"))
    print(f"{n} elementos indexados en {time.time() - t:.1f}s")
    for consulta in ("informe", "foto", "descargas"):
        print(consulta, "->", buscar_archivo(consulta)[:300])
