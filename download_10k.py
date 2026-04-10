"""
download_10k.py
===============
Download 10-K Risk Factor sections (Item 1A) from SEC EDGAR for ~30
trade-exposed firms (manufacturing, retail, tech hardware), 2022–2025.

Pipeline
--------
1. Resolve ticker → CIK via EDGAR company_tickers.json
2. Pull each company's filing history from the submissions API
3. Filter for 10-K filings with filingDate in [2022-01-01, 2025-12-31]
4. Download the primary .htm document for each filing
5. Extract the Item 1A "Risk Factors" section
6. Save a row to data/risk_factors.csv

Outputs
-------
  data/10k_raw/           raw HTML files (skipped if already present → resume-safe)
  data/risk_factors.csv   one row per filing: ticker, fiscal_year, item_1a_text, ...

Rate limiting
-------------
EDGAR allows max 10 req/s.  We target ~8 req/s (sleep 0.13 s between calls).

Usage
-----
  py download_10k.py
  py download_10k.py --tickers AAPL NKE CAT   # subset
"""

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Company universe  (~30 trade-exposed firms)
# ---------------------------------------------------------------------------

COMPANIES = [
    # --- Manufacturing ---
    {"ticker": "CAT",  "sector": "Manufacturing",  "name": "Caterpillar"},
    {"ticker": "DE",   "sector": "Manufacturing",  "name": "Deere & Company"},
    {"ticker": "F",    "sector": "Manufacturing",  "name": "Ford Motor"},
    {"ticker": "MMM",  "sector": "Manufacturing",  "name": "3M"},
    {"ticker": "HON",  "sector": "Manufacturing",  "name": "Honeywell"},
    {"ticker": "EMR",  "sector": "Manufacturing",  "name": "Emerson Electric"},
    {"ticker": "PH",   "sector": "Manufacturing",  "name": "Parker Hannifin"},
    {"ticker": "ITW",  "sector": "Manufacturing",  "name": "Illinois Tool Works"},
    {"ticker": "WHR",  "sector": "Manufacturing",  "name": "Whirlpool"},
    {"ticker": "SWK",  "sector": "Manufacturing",  "name": "Stanley Black & Decker"},
    # --- Tech Hardware ---
    {"ticker": "AAPL", "sector": "Tech Hardware",  "name": "Apple"},
    {"ticker": "INTC", "sector": "Tech Hardware",  "name": "Intel"},
    {"ticker": "QCOM", "sector": "Tech Hardware",  "name": "Qualcomm"},
    {"ticker": "TXN",  "sector": "Tech Hardware",  "name": "Texas Instruments"},
    {"ticker": "NVDA", "sector": "Tech Hardware",  "name": "Nvidia"},
    {"ticker": "MU",   "sector": "Tech Hardware",  "name": "Micron Technology"},
    {"ticker": "WDC",  "sector": "Tech Hardware",  "name": "Western Digital"},
    {"ticker": "GLW",  "sector": "Tech Hardware",  "name": "Corning"},
    {"ticker": "APH",  "sector": "Tech Hardware",  "name": "Amphenol"},
    {"ticker": "STX",  "sector": "Tech Hardware",  "name": "Seagate Technology"},
    # --- Retail / Consumer ---
    {"ticker": "NKE",  "sector": "Retail",         "name": "Nike"},
    {"ticker": "WMT",  "sector": "Retail",         "name": "Walmart"},
    {"ticker": "TGT",  "sector": "Retail",         "name": "Target"},
    {"ticker": "BBY",  "sector": "Retail",         "name": "Best Buy"},
    {"ticker": "GAP",  "sector": "Retail",         "name": "Gap Inc"},
    {"ticker": "HAS",  "sector": "Retail",         "name": "Hasbro"},
    {"ticker": "MAT",  "sector": "Retail",         "name": "Mattel"},
    {"ticker": "VFC",  "sector": "Retail",         "name": "VF Corporation"},
    {"ticker": "PVH",  "sector": "Retail",         "name": "PVH Corp"},
    {"ticker": "TPR",  "sector": "Retail",         "name": "Tapestry"},
]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATE_FROM = "2022-01-01"
DATE_TO   = "2025-12-31"

RAW_DIR  = Path("data/10k_raw")
OUT_CSV  = Path("data/risk_factors.csv")

# EDGAR requires a descriptive User-Agent identifying the requester
HEADERS = {"User-Agent": "Risk-Factor Research project@research.edu"}
SLEEP   = 0.13   # seconds between requests  (≈ 7.7 req/s, safely under the 10/s limit)

EDGAR_BASE       = "https://data.sec.gov"
TICKERS_URL      = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES_BASE    = "https://www.sec.gov/Archives/edgar/data"

