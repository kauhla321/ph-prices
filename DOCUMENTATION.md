# PH Daily Commodity Prices — Technical Documentation

A zero-backend static site showing today's official Philippine Department of
Agriculture (DA) commodity prices. It shows only the authoritative DA figures —
no third-party or supermarket prices — to keep the data safe and accurate.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [File Structure](#2-file-structure)
3. [prices.json — The Data Layer](#3-pricesjson--the-data-layer)
4. [index.html — The Frontend](#4-indexhtml--the-frontend)
5. [update.py — The Scraper](#5-updatepy--the-scraper)
6. [update.yml — GitHub Actions Cron](#6-updateyml--github-actions-cron)
7. [Categorization & Parsing Notes](#7-categorization--parsing-notes)
8. [DA Data Notes](#8-da-data-notes)

---

## 1. Project Overview

The site answers one question: **"What are today's official market prices for
basic goods in the Philippines?"**

The DA publishes a weekly average retail price bulletin as a PDF on their price
monitoring page. This project:

1. Downloads that PDF automatically every morning via GitHub Actions
2. Parses the price tables with `pdfplumber`
3. Writes the results into `prices.json`
4. Serves it as a static HTML page — no server, no database, zero hosting cost

```
GitHub Actions (5 AM PHT daily)
    │
    ├─→ fetch DA listing page
    ├─→ download latest PDF
    ├─→ parse prices with pdfplumber
    └─→ write prices.json → auto-deploy via GitHub Pages

User opens index.html
    └─→ fetch('prices.json') → render table → done
```

---

## 2. File Structure

```
ph-prices/
├── index.html              The page users see. Single file, no build step.
├── prices.json             Today's price data. Rewritten daily by update.py.
├── update.py               Scraper: downloads and parses the DA PDF.
├── requirements.txt        Python dependencies: pdfplumber, requests.
├── README.md               Quickstart guide.
├── DOCUMENTATION.md        This file.
└── .github/
    └── workflows/
        └── update.yml      GitHub Actions: runs update.py every morning.
```

---

## 3. prices.json — The Data Layer

This is the only file that changes. `index.html` reads it on load via `fetch()`.
`update.py` rewrites it every morning.

### Schema

```json
{
  "date": "2026-06-08",
  "source": "DA Daily Price Index",
  "source_url": "https://www.da.gov.ph/price-monitoring/",
  "updated_at": "2026-06-08T05:00:00+08:00",
  "items": [
    {
      "category": "Rice",
      "product": "Regular Milled",
      "price": 45.00,
      "unit": "kg"
    }
  ]
}
```

### Field reference

| Field | Description |
|---|---|
| `date` | Date the prices apply to (`YYYY-MM-DD`). |
| `source` | Human-readable name of the source bulletin. |
| `source_url` | Link to the DA price-monitoring page. |
| `updated_at` | ISO timestamp of when the file was generated. |
| `items[].category` | One of: Rice, Corn, Fish, Beef, Pork, Chicken, Eggs, Vegetables, Fruits, Sugar, Salt, Cooking Oil. |
| `items[].product` | DA official product name. |
| `items[].price` | DA official price (₱). |
| `items[].unit` | `kg` or `piece`. |

Every item has exactly these four fields.

### Why prices.json and not a database

- GitHub Pages hosts only static files — no server-side code allowed
- A JSON file committed to the repo is versioned, diffable, and free
- The GitHub Actions workflow writes the file and pushes the commit, triggering an automatic redeploy

---

## 4. index.html — The Frontend

A single HTML file using Tailwind CSS (Play CDN — no build step) and plain
vanilla JavaScript. It uses a fixed dark theme.

### How data loads

```javascript
const res  = await fetch('prices.json');
const data = await res.json();
allItems   = Array.isArray(data.items) ? data.items : [];
```

The page shows a loading skeleton while the fetch runs, a `role="alert"` error
message with a **Try again** button if it fails, and an empty-state message with
a **Clear search** button if the active filters return nothing.

### How the table renders

Items are grouped by category. Each category becomes a `<section>` with an `<h2>`
heading and a three-column `<table>`: **Product · Price · Unit**. The heading
shows the item count for that category.

### Search and category filter

Both filters apply simultaneously. Search matches against `product` and
`category` (case-insensitive). Category chips filter to a single category's
section. The item count updates live (e.g. "Showing 8 of 95").

### Mobile layout

On screens narrower than 640px, the `<table>` transforms into stacked cards
using only CSS — no duplicated HTML:

```css
@media (max-width: 639px) {
    td::before {
        content: attr(data-label);
        font-weight: 600;
    }
}
```

Each `<td>` has a `data-label` attribute (e.g. `data-label="Price"`) set in the
JavaScript when building the table. On mobile the `::before` pseudo-element
renders that label inline, turning each row into a labeled card.

---

## 5. update.py — The Scraper

Runs once daily. Handles the full pipeline: find PDF → download → parse → write.

### Command-line flags

```bash
python update.py              # full run: writes prices.json
python update.py --dry-run    # prints parsed JSON, does NOT write the file
python update.py --debug      # dumps the raw PDF table/text structure and exits
```

### Step 1 — Find the DA PDF link

```python
def find_today_pdf(session):
    resp = session.get("https://www.da.gov.ph/price-monitoring/")
    # Tries progressively broader regex patterns, preferring the weekly
    # average bulletin, then the daily price index, then any *.pdf.
    ...
```

The function tries several regex patterns so it still works even if the DA
renames their files.

### Step 2 — Download the PDF

Downloaded into a `tempfile` so it is automatically cleaned up regardless of
whether parsing succeeds or fails. The temp file is deleted in a `finally` block.

### Step 3 — Parse with pdfplumber

```python
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        ...
```

`pdfplumber` reads the PDF's embedded table structures directly — it does not do
OCR. It returns each table as a list of rows, where each row is a list of cell
strings. The parser looks for a header row containing the word "price" to locate
the price column, then reads every subsequent multi-column row as a product
entry. Single-cell rows (e.g. "FISH PRODUCTS") are treated as section markers.
See [Section 7](#7-categorization--parsing-notes) for how the category is then
assigned.

### Step 4 — Atomic file write

```python
tmp_out = OUTPUT_FILE.with_suffix(".json.tmp")
tmp_out.write_text(json.dumps(payload, ...), encoding="utf-8")
tmp_out.replace(OUTPUT_FILE)   # atomic rename
```

Writing to a `.tmp` file first and then renaming means a user can never load a
half-written `prices.json`. If the script crashes mid-write, the old file is
untouched.

### Step 5 — Failure behavior

If the DA PDF cannot be found or parses to zero items, the script exits with a
non-zero code and **does not touch `prices.json`**. The site keeps showing the
last successful data rather than going blank.

---

## 6. update.yml — GitHub Actions Cron

```yaml
on:
  schedule:
    - cron: "0 21 * * *"   # 21:00 UTC = 05:00 PHT next day
  workflow_dispatch:         # can also be triggered manually
```

The cron runs at 21:00 UTC which is 5:00 AM Philippine Time (UTC+8).

### Commit guard

```bash
if git diff --quiet prices.json; then
    echo "prices.json unchanged — nothing to commit."
else
    git add prices.json
    git commit -m "chore: update DA prices $(date -u +%Y-%m-%d)"
    git push
fi
```

The workflow only commits if `prices.json` actually changed, which keeps the git
history clean on days when prices are unchanged.

### Permissions

```yaml
permissions:
  contents: write
```

Required so the workflow can push the updated `prices.json` back to the
repository. Uses the built-in `GITHUB_TOKEN` — no personal access token needed.

---

## 7. Categorization & Parsing Notes

The DA weekly bulletin is a single large table. Its section headers
(`BEEF PRODUCTS`, `CORN PRODUCTS`, `LOWLAND VEGETABLES`, …) appear as single-cell
rows, but they are unreliable to use as the source of truth for categories — the
bulletin groups pork, chicken, and eggs under the same livestock header, and a
header occasionally gets merged into a data row, which would make the previous
category "stick" and mislabel everything after it.

So instead of trusting the header, `update.py` re-derives each item's category
from its **product name** using an ordered keyword table
(`PRODUCT_CATEGORY_RULES`), falling back to the header category only when no rule
matches:

```python
def classify_product_category(name, fallback=None):
    t = name.lower()
    for category, keywords in PRODUCT_CATEGORY_RULES:
        if any(kw in t for kw in keywords):
            return category
    return fallback
```

Ordering matters — for example "eggplant" is checked before the Eggs rule so it
is not mistaken for an egg, and "Chicken Egg" matches Eggs before Chicken.

Product names are also cleaned of trailing footnote/superscript markers
(e.g. `P20 Benteng Bigas Meron Naᵃ` → `…Na`).

If the parser ever needs tuning after a DA layout change, run
`python update.py --debug` to dump exactly how `pdfplumber` sees the PDF (every
table row plus the raw text lines) without writing `prices.json`.

---

## 8. DA Data Notes

All prices come from the **DA Weekly Average Retail Price** bulletin, the
authoritative source. It tracks a fixed list of basic commodities and does not
cover every item sold in a market. Examples of what is **not** tracked:

| Item | Why it's not included |
|---|---|
| Pork Liver, Beef Liver | DA only tracks main cuts (Kasim, Liempo, Brisket, Rump) |
| Chicken Breast, Chicken Thigh | DA only tracks Whole Chicken |
| Duck Eggs | Only chicken eggs (white and brown, multiple sizes) are tracked |

The exact set of commodities varies week to week with what the DA publishes; the
parser adapts to whatever is in that week's bulletin.
