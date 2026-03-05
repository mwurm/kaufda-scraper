import base64
from io import BytesIO

import os
import random
from datetime import datetime, time, timedelta, timezone
from collections import defaultdict
from typing import Iterable
import requests
import time as time_module
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

END_OF_WEEK = datetime.now().date() + timedelta(days=(6 - datetime.now().date().weekday()) % 7)

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
    description: float
    conditions: list[str]

    def price_range_str(self) -> str:
        if self.price_min == self.price_max:
            return f"{self.price_min}€"
        else:
            return f"{self.price_min}€ - {self.price_max}€"

    def normalized_price_by_base_unit(self) -> str | None:
        return normalize_price(self.price_by_base_unit)

    def extract_value_in_price_by_base_unit(self) -> float | None:
        """
        Vereinheitlicht Preisangaben pro kg oder l.

        Beispiele:
        "1 kg = 46.13"        -> 46.13
        "15.98 / kg"          -> 15.98
        "1kg = 11,63–6,20"    -> 6.2 (min value)
        "1 l = 1.80"          -> "1.80"
        """

        text = self.price_by_base_unit
        if not text:
            return None

        # Kommas in Punkte umwandeln
        text = text.replace(",", ".")

        # Zahlen extrahieren
        numbers = re.findall(r"\d+(?:\.\d+)?", text)

        if not numbers:
            return None

        try:
            values = [Decimal(n) for n in numbers]
        except InvalidOperation:
            return None

        # Falls Form wie "1 kg = 46.13" → die "1" ignorieren
        if len(values) >= 2 and values[0] == 1:
            values = values[1:]

        if not values:
            return None

        # Einzelpreis (bei mehreren: Minimum liefern)
        return float(min(values))

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

    def pub_date_strings(self) -> list[str]:
        date_strs = []
        for (start, end) in self.pub_dates:
            date_strs.append(f"{TAGE[start.weekday()]} {start.strftime('%d.%m') if start else '?'} - {TAGE[end.weekday()]} {end.strftime('%d.%m') if end else '?'}")
        return date_strs

    def has_no_deal_in_current_week(self) -> bool:
        today = datetime.now().date()
        if today.weekday() == 6:
            today = today + timedelta(days=1)

        for (start, end) in self.pub_dates:
            if start <= today <= end:
                return False
        return True

    def has_deal_outside_of_full_week(self) -> bool:
        today = datetime.now().date()
        if today.weekday() == 6:
            today = today + timedelta(days=1)

        mon_of_week = today - timedelta(days=(today.weekday()))
        sat_of_week = today + timedelta(days=(5 - today.weekday()) % 7)

        for (start, end) in self.pub_dates:
            if start != mon_of_week or end != sat_of_week:
                return True
        return False


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
            time_module.sleep(.1)  # Rate limiting

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

    if not preffered_publishers or 'aldi süd' in [p.lower() for p in preffered_publishers]:
        aldi_results = search_aldi(search_req)
        found_results.extend(aldi_results)

    seen = set()
    found_results_without_duplicates = []
    for found_result in found_results:
        price_per_kg = extract_normalized_price(found_result)
        result_key = (found_result.publisher_name, price_per_kg[1] if price_per_kg else random.random())
        if result_key not in seen:
            seen.add(result_key)
            found_results_without_duplicates.append(found_result)
    return list(found_results_without_duplicates)


def search_aldi(search_req: SearchRequest) -> List[SearchResult]:
    url = "https://www.kaufda.de/webapp/api/slots/brochureSearch?device=web_browser&query=Aldi%20S%C3%BCd&lat=47.965625499999994&lng=11.753921799999999&projection=web&executeSearchOn=contentSearchApi"
    rsp = requests_cache_session.get(url, headers=REQUEST_HEADERS)
    rsp.raise_for_status()

    (brochure_valid_from, brochure_valid_until) = (None, None)
    brochure_search_groups = rsp.json().get('brochureSearchGroups', [])
    aldi_brochure_id = None
    for group in brochure_search_groups:
        for brochure in group.get('_embedded', {}).get('brochureSearchReferences', []):
            brochure_content = brochure.get('content', {})
            brochure_content_retailer_name = brochure_content.get('retailer', {}).get('name', '')
            if brochure_content_retailer_name and brochure_content_retailer_name.lower() == 'aldi süd':
                aldi_brochure_id = brochure_content.get('contentId')
                brochure_valid_from = parser.parse(brochure.get('content').get('publishedFrom'))
                brochure_valid_until = parser.parse(brochure.get('content').get('validUntil'))
                break

    if aldi_brochure_id is None:
        return None


    brochure_url = f"https://content-viewer-be.kaufda.de/api/v1/brochures/{aldi_brochure_id}/pages?partner=kaufda_web&brochureKey=&lat=47.965625499999994&lng=11.753921799999999"
    rsp = requests_cache_session.get(brochure_url, headers=REQUEST_HEADERS)
    rsp.raise_for_status()

    search_results = []
    aldi_brochure_contents = rsp.json().get('contents', {})
    for content in aldi_brochure_contents:
        offers = content.get('offers', [])
        for offer in offers:
            search_result = extract_content(offer, search_req)
            if search_result:
                if len(search_result.pub_dates) == 0:
                    search_result.pub_dates.append((brochure_valid_from.date(), brochure_valid_until.date()))
                search_results.append(search_result)

    return search_results



