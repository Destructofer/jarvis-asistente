"""¿Estás cansado? Por la cámara, en la computadora (sin mandar fotos a ningún lado).

MediaPipe FaceLandmarker da, en cada cuadro, qué tan cerrados están tus ojos y qué tan abierta tu
boca (~3 ms por imagen). Con eso se mide como en los sistemas antisomnolencia de los autos:
- PERCLOS: el porcentaje del tiempo con los ojos cerrados en el último minuto. Despierto es ~5 %;
  arriba de 15 % es señal clara de cansancio.
- Bostezos: la boca muy abierta por más de 1.2 s. Dos o más en 5 minutos.

Solo corre de noche (ciclo.py lo enciende) y se pausa durante la realidad aumentada.
"""
import threading
import time
from collections import deque
from pathlib import Path

import camara
import skills

BASE = Path(__file__).parent
MODELO = BASE / "datos" / "face_landmarker.task"
URL_MODELO = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
              "float16/latest/face_landmarker.task")
OJOS_CERRADOS = 0.55
BOCA_BOSTEZO = 0.55
BOSTEZO_SEG = 1.2


class Cansancio:
    """El análisis, sin cámara: se alimenta con (momento, parpadeo izq, der, apertura de boca)."""

    def __init__(self):
        self.muestras = deque(maxlen=600)     # (t, ojos_cerrados)
        self.bostezos = deque(maxlen=20)      # momentos
        self._boca_desde = None

    def agregar(self, t, ojo_izq, ojo_der, boca):
        self.muestras.append((t, (ojo_izq + ojo_der) / 2 > OJOS_CERRADOS))
        if boca > BOCA_BOSTEZO:
            self._boca_desde = self._boca_desde or t
        else:
            if self._boca_desde is not None and t - self._boca_desde >= BOSTEZO_SEG:
                self.bostezos.append(t)
            self._boca_desde = None

    def estado(self, ahora):
        minuto = [c for t, c in self.muestras if ahora - t <= 60]
        perclos = sum(minuto) / len(minuto) if len(minuto) >= 40 else 0.0
        bostezos = sum(1 for t in self.bostezos if ahora - t <= 300)
        cansado = perclos >= 0.15 or bostezos >= 2
        motivo = ("los ojos se te cierran seguido" if perclos >= 0.15 else
                  "ya bostezaste varias veces" if bostezos >= 2 else "")
        return {"perclos": perclos, "bostezos": bostezos, "cansado": cansado, "motivo": motivo,
                "muestras": len(minuto)}


_estado = {"hilo": None, "activo": False, "analisis": Cansancio(), "visto": 0.0}


def _detector():
    import mediapipe as mp  # noqa: F401
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision as mpv
    if not MODELO.exists() or MODELO.stat().st_size < 1_000_000:
        import urllib.request
        MODELO.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL_MODELO, MODELO)
    opciones = mpv.FaceLandmarkerOptions(base_options=mpp.BaseOptions(model_asset_path=str(MODELO)),
                                         output_face_blendshapes=True, num_faces=1,
                                         running_mode=mpv.RunningMode.IMAGE)
    return mpv.FaceLandmarker.create_from_options(opciones)


def _bucle():
    import cv2
    import mediapipe as mp
    try:
        detector = _detector()
    except Exception as e:
        print(f"[Cansancio: no pude cargar el modelo de rostro ({type(e).__name__})]")
        _estado["hilo"] = None
        return
    while True:
        if not _estado["activo"] or camara.EXTERNO is not None:
            time.sleep(2)
            continue
        try:
            cuadro, _ts = camara.cuadro_usuario(skills._CFG or {})
        except Exception:
            time.sleep(10)
            continue
        if cuadro is None:
            time.sleep(0.5)
            continue
        try:
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(cv2.resize(cuadro, (320, 240)), cv2.COLOR_BGR2RGB))
            r = detector.detect(img)
            if r.face_blendshapes:
                b = {c.category_name: c.score for c in r.face_blendshapes[0]}
                ahora = time.time()
                _estado["analisis"].agregar(ahora, b.get("eyeBlinkLeft", 0), b.get("eyeBlinkRight", 0),
                                            b.get("jawOpen", 0))
                _estado["visto"] = ahora
        except Exception:
            pass
        time.sleep(0.25)


def encender(activo=True):
    """ciclo.py lo enciende de noche y lo apaga de día (para no gastar la cámara)."""
    _estado["activo"] = bool(activo)
    if activo and _estado["hilo"] is None:
        _estado["hilo"] = threading.Thread(target=_bucle, daemon=True, name="cansancio")
        _estado["hilo"].start()


def estado():
    """{perclos, bostezos, cansado, motivo} del último minuto (cansado=False si no te ve)."""
    ahora = time.time()
    if ahora - _estado["visto"] > 30:
        return {"perclos": 0.0, "bostezos": 0, "cansado": False, "motivo": "", "muestras": 0}
    return _estado["analisis"].estado(ahora)
