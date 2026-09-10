"""
email_scraper.py
Prova a estrarre un indirizzo email dal sito web di un lead.
Da usare dopo aver ottenuto il campo "website" da PagineGialle (o OSM).

Richiede:
    pip install requests beautifulsoup4

Uso base:
    from email_scraper import find_email_on_website

    email = find_email_on_website("https://esempio-bar.it")
    print(email)  # "info@esempio-bar.it" oppure None
"""

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

CANDIDATE_PATHS = ["", "/contatti", "/contact", "/contact-us", "/about", "/chi-siamo"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; A3DLabLeadBot/1.0)"
}

IGNORE_PATTERNS = ["example.com", "sentry.io", "wixpress.com", ".png", ".jpg"]


def _extract_email_from_html(html: str):
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            candidate = a["href"].split("mailto:")[1].split("?")[0].strip()
            if candidate and not any(p in candidate.lower() for p in IGNORE_PATTERNS):
                return candidate

    matches = EMAIL_REGEX.findall(html)
    for m in matches:
        if not any(p in m.lower() for p in IGNORE_PATTERNS):
            return m

    return None


def find_email_on_website(website_url: str, timeout: int = 10):
    if not website_url:
        return None

    if not website_url.startswith("http"):
        website_url = "https://" + website_url

    for path in CANDIDATE_PATHS:
        url = urljoin(website_url, path)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code != 200:
                continue
            email = _extract_email_from_html(resp.text)
            if email:
                return email
        except requests.RequestException:
            continue

    return None


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://esempio.it"
    print(f"Cerco email su: {url}")
    result = find_email_on_website(url)
    print(f"Email trovata: {result}")
