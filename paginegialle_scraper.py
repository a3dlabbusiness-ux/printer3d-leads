"""
paginegialle_scraper.py
Cerca attività su PagineGialle.it e prova a estrarre email diretta
dalla scheda, o in fallback il sito web (da passare poi a email_scraper.py).

Richiede:
    pip install requests beautifulsoup4 --break-system-packages

NOTA IMPORTANTE:
Questo è scraping di un sito pubblico, non un'API ufficiale.
- Rispetta pause tra le richieste (REQUEST_DELAY_SECONDS)
- La struttura HTML del sito può cambiare nel tempo: se lo script
  smette di trovare risultati, va aggiornato il parsing (i selettori
  CSS/HTML in _parse_listing_page)
- Uso consigliato: contatti B2B per proposte commerciali, con volumi
  contenuti (decine, non migliaia, di richieste al giorno)

Uso base:
    from paginegialle_scraper import search_paginegialle
    results = search_paginegialle("bar", "Catania", max_results=20)
"""

import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote

BASE_URL = "https://www.paginegialle.it"
SEARCH_URL_TEMPLATE = BASE_URL + "/ricerca/{category}/{city}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

REQUEST_DELAY_SECONDS = 2.0  # pausa tra una pagina e l'altra
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _parse_listing_page(html: str) -> list[dict]:
    """
    Estrae le schede attività da una pagina di risultati PagineGialle.
    Ritorna lista di dict con: name, address, phone, website, detail_url
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Le schede risultato sono in elementi con classe "list-element"
    # (verificare periodicamente che il selettore sia ancora valido)
    cards = soup.select("div.list-element")

    for card in cards:
        name_el = card.select_one("h2 a, .titleFirstLine")
        if not name_el:
            continue
        name = name_el.get_text(strip=True)

        detail_url = None
        link_el = card.select_one("a[href]")
        if link_el and link_el.get("href", "").startswith("http"):
            detail_url = link_el["href"]

        address_el = card.select_one(".description-fields, .address")
        address = address_el.get_text(strip=True) if address_el else ""

        phone_el = card.select_one("a[href^='tel:']")
        phone = phone_el["href"].replace("tel:", "") if phone_el else None

        website_el = card.select_one("a[href^='http']:not([href*='paginegialle.it'])")
        website = website_el["href"] if website_el else None

        results.append({
            "name": name,
            "address": address,
            "phone": phone,
            "website": website,
            "detail_url": detail_url,
        })

    return results


def _extract_email_from_detail_page(detail_url: str) -> str | None:
    """Visita la pagina scheda dell'attività e cerca un'email diretta."""
    try:
        resp = requests.get(detail_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # 1) Link mailto: (più affidabile)
    mailto = soup.select_one("a[href^='mailto:']")
    if mailto:
        email = mailto["href"].replace("mailto:", "").split("?")[0].strip()
        if email:
            return email

    # 2) Fallback: regex sul testo della pagina
    matches = EMAIL_REGEX.findall(resp.text)
    if matches:
        return matches[0]

    return None


def search_paginegialle(category: str, city: str, max_results: int = 20) -> list[dict]:
    """
    Cerca attività su PagineGialle per categoria e città.
    Per ogni risultato prova a recuperare l'email diretta dalla scheda.
    Ritorna lista di dict: name, address, phone, website, email (può essere None)
    """
    url = SEARCH_URL_TEMPLATE.format(
        category=quote(category.lower()),
        city=quote(city.lower()),
    )

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Errore nella ricerca PagineGialle: {e}")
        return []

    listings = _parse_listing_page(resp.text)[:max_results]
    results = []

    for item in listings:
        email = None
        if item.get("detail_url"):
            email = _extract_email_from_detail_page(item["detail_url"])
            time.sleep(REQUEST_DELAY_SECONDS)

        item["email"] = email
        results.append(item)

    return results


if __name__ == "__main__":
    import sys
    category = sys.argv[1] if len(sys.argv) > 1 else "bar"
    city = sys.argv[2] if len(sys.argv) > 2 else "Catania"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    print(f"Cerco: {category} a {city} (max {n})\n")
    for r in search_paginegialle(category, city, max_results=n):
        print(f"- {r['name']}")
        print(f"  Indirizzo: {r['address']}")
        print(f"  Telefono:  {r.get('phone')}")
        print(f"  Sito:      {r.get('website')}")
        print(f"  Email:     {r.get('email')}")
        print()
