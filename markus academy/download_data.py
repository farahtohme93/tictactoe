"""
Download US homeownership rates by age of householder.

Sources
-------
1. Census Bureau HVS Table 19  — quarterly rates by age, 1994–present
   URL: https://www.census.gov/housing/hvs/data/histtab19.xlsx
   Age groups: <25, 25-29, 30-34, 35-39, 40-44, 45-54, 55-64, 65+  (varies by vintage)

2. FRED (Consumer Expenditure Survey) — annual rates by age, 1990–present
   Series: CXUHOMEOWNLB0402M … CXUHOMEOWNLB0407M
   Requires a free FRED API key -> set env var FRED_API_KEY
   Sign up: https://fred.stlouisfed.org/docs/api/api_key.html

Outputs
-------
  data/hvs_table19_raw.xlsx          raw Excel download from Census
  data/hvs_homeownership_by_age.csv  tidy long-format CSV from HVS
  data/fred_homeownership_by_age.csv tidy long-format CSV from FRED (if key set)
"""

import os
import sys
import requests
import pandas as pd

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HVS_URL = "https://www.census.gov/housing/hvs/data/histtab19.xlsx"

FRED_SERIES = {
    "under_25":  "CXUHOMEOWNLB0402M",
    "25_to_34":  "CXUHOMEOWNLB0403M",
    "35_to_44":  "CXUHOMEOWNLB0404M",
    "45_to_54":  "CXUHOMEOWNLB0405M",
    "55_to_64":  "CXUHOMEOWNLB0406M",
    "65_plus":   "CXUHOMEOWNLB0407M",
}

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

DATA_DIR = "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def download_file(url: str, dest: str) -> None:
    headers = {"User-Agent": "Mozilla/5.0 (research project)"}
    print(f"  GET {url}")
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        f.write(r.content)
    print(f"  Saved -> {dest}  ({len(r.content):,} bytes)")


# ---------------------------------------------------------------------------
# Census HVS Table 19
# ---------------------------------------------------------------------------

def parse_hvs_excel(path: str) -> pd.DataFrame:
    """
    Parse the HVS Table 19 Excel file into tidy long format.

    Layout (confirmed by inspection):
      Row 3  : column headers — "Year and Quarter", "U.S.", age-group labels ...
      Row 7+ : data — column 0 is either a 4-digit year (year marker row, no values)
               or a quarter label like "1st...", "2nd..." with numeric values in cols 1+
    """
    raw = pd.read_excel(path, header=None, sheet_name=0)

    # Extract column headers from row 3
    header = raw.iloc[3].tolist()
    age_cols = header[1:]  # drop the "Year and Quarter" column label

    # Clean up age-group column names
    age_cols = [str(c).strip() for c in age_cols]

    records = []
    current_year = None

    for _, row in raw.iloc[7:].iterrows():
        first = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else ""

        # Skip blank rows
        if not first or first == "nan":
            continue

        # Detect year marker row (cell is a plain 4-digit integer like 1994)
        try:
            val = int(float(first))
            if 1900 < val < 2100:
                current_year = val
                continue
        except (ValueError, TypeError):
            pass

        # Skip footer/note rows
        if any(first.lower().startswith(kw) for kw in ("source", "note", "1/", "2/", "r", "na")):
            continue

        # Quarter label row — extract the quarter number (1st -> Q1, etc.)
        q_map = {"1": "Q1", "2": "Q2", "3": "Q3", "4": "Q4"}
        q_digit = first[0] if first else ""
        quarter = q_map.get(q_digit)

        if current_year is None or quarter is None:
            continue

        # Parse rate values for each age group
        for col_name, raw_val in zip(age_cols, row.iloc[1:]):
            rate = pd.to_numeric(raw_val, errors="coerce")
            if pd.isna(rate):
                continue
            # Normalise to percent if stored as decimal
            if rate < 2:
                rate *= 100
            records.append({
                "source":             "Census HVS",
                "age_group":          col_name,
                "year":               current_year,
                "quarter":            quarter,
                "period":             f"{current_year} {quarter}",
                "homeownership_rate": rate,
            })

    df = pd.DataFrame(records)
    df = df.sort_values(["age_group", "year", "quarter"]).reset_index(drop=True)
    return df[["source", "age_group", "year", "quarter", "period", "homeownership_rate"]]


def download_hvs() -> pd.DataFrame:
    raw_path = os.path.join(DATA_DIR, "hvs_table19_raw.xlsx")
    print("\n=== Census Bureau HVS Table 19 ===")
    download_file(HVS_URL, raw_path)
    print("  Parsing Excel …")
    df = parse_hvs_excel(raw_path)
    out_path = os.path.join(DATA_DIR, "hvs_homeownership_by_age.csv")
    df.to_csv(out_path, index=False)
    print(f"  Saved tidy CSV -> {out_path}  ({len(df):,} rows)")
    print(f"  Years covered: {df['year'].min()} – {df['year'].max()}")
    print(f"  Age groups: {sorted(df['age_group'].unique())}")
    return df


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def fetch_fred_series(series_id: str, api_key: str) -> pd.DataFrame:
    params = {
        "series_id": series_id,
        "api_key":   api_key,
        "file_type": "json",
    }
    r = requests.get(FRED_API_BASE, params=params, timeout=30)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    df = pd.DataFrame(obs)[["date", "value"]]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"]  = pd.to_datetime(df["date"])
    return df


def download_fred(api_key: str) -> pd.DataFrame:
    print("\n=== FRED Consumer Expenditure Survey ===")
    frames = []
    for age_label, series_id in FRED_SERIES.items():
        print(f"  Fetching {series_id}  ({age_label}) …")
        try:
            df = fetch_fred_series(series_id, api_key)
            df["age_group"] = age_label
            df["series_id"] = series_id
            frames.append(df)
        except Exception as e:
            print(f"  [WARNING] {series_id} failed: {e}")

    if not frames:
        print("  No FRED data retrieved.")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.rename(columns={"date": "period", "value": "homeownership_rate"})
    combined["year"]    = combined["period"].dt.year
    combined["quarter"] = None  # annual series
    combined["source"]  = "FRED/CES"
    combined = combined.dropna(subset=["homeownership_rate"])
    combined = combined.sort_values(["age_group", "period"]).reset_index(drop=True)

    out_path = os.path.join(DATA_DIR, "fred_homeownership_by_age.csv")
    combined[["source", "age_group", "year", "quarter", "period", "homeownership_rate", "series_id"]].to_csv(
        out_path, index=False
    )
    print(f"  Saved tidy CSV -> {out_path}  ({len(combined):,} rows)")
    print(f"  Years covered: {combined['year'].min()} – {combined['year'].max()}")
    return combined


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ensure_data_dir()

    hvs_df = download_hvs()

    fred_api_key = os.environ.get("FRED_API_KEY", "").strip()
    if fred_api_key:
        fred_df = download_fred(fred_api_key)
    else:
        print("\n=== FRED ===")
        print("  Skipped — set FRED_API_KEY env var to also download FRED data.")
        print("  Free key: https://fred.stlouisfed.org/docs/api/api_key.html")

    print("\nDone.")


if __name__ == "__main__":
    main()
