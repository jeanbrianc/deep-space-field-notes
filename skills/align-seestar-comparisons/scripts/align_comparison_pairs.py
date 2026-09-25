#!/usr/bin/env python3
"""Audit and align NightSkyAI previews to immutable Gallery edit references.

The matcher uses point-source asterisms rather than RGB pixels, so it remains
useful when the two treatments have different stretches, colors, crops, or
resolutions.  It is deliberately fail-closed: ``audit`` never edits images,
and ``apply`` only publishes derivatives after every pair passes both the
initial match and a pixel-level re-audit of the registered output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
except ImportError as exc:  # pragma: no cover - exercised by dependency check
    raise SystemExit(
        "Alignment requires NumPy and Pillow. Use the Codex workspace Python "
        "runtime or install the bounded packages listed in the skill package's "
        "requirements-alignment.txt."
    ) from exc


TOOL = "seestar_pair_alignment"
ALGORITHM = "deterministic-star-triangle-similarity-v1"
SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2
DETECTION_WIDTH = 270
MAX_STARS = 140
CAPTURE_ID_PATTERN = re.compile(r"^capture-[a-f0-9]{24}$")

MIN_INLIERS = 25
MIN_INLIER_RATIO = 0.60
MAX_AUDIT_RMS = 2.0
MIN_WINNER_MULTIPLIER = 2.0
MIN_COVERAGE_CELLS = 3

# Registration can legitimately leave fewer reference stars inside the source
# footprint after a large Gallery-directed rotation.  Identity, scale,
# translation, RMS, and a high absolute inlier count remain mandatory.
VERIFY_MIN_RATIO = 0.50
VERIFY_MAX_RMS = 1.5
VERIFY_MAX_ROTATION_DEGREES = 0.5
VERIFY_MAX_SCALE_DELTA = 0.01
# The second JPEG decode and centroiding pass can move a fitted origin by a few
# source pixels even when the stars themselves are sub-pixel aligned.  Keep the
# bound tiny relative to a 1080x1920 frame while avoiding a false failure on
# that harmless intercept noise.
VERIFY_MAX_TRANSLATION = 8.0


@dataclass(frozen=True)
class StarCatalog:
    points: np.ndarray
    width: int
    height: int
    scale_to_original: float


@dataclass(frozen=True)
class Candidate:
    reflected: bool
    a: complex
    b: complex
    matches: tuple[tuple[int, int, float], ...]
    eligible: int
    ratio: float
    rms: float
    coverage_cells: int
    score: float

    @property
    def rotation_degrees(self) -> float:
        return _normalize_degrees(math.degrees(math.atan2(self.a.imag, self.a.real)))

    @property
    def scale(self) -> float:
        return abs(self.a)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_degrees(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(normalized, -180.0) else normalized


def _rounded(value: float, digits: int) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _confined(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes the gallery root: {relative}") from exc
    return candidate


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(encoded)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def _open_displayed(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def _require_displayed_dimensions(path: Path, record: dict[str, Any], label: str) -> tuple[int, int]:
    width = record.get("width")
    height = record.get("height")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height <= 0
    ):
        raise ValueError(f"{label} has invalid declared dimensions: {width}x{height}")

    actual = _open_displayed(path).size
    expected = (width, height)
    if actual != expected:
        raise ValueError(
            f"{label} dimension mismatch: manifest declares {width}x{height}, "
            f"displayed image is {actual[0]}x{actual[1]}"
        )
    return actual


def _reject_already_aligned_manifest(manifest: dict[str, Any]) -> None:
    captures = manifest.get("captures")
    manifest_declares_alignment = "alignment" in manifest
    aligned_captures = []
    if isinstance(captures, list):
        aligned_captures = [
            capture.get("object", capture.get("captureId", "unknown target"))
            for capture in captures
            if isinstance(capture, dict)
            and isinstance(capture.get("nightSkyAI"), dict)
            and "alignment" in capture["nightSkyAI"]
        ]
    if manifest_declares_alignment or aligned_captures:
        detail = f" ({', '.join(aligned_captures[:3])})" if aligned_captures else ""
        raise ValueError(
            "Comparison manifest already contains Gallery-aligned derivatives"
            f"{detail}; use verify instead of auditing a derivative as source"
        )


def _detect_stars(path: Path, max_stars: int = MAX_STARS) -> StarCatalog:
    image = _open_displayed(path).convert("L")
    scale = DETECTION_WIDTH / image.width
    height = max(1, round(image.height * scale))
    image = image.resize((DETECTION_WIDTH, height), Image.Resampling.LANCZOS)

    fine = np.asarray(image.filter(ImageFilter.GaussianBlur(0.8)), dtype=np.float32)
    coarse = np.asarray(image.filter(ImageFilter.GaussianBlur(4.0)), dtype=np.float32)
    response = fine - coarse

    maxima = np.ones(response.shape, dtype=bool)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            if dx == 0 and dy == 0:
                continue
            maxima &= response > np.roll(np.roll(response, dy, axis=0), dx, axis=1)
    maxima[:6, :] = False
    maxima[-6:, :] = False
    maxima[:, :6] = False
    maxima[:, -6:] = False

    ys, xs = np.where(maxima & (response >= 5.0) & (fine >= 25.0))
    strengths = response[ys, xs]
    order = np.argsort(strengths, kind="stable")[::-1]

    selected: list[tuple[float, float]] = []
    for index in order:
        point = (float(xs[index]), float(ys[index]))
        if any((point[0] - x) ** 2 + (point[1] - y) ** 2 < 36.0 for x, y in selected):
            continue
        selected.append(point)
        if len(selected) == max_stars:
            break

    if len(selected) < 12:
        raise ValueError(f"Only {len(selected)} usable stars detected in {path.name}")

    return StarCatalog(
        points=np.asarray(selected, dtype=np.float64),
        width=image.width,
        height=image.height,
        scale_to_original=scale,
    )


def _triangle_catalog(points: np.ndarray, neighbors: int = 8) -> tuple[np.ndarray, np.ndarray]:
    distances = ((points[:, None, :] - points[None, :, :]) ** 2).sum(axis=2)
    seen: set[tuple[int, int, int]] = set()
    descriptors: list[np.ndarray] = []
    triangles: list[np.ndarray] = []

    for anchor in range(len(points)):
        nearby = np.argsort(distances[anchor], kind="stable")[1 : neighbors + 1]
        for left in range(len(nearby)):
            for right in range(left + 1, len(nearby)):
                indices = tuple(sorted((anchor, int(nearby[left]), int(nearby[right]))))
                if indices in seen:
                    continue
                seen.add(indices)
                triangle = points[list(indices)]
                opposite = np.asarray(
                    [
                        np.linalg.norm(triangle[1] - triangle[2]),
                        np.linalg.norm(triangle[0] - triangle[2]),
                        np.linalg.norm(triangle[0] - triangle[1]),
                    ]
                )
                order = np.argsort(opposite, kind="stable")
                sides = opposite[order]
                if sides[2] < 10.0 or sides[0] / sides[2] < 0.18 or sides[1] / sides[2] > 0.995:
                    continue
                descriptors.append(sides[:2] / sides[2])
                triangles.append(triangle[order])

    if not descriptors:
        raise ValueError("No stable star triangles were found")
    return np.asarray(descriptors), np.asarray(triangles)


def _fit_similarity(source: np.ndarray, reference: np.ndarray, reflected: bool) -> tuple[complex, complex, float]:
    source_complex = source[:, 0] + 1j * source[:, 1]
    reference_complex = reference[:, 0] + 1j * reference[:, 1]
    if reflected:
        source_complex = np.conj(source_complex)
    centered_source = source_complex - source_complex.mean()
    centered_reference = reference_complex - reference_complex.mean()
    denominator = float((np.abs(centered_source) ** 2).sum())
    if denominator <= 1e-12:
        raise ValueError("Degenerate star geometry")
    a = complex((np.conj(centered_source) * centered_reference).sum() / denominator)
    b = complex(reference_complex.mean() - a * source_complex.mean())
    predicted = a * source_complex + b
    rms = float(np.sqrt(np.mean(np.abs(predicted - reference_complex) ** 2)))
    return a, b, rms


def _apply_complex(points: np.ndarray, a: complex, b: complex, reflected: bool) -> np.ndarray:
    values = points[:, 0] + 1j * points[:, 1]
    if reflected:
        values = np.conj(values)
    transformed = a * values + b
    return np.column_stack((transformed.real, transformed.imag))


def _assign_unique(
    source: np.ndarray,
    reference: np.ndarray,
    a: complex,
    b: complex,
    reflected: bool,
    tolerance: float,
) -> tuple[tuple[int, int, float], ...]:
    transformed = _apply_complex(source, a, b, reflected)
    distances = ((transformed[:, None, :] - reference[None, :, :]) ** 2).sum(axis=2)
    possible = np.argwhere(distances <= tolerance * tolerance)
    ordered = sorted(
        ((float(distances[i, j]), int(i), int(j)) for i, j in possible),
        key=lambda item: (item[0], item[1], item[2]),
    )
    used_source: set[int] = set()
    used_reference: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for distance_squared, source_index, reference_index in ordered:
        if source_index in used_source or reference_index in used_reference:
            continue
        used_source.add(source_index)
        used_reference.add(reference_index)
        matches.append((source_index, reference_index, distance_squared))
    return tuple(matches)


def _coverage_cells(reference: np.ndarray, matches: Iterable[tuple[int, int, float]], width: int, height: int) -> int:
    cells: set[tuple[int, int]] = set()
    for _, reference_index, _ in matches:
        x, y = reference[reference_index]
        cells.add((min(2, int(3 * x / width)), min(2, int(3 * y / height))))
    return len(cells)


def _candidate_from_transform(
    source: StarCatalog,
    reference: StarCatalog,
    a: complex,
    b: complex,
    reflected: bool,
) -> Candidate | None:
    matches: tuple[tuple[int, int, float], ...] = ()
    for tolerance in (5.0, 4.0, 3.5, 3.5):
        matches = _assign_unique(source.points, reference.points, a, b, reflected, tolerance)
        if len(matches) < 3:
            return None
        source_inliers = np.asarray([source.points[i] for i, _, _ in matches])
        reference_inliers = np.asarray([reference.points[j] for _, j, _ in matches])
        a, b, _ = _fit_similarity(source_inliers, reference_inliers, reflected)

    matches = _assign_unique(source.points, reference.points, a, b, reflected, 3.5)
    if not matches:
        return None
    transformed = _apply_complex(source.points, a, b, reflected)
    inside = int(
        (
            (transformed[:, 0] >= 0)
            & (transformed[:, 0] < reference.width)
            & (transformed[:, 1] >= 0)
            & (transformed[:, 1] < reference.height)
        ).sum()
    )
    eligible = max(1, min(len(reference.points), inside))
    ratio = len(matches) / eligible
    rms = math.sqrt(sum(item[2] for item in matches) / len(matches))
    coverage = _coverage_cells(reference.points, matches, reference.width, reference.height)
    score = len(matches) + 20.0 * ratio - rms
    return Candidate(reflected, a, b, matches, eligible, ratio, rms, coverage, score)


def _descriptor_pairs(
    source_descriptors: np.ndarray,
    reference_descriptors: np.ndarray,
) -> Iterable[tuple[int, int, float]]:
    bin_width = 0.012
    tolerance = 0.018
    bins: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, descriptor in enumerate(reference_descriptors):
        bins[(round(float(descriptor[0] / bin_width)), round(float(descriptor[1] / bin_width)))].append(index)

    for source_index, descriptor in enumerate(source_descriptors):
        center = (round(float(descriptor[0] / bin_width)), round(float(descriptor[1] / bin_width)))
        candidates: list[tuple[float, int]] = []
        for delta_x in (-2, -1, 0, 1, 2):
            for delta_y in (-2, -1, 0, 1, 2):
                for reference_index in bins.get((center[0] + delta_x, center[1] + delta_y), []):
                    distance = float(np.linalg.norm(descriptor - reference_descriptors[reference_index]))
                    if distance <= tolerance:
                        candidates.append((distance, reference_index))
        for distance, reference_index in sorted(candidates, key=lambda item: (item[0], item[1]))[:8]:
            yield source_index, reference_index, distance


def _transform_bin(reflected: bool, a: complex, b: complex) -> tuple[int, int, int, int, int]:
    angle = _normalize_degrees(math.degrees(math.atan2(a.imag, a.real)))
    return (
        int(reflected),
        round(angle / 2.0),
        round(math.log(max(abs(a), 1e-12)) / 0.025),
        round(b.real / 4.0),
        round(b.imag / 4.0),
    )


def _same_solution(left: Candidate, right: Candidate) -> bool:
    if left.reflected != right.reflected:
        return False
    angle_delta = abs(_normalize_degrees(left.rotation_degrees - right.rotation_degrees))
    scale_delta = abs(math.log(left.scale / right.scale))
    translation_delta = abs(left.b - right.b)
    return angle_delta <= 4.0 and scale_delta <= 0.05 and translation_delta <= 12.0


def _match_catalogs(source: StarCatalog, reference: StarCatalog) -> tuple[Candidate, Candidate | None]:
    source_descriptors, source_triangles = _triangle_catalog(source.points)
    reference_descriptors, reference_triangles = _triangle_catalog(reference.points)

    clusters: dict[tuple[int, int, int, int, int], dict[str, Any]] = {}
    for source_index, reference_index, descriptor_distance in _descriptor_pairs(
        source_descriptors, reference_descriptors
    ):
        source_triangle = source_triangles[source_index]
        reference_triangle = reference_triangles[reference_index]
        for reflected in (False, True):
            try:
                a, b, residual = _fit_similarity(source_triangle, reference_triangle, reflected)
            except ValueError:
                continue
            if residual > 3.0 or not (0.35 <= abs(a) <= 3.0):
                continue
            key = _transform_bin(reflected, a, b)
            rank = (residual, descriptor_distance)
            cluster = clusters.get(key)
            if cluster is None:
                clusters[key] = {"votes": 1, "rank": rank, "a": a, "b": b, "reflected": reflected}
            else:
                cluster["votes"] += 1
                if rank < cluster["rank"]:
                    cluster.update({"rank": rank, "a": a, "b": b})

    if not clusters:
        raise ValueError("No plausible star-pattern transform was found")

    ranked_clusters = sorted(
        clusters.values(),
        key=lambda item: (-item["votes"], item["rank"], int(item["reflected"])),
    )[:80]
    candidates: list[Candidate] = []
    for cluster in ranked_clusters:
        candidate = _candidate_from_transform(
            source,
            reference,
            cluster["a"],
            cluster["b"],
            cluster["reflected"],
        )
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        raise ValueError("Star-pattern hypotheses did not produce a verified transform")
    candidates.sort(key=lambda item: (-item.score, -len(item.matches), item.rms))
    winner = candidates[0]
    alternate = next((item for item in candidates[1:] if not _same_solution(winner, item)), None)
    return winner, alternate


def _candidate_status(candidate: Candidate, alternate: Candidate | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if len(candidate.matches) < MIN_INLIERS:
        reasons.append(f"only {len(candidate.matches)} matched stars")
    if candidate.ratio < MIN_INLIER_RATIO:
        reasons.append(f"inlier ratio {candidate.ratio:.3f} is below {MIN_INLIER_RATIO:.2f}")
    if candidate.rms > MAX_AUDIT_RMS:
        reasons.append(f"RMS {candidate.rms:.3f}px exceeds {MAX_AUDIT_RMS:.1f}px")
    if candidate.coverage_cells < MIN_COVERAGE_CELLS:
        reasons.append(f"matches cover only {candidate.coverage_cells} grid cells")
    if alternate and len(candidate.matches) < math.ceil(MIN_WINNER_MULTIPLIER * len(alternate.matches)):
        reasons.append(
            f"winner has {len(candidate.matches)} matches versus {len(alternate.matches)} for the alternate"
        )
    if reasons:
        return "needs-review", reasons
    if len(candidate.matches) < 40:
        return "review-recommended", ["decisive match, but the Gallery crop leaves fewer than 40 shared stars"]
    return "high-confidence", []


def _forward_affine(candidate: Candidate, source: StarCatalog, reference: StarCatalog) -> list[float]:
    factor = source.scale_to_original / reference.scale_to_original
    a = candidate.a * factor
    b = candidate.b / reference.scale_to_original
    if candidate.reflected:
        matrix = [a.real, a.imag, b.real, a.imag, -a.real, b.imag]
    else:
        matrix = [a.real, -a.imag, b.real, a.imag, a.real, b.imag]
    return [_rounded(float(value), 10) for value in matrix]


def _candidate_payload(candidate: Candidate | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "reflected": candidate.reflected,
        "rotationDegrees": _rounded(candidate.rotation_degrees, 4),
        "scale": _rounded(candidate.scale, 6),
        "matchedStars": len(candidate.matches),
        "eligibleStars": candidate.eligible,
        "inlierRatio": _rounded(candidate.ratio, 4),
        "rmsPixels": _rounded(candidate.rms, 4),
        "coverageCells": candidate.coverage_cells,
        "score": _rounded(candidate.score, 4),
    }


def _audit_pair(gallery: Path, capture: dict[str, Any]) -> dict[str, Any]:
    capture_id = capture["captureId"]
    if not CAPTURE_ID_PATTERN.fullmatch(capture_id):
        raise ValueError(f"Invalid capture ID: {capture_id}")
    baseline_relative = capture["baseline"]["filename"]
    source_relative = capture["nightSkyAI"]["filename"]
    baseline_path = _confined(gallery / "public/images", baseline_relative)
    source_path = _confined(gallery / "public/comparisons", source_relative)

    baseline_hash = _sha256_path(baseline_path)
    source_hash = _sha256_path(source_path)
    if baseline_hash != capture["baseline"]["sha256"]:
        raise ValueError(f"Baseline hash mismatch for {capture['object']}")
    if source_hash != capture["nightSkyAI"]["sha256"]:
        raise ValueError(f"NightSkyAI hash mismatch for {capture['object']}")

    baseline_size = _require_displayed_dimensions(
        baseline_path, capture["baseline"], f"{capture['object']} Gallery edit"
    )
    source_size = _require_displayed_dimensions(
        source_path, capture["nightSkyAI"], f"{capture['object']} NightSkyAI source"
    )
    reference = _detect_stars(baseline_path)
    source = _detect_stars(source_path)
    winner, alternate = _match_catalogs(source, reference)
    status, reasons = _candidate_status(winner, alternate)

    return {
        "captureId": capture_id,
        "object": capture["object"],
        "status": status,
        "reasons": reasons,
        "baseline": {
            "filename": baseline_relative,
            "sha256": baseline_hash,
            "width": baseline_size[0],
            "height": baseline_size[1],
            "detectedStars": len(reference.points),
        },
        "nightSkyAISource": {
            "filename": source_relative,
            "sha256": source_hash,
            "width": source_size[0],
            "height": source_size[1],
            "detectedStars": len(source.points),
        },
        "transform": {
            **(_candidate_payload(winner) or {}),
            "forwardAffine": _forward_affine(winner, source, reference),
        },
        "alternate": _candidate_payload(alternate),
    }


def audit(gallery: Path, manifest_path: Path, report_path: Path) -> dict[str, Any]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    captures = manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise ValueError("Comparison manifest has no captures")
    _reject_already_aligned_manifest(manifest)

    results = [_audit_pair(gallery, capture) for capture in captures]
    counts: dict[str, int] = defaultdict(int)
    for result in results:
        counts[result["status"]] += 1
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "tool": TOOL,
        "algorithm": ALGORITHM,
        "createdAt": _utc_now(),
        "referencePolicy": "gallery-edit-is-immutable",
        "sourceManifest": str(manifest_path.relative_to(gallery)),
        "sourceManifestSha256": _sha256_bytes(manifest_bytes),
        "captureCount": len(results),
        "statusCounts": dict(sorted(counts.items())),
        "captures": results,
    }
    _atomic_json(report_path, report)
    return report


def _inverse_affine(forward: list[float]) -> tuple[float, float, float, float, float, float]:
    matrix = np.asarray([[forward[0], forward[1]], [forward[3], forward[4]]], dtype=np.float64)
    translation = np.asarray([forward[2], forward[5]], dtype=np.float64)
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) <= 1e-10:
        raise ValueError("Alignment transform is singular")
    inverse = np.linalg.inv(matrix)
    offset = -inverse @ translation
    return (
        float(inverse[0, 0]),
        float(inverse[0, 1]),
        float(offset[0]),
        float(inverse[1, 0]),
        float(inverse[1, 1]),
        float(offset[1]),
    )


def _render_registered(source_path: Path, reference_size: tuple[int, int], forward: list[float]) -> bytes:
    source = _open_displayed(source_path)
    aligned = source.transform(
        reference_size,
        Image.Transform.AFFINE,
        _inverse_affine(forward),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0),
    )
    output = io.BytesIO()
    aligned.save(output, format="JPEG", quality=95, subsampling=0, optimize=True)
    return output.getvalue()


def _verify_registered(output_path: Path, baseline_path: Path) -> dict[str, Any]:
    source = _detect_stars(output_path)
    reference = _detect_stars(baseline_path)
    winner, alternate = _match_catalogs(source, reference)
    translation = abs(winner.b) / reference.scale_to_original
    rotation = abs(winner.rotation_degrees)
    alternate_matches = len(alternate.matches) if alternate else 0
    decisive = alternate is None or len(winner.matches) >= math.ceil(
        MIN_WINNER_MULTIPLIER * alternate_matches
    )
    passed = (
        not winner.reflected
        and rotation <= VERIFY_MAX_ROTATION_DEGREES
        and abs(winner.scale - 1.0) <= VERIFY_MAX_SCALE_DELTA
        and translation <= VERIFY_MAX_TRANSLATION
        and len(winner.matches) >= MIN_INLIERS
        and winner.ratio >= VERIFY_MIN_RATIO
        and winner.rms <= VERIFY_MAX_RMS
        and winner.coverage_cells >= MIN_COVERAGE_CELLS
        and decisive
    )
    return {
        "passed": passed,
        "reflected": winner.reflected,
        "rotationDegrees": _rounded(winner.rotation_degrees, 4),
        "scale": _rounded(winner.scale, 6),
        "translationPixels": _rounded(translation, 4),
        "matchedStars": len(winner.matches),
        "coverageCells": winner.coverage_cells,
        "inlierRatio": _rounded(winner.ratio, 4),
        "rmsPixels": _rounded(winner.rms, 4),
        "alternateMatchedStars": alternate_matches,
        "decisive": decisive,
    }


def _comparison_id(capture_id: str, baseline_hash: str, nightsky_hash: str) -> str:
    material = f"{capture_id}\0{baseline_hash}\0{nightsky_hash}".encode("utf-8")
    return f"comparison-{hashlib.sha256(material).hexdigest()[:24]}"


def apply_alignment(
    gallery: Path,
    manifest_path: Path,
    report_path: Path,
    output_directory: Path,
    update_manifest: bool,
    allow_review_recommended: bool,
) -> dict[str, Any]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("algorithm") != ALGORITHM or report.get("tool") != TOOL:
        raise ValueError("Audit report was produced by an unsupported algorithm")
    if report.get("sourceManifestSha256") != _sha256_bytes(manifest_bytes):
        raise ValueError("Comparison manifest changed after the audit; run audit again")

    report_by_id = {item["captureId"]: item for item in report["captures"]}
    captures = manifest["captures"]
    if set(report_by_id) != {capture["captureId"] for capture in captures}:
        raise ValueError("Audit report and comparison manifest contain different captures")

    for item in report["captures"]:
        if item["status"] == "needs-review":
            raise ValueError(f"{item['object']} needs review and cannot be applied automatically")
        if item["status"] == "review-recommended" and not allow_review_recommended:
            raise ValueError(
                f"{item['object']} is review-recommended; inspect it and rerun with "
                "--allow-review-recommended"
            )

    comparison_root = (gallery / "public/comparisons").resolve()
    output_directory = output_directory.resolve()
    try:
        output_directory.relative_to(comparison_root)
    except ValueError as exc:
        raise ValueError("Aligned outputs must stay within public/comparisons") from exc
    output_directory.mkdir(parents=True, exist_ok=True)

    staged: list[tuple[Path, Path, bytes, dict[str, Any]]] = []
    with tempfile.TemporaryDirectory(prefix="seestar-alignment-", dir=output_directory.parent) as temporary:
        staging_directory = Path(temporary)
        for capture in captures:
            item = report_by_id[capture["captureId"]]
            baseline_path = _confined(gallery / "public/images", item["baseline"]["filename"])
            source_path = _confined(comparison_root, item["nightSkyAISource"]["filename"])
            if _sha256_path(baseline_path) != item["baseline"]["sha256"]:
                raise ValueError(f"Baseline changed after audit for {item['object']}")
            if _sha256_path(source_path) != item["nightSkyAISource"]["sha256"]:
                raise ValueError(f"NightSkyAI source changed after audit for {item['object']}")

            reference_size = _require_displayed_dimensions(
                baseline_path, item["baseline"], f"{item['object']} Gallery edit"
            )
            _require_displayed_dimensions(
                source_path, item["nightSkyAISource"], f"{item['object']} NightSkyAI source"
            )
            image_bytes = _render_registered(
                source_path,
                reference_size,
                item["transform"]["forwardAffine"],
            )
            target_name = f"{capture['captureId']}.jpg"
            staged_path = staging_directory / target_name
            staged_path.write_bytes(image_bytes)
            _require_displayed_dimensions(
                staged_path,
                {"width": reference_size[0], "height": reference_size[1]},
                f"{item['object']} aligned derivative",
            )
            verification = _verify_registered(staged_path, baseline_path)
            if not verification["passed"]:
                raise ValueError(f"Aligned output failed verification for {item['object']}: {verification}")
            staged.append((staged_path, output_directory / target_name, image_bytes, verification))

        updated_manifest = copy.deepcopy(manifest)
        updated_report = copy.deepcopy(report)
        updated_report_by_id = {item["captureId"]: item for item in updated_report["captures"]}
        relative_output_directory = output_directory.relative_to(comparison_root)
        applied_at = _utc_now()

        for capture, (staged_path, target_path, image_bytes, verification) in zip(
            updated_manifest["captures"], staged, strict=True
        ):
            item = report_by_id[capture["captureId"]]
            manually_accepted = (
                item["status"] == "review-recommended" and allow_review_recommended
            )
            review_metadata = {
                "auditStatus": item["status"],
                "reasons": list(item.get("reasons", [])),
                "manualAcceptance": manually_accepted,
                "acceptanceBasis": (
                    "explicit --allow-review-recommended"
                    if manually_accepted
                    else "automatic-thresholds"
                ),
                "acceptedAt": applied_at,
            }
            existing = target_path.read_bytes() if target_path.exists() else None
            if existing is not None and existing != image_bytes:
                raise ValueError(f"Refusing to overwrite a different aligned output: {target_path.name}")
            if existing is None:
                os.replace(staged_path, target_path)

            aligned_hash = _sha256_bytes(image_bytes)
            aligned_relative = str(relative_output_directory / target_path.name)
            original_nightsky = copy.deepcopy(capture["nightSkyAI"])
            capture["comparisonId"] = _comparison_id(
                capture["captureId"], capture["baseline"]["sha256"], aligned_hash
            )
            capture["curatedDefault"] = "seestar"
            capture["curatedDefaultSource"] = "orientation-aligned-default"
            capture["nightSkyAI"].update(
                {
                    "filename": aligned_relative,
                    "sha256": aligned_hash,
                    "width": item["baseline"]["width"],
                    "height": item["baseline"]["height"],
                    "alignment": {
                        "algorithm": ALGORITHM,
                        "mode": "registered-to-gallery-edit",
                        "referencePolicy": "gallery-edit-is-immutable",
                        "sourceFilename": original_nightsky["filename"],
                        "sourceSha256": original_nightsky["sha256"],
                        "sourceWidth": original_nightsky["width"],
                        "sourceHeight": original_nightsky["height"],
                        "reflected": item["transform"]["reflected"],
                        "rotationDegrees": item["transform"]["rotationDegrees"],
                        "forwardAffine": item["transform"]["forwardAffine"],
                        "matchedStars": item["transform"]["matchedStars"],
                        "inlierRatio": item["transform"]["inlierRatio"],
                        "rmsPixels": item["transform"]["rmsPixels"],
                        "review": review_metadata,
                        "verification": verification,
                    },
                }
            )
            updated_report_by_id[capture["captureId"]]["output"] = {
                "filename": aligned_relative,
                "sha256": aligned_hash,
                "width": item["baseline"]["width"],
                "height": item["baseline"]["height"],
                "review": review_metadata,
                "verification": verification,
            }
            updated_report_by_id[capture["captureId"]]["applicationDecision"] = review_metadata

        updated_manifest["schemaVersion"] = MANIFEST_SCHEMA_VERSION
        updated_manifest["createdAt"] = applied_at
        updated_manifest["defaultedToSeestarCount"] = len(updated_manifest["captures"])
        updated_manifest["alignment"] = {
            "algorithm": ALGORITHM,
            "referencePolicy": "gallery-edit-is-immutable",
            "mode": "registered-to-gallery-edit",
            "sourceAudit": str(report_path.relative_to(gallery)),
        }
        updated_report["appliedAt"] = applied_at
        updated_report["appliedCaptureCount"] = len(staged)
        _atomic_json(report_path, updated_report)
        if update_manifest:
            _atomic_json(manifest_path, updated_manifest)

    return updated_manifest


def verify_manifest(gallery: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    captures = manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise ValueError("Comparison manifest has no captures")

    results: list[dict[str, Any]] = []
    comparison_root = gallery / "public/comparisons"
    for capture in captures:
        capture_id = capture.get("captureId", "")
        if not CAPTURE_ID_PATTERN.fullmatch(capture_id):
            raise ValueError(f"Invalid capture ID: {capture_id}")
        alignment = capture["nightSkyAI"].get("alignment")
        if not alignment:
            raise ValueError(f"{capture['object']} has no alignment metadata")
        baseline = _confined(gallery / "public/images", capture["baseline"]["filename"])
        aligned = _confined(comparison_root, capture["nightSkyAI"]["filename"])
        source_record = {
            "width": alignment.get("sourceWidth"),
            "height": alignment.get("sourceHeight"),
        }
        source_filename = alignment.get("sourceFilename")
        source_hash = alignment.get("sourceSha256")
        if not isinstance(source_filename, str) or not source_filename:
            raise ValueError(f"{capture['object']} has no preserved source filename")
        if not isinstance(source_hash, str) or not source_hash:
            raise ValueError(f"{capture['object']} has no preserved source hash")
        source = _confined(comparison_root, source_filename)

        baseline_hash = _sha256_path(baseline)
        aligned_hash = _sha256_path(aligned)
        if baseline_hash != capture["baseline"]["sha256"]:
            raise ValueError(f"Gallery edit hash mismatch for {capture['object']}")
        if source_hash != _sha256_path(source):
            raise ValueError(f"NightSkyAI source hash mismatch for {capture['object']}")
        if aligned_hash != capture["nightSkyAI"]["sha256"]:
            raise ValueError(f"Aligned output hash mismatch for {capture['object']}")

        baseline_size = _require_displayed_dimensions(
            baseline, capture["baseline"], f"{capture['object']} Gallery edit"
        )
        _require_displayed_dimensions(
            source, source_record, f"{capture['object']} NightSkyAI source"
        )
        aligned_size = _require_displayed_dimensions(
            aligned, capture["nightSkyAI"], f"{capture['object']} aligned derivative"
        )
        if aligned_size != baseline_size:
            raise ValueError(
                f"Aligned output canvas does not match Gallery edit for {capture['object']}: "
                f"{aligned_size[0]}x{aligned_size[1]} versus {baseline_size[0]}x{baseline_size[1]}"
            )

        expected_comparison_id = _comparison_id(capture_id, baseline_hash, aligned_hash)
        if capture.get("comparisonId") != expected_comparison_id:
            raise ValueError(
                f"Comparison ID does not match displayed image hashes for {capture['object']}"
            )

        verification = _verify_registered(aligned, baseline)
        if not verification["passed"]:
            raise ValueError(f"Alignment verification failed for {capture['object']}: {verification}")
        results.append(
            {
                "captureId": capture_id,
                "comparisonId": expected_comparison_id,
                "object": capture["object"],
                **verification,
            }
        )
    return {"captureCount": len(results), "passed": True, "captures": results}


def _self_test() -> None:
    rng = np.random.default_rng(20260925)
    reference_points = rng.uniform([25.0, 25.0], [245.0, 455.0], size=(80, 2))
    angle = math.radians(17.0)
    a = 0.92 * complex(math.cos(angle), math.sin(angle))
    b = complex(22.0, 470.0)
    source_complex = reference_points[:, 0] + 1j * reference_points[:, 1]
    source_complex = np.conj((source_complex - b) / a)
    source_points = np.column_stack((source_complex.real, source_complex.imag))
    source = StarCatalog(source_points, 270, 480, 1.0)
    reference = StarCatalog(reference_points, 270, 480, 1.0)
    winner, alternate = _match_catalogs(source, reference)
    assert winner.reflected
    assert len(winner.matches) >= 75
    assert winner.rms < 0.1
    assert alternate is None or len(winner.matches) >= 2 * len(alternate.matches)

    aligned_manifests = (
        {"alignment": {"mode": "registered-to-gallery-edit"}, "captures": []},
        {
            "captures": [
                {
                    "captureId": "capture-000000000000000000000000",
                    "object": "synthetic",
                    "nightSkyAI": {"alignment": {"mode": "registered-to-gallery-edit"}},
                }
            ]
        },
    )
    for aligned_manifest in aligned_manifests:
        try:
            _reject_already_aligned_manifest(aligned_manifest)
        except ValueError as exc:
            assert "already contains Gallery-aligned derivatives" in str(exc)
        else:  # pragma: no cover - defensive assertion in the executable self-test
            raise AssertionError("Aligned manifests must be rejected by audit")

    _reject_already_aligned_manifest(
        {
            "captures": [
                {
                    "captureId": "capture-000000000000000000000000",
                    "nightSkyAI": {"filename": "images/synthetic.jpg"},
                }
            ]
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit", help="Audit every current comparison without editing images")
    audit_parser.add_argument("--gallery", type=Path, required=True)
    audit_parser.add_argument("--manifest", type=Path, default=Path("public/comparisons/manifest.json"))
    audit_parser.add_argument("--report", type=Path, required=True)

    apply_parser = subparsers.add_parser("apply", help="Create verified aligned derivatives from an audit")
    apply_parser.add_argument("--gallery", type=Path, required=True)
    apply_parser.add_argument("--manifest", type=Path, default=Path("public/comparisons/manifest.json"))
    apply_parser.add_argument("--audit", type=Path, required=True)
    apply_parser.add_argument("--output-dir", type=Path, default=Path("public/comparisons/aligned-v1"))
    apply_parser.add_argument("--update-manifest", action="store_true")
    apply_parser.add_argument("--allow-review-recommended", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Re-audit aligned images declared by a manifest")
    verify_parser.add_argument("--gallery", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, default=Path("public/comparisons/manifest.json"))

    subparsers.add_parser("self-test", help="Run the deterministic synthetic matcher test")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "self-test":
        _self_test()
        print("alignment self-test passed")
        return 0

    gallery = args.gallery.expanduser().resolve()
    manifest = args.manifest if args.manifest.is_absolute() else gallery / args.manifest
    manifest = manifest.resolve()
    if args.command == "audit":
        report = args.report if args.report.is_absolute() else gallery / args.report
        result = audit(gallery, manifest, report.resolve())
        print(json.dumps({"captureCount": result["captureCount"], "statusCounts": result["statusCounts"]}))
        return 0
    if args.command == "apply":
        report = args.audit if args.audit.is_absolute() else gallery / args.audit
        output = args.output_dir if args.output_dir.is_absolute() else gallery / args.output_dir
        result = apply_alignment(
            gallery,
            manifest,
            report.resolve(),
            output.resolve(),
            args.update_manifest,
            args.allow_review_recommended,
        )
        print(json.dumps({"captureCount": len(result["captures"]), "manifestUpdated": args.update_manifest}))
        return 0
    result = verify_manifest(gallery, manifest)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
