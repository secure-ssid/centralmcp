#!/usr/bin/env python3
"""
Download OpenAPI spec JSON files from internal-ui.central.arubanetworks.com/cnxconfig/docs/.
Spec names are derived from the config reference URL slugs.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "openapi_specs"
OUTPUT_DIR.mkdir(exist_ok=True)

BASE_URL = "https://internal-ui.central.arubanetworks.com/cnxconfig/docs"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

def load_spec_names():
    with open("/tmp/cfg_urls.json") as f:
        urls = json.load(f)
    # Extract slug from each URL: .../new-central-config/reference/<slug>
    return [u.split("/reference/")[-1] for u in urls]

def fetch_spec(name):
    out_path = OUTPUT_DIR / f"{name}.json"
    if out_path.exists():
        return f"SKIP {name}"
    url = f"{BASE_URL}/{name}.json"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        # Validate it's JSON
        json.loads(data)
        out_path.write_bytes(data)
        return f"OK {name} ({len(data)} bytes)"
    except Exception as e:
        return f"ERROR {name}: {e}"

def main():
    names = load_spec_names()
    print(f"Downloading {len(names)} OpenAPI specs -> {OUTPUT_DIR}")
    done = 0
    errors = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_spec, name): name for name in names}
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            if result.startswith("ERROR"):
                errors.append(result)
            if done % 100 == 0 or done == len(names):
                print(f"  [{done}/{len(names)}] {result}")
    print(f"\nDone. {len(errors)} errors.")
    for e in errors[:20]:
        print(" ", e)

if __name__ == "__main__":
    main()
