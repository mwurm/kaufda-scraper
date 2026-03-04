import base64
from io import BytesIO

import os
import random
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Iterable
import requests
import time
from typing import List, Dict
import yaml
from pathlib import Path
from hashlib import sha256
import re
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from dateutil import parser
from requests_cache import OriginalResponse, CachedResponse, CachedSession
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())
requests_cache_session = CachedSession(os.getenv('REQUESTS_CACHE_DB_PATH'), expire_after=timedelta(hours=6))

# original_save_response = self.requests_cache_session.cache.save_response
# def save_response_if_criteria_met(key, response, *args, **kwargs):
#     if response and hasattr(response, "text") and "werden gerade aufbereitet" in response.text.lower():
#         print("Not caching (criteria not met)", key)
#
#     else:
#         ori_rsp = original_save_response(key, response, *args, **kwargs)
#         return ori_rsp

# self.requests_cache_session.cache.save_response = save_response_if_criteria_met

PRINT_CATEGORY_PATHS = True
PRINT_DEALS = True

TAGE = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

REQUEST_HEADERS = {
    "accept": "application/json",
    "accept-language": "de-DE,de",
    "cache-control": "max-age=3600",
    "content-type": "application/json",
    "delivery_channel": "dest.kaufda",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "user_platform_category": "desktop.web.browser",
    "user_platform_os": "windows"
}


def normalize_price(text: str) -> str | None:
    """
    Vereinheitlicht Preisangaben pro kg oder l.

    Beispiele:
    "1 kg = 46.13"        -> "46.13 EUR/kg"
    "15.98 / kg"          -> "15.98 EUR/kg"
    "1kg = 11,63–6,20"    -> "6.20–11.63 EUR/kg"
    "1 l = 1.80"          -> "1.80 EUR/l"
    """

    if not text:
        return text

    original = text.lower().strip()

    # Einheit erkennen
    unit_match = re.search(r"(kg|ml|l)", original.replace(" ", ""))
    if not unit_match:
        return original

    unit = unit_match.group(1)

    # Kommas in Punkte umwandeln
    text = text.replace(",", ".")

    # Zahlen extrahieren
    numbers = re.findall(r"\d+(?:\.\d+)?", text)

    if not numbers:
        return original

    try:
        values = [Decimal(n) for n in numbers]
    except InvalidOperation:
        return original

    # Falls Form wie "1 kg = 46.13" → die "1" ignorieren
    if len(values) >= 2 and values[0] == 1:
        values = values[1:]

    if not values:
        return original

    # Einzelpreis
    if len(values) == 1:
        return f"{values[0]:.2f}€/{unit}"

    # Preisbereich
    min_val = min(values)
    max_val = max(values)

    return f"{min_val:.2f}–{max_val:.2f} EUR/{unit}"

def load_config(config_path: str = "kaufda.yaml") -> Dict:
    """Lädt die YAML-Konfigurationsdatei."""
    with open(config_path, 'r', encoding='utf-8') as file:
        return yaml.safe_load(file)

def get_all_articles(config: Dict) -> List[str]:
    """Extrahiert alle Artikel aus den Kategorien."""
    articles = []
    for category, items in config.get("articles", {}).items():
        if items:
            articles.extend(items)
    return articles


OFFER_SEARCH_URL = "https://www.kaufda.de/webapp/api/slots/offerSearch"

# https://www.kaufda.de/webapp/api/slots/offerSearch?searchQuery=h%C3%A4hnchenbrust&lat=47.965625499999994&lng=11.753921799999999&size=25
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}


@dataclass
class SearchRequest:
    name: str
    match_any: List[str]
    match_none: List[str]
    multisearch: List[str]

    @classmethod
    def from_dict(cls, name: str, data: Dict) -> 'SearchRequest':
        """Lädt einen SearchRequest aus einem Dict."""
        return (cls(
            name=name,
            match_any=data.get('match_any', []),
            match_none=data.get('match_none', []),
            multisearch=data.get('multisearch', [])
        ))

    @classmethod
    def from_config(cls, config: Dict) -> List['SearchRequest']:
        """Lädt mehrere SearchRequests aus der Konfiguration."""
        search_requests = []
        for name, data in config.items():
            req = cls.from_dict(name, data)
            if not 'multisearch' in data:
                req.multisearch = [name]
            if not 'match_any' in data:
                req.match_any = req.multisearch
            search_requests.append(req)

        return search_requests