def extract_content(result_entry: dict, search_req: SearchRequest) -> SearchResult | None:
    content_object = result_entry.get("content", {})

    publisher_name = content_object.get("publisher", {}).get("name", None)
    if not publisher_name:
        publisher_name = content_object.get("publisherName")
    publisher_name = 'Netto' if publisher_name and publisher_name.lower() == 'netto marken-discount' else publisher_name
    image = content_object.get("image", {})
    image_url = None
    if image and isinstance(image, dict):
        image_url = image.get("url", None)
    elif image and isinstance(image, str):
        image_url = image

    pub_dates = []
    for publicationProfile in content_object.get("publicationProfiles", []):
        start = publicationProfile.get("validity", {}).get("startDate")
        end = publicationProfile.get("validity", {}).get("endDate")

        start_parsed = parser.parse(start) if start else None
        end_parsed = parser.parse(end) if end else None

        cutoff = time(21, 0)

        if start_parsed.time() >= cutoff:
            start_parsed = start_parsed + timedelta(days=1)

        start_date = start_parsed.date()
        # Start = Sonntag? → Montag
        if start_date.weekday() == 6:
            start_date = start_date + timedelta(days=1)

        end_date = end_parsed.date()
        pub_dates.append((start_date, end_date))

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
            if isinstance(category_path, dict):
                category_path = [category_path]
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
                deal_description = deal.get("description", None)
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

                deals_dataobjects.append(Deal(type=deal_type, description=deal_description, price_min=price_min, price_max=price_max, price_by_base_unit=price_by_base_unit, conditions=condition_strings))

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


def extract_base_unit(text: str) -> tuple[float, str]:
    base_unit = (None, None)

    patterns_with_specific_units = [
        # Sonderfälle: kg-Preis immer 1 kg
        r"(kg|liter|l)-Preis",  # "kg-Preis ...

        # 3er-Matches
        r"(\d+)\s*x\s*(\d+)\s*(kg|ml|g|l)",  # "4 x 125g"

        # 2er-Matches
        r"(\d+)\s*(kg|ml|g|l)",  # "100g
        r"(\d+)-(kg|ml|g|l)-P",  # "100-g-Pckg

        # 1er-Matches
        r"/\s*(kg|ml|g|l)",  # "15.23 / kg
        r"\d+(?:[.,]\d+)?\s*(kg|ml|g|l)\s*=",  # "1 kg = ...
        r"pro\s+(kg|liter|l)",  # "pro kg = ...
        r"je\s+(kg|liter|l)",  # "pro kg = ...

    ]

    for pattern in patterns_with_specific_units:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if len(match.groups()) == 1:
                base_unit = (1.0, match.group(1))
                break
            if len(match.groups()) == 2:
                value = float(match.group(1))
                base_unit = match.group(2)
                if base_unit == "g":
                    base_unit = "kg"
                    value = value / 1000.0
                if base_unit == "ml":
                    base_unit = "l"
                    value = value / 1000.0
                base_unit = (value, base_unit)
                break
            if len(match.groups()) == 3:
                multiplier = float(match.group(1))
                value = float(match.group(2))
                base_unit = match.group(3)
                if base_unit == "g":
                    base_unit = "kg"
                    value = value / 1000.0
                if base_unit == "ml":
                    base_unit = "l"
                    value = value / 1000.0
                base_unit = (multiplier * value, base_unit)
                break


    return base_unit if base_unit[1] else (None, None)


def extract_price_of_base_unit(text: str) -> float | None:
    price_of_base_unit = None

    cleaned_text = text

    # bereinige Werte wie '10,- EUR' oder '10.- EUR' zu '10,00 EUR'
    cleaned_text = re.sub(r",-", ",00", cleaned_text, flags=re.IGNORECASE)
    cleaned_text = re.sub(r"\.-", ",00", cleaned_text, flags=re.IGNORECASE)

    # entferne Währungsangaben, um die Extraktion zu erleichtern
    cleaned_text = re.sub(r"EUR|€", "", cleaned_text, flags=re.IGNORECASE)


    patterns = [
        # 1er-Matches
        r"(\d+[.,]\d+(?:[–-]\d+[.,]\d+)?)?\s*/\s*[kg|ml|g|l]",  # "15.23 / kg" or "11,63–6,20 / kg"
        r"[kg|ml|g|l]\s*=\s*(\d+[.,]\d+(?:[–-]\d+[.,]\d+)?)?\s*",
        r"[kg|ml|g|l]-Preis\s*(\d+[.,]\d+(?:[–-]\d+[.,]\d+)?)?\s*",# "kg-Preis 11,63–6,20"
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned_text, re.IGNORECASE)
        if match and match.group(1):
            # Extrahiere alle Zahlen aus dem Match
            numbers = re.findall(r"\d+[.,]\d+", match.group(1))
            if numbers:
                try:
                    values = [float(n.replace(",", ".")) for n in numbers]
                    price_of_base_unit = min(values)
                    break
                except (ValueError, TypeError):
                    continue

    return price_of_base_unit


