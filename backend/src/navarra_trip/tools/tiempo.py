"""Previsión diaria con Open-Meteo (sin clave). Licencia de los datos: CC BY 4.0."""

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
DIARIO = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_sum,precipitation_probability_max"
)
# Códigos WMO que usa Open-Meteo, agrupados
WMO = {
    0: "despejado",
    1: "poco nuboso",
    2: "parcialmente nuboso",
    3: "cubierto",
    45: "niebla",
    48: "niebla con escarcha",
    51: "llovizna",
    53: "llovizna",
    55: "llovizna intensa",
    56: "llovizna helada",
    57: "llovizna helada",
    61: "lluvia débil",
    63: "lluvia",
    65: "lluvia fuerte",
    66: "lluvia helada",
    67: "lluvia helada",
    71: "nieve débil",
    73: "nieve",
    75: "nieve fuerte",
    77: "granizo fino",
    80: "chubascos",
    81: "chubascos",
    82: "chubascos fuertes",
    85: "chubascos de nieve",
    86: "chubascos de nieve",
    95: "tormenta",
    96: "tormenta con granizo",
    99: "tormenta con granizo",
}
# Umbral para proponer planes bajo techo. Solo la probabilidad no basta: una llovizna de 2 mm al
# 93 % cambiaba Irati y la Cascada del Cubo por monumentos.
LLUVIA_MM = 5


def mal_tiempo(dia: dict) -> bool:
    return (
        (dia["lluvia_mm"] or 0) >= LLUVIA_MM
        or dia["codigo"] >= 65  # lluvia fuerte, nieve, chubascos, tormenta
    )


def prevision(
    lat: float, lon: float, dias: int = 7, client: httpx.Client | None = None
) -> list[dict]:
    """Un dict por día: fecha, descripción, máx/mín, mm, prob. de lluvia y si hace mal tiempo."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": DIARIO,
        "timezone": "Europe/Madrid",
        "forecast_days": min(dias, 16),
    }
    r = (client or httpx).get(URL, params=params, timeout=30)
    r.raise_for_status()
    d = r.json()["daily"]
    out = []
    for i, fecha in enumerate(d["time"]):
        dia = {
            "fecha": fecha,
            "codigo": d["weather_code"][i],
            "descripcion": WMO.get(d["weather_code"][i], "desconocido"),
            "max": d["temperature_2m_max"][i],
            "min": d["temperature_2m_min"][i],
            "lluvia_mm": d["precipitation_sum"][i],
            "prob_lluvia": d["precipitation_probability_max"][i],
        }
        dia["mal_tiempo"] = mal_tiempo(dia)
        out.append(dia)
    return out
