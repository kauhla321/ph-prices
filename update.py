#!/usr/bin/env python3
"""
update.py — Fetch today's DA Daily Price Index + WalterMart store prices.

Writes prices.json with:
  • DA official prices (always present)
  • WalterMart supermarket prices for matched items (added when found)

Usage:
    python update.py              # fetch both sources, write prices.json
    python update.py --dry-run    # print result without writing
    python update.py --skip-store # skip WalterMart (DA prices only)
"""

import argparse
import json
import os
import re
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests

# ── Configuration ─────────────────────────────────────────────────────────────
DA_LISTING_URL      = "https://www.da.gov.ph/price-monitoring/"
WALTER_MART_API     = "https://api.freshop.ncrcloud.com/1/products"
WALTER_MART_APP_KEY = "walter_mart"     # public key in their page source
STORE_NAME          = "WalterMart"
STORE_URL           = "https://www.waltermartdelivery.com.ph/"
OUTPUT_FILE         = Path(__file__).parent / "prices.json"
HEADERS             = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PH-Price-Checker/1.0; "
        "+https://github.com/cjalcazar123/ph-price-site)"
    )
}
STORE_REQUEST_DELAY = 0.5   # seconds between Freshop API calls (be polite)

# ── DA category map ───────────────────────────────────────────────────────────
CATEGORY_MAP = {
    "rice":        "Rice",
    "corn":        "Corn",
    "pork":        "Pork",
    "beef":        "Beef",
    "chicken":     "Chicken",
    "fish":        "Fish",
    "vegetable":   "Vegetables",
    "fruit":       "Fruits",
    "egg":         "Eggs",
    "sugar":       "Sugar",
    "cooking oil": "Cooking Oil",
    "salt":        "Salt",
    "lpg":         "LPG",
}

# ── Product-name → category rules ─────────────────────────────────────────────
# The DA bulletin's section headers are unreliable to parse (they sometimes get
# merged into a data row, which makes the previous category "stick" and mislabel
# every row after it — e.g. vegetables ending up under "Eggs"). These rules
# re-derive the category from the product name itself, which is far more robust.
# Checked in order; the first keyword hit wins. "eggplant" is listed before the
# Eggs rule on purpose so it isn't mistaken for an egg.
PRODUCT_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Vegetables", ("eggplant", "talong")),                       # guard before Eggs
    ("Eggs",       ("egg", "itlog", "balut")),
    ("Rice",       ("rice", "bigas", "milled", "glutinous", "japonica",
                    "jasponica", "basmati", "benteng", "sinandomeng", "dinorado")),
    ("Corn",       ("corn", "mais")),
    ("Fish",       ("bangus", "tilapia", "galunggong", "alumahan", "sardines",
                    "tamban", "squid", "pusit", "tambakol", "tuna", "mackerel",
                    "fish", "isda", "milkfish", "shrimp", "hipon")),
    ("Pork",       ("pork", "kasim", "liempo", "baboy")),
    ("Beef",       ("beef", "brisket", "camto", "baka")),
    ("Chicken",    ("chicken", "manok")),
    ("Fruits",     ("banana", "lakatan", "latundan", "saba", "mango", "papaya",
                    "avocado", "calamansi", "melon", "pomelo", "watermelon",
                    "pineapple", "orange", "apple", "grapes")),
    ("Vegetables", ("pechay", "sitao", "sitaw", "squash", "kalabasa", "tomato",
                    "ampalaya", "pepper", "broccoli", "cauliflower", "cabbage",
                    "carrot", "celery", "chayote", "beans", "habichuelas",
                    "lettuce", "potato", "chilli", "chili", "garlic", "ginger",
                    "onion", "okra", "kangkong", "upo", "patola", "gabi",
                    "camote", "mungbean", "monggo", "mongo", "munggo")),
    ("Sugar",      ("sugar", "asukal")),
    ("Cooking Oil", ("cooking oil", "mantika", "palm oil", "coconut oil")),
    ("Salt",       ("salt", "asin")),
]

