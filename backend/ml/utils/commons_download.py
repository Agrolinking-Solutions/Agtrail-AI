"""
Downloads freely licensed photos from Wikimedia Commons for a given
search term, and saves them into a dataset folder.

No API key needed, Commons' API is fully open, but Wikimedia requires
a real User-Agent identifying the tool making requests.

Usage:
    python3 ml/utils/commons_download.py --search "Nigeria land preparation farming" --folder land_preparation --limit 20
"""

import argparse
import csv
import os
import time
from pathlib import Path

import httpx
from urllib.parse import urlparse

COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks for a contact address in the User-Agent, set CONTACT_EMAIL.
HEADERS = {"User-Agent": f"AgtrailDatasetCollector/1.0 (contact: {os.environ.get('CONTACT_EMAIL', 'unset')})"}

DATA_ROOT = Path(__file__).resolve().parents[3] / "ml" / "datasets" / "raw"

# Same rule as the Openverse script: this data feeds a commercial product,
# so NonCommercial (NC) and NoDerivatives (ND) licenses are excluded.
ALLOWED_LICENSE_PREFIXES = ("cc0", "cc by", "cc-by", "public domain", "pd")


def license_allowed(license_name: str) -> bool:
    name = license_name.strip().lower()
    if "nc" in name.replace("-", " ").split() or "nd" in name.replace("-", " ").split():
        return False
    return name.startswith(ALLOWED_LICENSE_PREFIXES)


def search_files(search_term: str, limit: int):
    resp = httpx.get(COMMONS_API_URL, headers=HEADERS, params={
        "action": "query",
        "list": "search",
        "srsearch": search_term,
        "srnamespace": 6,  # File namespace
        "srlimit": limit,
        "format": "json",
    }, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def get_file_info(title: str):
    resp = httpx.get(COMMONS_API_URL, headers=HEADERS, params={
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        imageinfo = page.get("imageinfo")
        if not imageinfo:
            return None
        info = imageinfo[0]
        license_name = info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", "unknown")
        return {"url": info["url"], "license": license_name}
    return None


def download_files(titles, folder_name: str, search_term: str):
    target_dir = DATA_ROOT / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    log_path = DATA_ROOT.parent / "source_log.csv"
    is_new_log = not log_path.exists()

    with open(log_path, "a", newline="") as log_file:
        writer = csv.writer(log_file)
        if is_new_log:
            writer.writerow(["filename", "source_url", "license_id", "folder", "search_term"])

        saved_count = 0
        for title in titles:
            info = get_file_info(title)
            if not info:
                print(f"  Skipped {title}, no image info returned")
                continue

            if not license_allowed(info["license"]):
                print(f"  Skipped {title}, license not allowed ({info['license']})")
                continue

            ext = urlparse(info["url"]).path.split(".")[-1].lower()
            if ext not in {"jpg", "jpeg", "png"}:
                print(f"  Skipped {title}, wrong file type ({ext})")
                continue

            safe_name = title.replace("File:", "").replace(" ", "_").replace("/", "_").replace("\\", "_")
            filename = f"commons_{safe_name}"
            filepath = target_dir / filename

            if filepath.exists():
                continue

            try:
                img_resp = httpx.get(info["url"], headers=HEADERS, timeout=15.0)
                img_resp.raise_for_status()
                filepath.write_bytes(img_resp.content)
            except Exception as exc:
                print(f"  Skipped {title}, download failed: {exc}")
                continue

            source_url = f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}"
            writer.writerow([filename, source_url, info["license"], folder_name, search_term])

            saved_count += 1
            print(f"  Saved {filename} (license: {info['license']})")
            time.sleep(0.3)

    print(f"\nDone. Saved {saved_count} new images to {target_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    print(f"Searching Wikimedia Commons for '{args.search}'...")
    titles = search_files(args.search, args.limit)
    print(f"Found {len(titles)} results.\n")

    if not titles:
        print("No matches. Try a different search term.")
    else:
        download_files(titles, args.folder, args.search)