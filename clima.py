"""El clima de donde estás: "¿cómo está el clima?", "¿va a llover mañana?" y el del resumen de la
mañana (ciclo.py).

Ubicación: la de Windows (GPS o, en una laptop, por las redes Wi-Fi cercanas: ~100 m), y si no
se puede, por la IP (menos precisa: la ciudad). Se guarda 30 min para no preguntarla en cada
consulta. Clima: Open-Meteo (gratis, sin cuenta ni clave), actual y pronóstico de 3 días.
"""
import asyncio
import json
import threading
import time
import urllib.request

from skills import Fallo, skill

UA = {"User-Agent": "Jarvis-asistente"}
_cache = {"lugar": None, "t": 0.0}

# Códigos WMO de Open-Meteo -> cómo se dice
CODIGOS = {
    0: "despejado", 1: "casi despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "con niebla", 48: "con niebla helada", 51: "con llovizna ligera", 53: "con llovizna",
    55: "con llovizna intensa", 56: "con llovizna helada", 57: "con llovizna helada",
    61: "con lluvia ligera", 63: "con lluvia", 65: "con lluvia fuerte", 66: "con lluvia helada",
    67: "con lluvia helada", 71: "con nevada ligera", 73: "con nevada", 75: "con nevada fuerte",
    77: "con granizo fino", 80: "con chubascos ligeros", 81: "con chubascos",
    82: "con chubascos fuertes", 85: "con chubascos de nieve", 86: "con nevadas fuertes",
    95: "con tormenta eléctrica", 96: "con tormenta y granizo", 99: "con tormenta y granizo",
}


def _json(url, timeout=8):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())


def _ubicacion_windows(limite=15):
    """(lat, lon, precisión en m) de Windows, o None (sin permiso, apagada o tardó mucho)."""
    res = {}

    def hilo():
        async def leer():
            from winrt.windows.devices.geolocation import GeolocationAccessStatus, Geolocator
            if await Geolocator.request_access_async() != GeolocationAccessStatus.ALLOWED:
                return None
            pos = await Geolocator().get_geoposition_async()
            c = pos.coordinate
            return c.point.position.latitude, c.point.position.longitude, c.accuracy
        try:
            res["v"] = asyncio.run(asyncio.wait_for(leer(), limite))
        except Exception as e:
            res["e"] = e
    t = threading.Thread(target=hilo, daemon=True, name="ubicacion")
    t.start()
    t.join(limite + 2)
    return res.get("v")


def _nombre_lugar(lat, lon):
    try:
        d = _json("https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=%s&longitude=%s"
                  "&localityLanguage=es" % (lat, lon), timeout=6)
        return d.get("city") or d.get("locality") or d.get("principalSubdivision") or ""
    except Exception:
        return ""


def ubicacion():
    """{lat, lon, lugar, fuente}. Windows primero (preciso), IP de respaldo."""
    if _cache["lugar"] and time.time() - _cache["t"] < 1800:
        return _cache["lugar"]
    lugar = None
    w = _ubicacion_windows()
    if w:
        lat, lon, _prec = w
        lugar = {"lat": lat, "lon": lon, "lugar": _nombre_lugar(lat, lon), "fuente": "ubicación de Windows"}
    else:
        for url, k_lat, k_lon, k_ciudad in (("http://ip-api.com/json/?lang=es", "lat", "lon", "city"),
                                            ("https://ipwho.is/", "latitude", "longitude", "city")):
            try:
                d = _json(url, timeout=6)
                if d.get(k_lat) is not None:
                    lugar = {"lat": d[k_lat], "lon": d[k_lon], "lugar": d.get(k_ciudad) or "",
                             "fuente": "tu IP (aproximada)"}
                    break
            except Exception:
                continue
    if lugar:
        _cache.update(lugar=lugar, t=time.time())
    return lugar


def _buscar_ciudad(nombre):
    d = _json("https://geocoding-api.open-meteo.com/v1/search?count=1&language=es&name="
              + urllib.request.quote(nombre))
    r = (d.get("results") or [None])[0]
    if not r:
        return None
    return {"lat": r["latitude"], "lon": r["longitude"], "lugar": r.get("name", nombre), "fuente": "búsqueda"}