@dataclass(frozen=True)
class Deal:
    type: str
    price_min: float
    price_max: float
    price_by_base_unit: str
    conditions: list[str]

    def price_range_str(self) -> str:
        if self.price_min == self.price_max:
            return f"{self.price_min}€"
        else:
            return f"{self.price_min}€ - {self.price_max}€"

    def normalized_price_by_base_unit(self) -> str | None:
        return normalize_price(self.price_by_base_unit)


@dataclass(frozen=True)
class SearchResult:
    publisher_name: str
    article: str
    image_url: str | None
    deals: tuple[Deal]
    description: str
    pub_dates: list[tuple[datetime, datetime]]

    def min_price(self) -> float:
        return min(deal.price_min for deal in self.deals)

    def __str__(self) -> str:
        obj_string =  f"{self.article} | {self.publisher_name} | {self.description} || {'|'.join([d.__str__() for d in self.deals])}"
        return obj_string

    def to_markdown(self):
        obj_string =  f"{self.publisher_name} {self.article}: {self.description}: \n"
        obj_string +=  f"{self.image_url}\n"
        for deal in self.deals:
            if deal.type in ['RECOMMENDED_RETAIL_PRICE', 'REGULAR_PRICE']:
                continue
            obj_string += f"- {deal.price_range_str()}"
            # obj_string += f"- {deal.type}: {deal.price_range_str()}"
            if deal.conditions:
                obj_string += f" [{', '.join(deal.conditions)}]"
            if deal.price_by_base_unit:
                obj_string += f" ({deal.normalized_price_by_base_unit()})"
            obj_string += "\n"
        for (start, end) in self.pub_dates:
            obj_string += f"- {TAGE[start.weekday()]} {start.strftime('%d.%m') if start else '?'} - {TAGE[end.weekday()]} {end.strftime('%d.%m') if end else '?'}\n"

        return obj_string

    def to_sha256(self):
        return sha256(self.to_markdown().encode('utf-8')).hexdigest()

def get_search_params(search_req: SearchRequest) -> list:
    return [{
        "searchQuery": entry,
        "lat": 47.965625499999994,
        "lng": 11.753921799999999,
        "size": 25
    } for entry in search_req.multisearch]


def search_article(search_req: SearchRequest, preffered_publishers: List[str] | None = None) -> List[SearchResult]:
    url = "https://www.kaufda.de/webapp/api/slots/offerSearch"
    responses =  []
    for p in get_search_params(search_req):
        rsp = requests_cache_session.get(url, params=p, headers=REQUEST_HEADERS)
        if not rsp.from_cache:
            time.sleep(.1)  # Rate limiting

        rsp.raise_for_status()
        responses.append(rsp)


    contents = []
    for rsp in responses:
        new_contents = rsp.json().get("_embedded", []).get("contents", [])
        contents.extend(new_contents)

    publisher_filtered_contents = list(filter(lambda e: e.get("content", {}).get("publisherName", "").lower() in preffered_publishers, contents)) if preffered_publishers else contents
    found_results = list()
    for result_entry in publisher_filtered_contents:
        try:
            search_result = extract_content(result_entry, search_req)
            if search_result:
                found_results.append(search_result)
        except Exception as e:
            print(f"Exception: {e}")
            continue

    seen = set()
    found_results_without_duplicates = []
    for found_result in found_results:
        price_per_kg = extract_price_per_kg(found_result)
        result_key = (found_result.publisher_name, price_per_kg if price_per_kg else random.random())
        if result_key not in seen:
            seen.add(result_key)
            found_results_without_duplicates.append(found_result)
    return list(found_results_without_duplicates)