def extract_normalized_price(result: SearchResult) -> tuple[Deal, float, str]:
    for deal in result.deals:
        base_unit = extract_base_unit(deal.price_by_base_unit)
        if not base_unit[1]:
            continue

        price_of_base_unit = extract_price_of_base_unit(deal.price_by_base_unit)
        if price_of_base_unit is not None:
            return (deal, price_of_base_unit / base_unit[0], base_unit[1])

    # Keine Base-Unit in Deals gefunden? In description suchen und mit min_price kombinieren
    base_unit = extract_base_unit(result.description)

    priority_types = ['SALES_PRICE', 'SPECIAL_PRICE']

    best_deal_guess = sorted(result.deals, key=lambda d: 0 if d.type in priority_types else 1)[0] if result.deals else None

    if base_unit[1]:
        price_of_base_unit = extract_price_of_base_unit(result.description)
        if price_of_base_unit is not None:
            return (best_deal_guess, price_of_base_unit / base_unit[0], base_unit[1])
        else:
            price_of_base_unit = min([deal.price_min for deal in result.deals])
            return (best_deal_guess, price_of_base_unit / base_unit[0], base_unit[1])

    return (best_deal_guess, float('inf'), "?")

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
        if r.has_no_deal_in_current_week():
            continue
        normalized_price = extract_normalized_price(r)

        grouped[r.article].append({
            "store": r.publisher_name,
            "price": normalized_price[1],
            "normalized_price_with_unit_tuple": normalized_price,
            "image": r.image_url,
            "badges": detect_badges(r),
            "result_object": r
        })

    return grouped


def format_cell(rank: int, entry: dict, second_price: float | None):
    medals = ["🥇", "🥈", "🥉"]
    medal = medals[rank]

    price = entry["price"]
    normalized_price_with_unit_tuple = entry["normalized_price_with_unit_tuple"]
    store = entry["store"]
    badges = entry["badges"]
    image = entry["image"] or ""
    result : SearchResult = entry["result_object"]


    extra = ""
    if rank == 0 and second_price:
        diff_ratio = (second_price - price) / second_price
        if diff_ratio > 0.20:
            extra = " 🔥"
        elif diff_ratio < 0.05:
            extra = " ⚖️"

    badge_str = f" {badges}" if badges else ""


    img_html = f'<img src="{image}" style="width:160px;border-radius:8px;"><br>'


    pub_dates_html_lines = "📆" + "<br>".join(result.pub_date_strings()) if result.has_deal_outside_of_full_week() else None

    return f"""
        <div style="text-align:center;">
            {img_html}
            <strong>{medal} {store}</strong><br>
            {normalized_price_with_unit_tuple[1]:.2f}€/{normalized_price_with_unit_tuple[2]}{extra}{badge_str}
            {"<br>" + pub_dates_html_lines if pub_dates_html_lines else ""}
            {"<br>" + normalized_price_with_unit_tuple[0].description if normalized_price_with_unit_tuple[0] else ""}
        </div>
    """


def generate_html_table(outfile: str, results_by_category: dict) -> str:

    html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <script>
            function filterTable() {{
                const input = document.getElementById("search");
                const filter = input.value.toLowerCase();
                const rows = document.querySelectorAll("table tr");
    
                rows.forEach((row, index) => {{
                    if (index === 0) return; // Tabellenheader immer sichtbar
    
                    const text = row.innerText.toLowerCase();
                    if (text.includes(filter)) {{
                        row.style.display = "";
                    }} else {{
                        row.style.display = "none";
                    }}
                }});
            }}
        </script>
    </head>
    <body style="font-family:Arial;">
        <h2>🛒 Einkaufsübersicht</h2>
        Für aktuelle Woche bis {TAGE[END_OF_WEEK.weekday()]} {END_OF_WEEK.strftime('%d.%m')}
        <br><br>
        <input 
            type="text" 
            id="search" 
            onkeyup="filterTable()" 
            placeholder="🔍 Produkt oder Markt suchen..."
            style="padding:8px;width:300px;font-size:16px;"
        >
        
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


    generate_html_table(outfile=f"docs/index.html", results_by_category=results_by_category)

    exit(0)



