"""
lead_search.py
Ricerca lead con email reale, in ordine di priorità:
  1. PagineGialle (email diretta dalla scheda, o sito web da scrappare)
  2. OpenStreetMap (fallback: sito web dal tag "website", poi scraping email)

Scarta i lead già presenti nel database (leads.db) per evitare duplicati.

Richiede:
    pip install requests beautifulsoup4 --break-system-packages

Dipende da (devono stare nella stessa cartella):
    email_scraper.py
    paginegialle_scraper.py

Schema tabella "leads" usato (verificato sul tuo database):
    id, name, category, city, province, address, website, domain,
    email, phone, social_links, source_id, campaign_id, status,
    site_analysis, suggested_products, discovered_at, approved_at,
    sent_at, notes, last_error
    UNIQUE(name, address)

NOTA sullo status: la colonna "status" è NOT NULL. Qui viene impostato
a "NEW" per i nuovi lead trovati. Se il tuo bot si aspetta un valore
diverso per "lead pronto da approvare" (es. "DISCOVERED", "PENDING"),
cambia la costante NEW_LEAD_STATUS più sotto.

Uso base:
    python lead_search.py bar Catania 10

Oppure importato nel bot:
    from lead_search import find_leads_with_email
    leads = find_leads_with_email("Catania", "bar", 10)
"""

import sys
import time
import sqlite3
import requests
from urllib.parse import urlparse
from datetime import datetime, timezone

from email_scraper import find_email_on_website
from paginegialle_scraper import search_paginegialle

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DB_PATH = "data/leads.db"  # stesso path che usa già il tuo bot

# Valore da usare nella colonna "status" (NOT NULL) per i lead appena trovati.
# Verifica che coincida con quello che il tuo bot Telegram si aspetta per
# mostrare il lead come "da approvare". Se il tuo bot usa un altro valore
# (es. "PENDING", "DISCOVERED"), cambialo qui.
NEW_LEAD_STATUS = "NEW"

MAX_OVERSAMPLE_FACTOR = 20  # tetto per non restare bloccati all'infinito su OSM
REQUEST_DELAY_SECONDS = 1.5  # pausa tra uno scraping e l'altro (buon vicinato)


# ---------------------------------------------------------------------------
# Utility comuni
# ---------------------------------------------------------------------------

def _extract_domain(website: str) -> str:
    """Estrae il dominio nudo da un URL, per popolare la colonna 'domain'."""
    if not website:
        return ""
    url = website if website.startswith("http") else "https://" + website
    netloc = urlparse(url).netloc
    return netloc.replace("www.", "")


