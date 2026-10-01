"""Geolocaliza direcciones del Registro de Turismo con CartoCiudad (IGN), con caché de consultas."""

import difflib
import re
import time
import unicodedata

import httpx

API = "https://www.cartociudad.es/geocoder/api/geocoder"
NAVARRA = "31"


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().strip()


def portal(direccion: str) -> tuple[str, int] | None:
    """'Acella 5 3º D' -> ('Acella', 5). None si no hay portal real ('Mayor 0 S/N').

    El piso y la letra se quitan: con ellos CartoCiudad no encuentra nada.
    """
    m = re.match(r"(.+?)\s+(\d+)\b", direccion)
    return (m[1], int(m[2])) if m and int(m[2]) > 0 else None


def parecida(a: str, b: str) -> bool:
    """CartoCiudad corrige la grafía (Guipuzcoa -> GIPUZKOA) pero a veces devuelve otra calle."""
    a, b = _norm(a), _norm(b)
    return a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _es(lugar: str) -> str:
    return lugar.split(" / ")[0]  # 'Pamplona / Iruña' -> 'Pamplona'


class Geocoder:
    """`cache` (consulta -> resultado o None) se guarda en disco y hace las repeticiones gratis."""

    def __init__(self, client: httpx.Client | None, cache: dict, pausa: float = 0.1):
        self.client, self.cache, self.pausa = client, cache, pausa

    def _get(self, accion: str, **params) -> dict | list | None:
        if self.client is None:
            raise KeyError(f"sin red y sin caché: {accion} {params}")
        for intento in range(3):
            r = self.client.get(f"{API}/{accion}", params=params)
            if r.status_code < 500:
                break
            time.sleep(self.pausa * 20 * (intento + 1))
        time.sleep(self.pausa)
        if len(self.cache) % 100 == 0:
            print(f"  {len(self.cache)} consultas a CartoCiudad", flush=True)
        # Algunas consultas dan 500 siempre ('de Urbasa 31, Olazti'): se tratan como "sin
        # resultado" y se cachean para no repetirlas. Un 4xx sí es un error nuestro.
        if r.status_code < 500:
            r.raise_for_status()
        return r.json() if r.status_code == 200 else None

    def buscar(self, q: str) -> dict | None:
        """Mejor resultado de `find` para un texto libre (puede ser de cualquier provincia)."""
        if q not in self.cache:
            j = self._get("find", q=q)
            keep = ("type", "address", "provinceCode", "lat", "lng")
            self.cache[q] = {k: j[k] for k in keep} if j else None
        return self.cache[q]

    def lugar(self, nombre: str, tipo: str, municipio: str = "") -> dict | None:
        """Centroide de una población o municipio de Navarra.

        `find` no filtra por provincia ('Los Arcos' cae en Burgos, 'Sada' en A Coruña), así que se
        buscan candidatos filtrados por Navarra y luego se pide la geometría por id.
        """
        clave = f"lugar|{tipo}|{nombre}|{municipio}"
        if clave not in self.cache:
            cands = self._get("candidates", q=nombre, limit=10, provincia_filter="Navarra") or []
            cands = [c for c in cands if c["type"].lower() == tipo and c["provinceCode"] == NAVARRA]
            cands.sort(key=lambda c: not (municipio and parecida(municipio, c["muni"])))
            j = self._get("find", id=cands[0]["id"], type=cands[0]["type"]) if cands else None
            self.cache[clave] = {"lat": j["lat"], "lng": j["lng"]} if j else None
        return self.cache[clave]

    def geocodificar(
        self, direccion: str, localidad: str, municipio: str
    ) -> tuple[float, float, str] | None:
        """(lon, lat, precisión): portal exacto, si no centroide de la localidad o del municipio.

        Formatos comprobados a mano: añadir ', Navarra' o el piso a una dirección hace que no la
        encuentre.
        """
        if p := portal(direccion):
            r = self.buscar(f"{p[0]} {p[1]}, {_es(localidad)}")
            if (
                r
                and r["type"] == "portal"
                and r["provinceCode"] == NAVARRA
                and parecida(p[0], r["address"])
            ):
                return r["lng"], r["lat"], "direccion"
        if r := self.lugar(_es(localidad), "poblacion", _es(municipio)):
            return r["lng"], r["lat"], "localidad"
        if r := self.lugar(_es(municipio), "municipio"):
            return r["lng"], r["lat"], "municipio"
        return None
