# PH Daily Commodity Prices — Technical Documentation

A zero-backend static site showing official Philippine Department of Agriculture (DA) commodity prices.

> **Note (current scope):** The supermarket (WalterMart) price-comparison layer
> has been **removed** to keep the data safe and accurate — the site now shows
> only the official DA figures. Sections below that describe the WalterMart
> Freshop API, store price normalization, and the `store_*` / `diff_pct` fields
> are retained for historical reference only and no longer reflect the code.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [File Structure](#2-file-structure)
3. [prices.json — The Data Layer](#3-pricesjson--the-data-layer)
4. [index.html — The Frontend](#4-indexhtml--the-frontend)
5. [update.py — The Scraper](#5-updatepy--the-scraper)
6. [update.yml — GitHub Actions Cron](#6-updateyml--github-actions-cron)
7. [Discovering the WalterMart API](#7-discovering-the-waltermart-api)
8. [Price Normalization Logic](#8-price-normalization-logic)
9. [DA Data Accuracy Notes](#9-da-data-accuracy-notes)

---

## 1. Project Overview

The site answers one question: **"What are today's official market prices for basic goods in the Philippines?"**

The DA (Department of Agriculture) publishes a Weekly Average Retail Prices bulletin as a PDF on their price monitoring page. This project:

1. Downloads that PDF automatically every morning via GitHub Actions
2. Parses the price tables with `pdfplumber`
3. Looks up the same products at WalterMart via their public API
4. Writes everything into `prices.json`
5. Serves it as a static HTML page — no server, no database, zero hosting cost

```
GitHub Actions (5 AM PHT daily)
    │
    ├─→ fetch DA listing page
    ├─→ download latest PDF
    ├─→ parse prices with pdfplumber
    ├─→ query WalterMart API for each product
    └─→ write prices.json → auto-deploy via GitHub Pages

User opens index.html
    └─→ fetch('prices.json') → render table → done
```

---

## 2. File Structure

```
Price Checker/
├── index.html              The page users see. Single file, no build step.
├── prices.json             Today's price data. Rewritten daily by update.py.
├── update.py               Scraper: DA PDF parser + WalterMart price fetcher.
├── requirements.txt        Python dependencies: pdfplumber, requests.
├── README.md               Quickstart guide.
├── DOCUMENTATION.md        This file.
└── .github/
    └── workflows/
        └── update.yml      GitHub Actions: runs update.py every morning.
```

---

## 3. prices.json — The Data Layer

This is the only file that changes. `index.html` reads it on load via `fetch()`. `update.py` rewrites it every morning.

### Schema

```json
{
  "date": "2026-06-08",
  "source": "DA Weekly Average Prices",
  "source_url": "https://www.da.gov.ph/price-monitoring/",
  "store_name": "WalterMart",
  "store_url": "https://www.waltermartdelivery.com.ph/",
  "updated_at": "2026-06-08T05:00:00+08:00",
  "items": [
    {
      "category": "Rice",
      "product": "Regular Milled Rice",
      "price": 45.00,
      "unit": "kg",
      "store_name": "WalterMart",
      "store_product": "Rej Regular Milled Rice | 5kg",
      "store_price": 48.00,
      "diff_pct": 6.7
    }
  ]
}
```

### Field reference

| Field | Always present | Description |
|---|---|---|
| `category` | Yes | Rice, Pork, Beef, Chicken, Fish, Vegetables, Fruits, Eggs |
| `product` | Yes | DA official product name |
| `price` | Yes | DA official price (₱) |
| `unit` | Yes | `kg` or `piece` |
| `store_name` | No | "WalterMart" when a match was found |
| `store_product` | No | Exact product name as listed on WalterMart |
| `store_price` | No | WalterMart price, normalized to the same unit as DA |
| `diff_pct` | No | `(store_price - da_price) / da_price × 100` |

Items without a WalterMart match (like Special Rice) have only the first four fields. The frontend handles both cases.

### Why prices.json and not a database

- GitHub Pages hosts only static files — no server-side code allowed
- A JSON file committed to the repo is versioned, diffable, and free
- The GitHub Actions workflow writes the file and pushes the commit, triggering an automatic redeploy

---

## 4. index.html — The Frontend

A single HTML file using Tailwind CSS (Play CDN — no build step) and plain vanilla JavaScript.

### How data loads

```javascript
fetch('prices.json')
  .then(r => r.json())
  .then(data => render(data))
```

The page shows a loading skeleton while the fetch runs, a `role="alert"` error message if it fails, and an empty-state message if search filters return nothing.

### How the table renders

Items are grouped by category. Each category becomes a `<section>` with an `<h2>` heading and a `<table>`. The script detects whether any item has a `store_price` field — if yes, it renders five columns (Product, DA Price, Unit, WalterMart, vs DA); if no, three columns (DA only).

```javascript
let hasStoreData = data.items.some(i => i.store_price !== undefined);
```

The WalterMart price cell has a `title` tooltip showing the exact product name from `store_product`, so users can see what specific item was matched (e.g., "Fishta Marinated Boneless Bangus Vacuum Pack | kg").

### Price difference coloring

```javascript
function diffClass(diff) {
    if (diff > 10)  return 'text-red-600 font-semibold';   // >10% markup → red
    if (diff < 0)   return 'text-green-600';                // cheaper → green
    return 'text-slate-500';                                // within 10% → neutral
}
```

The 10% threshold mirrors DA's own price alert guidelines — prices more than 10% above DA baseline are flagged as overpriced.

### Search and category filter

Both filters apply simultaneously. Search matches against `product` and `category` (case-insensitive). Category chips filter to show only that category's section. The item count updates live to show "showing X of Y items".

### Light/dark mode

```javascript
// On first load: check localStorage, fall back to OS preference
const saved = localStorage.getItem('ph-prices-theme');
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
if (saved === 'dark' || (!saved && prefersDark)) {
    document.documentElement.classList.add('dark');
}
```

The toggle button flips the `dark` class on `<html>` and saves the choice to `localStorage` so it persists across visits.

### Mobile layout

On screens narrower than 640px, the `<table>` transforms into stacked cards using only CSS — no duplicated HTML:

```css
@media (max-width: 639px) {
    td::before {
        content: attr(data-label);
        font-weight: 600;
    }
}
```

Each `<td>` has a `data-label` attribute (e.g., `data-label="DA Price"`) set in the JavaScript when building the table. On mobile the `::before` pseudo-element renders that label inline, turning each row into a labeled card.

---

## 5. update.py — The Scraper

Runs once daily. Handles the full pipeline: DA PDF → parse → WalterMart API → write.

### Command-line flags

```bash
python update.py              # full run: DA + WalterMart → writes prices.json
python update.py --dry-run    # prints parsed JSON, does NOT write the file
python update.py --skip-store # DA prices only, skips WalterMart API calls
```

### Step 1 — Find the DA PDF link

```python
def find_today_pdf(session):
    resp = session.get("https://www.da.gov.ph/price-monitoring/")
    # Searches HTML for PDF links matching patterns like:
    # "daily-price-index*.pdf", "dpi*.pdf", or any *.pdf as fallback
    ...
```

The DA listing page has links to the latest weekly and daily PDFs. The function tries four progressively broader regex patterns so it still works even if the DA renames their files.

### Step 2 — Download the PDF

Downloaded into a `tempfile` so it is automatically cleaned up regardless of whether parsing succeeds or fails. The temp file is deleted in a `finally` block.

### Step 3 — Parse with pdfplumber

```python
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        ...
```

`pdfplumber` reads the PDF's embedded table structures directly — it does not do OCR. It returns each table as a list of rows, where each row is a list of cell strings.

The parser looks for a header row containing the word "price" to identify which column holds prices, then reads every subsequent row as a product entry. Category header rows (e.g., "FISH PRODUCTS", "BEEF PRODUCTS") are detected and tracked so each item gets the right category label.

### Step 4 — Fetch WalterMart prices

For each DA product that has an entry in `PRODUCT_MAPPING`, the script queries the WalterMart API (see Section 7 for how this API was found). A 0.5-second delay is added between requests to be respectful of their servers.

### Step 5 — Atomic file write

```python
tmp_out = OUTPUT_FILE.with_suffix(".json.tmp")
tmp_out.write_text(json.dumps(payload, ...), encoding="utf-8")
tmp_out.replace(OUTPUT_FILE)   # atomic rename
```

Writing to a `.tmp` file first and then renaming means a user can never load a half-written `prices.json`. If the script crashes mid-write, the old file is untouched.

### Step 6 — Failure behavior

If the DA PDF cannot be found or parsed, the script exits with a non-zero code and **does not touch `prices.json`**. The site keeps showing the last successful data rather than going blank.

---

## 6. update.yml — GitHub Actions Cron

```yaml
on:
  schedule:
    - cron: "0 21 * * *"   # 21:00 UTC = 05:00 PHT next day
  workflow_dispatch:         # can also be triggered manually
```

The cron runs at 21:00 UTC which is 5:00 AM Philippine Time (UTC+8). The DA typically publishes their weekly bulletin by early morning, so this timing catches the latest data.

### Commit guard

```bash
if git diff --quiet prices.json; then
    echo "prices.json unchanged — nothing to commit."
else
    git add prices.json
    git commit -m "chore: update prices $(date -u +%Y-%m-%d) (DA + WalterMart)"
    git push
fi
```

The workflow only commits if `prices.json` actually changed. This prevents empty commits on days when DA prices are unchanged, keeping the git history clean.

### Permissions

```yaml
permissions:
  contents: write
```

Required so the workflow can push the updated `prices.json` back to the repository. Uses the built-in `GITHUB_TOKEN` — no personal access token needed.

---

## 7. Discovering the WalterMart API

This section documents exactly how the WalterMart product API was found and how the correct query parameters were identified.

### Why WalterMart

The original plan was to use GoRobinsons (Robinsons Supermarket). That was abandoned because GoRobinsons' website returned an SSL handshake failure (`SEC_E_ILLEGAL_MESSAGE`) on every request — a sign of Cloudflare bot protection that terminates non-browser TLS connections before serving any content.

WalterMart was chosen as the alternative for two reasons discovered during investigation:
1. Their delivery site (`waltermartdelivery.com.ph`) runs on the **Freshop** platform — a grocery e-commerce SaaS that uses a public REST API
2. Their Freshop initialization code explicitly sets **`allow_bots: true`**, which is a Freshop configuration option that permits non-browser access

### Step 1 — Reading WalterMart's page source

Opening `view-source:https://www.waltermartdelivery.com.ph/` and searching for "api" or "freshop" revealed a `<script>` tag loading Freshop's JS:

```html
<script src="https://asset.freshop.ncrcloud.com/...freshop.js"></script>
```

This identified that the platform is **Freshop by NCR Cloud** and that the assets are served from `asset.freshop.ncrcloud.com`.

### Step 2 — Finding the API domain from the Freshop JS

Fetching `freshop.js` and searching its source for "api" revealed the actual REST API base URL:

```
https://api.freshop.ncrcloud.com/1/
```

The subdomain is different from the asset CDN: `api.freshop.ncrcloud.com` (not `asset`).

### Step 3 — Finding the app_key

Inside the same `freshop.js` initialization block, the WalterMart-specific configuration was embedded:

```javascript
Freshop.init({
    app_key: "walter_mart",
    allow_bots: true,
    ...
})
```

`app_key` is the identifier that tells the Freshop API which store's catalog to query. `allow_bots: true` is an explicit opt-in that permits programmatic access. This is not a secret key — it is publicly embedded in the page's JavaScript that every browser downloads.

### Step 4 — Identifying the correct search parameter

This was the most important discovery. The Freshop products endpoint accepts search queries, but the parameter name is not obvious.

**Wrong parameter — `search=`:**

```
GET /1/products?app_key=walter_mart&search=bangus
```

This returned **14,097 results** — essentially the entire product catalog. The `search=` parameter does not filter by name; it appears to be ignored or treated as a non-filtering parameter.

**Correct parameter — `q=`:**

```
GET /1/products?app_key=walter_mart&q=bangus
```

This returned **72 results** — all bangus-related products. The `q=` parameter is the actual full-text search field for product names.

The correct parameter was found by reading the Freshop JS source more carefully and looking for where it constructs search requests internally. The relevant line showed `params.q = searchTerm` rather than `params.search`.

### Final API call

```python
resp = session.get(
    "https://api.freshop.ncrcloud.com/1/products",
    params={
        "app_key": "walter_mart",
        "limit": 10,
        "q": query,          # ← correct search param
    }
)
products = resp.json().get("items", [])
```

### Response structure

Each product in the `items` array has:

```json
{
  "name": "W Prime Meats Beef Brisket | kg",
  "size": "kg",
  "unit_price": 470.0,
  "status": "available"
}
```

Key fields used:
- `name` — shown as a tooltip on the WalterMart price cell
- `size` — `"kg"` means it's already a per-kg price; `"pc"` or `"pack"` means it needs normalization
- `unit_price` — the shelf price
- `status` — only `"available"` products are used; out-of-stock items are skipped

---

## 8. Price Normalization Logic

Not all WalterMart products are sold the same way DA tracks them. Two common cases required normalization:

### Rice — sold in 5 kg bags

DA tracks rice per kg. WalterMart sells rice in 5 kg bags with a single pack price. The product name contains "5kg":

```
"Rej Regular Milled Rice | 5kg"  →  size: "pc",  unit_price: 240.00
```

```python
def _extract_kg_weight(name):
    # Finds "5kg" in the name → returns 5.0
    m = re.search(r"(\d+\.?\d*)\s*kg", name, re.IGNORECASE)
    return float(m.group(1)) if m else None

# ₱240.00 ÷ 5 kg = ₱48.00 per kg
per_kg = unit_price / kg_weight
```

### Eggs — sold in 12-packs

DA tracks eggs per piece. WalterMart sells eggs in cartons of 12 labeled "12s":

```
"EggSakto Medium Eggs | 12s"  →  size: "pc",  unit_price: 118.00
```

```python
def _extract_piece_count(name):
    # Finds "12s" in the name → returns 12
    m = re.search(r"(\d+)\s*(?:s\b|pcs?\.?|pieces?)", name, re.IGNORECASE)
    return int(m.group(1)) if m else None

# ₱118.00 ÷ 12 pieces = ₱9.83 per piece
per_piece = unit_price / piece_count
```

---

## 9. DA Data Accuracy Notes

All prices in `prices.json` are sourced from the **DA Weekly Average Retail Price** bulletin. The bulletin is the authoritative source, but it has specific constraints:

### What DA tracks

The DA bulletin tracks a fixed list of commodities that are politically and nutritionally significant as basic food items. It does **not** track every item sold in a market.

### What is NOT tracked by DA

Several items that might seem obvious are absent from the DA bulletin:

| Item | Why it's not included |
|---|---|
| Pork Liver, Beef Liver | DA only tracks main cuts: Kasim, Liempo, Brisket, Rump |
| Chicken Breast, Chicken Thigh | DA only tracks Whole Chicken |
| Kangkong | Not in the lowland vegetables list (DA tracks Ampalaya, Chilli, Eggplant, Native Pechay, Pole Sitao, Squash, Tomato) |
| Duck Eggs | Only chicken eggs (white and brown, multiple sizes) are tracked |

All items currently in `prices.json` and `PRODUCT_MAPPING` have been verified against the live DA bulletin.

### Beef cuts specifically

The DA bulletin lists beef under "BEEF PRODUCTS" but groups pork and chicken in the same section. Exactly two beef cuts are tracked:

- **Beef Brisket, Local** — Meat with Bones
- **Beef Rump, Local** — Lean Meat / Tapadera (also called Camto in local markets)

The older DA daily price index (a separate document from the weekly bulletin) used to track additional cuts including Kalamnan and Biyaya/Kenchi. These were removed from the current weekly bulletin, which is why they were replaced in this project.

### Seed data vs scraped data

`prices.json` was initially seeded with estimated prices so the site would render before `update.py` was run for the first time. After the first successful GitHub Actions run, all prices are replaced with live scraped values. The estimates are no longer used once the automation is running.

---

*Data source: [DA Agribusiness and Marketing Assistance Service (DA-AMAS)](https://www.da.gov.ph/price-monitoring/)*
*Store comparison: [WalterMart Delivery](https://www.waltermartdelivery.com.ph/) via Freshop NCR Cloud API*