# ── WalterMart store matchers ─────────────────────────────────────────────────
# (category, keyword in DA product name, Freshop query, expected unit)
# Matched by substring against the DA product name (scoped to a category so a
# generic keyword like "premium" can't leak across sections), so DA names like
# "Bangus, Large" or "Galunggong, Local" map correctly. Checked in order.
# Rice bags (sold as 5 kg packs) and egg trays (sold as 12s) are normalised to
# per-kg / per-piece automatically by fetch_store_price().
STORE_MATCHERS: list[tuple[str, str, str, str]] = [
    # ── Rice ──
    ("Rice",       "regular milled", "regular milled rice", "kg"),
    ("Rice",       "well milled",    "well milled rice",    "kg"),
    ("Rice",       "premium",        "premium rice",        "kg"),
    ("Rice",       "special",        "special rice",        "kg"),
    # ── Fish ──
    ("Fish",       "bangus",         "bangus",              "kg"),
    ("Fish",       "tilapia",        "tilapia",             "kg"),
    ("Fish",       "galunggong",     "galunggong",          "kg"),
    ("Fish",       "alumahan",       "alumahan mackerel",   "kg"),
    ("Fish",       "tambakol",       "tuna",                "kg"),
    ("Fish",       "squid",          "squid pusit",         "kg"),
    # ── Pork ──
    ("Pork",       "kasim",          "pork kasim",          "kg"),
    ("Pork",       "liempo",         "pork liempo",         "kg"),
    # ── Beef ──
    ("Beef",       "brisket",        "beef brisket",        "kg"),
    ("Beef",       "camto",          "beef camto",          "kg"),
    # ── Chicken ──
    ("Chicken",    "chicken",        "whole chicken",       "kg"),
    # ── Vegetables ──
    ("Vegetables", "ampalaya",       "ampalaya",            "kg"),
    ("Vegetables", "tomato",         "tomato",              "kg"),
    ("Vegetables", "sitao",          "sitaw",               "kg"),
    ("Vegetables", "sitaw",          "sitaw",               "kg"),
    ("Vegetables", "squash",         "squash kalabasa",     "kg"),
    ("Vegetables", "pechay",         "pechay",              "kg"),
    ("Vegetables", "carrot",         "carrots",             "kg"),
    ("Vegetables", "potato",         "potato",              "kg"),
    ("Vegetables", "cabbage",        "cabbage",             "kg"),
    ("Vegetables", "red onion",      "red onion",           "kg"),
    ("Vegetables", "white onion",    "white onion",         "kg"),
    ("Vegetables", "garlic",         "garlic",              "kg"),
    ("Vegetables", "ginger",         "ginger",              "kg"),
    ("Vegetables", "eggplant",       "eggplant talong",     "kg"),
    # ── Fruits ──
    ("Fruits",     "lakatan",        "banana lacatan",      "kg"),
    ("Fruits",     "latundan",       "banana latundan",     "kg"),
    ("Fruits",     "saba",           "banana saba",         "kg"),
    ("Fruits",     "papaya",         "papaya",              "kg"),
    ("Fruits",     "mango",          "mango",               "kg"),
    ("Fruits",     "calamansi",      "calamansi",           "kg"),
    ("Fruits",     "watermelon",     "watermelon",          "kg"),
    ("Fruits",     "melon",          "melon",               "kg"),
    # ── Eggs ──
    ("Eggs",       "egg",            "chicken egg",         "piece"),
]


def classify_product_category(name: str, fallback: str | None = None) -> str | None:
    """Re-derive a product's category from its name; fall back to the header
    category when no rule matches (e.g. an item we don't have a keyword for)."""
    t = name.lower()
    for category, keywords in PRODUCT_CATEGORY_RULES:
        if any(kw in t for kw in keywords):
            return category
    return fallback


# ═══════════════════════════════════════════════════════════════════════════════
#  DA PDF scraper (Phase 1 — unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