CSV_FIELDS = [
    "ticker", "name", "sector", "cik",
    "accession_number", "filing_date", "report_date", "fiscal_year",
    "primary_document", "item_1a_text", "item_1a_word_count",
]

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _get(url: str, **kwargs) -> requests.Response:
    """GET with shared headers, auto-retry on 429, and rate limiting."""
    time.sleep(SLEEP)
    for attempt in range(3):
        r = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10))
            print(f"    [429] rate-limited — sleeping {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"Failed after retries: {url}")


def accession_nodashes(acc: str) -> str:
    return acc.replace("-", "")


# ---------------------------------------------------------------------------
# Step 1 — resolve tickers to CIKs
# ---------------------------------------------------------------------------

def build_cik_map(tickers: list[str]) -> dict[str, str]:
    """Return {ticker: cik_10digit} for the requested tickers."""
    print("Fetching EDGAR company tickers list ...")
    data = _get(TICKERS_URL).json()

    # Build a lookup: uppercase ticker -> zero-padded CIK string
    lookup = {}
    for entry in data.values():
        t = entry["ticker"].upper()
        cik = f"{entry['cik_str']:010d}"
        lookup[t] = cik

    result = {}
    missing = []
    for ticker in tickers:
        t = ticker.upper()
        if t in lookup:
            result[t] = lookup[t]
        else:
            missing.append(t)

    if missing:
        print(f"  [WARN] CIK not found for: {missing}")
    print(f"  Resolved {len(result)}/{len(tickers)} tickers")
    return result


# ---------------------------------------------------------------------------
# Step 2 — get 10-K filings for a company
# ---------------------------------------------------------------------------

def get_10k_filings(cik: str, date_from: str, date_to: str) -> list[dict]:
    """
    Return list of 10-K filing dicts for the given CIK and date range.
    Checks both `filings.recent` and any paginated `filings.files`.
    """
    url = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
    data = _get(url).json()

    def extract_filings(recent: dict) -> list[dict]:
        forms   = recent.get("form", [])
        dates   = recent.get("filingDate", [])
        reports = recent.get("reportDate", [])
        accnums = recent.get("accessionNumber", [])
        docs    = recent.get("primaryDocument", [])

        filings = []
        for form, date, report, acc, doc in zip(forms, dates, reports, accnums, docs):
            if form != "10-K":
                continue
            if date < date_from or date > date_to:
                continue
            filings.append({
                "accession_number": acc,
                "filing_date":      date,
                "report_date":      report,
                "primary_document": doc,
            })
        return filings

    filings = extract_filings(data["filings"]["recent"])

    # Paginated older files (usually pre-2015; our range is 2022+ so rarely needed)
    for page_ref in data["filings"].get("files", []):
        if page_ref.get("filingTo", "") < date_from:
            continue   # all filings in this page are too old
        page_url = f"{EDGAR_BASE}/submissions/{page_ref['name']}"
        page_data = _get(page_url).json()
        filings.extend(extract_filings(page_data))

    # Deduplicate on accession number
    seen = set()
    unique = []
    for f in filings:
        if f["accession_number"] not in seen:
            seen.add(f["accession_number"])
            unique.append(f)

    return unique


# ---------------------------------------------------------------------------
# Step 3 — download the primary document
# ---------------------------------------------------------------------------

def download_primary_doc(cik: str, filing: dict, raw_dir: Path) -> Path | None:
    """
    Download the primary .htm document.  Returns local path, or None on failure.
    Skips download if file already exists (resume-safe).
    """
    acc_nd  = accession_nodashes(filing["accession_number"])
    doc     = filing["primary_document"]
    url     = f"{ARCHIVES_BASE}/{int(cik)}/{acc_nd}/{doc}"

    # Local filename: remove path separators from doc name
    safe_doc = doc.replace("/", "_")
    dest = raw_dir / f"{cik}_{acc_nd}_{safe_doc}"

    if dest.exists():
        return dest  # already downloaded

    try:
        r = _get(url)
    except Exception as e:
        print(f"    [ERROR] download failed: {e}")
        return None

    dest.write_bytes(r.content)
    return dest


# ---------------------------------------------------------------------------
# Step 4 — extract Item 1A
# ---------------------------------------------------------------------------

# Matches "ITEM 1A" or "Item 1A" with optional period and/or "RISK FACTORS"
_ITEM1A = re.compile(r"ITEM\s+1A[\.\s]*(?:RISK\s+FACTORS)?", re.IGNORECASE)
_ITEM1B = re.compile(r"ITEM\s+1B", re.IGNORECASE)
_ITEM2  = re.compile(r"ITEM\s+2[\.\s]", re.IGNORECASE)

# Fallback: bare "RISK FACTORS" heading (used by Intel, Honeywell, etc.)
# A TOC entry looks like "\nRisk Factors\n42\n"; a body heading is followed by text
_RF_HEADING  = re.compile(r"\n(?:RISK FACTORS|Risk Factors)\n(?!\d)", re.MULTILINE)
# Common sections that follow Risk Factors in the document body
_SECTION_END = re.compile(
    r"\n(?:ITEM\s+1B|ITEM\s+2|PROPERTIES|UNRESOLVED STAFF COMMENTS|LEGAL PROCEEDINGS)",
    re.IGNORECASE,
)


def _html_to_text(html_bytes: bytes) -> str:
    """Strip HTML tags, collapse whitespace."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def extract_item_1a(local_path: Path) -> str:
    """
    Extract the Item 1A Risk Factors section from a 10-K HTML file.

    Strategy:
      - Convert HTML to plain text
      - Find ALL spans of text between an "Item 1A" marker and
        the next "Item 1B" or "Item 2" marker
      - Return the longest span (TOC entries are short; body is long)
    """
    raw = local_path.read_bytes()
    text = _html_to_text(raw)

    # Split on Item 1A occurrences
    parts = _ITEM1A.split(text)
    if len(parts) < 2:
        return ""   # section not found

    candidates = []
    for chunk in parts[1:]:   # everything after each "Item 1A" hit
        # Trim at Item 1B or Item 2 (whichever comes first)
        end1b = _ITEM1B.search(chunk)
        end2  = _ITEM2.search(chunk)
        ends  = [m.start() for m in [end1b, end2] if m]
        if ends:
            chunk = chunk[: min(ends)]
        candidates.append(chunk.strip())

    if candidates:
        best = max(candidates, key=len)
        if len(best.split()) >= 200:
            return best

    # --- Fallback: bare "RISK FACTORS" heading (Intel, Honeywell, etc.) ---
    # Find body occurrences (not TOC: TOC entries are followed by a page number)
    body_parts = []
    for m in _RF_HEADING.finditer(text):
        chunk = text[m.end():]
        end_m = _SECTION_END.search(chunk)
        if end_m:
            chunk = chunk[: end_m.start()]
        if len(chunk.split()) >= 200:
            body_parts.append(chunk.strip())

    if not body_parts:
        return ""

    # Take the longest body chunk (first meaningful body section)
    return max(body_parts, key=len)


# ---------------------------------------------------------------------------
# Step 5 — write a row to CSV
# ---------------------------------------------------------------------------

def init_csv(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def append_csv(path: Path, row: dict) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Download 10-K Risk Factors from EDGAR")
    p.add_argument("--tickers", nargs="+", metavar="TICKER",
                   help="Run only these tickers (default: all 30)")
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--date-to",   default=DATE_TO)
    return p.parse_args()


def main():
    args = parse_args()

    companies = COMPANIES
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in COMPANIES if c["ticker"].upper() in wanted]
        if not companies:
            sys.exit(f"No matching tickers in company list: {args.tickers}")

    tickers = [c["ticker"] for c in companies]

    # Resolve CIKs
    cik_map = build_cik_map(tickers)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    init_csv(OUT_CSV)

    total_filings = 0
    total_extracted = 0

    for company in companies:
        ticker = company["ticker"]
        cik    = cik_map.get(ticker.upper())
        if not cik:
            print(f"\n[{ticker}] CIK not found — skipping")
            continue

        print(f"\n[{ticker}]  CIK={cik}  ({company['name']})")

        # Get 10-K filing list
        filings = get_10k_filings(cik, args.date_from, args.date_to)
        print(f"  Found {len(filings)} 10-K filings in {args.date_from[:4]}–{args.date_to[:4]}")

        for filing in filings:
            filing_date = filing["filing_date"]
            acc         = filing["accession_number"]
            fiscal_year = filing["report_date"][:4] if filing["report_date"] else filing_date[:4]

            print(f"  {filing_date}  {acc}  FY{fiscal_year}", end=" ... ", flush=True)
            total_filings += 1

            # Download
            local = download_primary_doc(cik, filing, RAW_DIR)
            if local is None:
                print("DOWNLOAD FAILED")
                continue

            # Extract Item 1A
            text = extract_item_1a(local)
            if not text:
                print("Item 1A NOT FOUND")
            else:
                wc = len(text.split())
                print(f"{wc:,} words")
                total_extracted += 1

            # Save row
            append_csv(OUT_CSV, {
                "ticker":            ticker,
                "name":              company["name"],
                "sector":            company["sector"],
                "cik":               cik,
                "accession_number":  acc,
                "filing_date":       filing_date,
                "report_date":       filing["report_date"],
                "fiscal_year":       fiscal_year,
                "primary_document":  filing["primary_document"],
                "item_1a_text":      text,
                "item_1a_word_count": len(text.split()) if text else 0,
            })

    print(f"\n{'='*60}")
    print(f"Filings processed : {total_filings}")
    print(f"Item 1A extracted : {total_extracted}")
    print(f"CSV saved         : {OUT_CSV}")
    print(f"Raw HTML saved in : {RAW_DIR}/")


if __name__ == "__main__":
    main()
