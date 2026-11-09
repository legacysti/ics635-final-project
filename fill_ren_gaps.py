"""Fill the blank AOR/HYC values in the bulk file using EIA's official REN totals from the API.

The bulk file (see download_eia_data.py) has no REN series, so the notebook builds REN = AOR + HYC.
For a few state-months EIA left one of those two parts blank, mostly HYC in the preliminary
2025-2026 months, so REN, and with it the target, is missing. EIA's API does publish REN for those
months, so the blank part is REN minus the other part.

    python fill_ren_gaps.py            # use data/eia_ren_api_gaps.csv if it exists, else call the API once
    python fill_ren_gaps.py --refresh  # call the API again and overwrite data/eia_ren_api_gaps.csv

Calling the API needs a free key from https://www.eia.gov/opendata/register.php, set in the
EIA_API_KEY environment variable or in a .env file next to this script (EIA_API_KEY=...).
Never commit the key. The API rows are saved to data/eia_ren_api_gaps.csv, so after one call
nobody else needs a key to rebuild the combined file.

Output: data/eia_generation_monthly_combined.csv, the bulk rows with the blanks filled and one extra
column, `source`: "eia_bulk" for values as published in the bulk file, "derived_from_api_ren" for
filled values (REN from the API minus the other part from the bulk file).
"""
import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
RAW_PATH = DATA_DIR / "eia_generation_monthly_raw.csv"
API_PATH = DATA_DIR / "eia_ren_api_gaps.csv"
OUT_PATH = DATA_DIR / "eia_generation_monthly_combined.csv"
API_URL = "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/"
MAX_ROWS = 5000   # the API returns at most 5,000 rows per call
TOLERANCE = 0.01  # thousand MWh; how closely API REN must match bulk AOR + HYC
COLUMNS = ["period", "location", "stateDescription", "sectorid", "sectorDescription",
           "fueltypeid", "fuelTypeDescription", "generation", "generation-units"]
STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
             "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
PARTS = ("AOR", "HYC")


def num(value):
    return None if value in ("", None) else float(value)


