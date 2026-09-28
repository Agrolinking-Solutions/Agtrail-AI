"""
Downloads openly licensed photos from Openverse, which aggregates
Flickr, Wikimedia, and dozens of other free-to-use sources into one
search index.

Usage:
    python3 ml/utils/openverse_download.py --search "pruning cocoa tree Nigeria" --folder pruning --limit 20
"""

import argparse
import csv
import os
import time
from pathlib import Path

import httpx

OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"
# Wikimedia asks for a contact address in the User-Agent, set CONTACT_EMAIL.
HEADERS = {"User-Agent": f"AgtrailDatasetCollector/1.0 (contact: {os.environ.get('CONTACT_EMAIL', 'unset')})"}

# Reusable, non-restrictive licenses only, same reasoning as the Commons
# script: excludes NonCommercial and NoDerivatives licenses since this
# data feeds a commercial product.
ALLOWED_LICENSES = "cc0,pdm,by,by-sa"

DATA_ROOT = Path(__file__).resolve().parents[3] / "ml" / "datasets" / "raw"


def search_images(search_term: str, limit: int):
    try:
        resp = httpx.get(OPENVERSE_API_URL, headers=HEADERS, params={
            "q": search_term,
            "license": ALLOWED_LICENSES,
            "page_size": min(limit, 20),
        }, timeout=20.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Search request failed: {exc}")
        return []

    return resp.json().get("results", [])


def download_images(results, folder_name: str, search_term: str):
    target_dir = DATA_ROOT / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    log_path = DATA_ROOT.parent / "source_log.csv"
    is_new_log = not log_path.exists()

    with open(log_path, "a", newline="") as log_file:
        writer = csv.writer(log_file)
        if is_new_log:
            writer.writerow(["filename", "source_url", "license_id", "folder", "search_term"])

        saved_count = 0
        for item in results:
            image_url = item.get("url")
            if not image_url:
                continue

            ext = image_url.split(".")[-1].split("?")[0].lower()
            if ext not in {"jpg", "jpeg", "png"}:
                print(f"  Skipped {item.get('id')}, wrong file type ({ext})")
                continue

            filename = f"openverse_{item['id']}.{ext}"
            filepath = target_dir / filename

            if filepath.exists():
                continue

            try:
                img_resp = httpx.get(image_url, headers=HEADERS, timeout=20.0)
                img_resp.raise_for_status()
                filepath.write_bytes(img_resp.content)
            except httpx.HTTPError as exc:
                print(f"  Skipped {item.get('id')}, download failed: {exc}")
                continue

            source_url = item.get("foreign_landing_url", "")
            writer.writerow([filename, source_url, item.get("license"), folder_name, search_term])

            saved_count += 1
            print(f"  Saved {filename} (source: {item.get('source')}, license: {item.get('license')})")
            time.sleep(0.3)

    print(f"\nDone. Saved {saved_count} new images to {target_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    print(f"Searching Openverse for '{args.search}'...")
    results = search_images(args.search, args.limit)
    print(f"Found {len(results)} results.\n")

    if not results:
        print("No matches. Try a different search term.")
    else:
        download_images(results, args.folder, args.search)