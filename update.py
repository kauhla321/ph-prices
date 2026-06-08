#!/usr/bin/env python3
"""
update.py — Fetch today's DA (Department of Agriculture) price bulletin and
write prices.json with the official commodity prices.

Usage:
    python update.py              # fetch and write prices.json
    python update.py --dry-run    # print result without writing
    python update.py --debug      # dump raw PDF structure and exit (read-only)
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests

# ── Configuration ─────────────────────────────────────────────────────────────
DA_LISTING_URL = "https://www.da.gov.ph/price-monitoring/"
OUTPUT_FILE    = Path(__file__).parent / "prices.json"
HEADERS        = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PH-Price-Checker/1.0; "
        "+https://github.com/cjalcazar123/ph-price-site)"
    )
}

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
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the DA price bulletin and write prices.json."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print result without writing prices.json.")
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

        # ── Step 4: build JSON payload ──
        today   = datetime.now().strftime("%Y-%m-%d")
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")

        payload = {
            "date":       today,
            "source":     "DA Daily Price Index",
            "source_url": DA_LISTING_URL,
            "updated_at": now_iso,
            "items":      items,
        }

        if args.dry_run:
            print("── Dry-run output (not written) ──")
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        # ── Step 5: write atomically ──
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