def extract_content(result_entry: dict, search_req: SearchRequest) -> SearchResult | None:
    content_object = result_entry.get("content", {})

    publisher_name = content_object.get("publisherName")
    publisher_name = 'Netto' if publisher_name and publisher_name.lower() == 'netto marken-discount' else publisher_name
    image_url = content_object.get("image", {}).get("url", None)

    pub_dates = []
    for publicationProfile in content_object.get("publicationProfiles", []):
        start = publicationProfile.get("validity", {}).get("startDate")
        end = publicationProfile.get("validity", {}).get("endDate")

        start_parsed = parser.parse(start) if start else None
        end_parsed = parser.parse(end) if end else None
        pub_dates.append((start_parsed, end_parsed))

    # finde gesuchten artikel in categoryPaths
    products = content_object.get("products", [])
    found = False

    for p in products:
        name_and_description = p.get("name") + "; " + ", ".join([desc.get("paragraph") for desc in p.get('description', [])])

        if PRINT_CATEGORY_PATHS:
            print(f"name;description: {name_and_description}")
            print(f"{image_url}")

        product_name_with_all_context_paths = name_and_description
        category_path_strings = []
        for category_path in p.get('categoryPaths', []):
            path = "/".join([cat.get("name", "").lower() for cat in category_path])
            category_path_strings.append(path)
            if PRINT_CATEGORY_PATHS:
                print(f"   category path: {path}")

        product_name_with_all_context_paths += ";" + ";".join(category_path_strings)


        for match_pattern in search_req.match_any:
            if re.search(match_pattern, product_name_with_all_context_paths, re.IGNORECASE):
                found = True
                break

        if search_req.match_none:
            for nomatch_pattern in search_req.match_none:
                if re.search(nomatch_pattern, product_name_with_all_context_paths, re.IGNORECASE):
                    found = False
                    break

        if found:
            deals = content_object.get("deals", [])

            if PRINT_DEALS:
                for d in deals:
                    print(f"   {d}")

            deals_dataobjects: list[Deal] = []
            for deal in deals:
                deal_type = deal.get("type")
                price_min = min(deal.get("min"), deal.get("max"))
                price_max = max(deal.get("min"), deal.get("max"))
                price_by_base_unit = deal.get("priceByBaseUnit")
                conditions = deal.get('conditions', [])
                condition_strings = []
                for condition in conditions:
                    for key, value in condition.items():
                        if isinstance(value, str):
                            condition_strings.append(value)
                        else:
                            condition_strings.append(f"{key}: {value}")

                if 'EUR' != deal.get("currencyCode", "EUR"):
                    raise ValueError(f"Unexpected currency: {deal.get('currencyCode')}")

                deals_dataobjects.append(Deal(type=deal_type, price_min=price_min, price_max=price_max, price_by_base_unit=price_by_base_unit, conditions=condition_strings))

            # Versuche zuerst SALES_PRICE zu extrahieren, sonst REGULAR_PRICE
            search_result = SearchResult(publisher_name=publisher_name, article=search_req.name,
                                         image_url=image_url,
                                         deals=tuple(deals_dataobjects), description=name_and_description, pub_dates=pub_dates)
            return search_result

        return None




def run_search_req(search_request: SearchRequest, publishers: List[str] | None = None):

    try:
        results = search_article(search_request, publishers)

        if results:
            print(f"## {search_request.name}")
            for result in sorted(results, key=lambda r: r.min_price()):
                print(result.to_markdown())
            return results
        else:
            return []



    except Exception as e:
        print(f"   Fehler: {e}\n")



def extract_price_per_kg(result: SearchResult) -> float | None:
    prices = []

    for deal in result.deals:
        if deal.price_min == 0.0:
            continue

        normalized = deal.normalized_price_by_base_unit()
        if not normalized:
            continue

        match = re.search(r"([\d\.]+)\s*€/", normalized)
        if match:
            prices.append(float(match.group(1)))

    return min(prices) if prices else None


def detect_badges(result: SearchResult) -> str:
    text = (result.description or "").lower()
    badges = []

    if "tiefgefroren" in text:
        badges.append("❄️")

    if "bio" in text:
        badges.append("🌱")

    return " ".join(badges)


