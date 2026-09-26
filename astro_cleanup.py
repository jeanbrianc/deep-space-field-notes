#!/usr/bin/env python3
"""Conservatively color-correct and stretch Seestar JPEG stacks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


STACKED_NAME = re.compile(r"^Stacked_(\d+)_(.+)_([\d.]+)s_(LP|IRCUT)_(\d{8})-(\d{6})\.jpe?g$", re.IGNORECASE)
COLLECTOR_MANIFEST_NAME = "selected_jpegs.txt"
COLLECTOR_MANIFEST_HEADER = ["frames", "local_file", "source_file"]
CLEANUP_MANIFEST_NAME = "cleanup_manifest.json"


class CollectorManifestError(ValueError):
    """Raised when a collector manifest cannot be trusted."""


@dataclass(frozen=True)
class PreparedImage:
    source: Path
    frames: int
    object_name: str
    relative: Path
    finished: Path | None


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array(
        [[0.4124564, 0.2126729, 0.0193339], [0.3575761, 0.7151522, 0.1191920], [0.1804375, 0.0721750, 0.9503041]],
        dtype=np.float32,
    )
    xyz /= np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    return np.stack((116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])), axis=-1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    delta = 6 / 29
    f = np.stack((fx, fy, fz), axis=-1)
    xyz = np.where(f > delta, f**3, 3 * delta**2 * (f - 4 / 29))
    xyz *= np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
    linear = xyz @ np.array(
        [[3.2404542, -0.9692660, 0.0556434], [-1.5371385, 1.8760108, -0.2040259], [-0.4985314, 0.0415560, 1.0572252]],
        dtype=np.float32,
    )
    rgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - 0.055)
    return np.clip(rgb, 0.0, 1.0)


def estimate_background(lab: np.ndarray) -> np.ndarray:
    """Estimate sky background in perceptual Lab space from dim pixels."""
    sample = lab[lab[..., 0] <= np.percentile(lab[..., 0], 55.0)]
    for _ in range(3):
        median = np.median(sample, axis=0)
        deviation = np.median(np.abs(sample - median), axis=0) + 1e-6
        keep = np.all(np.abs(sample - median) <= 3.5 * 1.4826 * deviation, axis=1)
        if keep.sum() < 100:
            break
        sample = sample[keep]
    return np.median(sample, axis=0)


def cleanup_array(rgb: np.ndarray, stretch: float = 7.0, saturation: float = 1.08) -> tuple[np.ndarray, dict]:
    source = np.asarray(rgb, dtype=np.float32) / 255.0
    lab = rgb_to_lab(source)
    background = estimate_background(lab)

    # Fade the background chroma correction toward zero at stellar highlights.
    # This neutralizes the sky without painting white stars the opposite color.
    correction_weight = np.clip((100.0 - lab[..., 0]) / max(100.0 - background[0], 1e-4), 0.0, 1.0)
    lab[..., 1] = (lab[..., 1] - background[1] * correction_weight) * saturation
    lab[..., 2] = (lab[..., 2] - background[2] * correction_weight) * saturation
    high_l = max(float(np.percentile(lab[..., 0], 99.85)), background[0] + 1e-4)
    normalized_l = np.clip((lab[..., 0] - background[0]) / (high_l - background[0]), 0.0, 1.0)
    lab[..., 0] = 100.0 * np.arcsinh(stretch * normalized_l) / np.arcsinh(stretch)

    result = np.round(lab_to_rgb(lab) * 255.0).astype(np.uint8)
    cast_strength = float(np.hypot(background[1], background[2]))
    if cast_strength < 4.0:
        cast = "near-neutral"
    elif background[1] > 3 and background[2] < -3:
        cast = "magenta"
    elif background[1] < -3 and background[2] > 3:
        cast = "green-yellow"
    elif abs(background[1]) >= abs(background[2]):
        cast = "red" if background[1] > 0 else "green"
    else:
        cast = "yellow" if background[2] > 0 else "blue"
    metrics = {
        "background_lab": [round(float(value), 3) for value in background],
        "detected_cast": cast,
        "cast_strength": round(cast_strength, 3),
        "stretch": stretch,
        "saturation": saturation,
        "highlight_l": round(float(high_l), 3),
    }
    return result, metrics


def reduce_chroma_noise(image: Image.Image, radius: float = 0.65) -> Image.Image:
    y, cb, cr = image.convert("YCbCr").split()
    cb = cb.filter(ImageFilter.GaussianBlur(radius=radius))
    cr = cr.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def process_file(source: Path, target: Path, stretch: float, saturation: float) -> dict:
    with Image.open(source) as opened:
        rgb = np.asarray(opened.convert("RGB"))
        cleaned, metrics = cleanup_array(rgb, stretch=stretch, saturation=saturation)
        output = reduce_chroma_noise(Image.fromarray(cleaned, mode="RGB"))
        target.parent.mkdir(parents=True, exist_ok=True)
        output.save(target, quality=95, subsampling=0, optimize=True)
    return {"source": str(source), "output": str(target), **metrics}


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def collector_manifest_files(source: Path, manifest: Path) -> list[Path]:
    """Validate a collector manifest completely before returning its JPEGs."""
    source_root = source.resolve()
    try:
        resolved_manifest = manifest.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CollectorManifestError(f"could not resolve {manifest}: {exc}") from exc
    if not is_within(resolved_manifest, source_root):
        raise CollectorManifestError(f"manifest resolves outside the source directory: {manifest}")
    if not resolved_manifest.is_file():
        raise CollectorManifestError(f"collector manifest is not a file: {manifest}")

    try:
        with resolved_manifest.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle, delimiter="\t", strict=True))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CollectorManifestError(f"could not read collector manifest: {exc}") from exc

    if not rows or rows[0] != COLLECTOR_MANIFEST_HEADER:
        raise CollectorManifestError(
            "collector manifest header must be: " + "\t".join(COLLECTOR_MANIFEST_HEADER)
        )

    selected: list[Path] = []
    seen: set[Path] = set()
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(COLLECTOR_MANIFEST_HEADER) or any(not value for value in row):
            raise CollectorManifestError(f"malformed collector manifest row {line_number}")
        frames_text, local_file, _source_file = row
        try:
            frames = int(frames_text)
        except ValueError as exc:
            raise CollectorManifestError(
                f"collector manifest row {line_number} has a non-integer frame count"
            ) from exc
        if frames < 0:
            raise CollectorManifestError(
                f"collector manifest row {line_number} has a negative frame count"
            )

        candidate = Path(local_file).expanduser()
        if not candidate.is_absolute():
            candidate = source_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CollectorManifestError(
                f"collector manifest row {line_number} local_file does not exist: {local_file}"
            ) from exc
        if not is_within(resolved, source_root):
            raise CollectorManifestError(
                f"collector manifest row {line_number} local_file escapes the source directory: {local_file}"
            )
        if not resolved.is_file():
            raise CollectorManifestError(
                f"collector manifest row {line_number} local_file is not a file: {local_file}"
            )
        if resolved.suffix.lower() not in {".jpg", ".jpeg"}:
            raise CollectorManifestError(
                f"collector manifest row {line_number} local_file is not a JPEG: {local_file}"
            )
        try:
            with Image.open(resolved) as opened:
                if opened.format != "JPEG":
                    raise CollectorManifestError(
                        f"collector manifest row {line_number} local_file is not a JPEG: {local_file}"
                    )
                opened.verify()
        except CollectorManifestError:
            raise
        except (OSError, ValueError) as exc:
            raise CollectorManifestError(
                f"collector manifest row {line_number} local_file is not a valid JPEG: {local_file}"
            ) from exc
        if resolved in seen:
            raise CollectorManifestError(
                f"collector manifest row {line_number} repeats local_file: {local_file}"
            )
        seen.add(resolved)
        selected.append(resolved)
    return selected


def image_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.lower() in {".jpg", ".jpeg"} else []
    manifest = source / COLLECTOR_MANIFEST_NAME
    try:
        manifest.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise CollectorManifestError(f"could not inspect collector manifest: {exc}") from exc
    else:
        return collector_manifest_files(source, manifest)
    return sorted(
        (path for path in source.rglob("*") if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}),
        key=lambda path: str(path).casefold(),
    )


def stack_info(path: Path) -> tuple[int, str, bool] | None:
    match = STACKED_NAME.match(path.name)
    if not match:
        return None
    raw_object_name = match.group(2)
    is_mosaic = raw_object_name.casefold().startswith("mosaic_")
    object_name = raw_object_name[len("mosaic_"):] if is_mosaic else raw_object_name
    return int(match.group(1)), object_name, is_mosaic


def find_finished_image(
    processed_root: Path | None, object_name: str, *, is_mosaic: bool = False
) -> Path | None:
    if processed_root is None or not processed_root.is_dir():
        return None
    processed_name = f"{object_name}_mosaic" if is_mosaic else object_name
    normalized = processed_name.casefold().replace(" ", "")
    proc_folders = [
        path for path in processed_root.rglob("proc")
        if path.is_dir() and path.parent.name.removesuffix("_sub").casefold().replace(" ", "") == normalized
    ]
    candidates = [
        path for folder in proc_folders for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    if not candidates:
        return None

    def rank(path: Path) -> tuple[int, int, int, int, str]:
        name = path.stem.casefold()
        try:
            with Image.open(path) as image:
                pixels = image.width * image.height
        except OSError:
            pixels = 0
        return (
            int("stretched" in name),
            int("graxpert" in name),
            int(path.suffix.lower() == ".png"),
            pixels,
            path.name,
        )

    return max(candidates, key=rank)


def familiar_output_name(item: PreparedImage) -> str:
    if item.finished:
        return f"{item.relative.stem}_hand_processed{item.finished.suffix.lower()}"
    return f"{item.relative.stem}_cleaned.jpg"


def output_names(items: list[PreparedImage]) -> list[str]:
    """Keep familiar flat names when unique and suffix every collision stably."""
    familiar = [familiar_output_name(item) for item in items]
    counts = Counter(name.casefold() for name in familiar)
    reserved = {name.casefold() for name in familiar if counts[name.casefold()] == 1}
    assigned: set[str] = set()
    results: list[str] = []

    for item, name in zip(items, familiar, strict=True):
        key = name.casefold()
        if counts[key] == 1:
            candidate = name
        else:
            output_path = Path(name)
            identity = item.relative.as_posix()
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            width = 10
            candidate = f"{output_path.stem}__{digest[:width]}{output_path.suffix}"
            while candidate.casefold() in assigned or candidate.casefold() in reserved:
                width += 2
                if width <= len(digest):
                    candidate = f"{output_path.stem}__{digest[:width]}{output_path.suffix}"
                else:
                    candidate = f"{output_path.stem}__{digest}-{len(assigned) + 1}{output_path.suffix}"
                    break
        assigned.add(candidate.casefold())
        results.append(candidate)
    return results


def destination_root(path: Path) -> Path:
    """Return a canonical destination root without following a root symlink."""
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError(f"destination root must not be a symlink: {requested}")
    resolved = requested.resolve(strict=False)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"destination root is not a directory: {resolved}")
    return resolved


def validate_destination_file(destination: Path, target: Path) -> None:
    """Reject target escapes, symlinks, and non-directory ancestors."""
    root = destination.absolute()
    candidate_target = target.absolute()
    try:
        relative = candidate_target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"destination path escapes the destination root: {target}") from exc
    if ".." in relative.parts:
        raise ValueError(f"destination path escapes the destination root: {target}")

    candidate = root
    if candidate.is_symlink():
        raise ValueError(f"destination path contains a symlink: {candidate}")
    if candidate.exists() and not candidate.is_dir():
        raise ValueError(f"destination root is not a directory: {candidate}")
    for index, component in enumerate(relative.parts):
        candidate /= component
        if candidate.is_symlink():
            raise ValueError(f"destination path contains a symlink: {candidate}")
        is_target = index == len(relative.parts) - 1
        if candidate.exists() and not is_target and not candidate.is_dir():
            raise ValueError(
                f"destination path has a non-directory ancestor: {candidate}"
            )
        if candidate.exists() and is_target and not candidate.is_file():
            raise ValueError(f"destination target is not a regular file: {candidate}")

    try:
        candidate_target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"destination path escapes the destination root: {target}") from exc


def write_manifest_atomic(manifest: Path, temporary: Path, records: list[dict]) -> None:
    """Write the cleanup manifest without following or partially replacing it."""
    payload = json.dumps(records, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(manifest)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def previous_outputs_to_prune(destination: Path) -> list[Path]:
    """Validate the previous flat output list without deleting anything."""
    manifest = destination / CLEANUP_MANIFEST_NAME
    if not manifest.is_file():
        return []
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read the previous cleanup manifest: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError("the previous cleanup manifest must contain a list")

    outputs: list[Path] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict) or not isinstance(record.get("output"), str):
            raise ValueError(
                f"previous cleanup manifest record {index} has no valid output path"
            )
        output = Path(record["output"]).expanduser()
        if not output.is_absolute():
            output = destination / output
        if output.absolute().parent != destination.absolute():
            raise ValueError(
                f"previous cleanup output is not a direct child of the destination: {output}"
            )
        validate_destination_file(destination, output)
        outputs.append(output)
    return outputs


def prune_previous_outputs(outputs: list[Path]) -> None:
    for output in outputs:
        output.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Neutralize sky color and gently stretch Seestar JPEG stacks.")
    parser.add_argument("--source", required=True, type=Path, help="A JPEG or directory of JPEGs.")
    parser.add_argument("--destination", required=True, type=Path, help="Output directory; source files are never changed.")
    parser.add_argument("--stretch", type=float, default=7.0, help="Asinh stretch strength (default: 7.0).")
    parser.add_argument("--saturation", type=float, default=1.08, help="Color saturation multiplier (default: 1.08).")
    parser.add_argument("--min-frames", type=int, default=50, help="Skip stacks below this frame count (default: 50).")
    parser.add_argument("--processed-root", type=Path, help="Prefer matching finished images found under <object>_sub/proc folders.")
    parser.add_argument("--prune", action="store_true", help="Remove files listed by the previous manifest before writing this run.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.source.expanduser().resolve()
    try:
        destination = destination_root(args.destination)
    except ValueError as exc:
        print(f"Error: refusing unsafe cleanup destination: {exc}", file=sys.stderr)
        return 2
    processed_root = args.processed_root.expanduser().resolve() if args.processed_root else None
    if not source.exists():
        raise SystemExit(f"Source does not exist: {source}")
    if args.stretch <= 0 or args.saturation < 0:
        raise SystemExit("Stretch must be positive and saturation cannot be negative.")

    try:
        files = image_files(source)
    except CollectorManifestError as exc:
        print(f"Error: invalid {COLLECTOR_MANIFEST_NAME}: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("No JPEG files found.")
        return 0

    root = source if source.is_dir() else source.parent
    prepared: list[PreparedImage] = []
    for image in files:
        info = stack_info(image)
        if info is None:
            continue
        frames, object_name, is_mosaic = info
        if frames < args.min_frames:
            continue
        relative = image.relative_to(root)
        finished = find_finished_image(
            processed_root, object_name, is_mosaic=is_mosaic
        )
        prepared.append(PreparedImage(image, frames, object_name, relative, finished))

    names = output_names(prepared)
    plans = [
        (item, destination / output_name)
        for item, output_name in zip(prepared, names, strict=True)
    ]
    manifest = destination / CLEANUP_MANIFEST_NAME
    manifest_temporary = destination / (
        f".{CLEANUP_MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    )
    try:
        for _item, target in plans:
            validate_destination_file(destination, target)
        validate_destination_file(destination, manifest)
        validate_destination_file(destination, manifest_temporary)
        prune_outputs = previous_outputs_to_prune(destination) if args.prune else []
    except ValueError as exc:
        print(f"Error: refusing unsafe cleanup destination: {exc}", file=sys.stderr)
        return 2

    records = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if args.prune:
            prune_previous_outputs(prune_outputs)
        for item, target in plans:
            if item.finished:
                shutil.copy2(item.finished, target)
                record = {
                    "source": str(item.source), "output": str(target), "frames": item.frames,
                    "object": item.object_name, "provenance": "hand-processed",
                    "preferred_source": str(item.finished),
                }
                print(f"Preferred finished image: {item.finished} -> {target}")
            else:
                record = process_file(item.source, target, args.stretch, args.saturation)
                record.update({
                    "frames": item.frames,
                    "object": item.object_name,
                    "provenance": "automatic-cleanup",
                })
                print(f"Cleaned: {item.source} -> {target}")
            records.append(record)
        write_manifest_atomic(manifest, manifest_temporary, records)
    except (OSError, ValueError) as exc:
        print(f"Error while writing cleanup outputs: {exc}", file=sys.stderr)
        return 1

    preferred = sum(record["provenance"] == "hand-processed" for record in records)
    print(f"Prepared {len(records)} image(s), including {preferred} preferred hand-processed image(s). Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