def read_raw():
    with open(RAW_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_gaps(raw):
    """State-months in the 50 states where exactly one of AOR and HYC is blank."""
    parts = defaultdict(dict)   # (location, period) -> {fuel: value}
    for r in raw:
        if r["location"] in STATES and r["fueltypeid"] in PARTS:
            parts[(r["location"], r["period"])][r["fueltypeid"]] = num(r["generation"])
    gaps, unfillable = {}, []
    for key, p in parts.items():
        blank = [fuel for fuel in PARTS if fuel in p and p[fuel] is None]
        if len(blank) == 2:
            unfillable.append(key)
        elif blank:
            gaps[key] = blank[0]
    return gaps, unfillable, parts


def api_key():
    key = os.environ.get("EIA_API_KEY")
    env_file = HERE / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("EIA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        sys.exit("EIA_API_KEY is not set (environment variable or .env). It is needed for the API call.")
    return key


def fetch_ren(states, start, end):
    """One API call: monthly REN, all sectors, for the given states and months."""
    params = [("api_key", api_key()), ("frequency", "monthly"), ("data[0]", "generation"),
              ("facets[sectorid][]", "99"), ("facets[fueltypeid][]", "REN"),
              ("start", start), ("end", end), ("length", str(MAX_ROWS)),
              ("sort[0][column]", "location"), ("sort[0][direction]", "asc"),
              ("sort[1][column]", "period"), ("sort[1][direction]", "asc")]
    params += [("facets[location][]", s) for s in sorted(states)]
    print(f"Calling the EIA API once: REN for {len(states)} states, {start} to {end}...")
    try:
        with urllib.request.urlopen(f"{API_URL}?{urllib.parse.urlencode(params)}", timeout=120) as resp:
            body = json.load(resp)["response"]
    except urllib.error.HTTPError as e:   # message has the status only, never the URL with the key
        sys.exit(f"EIA API error: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the EIA API ({e.reason}). Check your internet connection or proxy.")
    total, rows = int(body["total"]), body["data"]
    if total > MAX_ROWS:
        sys.exit(f"The request matches {total:,} rows, more than the {MAX_ROWS:,} one call returns.")
    if len(rows) != total:
        sys.exit(f"The API returned {len(rows):,} of {total:,} rows.")
    print(f"Received {len(rows):,} rows")
    return rows


def save_api(rows):
    with open(API_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows):,} API rows to {API_PATH.relative_to(HERE)}")


def read_api():
    with open(API_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ren_by_state_month(rows):
    return {(r["location"], r["period"]): num(r["generation"]) for r in rows if r["fueltypeid"] == "REN"}


def check_same_version(api_ren, parts, gap_months):
    """API REN must equal bulk AOR + HYC wherever the bulk file has both parts.

    The API is updated after the bulk file, so it can carry revised numbers. Subtracting a revised
    total from an unrevised part would give a wrong value, so stop if the versions disagree in the
    months near the gaps.
    """
    gap_years = {period[:4] for _, period in gap_months}
    compared, mismatches = 0, []
    for key, ren in api_ren.items():
        p = parts.get(key, {})
        if key in gap_months or ren is None or p.get("AOR") is None or p.get("HYC") is None:
            continue
        compared += 1
        diff = ren - (p["AOR"] + p["HYC"])
        if abs(diff) > TOLERANCE:
            mismatches.append((key, ren, p["AOR"] + p["HYC"], diff))
    recent = [m for m in mismatches if m[0][1][:4] in gap_years]
    print(f"Version check: {compared:,} state-months with both parts in the bulk file, "
          f"{len(mismatches)} differ from API REN by more than {TOLERANCE} "
          f"({len(recent)} of them in a year with a gap: {', '.join(sorted(gap_years))})")
    for (loc, period), ren, bulk, diff in sorted(mismatches, key=lambda m: -abs(m[3]))[:10]:
        print(f"  {loc} {period}: API REN {ren:,.3f}, bulk AOR+HYC {bulk:,.3f}, diff {diff:+,.3f}")
    if recent:
        sys.exit("The API and the bulk file disagree near the gaps, so they are probably different "
                 "versions. Rebuild the raw CSV from a fresh ELEC.zip (python download_eia_data.py), "
                 "then run python fill_ren_gaps.py --refresh.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="call the API even if the saved file exists")
    args = parser.parse_args()

    raw = read_raw()
    gaps, unfillable, parts = find_gaps(raw)
    if unfillable:
        print(f"Both AOR and HYC are blank in {len(unfillable)} state-months; these stay blank: {sorted(unfillable)}")
    if not gaps:
        sys.exit("No blank AOR or HYC values in the 50 states; nothing to fill.")
    states = {loc for loc, _ in gaps}
    print(f"{len(gaps)} state-months have a blank AOR or HYC, in {len(states)} states: {' '.join(sorted(states))}")

    fetched = args.refresh or not API_PATH.exists()
    api_rows = (fetch_ren(states, min(p for _, p in gaps), max(r["period"] for r in raw))
                if fetched else read_api())
    api_ren = ren_by_state_month(api_rows)

    missing = sorted(k for k in gaps if api_ren.get(k) is None)
    if missing:
        sys.exit(f"{API_PATH.name} has no REN value for {len(missing)} gap state-months, e.g. {missing[:5]}. "
                 "Run python fill_ren_gaps.py --refresh.")
    check_same_version(api_ren, parts, gaps)
    if fetched:   # saved only after the checks pass, so a bad call never replaces a good file
        save_api(api_rows)

    # Blank part = API REN minus the other part from the bulk file
    filled = {}
    for (loc, period), blank in sorted(gaps.items()):
        other = "HYC" if blank == "AOR" else "AOR"
        filled[(loc, period, blank)] = api_ren[(loc, period)] - parts[(loc, period)][other]

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS + ["source"])
        writer.writeheader()
        for r in raw:
            key = (r["location"], r["period"], r["fueltypeid"])
            if key in filled:
                writer.writerow({**r, "generation": f"{filled[key]:.5f}", "source": "derived_from_api_ren"})
            else:
                writer.writerow({**r, "source": "eia_bulk"})

    print(f"\nFilled {len(filled)} values (thousand MWh). Negative values are flagged with '!':")
    print(f"  {'state':5} {'month':7} {'fuel':4} {'filled':>10} {'API REN':>10}")
    for (loc, period, fuel), value in filled.items():
        flag = " !" if value < -TOLERANCE else ""
        print(f"  {loc:5} {period:7} {fuel:4} {value:10.3f} {api_ren[(loc, period)]:10.3f}{flag}")
    print(f"\nWrote {OUT_PATH.relative_to(HERE)}: {len(raw):,} rows, {len(filled)} of them filled")


if __name__ == "__main__":
    main()
