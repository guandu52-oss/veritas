from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def find_images(images_dir: Path) -> list[Path]:
    if not images_dir.is_dir():
        return []
    return [
        path
        for path in sorted(images_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]


def hamming_distance(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def dhash(path: Path, hash_size: int = 8) -> int:
    from PIL import Image

    with Image.open(path) as image:
        resized = image.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(resized.getdata())
    value = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            value = (value << 1) | int(left > right)
    return value


def generate_similarity_candidates(
    images_dir: Path,
    *,
    max_distance: int = 8,
    max_candidates: int = 200,
) -> dict[str, Any]:
    images = find_images(images_dir)
    if not images:
        return {
            "schema_version": "1.0",
            "created_by": "engine/static_audit/tools/image_similarity.py",
            "status": "skipped",
            "method": "dhash",
            "inputs": {"images_dir": str(images_dir)},
            "image_count": 0,
            "candidate_count": 0,
            "candidates": [],
            "errors": [],
            "limitations": ["No image files were found."],
        }

    try:
        hashes = [(path, dhash(path)) for path in images]
    except ImportError:
        return {
            "schema_version": "1.0",
            "created_by": "engine/static_audit/tools/image_similarity.py",
            "status": "not_available",
            "method": "dhash",
            "inputs": {"images_dir": str(images_dir)},
            "image_count": len(images),
            "candidate_count": 0,
            "candidates": [],
            "errors": ["Pillow is not installed; near-duplicate image candidates were not computed."],
            "limitations": ["Install Pillow to enable deterministic dHash image similarity candidates."],
        }

    candidates: list[dict[str, Any]] = []
    for idx, (left_path, left_hash) in enumerate(hashes):
        for right_path, right_hash in hashes[idx + 1 :]:
            distance = hamming_distance(left_hash, right_hash)
            if distance <= max_distance:
                candidates.append(
                    {
                        "left_image": str(left_path),
                        "right_image": str(right_path),
                        "method": "dhash",
                        "distance": distance,
                        "max_distance": max_distance,
                        "manual_review_needed": True,
                    }
                )
                if len(candidates) >= max_candidates:
                    break
        if len(candidates) >= max_candidates:
            break

    return {
        "schema_version": "1.0",
        "created_by": "engine/static_audit/tools/image_similarity.py",
        "status": "ran",
        "method": "dhash",
        "inputs": {"images_dir": str(images_dir), "max_distance": max_distance},
        "image_count": len(images),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "errors": [],
        "limitations": [
            "dHash candidates are triage leads only; crops, rotations, contrast changes, and local reuse require visual or manual review.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find near-duplicate image candidates with dHash.")
    parser.add_argument("images_dir", help="Directory containing extracted paper images.")
    parser.add_argument("--output", required=True, help="Output JSON path.")
    parser.add_argument("--max-distance", type=int, default=8, help="Maximum dHash Hamming distance.")
    parser.add_argument("--max-candidates", type=int, default=200, help="Maximum candidate pairs to emit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    images_dir = Path(args.images_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    result = generate_similarity_candidates(
        images_dir,
        max_distance=args.max_distance,
        max_candidates=args.max_candidates,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result["status"], "candidate_count": result["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

