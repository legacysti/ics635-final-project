"""Download the EIA data used by ICS635_Final_Project.ipynb into data/.

Uses only the Python standard library.

    python download_eia_data.py                 # rebuild the generation CSV from EIA's bulk file
    python download_eia_data.py --zip ELEC.zip  # same, from an ELEC.zip you already downloaded
    python download_eia_data.py --ren-only      # refresh only the official REN file (needs a key)

Set the EIA_API_KEY environment variable to also download the official REN rows.
A free key comes from https://www.eia.gov/opendata/register.php. Never commit the key.

Two sources are needed because neither one has everything:
  * The bulk file needs no key and has no rate limit, but has no REN code.
  * The API has REN, but only from 2003, and returns at most 5,000 rows per request.
"""
import argparse
import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
RAW_PATH = DATA_DIR / "eia_generation_monthly_raw.csv"
REN_PATH = DATA_DIR / "eia_ren_official_api.csv"
BULK_URL = "https://www.eia.gov/opendata/bulk/ELEC.zip"
API_URL = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"
PAGE_SIZE = 5000
COLUMNS = ["period", "location", "stateDescription", "sectorid", "sectorDescription",
           "fueltypeid", "fuelTypeDescription", "generation", "generation-units"]
# ELEC.GEN.<fuel>-<location>-99.M = net generation, all sectors, monthly
SERIES = re.compile(r"ELEC\.GEN\.([A-Z0-9]+)-([A-Z0-9]+)-99\.M")


def build_raw_csv(zip_path):
    """Flatten the monthly all-sector generation series into one row per location-fuel-month."""
    n_series = n_rows = 0
    with zipfile.ZipFile(zip_path) as z, z.open("ELEC.txt") as lines, \
            open(RAW_PATH, "w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        for line in lines:
            if b'"series_id":"ELEC.GEN.' not in line[:200]:
                continue
            series = json.loads(line)
            match = SERIES.fullmatch(series["series_id"])
            if match is None:
                continue
            fuel, location = match.groups()
            # e.g. "Net generation : Utah : all sectors : wind : monthly"
            parts = [p.strip() for p in series["name"].split(":")]
            n_series += 1
            for period, value in series["data"]:
                # Blank values stay blank here. The notebook decides how to treat them.
                writer.writerow([f"{period[:4]}-{period[4:]}", location, parts[1], "99", "All Sectors",
                                 fuel, parts[3], "" if value is None else value, series["units"]])
                n_rows += 1
    print(f"{RAW_PATH.name}: {n_series:,} series, {n_rows:,} rows")


def download_official_ren(api_key):
    """Page through the API for REN, all sectors. REN starts in 2003-01."""
    rows, offset = [], 0
    while True:
        query = urllib.parse.urlencode({
            "api_key": api_key, "frequency": "monthly", "data[0]": "generation",
            "facets[sectorid][]": "99", "facets[fueltypeid][]": "REN",
            "sort[0][column]": "period", "sort[0][direction]": "asc",
            "sort[1][column]": "location", "sort[1][direction]": "asc",
            "length": PAGE_SIZE, "offset": offset,
        })
        with urllib.request.urlopen(f"{API_URL}?{query}", timeout=120) as response:
            body = json.load(response)["response"]
        rows += body["data"]
        offset += PAGE_SIZE
        print(f"  {len(rows):,} of {body['total']} REN rows")
        if offset >= int(body["total"]):
            break
    with open(REN_PATH, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{REN_PATH.name}: {len(rows):,} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, help="existing ELEC.zip (otherwise it is downloaded)")
    parser.add_argument("--ren-only", action="store_true", help="skip the bulk file")
    args = parser.parse_args()
    DATA_DIR.mkdir(exist_ok=True)

    if not args.ren_only:
        zip_path = args.zip or DATA_DIR / "ELEC.zip"
        if not zip_path.exists():
            print(f"Downloading {BULK_URL} (about 300 MB)...")
            urllib.request.urlretrieve(BULK_URL, zip_path)
        build_raw_csv(zip_path)

    key = os.environ.get("EIA_API_KEY")
    if key:
        download_official_ren(key)
    else:
        print("EIA_API_KEY is not set, so the official REN file was left as it is.")