def find_today_pdf(session: requests.Session) -> str | None:
    resp = session.get(DA_LISTING_URL, timeout=30)
    resp.raise_for_status()
    html = resp.text
    # Weekly Average Prices preferred — consistent curated commodity list.
    # Falls back to Daily Price Index if weekly is not yet published.
    patterns = [
        r'href=["\']([^"\']*[Ww]eekly[_\-\s]?[Aa]verage[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']*[Ww]eekly[^"\']*[Pp]rice[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']*daily[_\-]?price[_\-]?index[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']*dpi[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']*price[_\-]?index[^"\']*\.pdf)["\']',
        r'href=["\']([^"\']+\.pdf)["\']',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            url = matches[0]
            return url if url.startswith("http") else urljoin(DA_LISTING_URL, url)
    return None


def classify_category(text: str) -> str | None:
    t = text.strip().lower()
    for keyword, display in CATEGORY_MAP.items():
        if keyword in t:
            return display
    return None


def parse_price(text: str) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[₱,\s]", "", str(text))
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def normalize_unit(text: str) -> str:
    if not text:
        return "kg"
    t = text.strip().lower()
    for v in ("kg", "kilo", "kilogram", "per kg", "/kg"):
        if v in t:
            return "kg"
    for v in ("pc", "pcs", "piece", "pieces", "per pc", "/pc"):
        if v in t:
            return "piece"
    for v in ("liter", "litre", "l", "per liter"):
        if v in t:
            return "liter"
    return "kg"


# Footnote/superscript markers the DA bulletin appends to some product names
# (e.g. "P20 Benteng Bigas Meron Naᵃ"). Stripped from the trailing edge only.
_FOOTNOTE_CHARS = "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ⁰¹²³⁴⁵⁶⁷⁸⁹*†‡§¶"


def clean_product_name(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s.rstrip(_FOOTNOTE_CHARS).rstrip()


def _price_col(header_row: list) -> int | None:
    for kw in ("prevailing", "retail", "high", "price"):
        for i, c in enumerate(header_row):
            if c and kw in str(c).lower():
                return i
    return None


def _unit_col(header_row: list) -> int | None:
    for i, c in enumerate(reversed(header_row)):
        if c and "unit" in str(c).lower():
            return len(header_row) - 1 - i
    return None


_MEAT_HINTS = ("pork", "beef", "chicken", "kasim", "liempo", "brisket",
               "rump", "camto", "manok", "baboy", "baka", "egg", "itlog",
               "livestock", "poultry", "carcass", "ham")


def debug_dump_pdf(pdf_path: str) -> None:
    """Read-only diagnostic: dump how pdfplumber sees the PDF so we can find out
    why a commodity section (e.g. livestock/poultry/eggs) is being dropped.
    Prints table structure plus any text lines that mention meat/egg keywords."""
    with pdfplumber.open(pdf_path) as pdf:
        print(f"\n===== PDF DEBUG: {len(pdf.pages)} page(s) =====")
        for pno, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            print(f"\n----- page {pno}: {len(tables)} table(s) -----")
            for tno, table in enumerate(tables):
                rows = table or []
                print(f"  table {tno}: {len(rows)} row(s); first cell of each row:")
                for r in rows:
                    first = next((str(c).strip() for c in r if c and str(c).strip()), "")
                    print(f"      | {first[:60]}")
            # Text fallback: surface any line that looks like a meat/egg entry,
            # which tells us if the section exists in text but not in a table.
            text = page.extract_text() or ""
            hits = [ln.strip() for ln in text.splitlines()
                    if any(h in ln.lower() for h in _MEAT_HINTS)]
            if hits:
                print(f"  text lines mentioning meat/egg ({len(hits)}):")
                for ln in hits:
                    print(f"      » {ln[:90]}")
    print("\n===== END PDF DEBUG =====\n")


def parse_pdf(pdf_path: str) -> list[dict]:
    """
    Parse prices from a DA Daily Price Index PDF.
    Typical layout: category header rows + product rows with
    columns: Product | Low | High | Prevailing | Unit

    Tune _price_col() if the column order differs from the above.
    Run with --dry-run to verify output before writing.
    """
    items: list[dict] = []
    current_category: str | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for table in tables:
                if not table:
                    continue
                price_col = unit_col = None
                data_start = 0
                for i, row in enumerate(table[:2]):
                    if row and any("price" in str(c or "").lower() for c in row):
                        price_col  = _price_col(row)
                        unit_col   = _unit_col(row)
                        data_start = i + 1
                        break
                for row in table[data_start:]:
                    if not row or not any(row):
                        continue
                    cells     = [str(c).strip() if c is not None else "" for c in row]
                    non_empty = [c for c in cells if c]
                    if len(non_empty) <= 1:
                        cat = classify_category(non_empty[0] if non_empty else "")
                        if cat:
                            current_category = cat
                        continue
                    # Weekly bulletin has a blank col 0; commodity is at col 1.
                    # Daily DPI has the commodity at col 0. Try both.
                    product = cells[0] or (cells[1] if len(cells) > 1 else "")
                    if not product or not current_category:
                        continue
                    # NOTE: a multi-column row with a price is always a product,
                    # never a section header. We must NOT reclassify it here — doing
                    # so dropped every commodity whose name contains its own category
                    # keyword (Beef Brisket, Pork Belly, Whole Chicken, Chicken Egg,
                    # Corn, Sugar, Salt, Cooking Oil, Eggplant). Section headers are
                    # handled above via the single-cell-row branch. The correct
                    # category is assigned per-item by classify_product_category().
                    price: float | None = None
                    if price_col is not None and price_col < len(cells):
                        price = parse_price(cells[price_col])
                    if price is None:
                        for cell in reversed(cells[1:-1]):
                            price = parse_price(cell)
                            if price is not None:
                                break
                    unit = "kg"
                    if unit_col is not None and unit_col < len(cells):
                        unit = normalize_unit(cells[unit_col]) or "kg"
                    elif cells:
                        unit = normalize_unit(cells[-1]) or "kg"
                    if product and price is not None:
                        clean_name = clean_product_name(product)
                        # Trust the product name over the (fragile) header category.
                        category = classify_product_category(clean_name, current_category)
                        items.append({
                            "category": category,
                            "product":  clean_name,
                            "price":    price,
                            "unit":     unit,
                        })
    return items


# ═══════════════════════════════════════════════════════════════════════════════
#  WalterMart Freshop price fetcher (Phase 2)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_kg_weight(name: str) -> float | None:
    """Extract pack weight in kg from a product name, e.g. '5kg' → 5.0."""
    m = re.search(r"(\d+\.?\d*)\s*kg", name, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _extract_piece_count(name: str) -> int | None:
    """Extract piece count from a pack name, e.g. '12s' → 12."""
    m = re.search(r"(\d+)\s*(?:s\b|pcs?\.?|pieces?)", name, re.IGNORECASE)
    count = int(m.group(1)) if m else None
    return count if (count and count > 1) else None


def _is_relevant(query: str, name: str) -> bool:
    """Guard against the API returning an unrelated first result: require at
    least one meaningful word from the query to appear in the product name."""
    tokens = [w for w in re.split(r"\W+", query.lower()) if len(w) >= 3]
    if not tokens:
        return True
    n = name.lower()
    return any(tok in n for tok in tokens)


def _absolute_url(url: str | None) -> str | None:
    """Freshop returns full URLs today, but join defensively in case it ever
    returns a bare path."""
    if not url:
        return None
    return url if url.startswith("http") else urljoin(STORE_URL, url)


def fetch_store_price(
    session: requests.Session,
    query: str,
    target_unit: str,
) -> tuple[float | None, str | None, str | None]:
    """
    Search the WalterMart Freshop API for a product and return a normalised
    (price_per_unit, matched_product_name, canonical_url) tuple.

    For kg items: returns price per kg (normalising 5 kg rice bags, etc.).
    For piece items: returns price per piece (normalising 12-egg cartons, etc.).
    Returns (None, None, None) when no suitable match is found.

    API notes:
    - Base: https://api.freshop.ncrcloud.com/1/products
    - app_key is publicly embedded in the WalterMart site JS (allow_bots=true)
    - Crawl-delay in robots.txt applies to their WP site, not this API domain
    - We fetch only ~25 items, once a day → negligible traffic
    """
    # The Freshop API intermittently returns 4xx/5xx under bursty traffic, so
    # retry a couple of times with a short backoff before giving up.
    products = None
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = session.get(
                WALTER_MART_API,
                params={"app_key": WALTER_MART_APP_KEY, "limit": 10, "q": query},
                timeout=15,
            )
            resp.raise_for_status()
            products = resp.json().get("items", [])
            break
        except Exception as exc:           # noqa: BLE001 — network/HTTP/JSON
            last_exc = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if products is None:
        print(f"  [WalterMart] fetch error for '{query}': {last_exc}", file=sys.stderr)
        return None, None, None

    for product in products:
        if product.get("status") != "available":
            continue

        name = product.get("name", "")
        if not _is_relevant(query, name):
            continue

        size          = (product.get("size") or "").lower()
        unit_price    = product.get("unit_price") or product.get("price")
        canonical_url = _absolute_url(product.get("canonical_url"))
        if not unit_price:
            continue

        # ── kg target ──
        if target_unit == "kg":
            if "kg" in size or "kilo" in size:
                return float(unit_price), name, canonical_url
            # Pack sold by piece/pack but weight is in the name (e.g. "5kg bag")
            kg = _extract_kg_weight(name) or _extract_kg_weight(size)
            if kg and kg > 0 and size in ("pc", "pack", "ea", "each", ""):
                return round(float(unit_price) / kg, 2), name, canonical_url

        # ── piece target ──
        elif target_unit == "piece":
            if size in ("pc", "pack", "piece", "ea", "each", ""):
                count = _extract_piece_count(name)
                if count:                              # multi-pack → normalise
                    return round(float(unit_price) / count, 2), name, canonical_url
                return float(unit_price), name, canonical_url  # already per-piece

    return None, None, None


def _find_matcher(item: dict) -> tuple[str, str] | None:
    """Return (query, unit) for the first STORE_MATCHERS rule whose category and
    keyword match this DA item, or None."""
    category = item.get("category")
    name     = item["product"].lower()
    for m_cat, keyword, query, unit in STORE_MATCHERS:
        if m_cat and m_cat != category:
            continue
        if keyword in name:
            return query, unit
    return None


def add_store_prices(
    session: requests.Session,
    items: list[dict],
    verbose: bool = True,
) -> int:
    """
    Iterate over DA items, look up each in WalterMart, and add store fields
    in-place.  Returns the number of items successfully matched. Identical
    queries are cached so we hit the Freshop API at most once per query.
    """
    matched = 0
    cache: dict[tuple[str, str], tuple] = {}
    for item in items:
        mapping = _find_matcher(item)
        if not mapping:
            continue

        query, target_unit = mapping
        cache_key = (query, target_unit)
        if cache_key in cache:
            store_price, store_product, store_url = cache[cache_key]
        else:
            store_price, store_product, store_url = fetch_store_price(session, query, target_unit)
            cache[cache_key] = (store_price, store_product, store_url)
            time.sleep(STORE_REQUEST_DELAY)   # only delay on real API calls

        if store_price is not None:
            diff_pct = round((store_price - item["price"]) / item["price"] * 100, 1)
            item["store_name"]    = STORE_NAME
            item["store_price"]   = store_price
            item["store_product"] = store_product   # the actual WalterMart name
            item["store_url"]     = store_url
            item["diff_pct"]      = diff_pct
            matched += 1
            if verbose:
                flag = "🔴" if diff_pct > 10 else ("🟢" if diff_pct < 0 else "  ")
                print(
                    f"  {flag} {item['product']:<30s} "
                    f"DA ₱{item['price']:>7.2f}  WM ₱{store_price:>7.2f}  "
                    f"({diff_pct:+.1f}%)"
                )
        else:
            if verbose:
                print(f"     {item['product']:<30s} — no WalterMart match")

    return matched


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch DA Daily Price Index and WalterMart prices, write prices.json."
    )
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print result without writing prices.json.")
    parser.add_argument("--skip-store", action="store_true",
                        help="Skip WalterMart fetch (DA prices only).")
    parser.add_argument("--debug", action="store_true",
                        help="Dump raw PDF table/text structure and exit (read-only).")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    # ── Step 1: find today's DA PDF ──
    print("Finding today's DA Daily Price Index PDF…")
    pdf_url = find_today_pdf(session)
    if not pdf_url:
        print("ERROR: No PDF link on the DA listing page.", file=sys.stderr)
        print(f"       Check {DA_LISTING_URL} manually.", file=sys.stderr)
        sys.exit(1)
    print(f"  Found: {pdf_url}")

    # ── Step 2: download the PDF ──
    print("Downloading PDF…")
    try:
        pdf_resp = session.get(pdf_url, timeout=60)
        pdf_resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"ERROR: Download failed — {exc}", file=sys.stderr)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_resp.content)
        tmp_path = tmp.name

    try:
        if args.debug:
            debug_dump_pdf(tmp_path)
            return

        # ── Step 3: parse DA prices ──
        print("Parsing DA PDF tables…")
        items = parse_pdf(tmp_path)
        if not items:
            print(
                "WARNING: No items parsed. Run --dry-run and inspect output.\n"
                "         Existing prices.json has NOT been changed.",
                file=sys.stderr,
            )
            sys.exit(1)
        cats = len({i["category"] for i in items})
        print(f"  Parsed {len(items)} items across {cats} categories.")

        # ── Step 4: add WalterMart store prices ──
        if not args.skip_store:
            print(f"\nFetching WalterMart prices ({len(STORE_MATCHERS)} matchers)…")
            matched = add_store_prices(session, items)
            print(f"\n  Matched {matched} item(s) to WalterMart products.\n")

        # ── Step 5: build JSON payload ──
        today   = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")

        payload = {
            "date":       today,
            "source":     "DA Daily Price Index",
            "source_url": DA_LISTING_URL,
            "store_name": STORE_NAME,
            "store_url":  STORE_URL,
            "updated_at": now_iso,
            "items":      items,
        }

        if args.dry_run:
            print("── Dry-run output (not written) ──")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        # ── Step 6: write atomically ──
        tmp_out = OUTPUT_FILE.with_suffix(".json.tmp")
        tmp_out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_out.replace(OUTPUT_FILE)
        print(f"Written → {OUTPUT_FILE}")

    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    main()