def group_by_article(results: Iterable[SearchResult]):
    grouped = defaultdict(list)

    for r in results:
        price = extract_price_per_kg(r)

        grouped[r.article].append({
            "store": r.publisher_name,
            "price": price,
            "image": r.image_url,
            "badges": detect_badges(r)
        })

    return grouped


def encode_image_to_base64(image_url: str) -> str:
    """Konvertiert eine Bild-URL zu Base64-kodiertem Datenformat."""
    try:
        print(f"Encoding image: {image_url}")
        response = requests_cache_session.get(image_url, timeout=5)
        response.raise_for_status()
        if not response.from_cache:
            time.sleep(.5)
        b64 = base64.b64encode(response.content).decode('utf-8')
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"Fehler beim Encoding des Bildes {image_url}: {e}")
        return image_url


def format_cell(rank: int, entry: dict, second_price: float | None):
    medals = ["🥇", "🥈", "🥉"]
    medal = medals[rank]

    price = entry["price"]
    store = entry["store"]
    badges = entry["badges"]
    image = entry["image"] or ""

    extra = ""
    if rank == 0 and second_price:
        diff_ratio = (second_price - price) / second_price
        if diff_ratio > 0.20:
            extra = " 🔥"
        elif diff_ratio < 0.05:
            extra = " ⚖️"

    badge_str = f" {badges}" if badges else ""


    img_html = f'<img src="{image}" style="width:80px;border-radius:8px;"><br>'

    price = price if price is not None else float('inf')  # Unbekannter Preis wird als unendlich teuer behandelt
    return f"""
        <div style="text-align:center;">
            {img_html}
            <strong>{medal} {store}</strong><br>
            {price:.2f}€/kg{extra}{badge_str}
        </div>
    """


def generate_html_table(outfile: str, results_by_category: dict) -> str:

    html = """
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body style="font-family:Arial;">
        <h2>🛒 Einkaufsübersicht</h2>
    """

    for (category, results) in results_by_category.items():
        grouped = group_by_article(results)

        if len(grouped) == 0:
            continue

        html += f"\n<h3>{category}</h3>\n"
        html +=   """<table border="1" cellpadding="10" cellspacing="0" style="border-collapse:collapse;">
            <tr style="background:#f2f2f2;">
                <th>Produkt</th>
                <th>🥇 Platz 1</th>
                <th>🥈 Platz 2</th>
                <th>🥉 Platz 3</th>
            </tr>
            """


        for article, entries in sorted(grouped.items()):
            entries = sorted(entries, key=lambda x: x["price"] if x["price"] else float('inf'))
            top3 = entries[:3]

            second_price = top3[1]["price"] if len(top3) > 1 else None

            cells = []
            for i in range(3):
                if i < len(top3):
                    cells.append(format_cell(i, top3[i], second_price))
                else:
                    cells.append("")

            html += f"""
                <tr>
                    <td><strong>{article}</strong></td>
                    <td>{cells[0]}</td>
                    <td>{cells[1]}</td>
                    <td>{cells[2]}</td>
                </tr>
            """
        html += """
            </table>
        """

    html += """
    </body>
    </html>
    """
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(html)

    return html

if __name__ == "__main__":
    config = load_config("kaufda.yaml")
    publishers = config.get("publishers")

    results_by_category = {}
    for category, items in config.get("articles", {}).items():
        print("")
        print(f"# {category}")

        all_results = []
        if items:
            for item in items:
                if isinstance(item, dict):
                    search_requests = SearchRequest.from_config(config=item)
                    for req in search_requests:
                        results = run_search_req(req, publishers)
                        all_results.extend(results)

                elif isinstance(item, str):
                    results = run_search_req(SearchRequest(name=item, match_any=[item], match_none=None, multisearch=[item]), publishers)
                    all_results.extend(results)
                else:
                    print(f"Unsupported item type: {item.__class__} in category '{category}'")
            results_by_category[category] = all_results


    generate_html_table(outfile=f"target/einkaufsuebersicht.html", results_by_category=results_by_category)

    exit(0)



