# PH Daily Commodity Prices

A zero-backend static site that shows today's official Philippine commodity prices from the [Department of Agriculture Daily Price Index](https://www.da.gov.ph/price-monitoring/).

## How it works

```
Every morning:  update.py downloads the DA Daily Price Index PDF,
                parses the price tables, writes prices.json

The page:       index.html fetches prices.json and renders the data —
                no server, no database, $0 cost
```

## Local preview

```bash
# Install Python deps
pip install -r requirements.txt

# Dry-run: fetch today's PDF and print parsed data without writing
python update.py --dry-run

# Full run: fetch and write prices.json
python update.py

# Serve the page (fetch() requires http://, not file://)
python -m http.server 8000
# → open http://localhost:8000
```

> **Note:** The PDF parser targets the most common DA DPI table layout.
> Run `python update.py --dry-run` first to verify parsed output.
> If parsing returns no items, inspect the PDF and adjust `parse_pdf()` in `update.py`.

## Deploy

**GitHub Pages** (recommended for free static hosting):
1. Push the repo to GitHub.
2. Settings → Pages → Source: `main` branch, `/ (root)`.
3. Enable the Actions workflow — it runs daily at 5 AM PHT and pushes updated `prices.json`.

**Vercel:**
1. Import the repo. Framework Preset: **Other**. Output Directory: `.` (root).
2. Keep using GitHub Actions to push `prices.json` daily (Vercel auto-deploys on each push).

## Files

```
├── index.html              the page users see
├── prices.json             today's data (rewritten daily by update.py)
├── update.py               daily DA PDF parser
├── requirements.txt        pdfplumber, requests
└── .github/
    └── workflows/
        └── update.yml      GitHub Actions daily cron
```

## Supermarket price comparison

`update.py` also looks up matched commodities on **WalterMart** (via the public
Freshop API) and adds `store_price`, `store_product`, `store_url`, and `diff_pct`
to each matched item. The page renders these as an extra column and links the
store price straight to the actual product page on WalterMart.

- Matching is keyword + category based (see `STORE_MATCHERS` in `update.py`), so
  DA names like `Bangus, Large` or `Galunggong, Local` map correctly.
- Categories are re-derived from the product name (`PRODUCT_CATEGORY_RULES`),
  which is more reliable than the DA bulletin's section headers.
- Run `python update.py --skip-store` to fetch DA prices only.
