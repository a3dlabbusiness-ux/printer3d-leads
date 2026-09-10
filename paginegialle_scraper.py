"""
Provider basato su PagineGialle.it (scraping pagina di ricerca).

NOTE IMPORTANTI:
- Il numero di telefono su PagineGialle è offuscato nell'HTML e viene
  ricomposto solo via JavaScript lato browser: non è estraibile in modo
  affidabile con semplice scraping, quindi qui il campo phone resta
  SEMPRE None (rispettando la regola in base.py: mai inventare dati).
- Il sito web esterno dell'attività NON è quasi mai presente nella
  scheda di ricerca; a volte è nella pagina di dettaglio. Qui proviamo
  a cercarlo anche lì, ma il tasso di successo è basso: questo provider
  è quindi più debole per l'email (che dipende dal sito) rispetto a OSM.
- Se la struttura HTML del sito cambia, i selettori sotto vanno
  aggiornati (vedi i log di ricerca precedenti nel repo per il debug).
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from app.leads.providers.base import LeadProvider, RawLead

logger = logging.getLogger(__name__)

BASE_URL = "https://www.paginegialle.it"
SEARCH_URL_TEMPLATE = BASE_URL + "/ricerca/{category}/{city}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

REQUEST_DELAY_SECONDS = 1.5


class PagineGialleProvider(LeadProvider):
    name: str = "paginegialle"

    def search(
        self,
        city: str,
        category: str,
        province: Optional[str] = None,
        max_results: int = 20,
        keywords: Optional[str] = None,
    ) -> List[RawLead]:
        url = SEARCH_URL_TEMPLATE.format(
            category=quote(category.lower()),
            city=quote(city.lower()),
        )

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("PagineGialleProvider: errore richiesta a %s: %s", url, e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.search-itm")[:max_results]

        results: List[RawLead] = []

        for card in cards:
            name = self._extract_name(card)
            if not name:
                continue

            address = self._extract_text(card, ".search-itm__adr")
            detail_url = self._extract_detail_url(card)

            website = None
            if detail_url:
                website = self._find_external_website(detail_url)
                time.sleep(REQUEST_DELAY_SECONDS)

            results.append(
                RawLead(
                    name=name,
                    category=category,
                    city=city,
                    province=province,
                    address=address,
                    website=website,
                    phone=None,  # offuscato via JS, non estraibile in modo affidabile
                    email=None,  # praticamente mai presente direttamente
                    source_name=self.name,
                )
            )

        logger.info(
            "PagineGialleProvider: trovati %d risultati per '%s' a '%s'",
            len(results), category, city,
        )
        return results

    # -----------------------------------------------------------------
    # Helper interni
    # -----------------------------------------------------------------

    @staticmethod
    def _extract_name(card) -> Optional[str]:
        h2 = card.select_one("h2")
        if not h2:
            return None
        # Il testo dell'h2 a volte include la categoria concatenata
        # (es. "Bar Caffe'Desirèe"): teniamo il testo grezzo, è comunque
        # meglio di niente; se serve pulizia ulteriore va affinata qui.
        return h2.get_text(strip=True) or None

    @staticmethod
    def _extract_text(card, selector: str) -> Optional[str]:
        el = card.select_one(selector)
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _extract_detail_url(card) -> Optional[str]:
        a = card.select_one("a[href^='http']")
        if a and BASE_URL in a.get("href", ""):
            return a["href"]
        return None

    @staticmethod
    def _find_external_website(detail_url: str) -> Optional[str]:
        """Cerca un link esterno (sito web reale) nella pagina di dettaglio."""
        try:
            resp = requests.get(detail_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return None
        except requests.RequestException:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href^='http']"):
            href = a["href"]
            if "paginegialle.it" in href or "wa.me" in href or "whatsapp" in href:
                continue
            return href
        return None
