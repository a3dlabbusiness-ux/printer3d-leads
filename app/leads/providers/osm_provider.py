"""
Provider basato su OpenStreetMap (Nominatim per geocodifica + Overpass API per la ricerca).

Nessuna API key richiesta, nessun account, nessuna carta di credito:
sono servizi pubblici e gratuiti mantenuti dalla community OpenStreetMap.

Strategia in due passi (più veloce e affidabile della ricerca per confine comunale):
1. Geocodifica il nome della città in coordinate (lat/lon) tramite Nominatim;
2. Cerca le attività in un raggio di qualche km da quel punto tramite Overpass,
   usando "around" invece del confine amministrativo completo (molto più leggero
   per il server pubblico, evita i timeout).

Limiti onesti rispetto a Google Places:
- La copertura dei dati dipende da quanto la community ha mappato quella zona.
- Le email non sono quasi mai presenti direttamente nei dati OSM: come per gli
  altri provider, l'email viene cercata successivamente sul sito web (se presente)
  dal modulo site_analyzer, mai inventata.
- Sono servizi pubblici con rate limit gentile: evitiamo ricerche troppo frequenti.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import httpx

from app.leads.providers.base import LeadProvider, RawLead

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Più server pubblici Overpass, mantenuti da organizzazioni diverse: se il primo
# è sovraccarico o in timeout, proviamo automaticamente i successivi.
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

USER_AGENT = "Printer3DLeadsBot/1.0 (contact: a3dlabbusiness@gmail.com)"

# Mappa le categorie in italiano ai tag OpenStreetMap più comuni.
# Se una categoria non è in questa mappa, proviamo una ricerca generica per nome.
CATEGORY_TAG_MAP = {
    "bar": [("amenity", "bar"), ("amenity", "cafe"), ("amenity", "pub")],
    "pasticceria": [("shop", "pastry"), ("shop", "confectionery")],
    "ristorante": [("amenity", "restaurant")],
    "ristoranti": [("amenity", "restaurant")],
    "pizzeria": [("amenity", "fast_food"), ("cuisine", "pizza")],
    "hotel": [("tourism", "hotel")],
    "b&b": [("tourism", "guest_house")],
    "negozio": [("shop", "yes")],
    "palestra": [("leisure", "fitness_centre")],
    "parrucchiere": [("shop", "hairdresser")],
    "centro estetico": [("shop", "beauty")],
    "concessionaria": [("shop", "car")],
    "agenzia immobiliare": [("office", "estate_agent")],
}

DEFAULT_RADIUS_METERS = 6000


class OsmProvider(LeadProvider):
    name = "osm"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def _geocode_city(self, city: str) -> Tuple[float, float]:
        headers = {"User-Agent": USER_AGENT}
        params = {"q": city, "format": "json", "limit": 1}
        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            resp = client.get(NOMINATIM_URL, params=params)
            resp.raise_for_status()
            results = resp.json()
        if not results:
            raise ValueError(f"Città non trovata su OpenStreetMap: '{city}'")
        return float(results[0]["lat"]), float(results[0]["lon"])

    def _build_query(self, lat: float, lon: float, category: str, max_results: int) -> str:
        category_key = category.strip().lower()
        tags = CATEGORY_TAG_MAP.get(category_key)

        radius = DEFAULT_RADIUS_METERS
        if tags:
            tag_filters = "".join(
                f'nwr["{k}"="{v}"](around:{radius},{lat},{lon});' for k, v in tags
            )
        else:
            tag_filters = f'nwr["name"~"{category}",i](around:{radius},{lat},{lon});'

        query = f"""
        [out:json][timeout:20];
        (
          {tag_filters}
        );
        out center {max_results};
        """
        return query

    def search(
        self,
        city: str,
        category: str,
        province: Optional[str] = None,
        max_results: int = 20,
        keywords: Optional[str] = None,
    ) -> List[RawLead]:
        lat, lon = self._geocode_city(city)
        query = self._build_query(lat, lon, category, max_results)

        headers = {"User-Agent": USER_AGENT}
        data = None
        last_error: Optional[Exception] = None

        for overpass_url in OVERPASS_URLS:
            try:
                with httpx.Client(timeout=self.timeout, headers=headers) as client:
                    resp = client.post(overpass_url, data={"data": query})
                    resp.raise_for_status()
                    data = resp.json()
                logger.info("OsmProvider: risposta ricevuta da %s", overpass_url)
                break
            except httpx.HTTPError as exc:
                logger.warning("OsmProvider: %s non disponibile (%s), provo il prossimo...", overpass_url, exc)
                last_error = exc
                continue

        if data is None:
            logger.error("OsmProvider: tutti i server Overpass non disponibili: %s", last_error)
            raise last_error

        results: List[RawLead] = []
        for element in data.get("elements", [])[:max_results]:
            tags = element.get("tags", {})
            name = tags.get("name")
            if not name:
                continue

            address_parts = [
                tags.get("addr:street"),
                tags.get("addr:housenumber"),
            ]
            address = " ".join(p for p in address_parts if p) or None

            website = tags.get("website") or tags.get("contact:website")
            phone = tags.get("phone") or tags.get("contact:phone")
            email = tags.get("email") or tags.get("contact:email")  # raro ma a volte presente

            results.append(
                RawLead(
                    name=name,
                    category=category,
                    city=city,
                    province=province,
                    address=address,
                    website=website,
                    phone=phone,
                    email=email,
                    source_name=self.name,
                )
            )

        logger.info("OsmProvider: trovati %d risultati per '%s' a '%s'", len(results), category, city)
        return results
