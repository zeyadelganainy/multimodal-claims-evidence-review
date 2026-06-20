"""Scratch smoke test for image_utils.py — not part of the pipeline, kept for records."""

import shutil
import tempfile
from pathlib import Path

from PIL import Image

from image_utils import (
    resolve_image_paths,
    validate_images_exist,
    find_duplicate_pairs,
)

DATASET_ROOT = Path("../dataset")

# 1. Real-data check: scan every multi-image test case for accidental existing duplicates.
print("=== Scanning real test images for existing duplicates ===")
test_dir = DATASET_ROOT / "images" / "test"
any_found = False
for case_dir in sorted(test_dir.iterdir()):
    if not case_dir.is_dir():
        continue
    imgs = sorted(case_dir.glob("img_*.jpg"))
    if len(imgs) < 2:
        continue
    pairs = find_duplicate_pairs(imgs)
    if pairs:
        any_found = True
        print(f"{case_dir.name}: {pairs}")
if not any_found:
    print("No accidental duplicates found among real multi-image test cases (expected).")

# 2. Synthetic check: exact duplicate (byte copy) and near-duplicate (recompressed).
print("\n=== Synthetic duplicate detection check ===")
sample_img = next((DATASET_ROOT / "images" / "test").glob("case_001/img_1.jpg"))
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    exact_copy = tmp / "img_2.jpg"
    shutil.copyfile(sample_img, exact_copy)

    recompressed = tmp / "img_3.jpg"
    with Image.open(sample_img) as im:
        im.convert("RGB").save(recompressed, "JPEG", quality=40)

    distinct_other = tmp / "img_4.jpg"
    other_sample = next((DATASET_ROOT / "images" / "test").glob("case_004/img_1.jpg"))
    shutil.copyfile(other_sample, distinct_other)

    fake_original = tmp / "img_1.jpg"
    shutil.copyfile(sample_img, fake_original)

    paths = [fake_original, exact_copy, recompressed, distinct_other]
    pairs = find_duplicate_pairs(paths)
    for p in pairs:
        print(p)

# 3. Missing-file validation check.
print("\n=== Missing file validation check ===")
fake_row = {"image_paths": "images/test/case_001/img_1.jpg;images/test/case_001/img_nonexistent.jpg"}
resolved = resolve_image_paths(fake_row, DATASET_ROOT)
missing = validate_images_exist(resolved)
print("Missing:", [str(p) for p in missing])
