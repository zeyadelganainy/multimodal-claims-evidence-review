"""Image loading and duplicate/near-duplicate detection.

Duplicate detection is deterministic (hashing), run before any LLM call,
per the locked rule that padding a claim's evidence with repeated images
must be caught reliably rather than left to a model to "notice."
"""

import hashlib
from pathlib import Path

import imagehash
from PIL import Image

# Hamming distance below which two perceptual hashes count as a near-duplicate.
# 0 = identical hash; small values tolerate recompression/resizing of the same photo.
PHASH_NEAR_DUPLICATE_THRESHOLD = 5


def resolve_image_paths(row: dict, dataset_root: str | Path) -> list[Path]:
    """Resolve a row's semicolon-separated image_paths to absolute Paths."""
    dataset_root = Path(dataset_root)
    return [dataset_root / p for p in row["image_paths"].split(";")]


def validate_images_exist(paths: list[Path]) -> list[Path]:
    """Return the subset of paths that are missing on disk."""
    return [p for p in paths if not p.is_file()]


def sha256_of_file(path: Path) -> str:
    """Exact-duplicate signature: hash of the raw file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phash_of_file(path: Path) -> imagehash.ImageHash:
    """Near-duplicate signature: perceptual hash, tolerant of resize/recompression."""
    with Image.open(path) as img:
        return imagehash.phash(img)


def find_duplicate_pairs(paths: list[Path]) -> list[dict]:
    """Compare every pair of images in one claim's set; report exact and
    near-duplicate pairs. Each result: {a, b, kind, distance}."""
    hashes = []
    for p in paths:
        sha = sha256_of_file(p)
        ph = phash_of_file(p)
        hashes.append((p, sha, ph))

    results = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            path_a, sha_a, ph_a = hashes[i]
            path_b, sha_b, ph_b = hashes[j]
            if sha_a == sha_b:
                results.append(
                    {"a": path_a.stem, "b": path_b.stem, "kind": "exact", "distance": 0}
                )
            else:
                distance = int(ph_a - ph_b)
                if distance <= PHASH_NEAR_DUPLICATE_THRESHOLD:
                    results.append(
                        {"a": path_a.stem, "b": path_b.stem, "kind": "near", "distance": distance}
                    )
    return results