def _is_already_known(name: str, address: str) -> bool:
    """
    Controlla se il lead esiste già nel database.
    Usa (name, address) perché è il vincolo UNIQUE reale della tabella leads.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM leads WHERE name = ? AND address = ? LIMIT 1",
        (name, address),
    )
    found = cur.fetchone() is not None
    conn.close()
    return found


def _save_new_lead(name: str, email: str, city: str, category: str,
                    website: str, address: str, phone: str = None) -> None:
    """Salva il nuovo lead nel database così non verrà riproposto in futuro."""
    domain = _extract_domain(website)
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO leads
               (name, category, city, address, website, domain, email,
                phone, status, discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, category, city, address, website, domain, email,
             phone, NEW_LEAD_STATUS, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Un altro processo lo ha già inserito nel frattempo (name+address duplicati)
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fonte 1: PagineGialle (primaria)
# ---------------------------------------------------------------------------

def _find_leads_paginegialle(city: str, category: str, count: int) -> list:
    """
    Cerca su PagineGialle. Per ogni scheda: usa l'email diretta se c'è,
    altrimenti prova a scrappare il sito web collegato.
    """
    found_leads = []

    # Sovra-peschiamo perché non tutte le schede avranno email utilizzabile
    raw_results = search_paginegialle(category, city, max_results=count * 4)
    print(f"PagineGialle: {len(raw_results)} schede trovate, filtro quelle con email...")

    for item in raw_results:
        if len(found_leads) >= count:
            break

        name = item["name"]
        address = item.get("address", "")

        if _is_already_known(name, address):
            continue  # già proposto in passato

        email = item.get("email")

        # Se PagineGialle non ha email diretta ma c'è un sito, proviamo a scrapparlo
        if not email and item.get("website"):
            email = find_email_on_website(item["website"])
            time.sleep(REQUEST_DELAY_SECONDS)

        if not email:
            continue  # niente email reperibile, scartiamo questo lead

        lead = {
            "name": name,
            "email": email,
            "website": item.get("website"),
            "phone": item.get("phone"),
            "address": address,
            "city": city,
            "category": category,
        }
        found_leads.append(lead)
        _save_new_lead(name, email, city, category, item.get("website"),
                        address, item.get("phone"))
        print(f"  OK (PagineGialle): {name} -> {email}")

    return found_leads


# ---------------------------------------------------------------------------
# Fonte 2: OpenStreetMap (fallback)
# ---------------------------------------------------------------------------

def _geocode_city(city: str):
    """Ottiene lat/lon approssimativi della città tramite Nominatim (gratis)."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": city, "format": "json", "limit": 1},
        headers={"User-Agent": "A3DLabLeadBot/1.0"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def _overpass_query(lat: float, lon: float, category: str, radius_m: int = 8000):
    """
    Interroga Overpass per punti della categoria richiesta attorno a lat/lon.
    category deve essere un valore di tag OSM tipo "bar", "restaurant", "cafe", ecc.
    """
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="{category}"](around:{radius_m},{lat},{lon});
      way["amenity"="{category}"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        website = tags.get("website") or tags.get("contact:website")
        if not name:
            continue
        results.append({
            "name": name,
            "website": website,
            "address": tags.get("addr:street", ""),
        })
    return results


def _find_leads_osm(city: str, category: str, count: int) -> list:
    """Fallback su OSM quando PagineGialle non basta a raggiungere il target."""
    coords = _geocode_city(city)
    if not coords:
        print(f"Città non trovata su OSM: {city}")
        return []
    lat, lon = coords

    found_leads = []
    factor = 5

    while len(found_leads) < count and factor <= MAX_OVERSAMPLE_FACTOR:
        raw_results = _overpass_query(lat, lon, category, radius_m=5000 + factor * 1000)
        print(f"OSM: {len(raw_results)} risultati grezzi (raggio allargato)...")

        for place in raw_results:
            if len(found_leads) >= count:
                break

            name = place["name"]
            website = place.get("website")
            address = place.get("address", "")

            if not website:
                continue

            if _is_already_known(name, address):
                continue

            email = find_email_on_website(website)
            time.sleep(REQUEST_DELAY_SECONDS)

            if not email:
                continue

            lead = {
                "name": name,
                "email": email,
                "website": website,
                "address": address,
                "city": city,
                "category": category,
            }
            found_leads.append(lead)
            _save_new_lead(name, email, city, category, website, address)
            print(f"  OK (OSM): {name} -> {email}")

        factor += 5

    return found_leads


# ---------------------------------------------------------------------------
# Funzione principale
# ---------------------------------------------------------------------------

def find_leads_with_email(city: str, category: str, count: int) -> list:
    """
    Ritorna fino a `count` lead con email reale trovata.
    Prova prima PagineGialle; se non basta, completa con OSM.
    """
    leads = _find_leads_paginegialle(city, category, count)

    if len(leads) < count:
        remaining = count - len(leads)
        print(f"\nPagineGialle ha dato {len(leads)}/{count}, provo OSM per {remaining} in più...")
        leads += _find_leads_osm(city, category, remaining)

    return leads


if __name__ == "__main__":
    category = sys.argv[1] if len(sys.argv) > 1 else "bar"
    city = sys.argv[2] if len(sys.argv) > 2 else "Catania"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    print(f"Cerco {count} lead con email reale: {category} a {city}\n")
    leads = find_leads_with_email(city, category, count)

    print(f"\n--- Trovati {len(leads)}/{count} lead validi ---")
    for l in leads:
        print(f"{l['name']} | {l['email']} | {l.get('website')}")
