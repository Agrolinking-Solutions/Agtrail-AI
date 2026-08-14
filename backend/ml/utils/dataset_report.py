"""
Checks the health of the farm stage training dataset before a training
run. Run this anytime you've added new images, to catch problems early
rather than discovering them mid-training.

Usage:
    python3 ml/utils/dataset_report.py
"""

import hashlib
from collections import defaultdict
from pathlib import Path

from PIL import Image, UnidentifiedImageError

DATA_DIR = Path(__file__).resolve().parents[3] / "ml" / "datasets" / "raw"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".ppm", ".bmp", ".pgm", ".tif", ".tiff", ".webp"}
TARGET_PER_CLASS = 15


def file_hash(path: Path) -> str:
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main():
    if not DATA_DIR.exists():
        print(f"Dataset folder not found at {DATA_DIR}")
        return

    class_folders = sorted([p for p in DATA_DIR.iterdir() if p.is_dir()])

    if not class_folders:
        print(f"No class folders found under {DATA_DIR}")
        return

    seen_hashes = defaultdict(list)
    corrupt_files = []
    counts = {}

    print(f"Scanning {DATA_DIR}\n")

    for folder in class_folders:
        images = [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS]
        counts[folder.name] = len(images)

        for image_path in images:
            try:
                with Image.open(image_path) as img:
                    img.verify()
            except (UnidentifiedImageError, OSError):
                corrupt_files.append(image_path)
                continue

            h = file_hash(image_path)
            seen_hashes[h].append(image_path)

    total = sum(counts.values())
    print(f"{'Class':<26}{'Images':<10}{'Status'}")
    print("-" * 55)
    for class_name, count in counts.items():
        status = "OK" if count >= TARGET_PER_CLASS else f"needs {TARGET_PER_CLASS - count} more"
        print(f"{class_name:<26}{count:<10}{status}")

    print("-" * 55)
    print(f"{'Total':<26}{total}\n")

    duplicates = {h: paths for h, paths in seen_hashes.items() if len(paths) > 1}
    if duplicates:
        print(f"Found {len(duplicates)} duplicate image(s):")
        for h, paths in duplicates.items():
            print("  Same file saved more than once:")
            for p in paths:
                print(f"    {p}")
    else:
        print("No duplicate images found.")

    if corrupt_files:
        print(f"\nFound {len(corrupt_files)} file(s) that could not be opened as valid images:")
        for p in corrupt_files:
            print(f"  {p}")
    else:
        print("No corrupt or unreadable image files found.")


if __name__ == "__main__":
    main()