def pronostico(lugar):
    """Datos crudos de Open-Meteo (actual, por hora y 3 días)."""
    return _json(
        "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
        "wind_speed_10m,precipitation&hourly=precipitation_probability,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
        "uv_index_max&timezone=auto&forecast_days=3" % (lugar["lat"], lugar["lon"]))


def _cuando_llueve(d):
    """'como a las 4 de la tarde' si hoy hay una hora con ≥50 % de lluvia (de ahora en adelante)."""
    ahora = d["current"]["time"][:13]
    for t, p in zip(d["hourly"]["time"], d["hourly"]["precipitation_probability"]):
        if t[:10] == ahora[:10] and t[:13] >= ahora and (p or 0) >= 50:
            h = int(t[11:13])
            parte = "de la mañana" if h < 12 else ("de la tarde" if h < 19 else "de la noche")
            return f"como a las {h % 12 or 12} {parte}"
    return ""


def describir(d, lugar="", dias=2):
    """Frases para decir en voz alta: ahora, hoy y mañana, con un consejo si hace falta."""
    a, dia = d["current"], d["daily"]
    estado = CODIGOS.get(a["weather_code"], "")
    donde = f"En {lugar} " if lugar else "Ahorita "
    partes = [f"{donde}hace {round(a['temperature_2m'])} grados y está {estado}"
              + (f", con sensación de {round(a['apparent_temperature'])}"
                 if abs(a["apparent_temperature"] - a["temperature_2m"]) >= 3 else "") + "."]
    lluvia = dia["precipitation_probability_max"][0] or 0
    hoy = (f"Hoy la máxima es de {round(dia['temperature_2m_max'][0])} y la mínima de "
           f"{round(dia['temperature_2m_min'][0])}")
    if lluvia >= 30:
        cuando = _cuando_llueve(d)
        hoy += f", con {lluvia}% de probabilidad de lluvia{' ' + cuando if cuando else ''}"
    partes.append(hoy + ".")
    consejos = []
    if lluvia >= 50:
        consejos.append("lleva paraguas")
    if dia["temperature_2m_min"][0] <= 10:
        consejos.append("abrígate, que en la noche baja bastante")
    if (dia.get("uv_index_max") or [0])[0] and dia["uv_index_max"][0] >= 8:
        consejos.append("ponte bloqueador, el sol va a pegar fuerte")
    if consejos:
        partes.append(" y ".join(consejos).capitalize() + ".")
    if dias >= 2 and len(dia["time"]) > 1:
        m_lluvia = dia["precipitation_probability_max"][1] or 0
        partes.append(f"Mañana: {round(dia['temperature_2m_max'][1])} grados de máxima, "
                      f"{CODIGOS.get(dia['weather_code'][1], '').replace('con ', '')}"
                      + (f" y {m_lluvia}% de lluvia." if m_lluvia >= 30 else "."))
    return " ".join(partes)


def resumen_clima(dias=2):
    """El texto del clima de donde estás, o '' si no se pudo (sin internet, etc.)."""
    try:
        lugar = ubicacion()
        if not lugar:
            return ""
        return describir(pronostico(lugar), lugar.get("lugar", ""), dias)
    except Exception as e:
        print(f"[Clima: {type(e).__name__}: {str(e)[:80]}]")
        return None   # falló (sin internet, etc.)


@skill("clima",
       "Dice el clima actual y el pronóstico (hoy y mañana) de donde está el usuario, con su "
       "ubicación de Windows, o de la ciudad que diga: '¿cómo está el clima?', '¿va a llover?', "
       "'¿qué temperatura hace en Monterrey?', '¿cómo estará mañana?'.",
       {"ciudad": {"type": "string", "description": "Opcional: otra ciudad; vacío = donde está"}},
       requeridos=[])
def clima(ciudad=""):
    try:
        lugar = _buscar_ciudad(ciudad) if (ciudad or "").strip() else ubicacion()
    except Exception:
        lugar = None
    if not lugar:
        return Fallo("No pude saber dónde estás (revisa que la ubicación de Windows esté activada) "
                     "ni encontrar la ciudad." if not ciudad else f"No encontré la ciudad {ciudad}.")
    try:
        return describir(pronostico(lugar), lugar.get("lugar", ""))
    except Exception as e:
        return Fallo(f"No pude consultar el clima ({type(e).__name__}); revisa el internet.")
