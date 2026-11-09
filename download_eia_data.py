"""Rebuild data/eia_generation_monthly_raw.csv from EIA's bulk electricity file.

No API key is needed. Uses only the Python standard library.

    python download_eia_data.py                 # download ELEC.zip from EIA, then build the CSV
    python download_eia_data.py --zip ELEC.zip  # build the CSV from an ELEC.zip you already have

Source: https://www.eia.gov/opendata/bulk/ELEC.zip (about 300 MB), listed on
https://www.eia.gov/opendata/bulk.php. The script keeps the monthly net generation series for
all sectors (series IDs ELEC.GEN.<fuel>-<location>-99.M) and writes one row per
location, fuel, and month.

EIA updates the bulk file about once a month, so a new download can add months or revise values.
The script prints the file's date and SHA-256 so you can tell which version you used.
"""
import argparse
import csv
import hashlib
import json
import re
import urllib.request
import zipfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
RAW_PATH = DATA_DIR / "eia_generation_monthly_raw.csv"
BULK_URL = "https://www.eia.gov/opendata/bulk/ELEC.zip"
COLUMNS = ["period", "location", "stateDescription", "sectorid", "sectorDescription",
           "fueltypeid", "fuelTypeDescription", "generation", "generation-units"]
# ELEC.GEN.<fuel>-<location>-99.M = net generation, all sectors, monthly
SERIES = re.compile(r"ELEC\.GEN\.([A-Z0-9]+)-([A-Z0-9]+)-99\.M")


def download(zip_path):
    """Download the bulk file and report the date EIA last changed it."""
    print(f"Downloading {BULK_URL} (about 300 MB)...")
    with urllib.request.urlopen(BULK_URL, timeout=600) as response, open(zip_path, "wb") as out:
        print(f"EIA last modified: {response.headers.get('Last-Modified', 'unknown')}")
        while chunk := response.read(1 << 20):
            out.write(chunk)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", type=Path, help="existing ELEC.zip (otherwise it is downloaded)")
    args = parser.parse_args()
    DATA_DIR.mkdir(exist_ok=True)

    zip_path = args.zip or DATA_DIR / "ELEC.zip"
    if args.zip is None:
        download(zip_path)
    print(f"{zip_path.name} SHA-256: {sha256(zip_path)}")
    build_raw_csv(zip_path)
