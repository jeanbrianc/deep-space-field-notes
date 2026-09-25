#!/usr/bin/env python3
"""Review current gallery images beside locally produced Siril stacks.

The local server exposes only discovered images through opaque IDs, stores each
human choice atomically, and exports winners into a new snapshot. It never
overwrites the gallery or any stack output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import sys
import threading
import unicodedata
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


TOOL_NAME = "seestar_stack_compare"
CHOICES_SCHEMA_VERSION = 1
EXPORT_SCHEMA_VERSION = 1
PUBLIC_EXPORT_SCHEMA_VERSION = 1
DEFAULT_MIN_FRAMES = 50
MIN_PUBLIC_AXIS_RETENTION = 0.90
MIN_PUBLIC_AREA_RETENTION = 0.90
PUBLIC_SEESTAR_WIDTH = 1080
PUBLIC_SEESTAR_HEIGHT = 1920
MAX_REQUEST_BYTES = 64 * 1024
SITE_TIMESTAMP_GRACE = timedelta(minutes=5)
CAPTURE_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"

SITE_FILENAME = re.compile(
    r"^Stacked_(?P<frames>\d+)_(?P<object>.+)_(?P<exposure>\d+(?:\.\d+)?)s_"
    r"(?P<filter>LP|IRCUT)_(?P<timestamp>\d{8}-\d{6})_"
    r"(?P<treatment>cleaned|hand_processed)\.(?P<extension>jpe?g|png)$",
    re.IGNORECASE,
)
CATALOG_NUMBER = re.compile(r"^(M|NGC|IC|C|SH2)[ -]?(\d+[A-Z]?)$", re.IGNORECASE)


@dataclass(frozen=True)
class MatchKey:
    object_id: str
    mosaic: bool
    exposure: str
    filter_name: str


@dataclass(frozen=True)
class SiteImage:
    path: Path
    filename: str
    frames: int
    object_name: str
    treatment: str
    timestamp: str
    key: MatchKey
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class LocalStack:
    path: Path
    manifest_path: Path
    frames: int
    object_name: str
    exposure_seconds: str
    filter_name: str
    capture_first_timestamp: str
    capture_last_timestamp: str
    capture_nights: tuple[str, ...]
    finished_at: str
    input_fingerprint: str
    preview_settings: dict[str, object]
    key: MatchKey
    candidate_id: str
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class ComparisonPair:
    pair_id: str
    target: str
    site: SiteImage
    ours: tuple[LocalStack, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path) -> tuple[int, int]:
    """Read JPEG or PNG dimensions without accepting malformed image headers."""

    try:
        with path.open("rb") as image:
            signature = image.read(24)
            if signature.startswith(b"\x89PNG\r\n\x1a\n"):
                if len(signature) < 24 or signature[12:16] != b"IHDR":
                    raise ValueError("PNG has no valid IHDR header")
                width = int.from_bytes(signature[16:20], "big")
                height = int.from_bytes(signature[20:24], "big")
            elif signature[:2] == b"\xff\xd8":
                image.seek(2)
                width = height = 0
                frame_markers = {
                    0xC0,
                    0xC1,
                    0xC2,
                    0xC3,
                    0xC5,
                    0xC6,
                    0xC7,
                    0xC9,
                    0xCA,
                    0xCB,
                    0xCD,
                    0xCE,
                    0xCF,
                }
                while True:
                    prefix = image.read(1)
                    if not prefix:
                        raise ValueError("JPEG has no start-of-frame marker")
                    if prefix != b"\xff":
                        continue
                    marker_byte = image.read(1)
                    while marker_byte == b"\xff":
                        marker_byte = image.read(1)
                    if not marker_byte:
                        raise ValueError("JPEG marker is truncated")
                    marker = marker_byte[0]
                    if marker == 0x00 or marker == 0x01 or 0xD0 <= marker <= 0xD9:
                        continue
                    length_bytes = image.read(2)
                    if len(length_bytes) != 2:
                        raise ValueError("JPEG segment length is truncated")
                    segment_length = int.from_bytes(length_bytes, "big")
                    if segment_length < 2:
                        raise ValueError("JPEG segment length is invalid")
                    if marker in frame_markers:
                        frame = image.read(segment_length - 2)
                        if len(frame) < 5:
                            raise ValueError("JPEG start-of-frame segment is truncated")
                        height = int.from_bytes(frame[1:3], "big")
                        width = int.from_bytes(frame[3:5], "big")
                        break
                    image.seek(segment_length - 2, 1)
            else:
                raise ValueError("file is not a recognized JPEG or PNG")
    except OSError as exc:
        raise ValueError(f"could not read image dimensions: {exc}") from exc
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    return width, height


def validate_public_framing(site: SiteImage, candidate: LocalStack) -> None:
    """Reject a candidate that retains less than a near-full baseline field."""

    site_width, site_height = image_dimensions(site.path)
    candidate_width, candidate_height = image_dimensions(candidate.path)
    if (site_width, site_height) != (site.width, site.height):
        raise ValueError(f"gallery image dimensions changed while exporting: {site.path}")
    if (candidate_width, candidate_height) != (candidate.width, candidate.height):
        raise ValueError(f"NightSkyAI image dimensions changed while exporting: {candidate.path}")
    if (candidate_width, candidate_height) != (
        PUBLIC_SEESTAR_WIDTH,
        PUBLIC_SEESTAR_HEIGHT,
    ):
        raise ValueError(
            f"NightSkyAI comparison for {site.object_name} is not the full vertical "
            f"Seestar field: {candidate_width}x{candidate_height}; expected "
            f"{PUBLIC_SEESTAR_WIDTH}x{PUBLIC_SEESTAR_HEIGHT}"
        )

    width_ratio = candidate_width / site_width
    height_ratio = candidate_height / site_height
    area_ratio = (candidate_width * candidate_height) / (site_width * site_height)
    if (
        width_ratio < MIN_PUBLIC_AXIS_RETENTION
        or height_ratio < MIN_PUBLIC_AXIS_RETENTION
        or area_ratio < MIN_PUBLIC_AREA_RETENTION
    ):
        raise ValueError(
            f"NightSkyAI comparison for {site.object_name} is visibly cropped: "
            f"{candidate_width}x{candidate_height} versus baseline "
            f"{site_width}x{site_height} "
            f"(width {width_ratio:.1%}, height {height_ratio:.1%}, area {area_ratio:.1%})"
        )


def canonical_object(raw_name: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFKC", raw_name).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    mosaic = bool(re.match(r"^mosaic(?:[_ -]+)", normalized, re.IGNORECASE))
    if mosaic:
        normalized = re.sub(r"^mosaic(?:[_ -]+)", "", normalized, flags=re.IGNORECASE)
    catalog = CATALOG_NUMBER.fullmatch(normalized)
    if catalog:
        object_id = f"{catalog.group(1).casefold()}:{catalog.group(2).casefold()}"
    else:
        object_id = normalized.casefold()
    return object_id, mosaic


def normalize_exposure(value: object) -> str:
    try:
        number = float(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid exposure value: {value!r}") from exc
    return f"{number:.6f}".rstrip("0").rstrip(".")


def parse_capture_timestamp(value: object) -> datetime:
    text = str(value)
    if not re.fullmatch(r"\d{8}-\d{6}", text):
        raise ValueError(f"invalid capture timestamp: {value!r}")
    try:
        return datetime.strptime(text, CAPTURE_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"invalid capture timestamp: {value!r}") from exc


def match_key(object_name: str, exposure: object, filter_name: object) -> MatchKey:
    object_id, mosaic = canonical_object(object_name)
    return MatchKey(object_id, mosaic, normalize_exposure(exposure), str(filter_name).strip().upper())


def site_catalog_filenames(gallery: Path) -> list[str]:
    page = gallery / "app" / "page.tsx"
    if not page.is_file():
        raise ValueError(f"gallery catalog does not exist: {page}")
    text = page.read_text(encoding="utf-8")
    block = re.search(r"const\s+imageFiles\s*=\s*\[(.*?)\];", text, re.DOTALL)
    if not block:
        raise ValueError(f"could not find imageFiles catalog in {page}")
    filenames = re.findall(r'"([^"\n]+\.(?:jpe?g|png))"', block.group(1), re.IGNORECASE)
    if not filenames:
        raise ValueError(f"imageFiles catalog is empty in {page}")
    if len(filenames) != len(set(filenames)):
        raise ValueError(f"imageFiles catalog contains duplicate filenames in {page}")
    return filenames


def load_site_images(gallery: Path, min_frames: int) -> tuple[list[SiteImage], list[str]]:
    images: list[SiteImage] = []
    warnings: list[str] = []
    image_root = gallery / "public" / "images"
    for filename in site_catalog_filenames(gallery):
        parsed = SITE_FILENAME.fullmatch(filename)
        if not parsed:
            warnings.append(f"Gallery filename is not recognized: {filename}")
            continue
        path = image_root / filename
        if not path.is_file():
            warnings.append(f"Gallery catalog image is missing: {path}")
            continue
        frames = int(parsed.group("frames"))
        if frames < min_frames:
            continue
        object_name = parsed.group("object")
        width, height = image_dimensions(path)
        images.append(
            SiteImage(
                path=path.resolve(),
                filename=filename,
                frames=frames,
                object_name=object_name,
                treatment=parsed.group("treatment").replace("_", " "),
                timestamp=parsed.group("timestamp"),
                key=match_key(object_name, parsed.group("exposure"), parsed.group("filter")),
                sha256=sha256_file(path),
                width=width,
                height=height,
            )
        )
    return images, warnings


def _manifest_output_path(manifest_path: Path, raw_path: object) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def load_local_stacks(root: Path, min_frames: int) -> tuple[list[LocalStack], list[str]]:
    stacks: list[LocalStack] = []
    warnings: list[str] = []
    if not root.is_dir():
        return [], [f"Local stack folder does not exist yet: {root}"]
    resolved_root = root.resolve()
    for manifest_path in sorted(root.rglob("manifest.json"), key=lambda path: str(path).casefold()):
        try:
            resolved_manifest = manifest_path.resolve()
            if not _is_relative_to(resolved_manifest, resolved_root):
                raise ValueError("manifest resolves outside the local stack root")
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest root must be an object")
            if payload.get("tool") != "seestar_siril_stack" or payload.get("schema_version") not in {1, 2}:
                raise ValueError("manifest tool or schema is not recognized")
            if payload.get("status") != "success":
                continue
            capture = payload["capture"]
            outputs = payload["outputs"]
            input_count = int(payload["input_count"])
            registered_count = int(payload["registered_count"])
            frames = int(payload["stacked_count"])
            if not 1 <= frames <= registered_count <= input_count:
                raise ValueError(
                    f"invalid frame counts: stacked={frames}, registered={registered_count}, inputs={input_count}"
                )
            preview_path = _manifest_output_path(manifest_path, outputs["preview_jpeg"])
            if not _is_relative_to(preview_path, resolved_manifest.parent):
                raise ValueError("preview JPEG resolves outside its owned run folder")
            if not preview_path.is_file():
                raise ValueError(f"preview JPEG is missing: {preview_path}")
            preview_width, preview_height = image_dimensions(preview_path)
            preview_sha256 = sha256_file(preview_path)
            if payload.get("schema_version") == 2:
                hashes = payload.get("output_sha256")
                if not isinstance(hashes, dict) or hashes.get("preview_jpeg") != preview_sha256:
                    raise ValueError("preview JPEG checksum does not match its manifest")
            if frames < min_frames:
                continue
            object_name = str(capture["object"])
            exposure_seconds = str(capture["exposure_seconds"]).strip()
            filter_name = str(capture["filter"]).strip().upper()
            key = match_key(object_name, exposure_seconds, filter_name)
            inputs = payload["inputs"]
            if not isinstance(inputs, list) or not inputs:
                raise ValueError("manifest inputs must be a non-empty list")
            input_timestamps: list[str] = []
            for item in inputs:
                if not isinstance(item, dict) or "timestamp" not in item:
                    raise ValueError("every manifest input must include a timestamp")
                timestamp = str(item["timestamp"])
                parse_capture_timestamp(timestamp)
                input_timestamps.append(timestamp)
            first_timestamp = min(input_timestamps)
            last_timestamp = max(input_timestamps)
            capture_nights = tuple(sorted({timestamp[:8] for timestamp in input_timestamps}))
            declared_first_timestamp = str(capture["first_timestamp"])
            parse_capture_timestamp(declared_first_timestamp)
            if declared_first_timestamp != first_timestamp:
                raise ValueError(
                    "capture.first_timestamp does not match the earliest input timestamp"
                )
            settings = payload.get("settings", {})
            if not isinstance(settings, dict):
                raise ValueError("manifest settings must be an object")
            preview_settings = {
                key: settings.get(key)
                for key in (
                    "preview_style",
                    "preview_brightness",
                    "preview_shadow_sigma",
                    "preview_rotation_degrees",
                    "preview_flip",
                    "jpeg_quality",
                )
            }
            fingerprint = str(payload["input_fingerprint"])
            identity = json.dumps(
                {
                    "manifest": str(manifest_path.resolve()),
                    "fingerprint": fingerprint,
                    "preview": preview_settings,
                    "preview_sha256": preview_sha256,
                },
                sort_keys=True,
            )
            candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            stacks.append(
                LocalStack(
                    path=preview_path,
                    manifest_path=manifest_path.resolve(),
                    frames=frames,
                    object_name=object_name,
                    exposure_seconds=exposure_seconds,
                    filter_name=filter_name,
                    capture_first_timestamp=first_timestamp,
                    capture_last_timestamp=last_timestamp,
                    capture_nights=capture_nights,
                    finished_at=str(payload.get("finished_at", "")),
                    input_fingerprint=fingerprint,
                    preview_settings=preview_settings,
                    key=key,
                    candidate_id=candidate_id,
                    sha256=preview_sha256,
                    width=preview_width,
                    height=preview_height,
                )
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            warnings.append(f"Ignoring invalid stack manifest {manifest_path}: {exc}")
    return stacks, warnings


def build_pairs(
    site_images: list[SiteImage], local_stacks: list[LocalStack]
) -> tuple[list[ComparisonPair], list[SiteImage], list[LocalStack]]:
    local_by_key: dict[MatchKey, list[LocalStack]] = {}
    for stack in local_stacks:
        if stack.key.object_id == "unknown":
            continue
        local_by_key.setdefault(stack.key, []).append(stack)
    for candidates in local_by_key.values():
        candidates.sort(
            key=lambda stack: (stack.frames, stack.finished_at, str(stack.manifest_path).casefold()),
            reverse=True,
        )

    pairs: list[ComparisonPair] = []
    unpaired_site: list[SiteImage] = []
    used_candidate_ids: set[str] = set()
    for site in site_images:
        if site.key.object_id == "unknown":
            unpaired_site.append(site)
            continue
        site_timestamp = parse_capture_timestamp(site.timestamp)
        candidates = [
            stack
            for stack in local_by_key.get(site.key, [])
            if parse_capture_timestamp(stack.capture_first_timestamp)
            <= site_timestamp
            <= parse_capture_timestamp(stack.capture_last_timestamp) + SITE_TIMESTAMP_GRACE
        ]
        if not candidates:
            unpaired_site.append(site)
            continue
        identity = json.dumps(
            {
                "site_filename": site.filename,
                "site_sha256": site.sha256,
                "candidate_ids": [candidate.candidate_id for candidate in candidates],
            },
            sort_keys=True,
        )
        pair_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        pairs.append(ComparisonPair(pair_id, site.object_name, site, tuple(candidates)))
        used_candidate_ids.update(candidate.candidate_id for candidate in candidates)

    pairs.sort(key=lambda pair: (pair.target.casefold(), pair.site.filename.casefold()))
    unpaired_site.sort(key=lambda image: image.filename.casefold())
    unpaired_local = sorted(
        (stack for stack in local_stacks if stack.candidate_id not in used_candidate_ids),
        key=lambda stack: (stack.object_name.casefold(), -stack.frames, str(stack.path).casefold()),
    )
    return pairs, unpaired_site, unpaired_local


def load_choices(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {
            "schema_version": CHOICES_SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "updated_at": None,
            "choices": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read choices {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), dict):
        raise ValueError(f"invalid choices document: {path}")
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def record_choice(
    choices_path: Path,
    pairs_by_id: dict[str, ComparisonPair],
    pair_id: str,
    choice: str,
    candidate_id: str | None = None,
) -> dict[str, object]:
    if choice not in {"site", "ours", "skip"}:
        raise ValueError("choice must be site, ours, or skip")
    pair = pairs_by_id.get(pair_id)
    if pair is None:
        raise ValueError("comparison is stale or unknown; refresh before choosing")
    selected: LocalStack | None = None
    if choice == "ours":
        selected = next((candidate for candidate in pair.ours if candidate.candidate_id == candidate_id), None)
        if selected is None:
            raise ValueError("choose one of the available local stack candidates")

    payload = load_choices(choices_path)
    choices = payload["choices"]
    assert isinstance(choices, dict)
    record: dict[str, object] = {
        "pair_id": pair_id,
        "target": pair.target,
        "choice": choice,
        "selected_at": utc_now(),
        "site_file": str(pair.site.path),
        "site_sha256": pair.site.sha256,
    }
    if selected:
        record.update(
            {
                "ours_candidate_id": selected.candidate_id,
                "ours_file": str(selected.path),
                "ours_manifest": str(selected.manifest_path),
                "ours_input_fingerprint": selected.input_fingerprint,
            }
        )
    choices[pair_id] = record
    payload["schema_version"] = CHOICES_SCHEMA_VERSION
    payload["tool"] = TOOL_NAME
    payload["updated_at"] = utc_now()
    write_json(choices_path, payload)
    return record


def resolve_saved_choice(
    pair: ComparisonPair, raw_choice: object
) -> tuple[str, LocalStack | None]:
    """Validate a saved choice against the exact comparison it belongs to.

    Returns ``missing``, ``skip``, ``site``, or ``ours``. A skip remains a
    deliberate UI marker, but is not a completed decision.
    """

    if raw_choice is None:
        return "missing", None
    if not isinstance(raw_choice, dict):
        raise ValueError(f"invalid saved choice for {pair.target}: record is not an object")
    expected = {
        "pair_id": pair.pair_id,
        "target": pair.target,
        "site_file": str(pair.site.path),
        "site_sha256": pair.site.sha256,
    }
    for key, value in expected.items():
        if raw_choice.get(key) != value:
            raise ValueError(f"stale saved choice for {pair.target}: {key} no longer matches")
    if not isinstance(raw_choice.get("selected_at"), str) or not raw_choice["selected_at"]:
        raise ValueError(f"invalid saved choice for {pair.target}: selected_at is missing")

    choice = raw_choice.get("choice")
    if choice == "skip":
        return "skip", None
    if choice == "site":
        return "site", None
    if choice != "ours":
        raise ValueError(f"invalid saved choice for {pair.target}: unrecognized choice")

    candidate_id = raw_choice.get("ours_candidate_id")
    candidate = next((item for item in pair.ours if item.candidate_id == candidate_id), None)
    if candidate is None:
        raise ValueError(f"stale local choice for {pair.target}; review it again")
    local_expected = {
        "ours_file": str(candidate.path),
        "ours_manifest": str(candidate.manifest_path),
        "ours_input_fingerprint": candidate.input_fingerprint,
    }
    for key, value in local_expected.items():
        if raw_choice.get(key) != value:
            raise ValueError(f"stale local choice for {pair.target}: {key} no longer matches")
    return "ours", candidate


def pair_to_api(pair: ComparisonPair, choice: object) -> dict[str, object]:
    return {
        "pair_id": pair.pair_id,
        "target": pair.target,
        "choice": choice,
        "site": {
            "filename": pair.site.filename,
            "frames": pair.site.frames,
            "treatment": pair.site.treatment,
            "timestamp": pair.site.timestamp,
            "media_url": f"/media/site-{pair.pair_id}",
        },
        "ours": [
            {
                "candidate_id": candidate.candidate_id,
                "filename": candidate.path.name,
                "frames": candidate.frames,
                "finished_at": candidate.finished_at,
                "settings": candidate.preview_settings,
                "media_url": f"/media/ours-{candidate.candidate_id}",
            }
            for candidate in pair.ours
        ],
    }


def api_payload(
    pairs: list[ComparisonPair],
    unpaired_site: list[SiteImage],
    unpaired_local: list[LocalStack],
    choices_payload: dict[str, object],
) -> dict[str, object]:
    choices = choices_payload.get("choices", {})
    assert isinstance(choices, dict)
    api_pairs: list[dict[str, object]] = []
    decided = 0
    invalid = 0
    for pair in pairs:
        raw_choice = choices.get(pair.pair_id)
        try:
            status, _candidate = resolve_saved_choice(pair, raw_choice)
        except ValueError:
            status = "invalid"
            invalid += 1
        if status in {"site", "ours"}:
            decided += 1
        visible_choice = raw_choice if status in {"site", "ours", "skip"} else None
        api_pairs.append(pair_to_api(pair, visible_choice))
    return {
        "pairs": api_pairs,
        "summary": {
            "pair_count": len(pairs),
            "decided_count": decided,
            "invalid_choice_count": invalid,
            "unpaired_gallery_count": len(unpaired_site),
            "unpaired_local_count": len(unpaired_local),
        },
        "unpaired_gallery": [image.filename for image in unpaired_site],
        "unpaired_local": [str(stack.path) for stack in unpaired_local],
    }


HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Seestar Stack Review</title>
  <style>
    :root{color-scheme:dark;--ink:#e9edf0;--muted:#95a0a8;--amber:#d59b55;--line:#273037;--panel:#0c1115}
    *{box-sizing:border-box}body{margin:0;background:#050809;color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 50% -10%,#23303a99,transparent 42%),linear-gradient(#0000 70%,#07100d)}
    main{position:relative;width:min(1500px,100%);margin:auto;padding:22px}.top{display:flex;gap:22px;justify-content:space-between;align-items:end;margin-bottom:16px}
    .eyebrow{color:var(--amber);font:600 11px/1.2 ui-monospace,monospace;letter-spacing:.18em;text-transform:uppercase}h1{margin:.25rem 0 0;font:400 clamp(26px,4vw,48px)/1.05 Georgia,serif}
    .status{text-align:right;color:var(--muted)}.bar{width:min(360px,38vw);height:3px;background:#253038;margin-top:8px}.fill{height:100%;background:var(--amber);transition:width .2s}
    .target{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--line);border-bottom:0;background:#090d10;padding:12px 14px}.target h2{margin:0;font:400 21px/1.2 Georgia,serif}.count{color:var(--muted);font-family:ui-monospace,monospace}
    .compare{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);min-height:min(68vh,820px)}
    figure{margin:0;background:#020303;display:grid;grid-template-rows:auto 1fr;min-width:0}.label{display:flex;justify-content:space-between;gap:12px;padding:10px 13px;background:var(--panel);border-bottom:1px solid var(--line);color:var(--muted)}.label strong{color:var(--ink);font-weight:600}
    .image-wrap{display:grid;place-items:center;min-height:420px;overflow:hidden;background:radial-gradient(circle,#10171a,#010202 70%)}img{display:block;max-width:100%;max-height:calc(68vh - 44px);object-fit:contain}
    .controls{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;padding:16px 0}.controls button{border:1px solid #39434a;background:#0d1317;color:var(--ink);padding:11px 16px;border-radius:2px;cursor:pointer}.controls button:hover{border-color:var(--amber)}.controls .choose{border-color:#80613c}.controls button.selected{background:#6d4b27;border-color:#e1ad6b}
    select{background:#0d1317;color:var(--ink);border:1px solid #39434a;padding:10px;max-width:280px}.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.empty{border:1px solid var(--line);padding:48px;text-align:center;color:var(--muted)}
    @media(max-width:760px){main{padding:12px}.top{align-items:start}.status{font-size:12px}.bar{width:30vw}.compare{grid-template-columns:1fr;min-height:0}.image-wrap{min-height:54vh}img{max-height:54vh}.target{position:sticky;top:0;z-index:2}.foot{display:block}.foot span{display:block;margin-top:4px}}
  </style>
</head>
<body><main><header class="top"><div><div class="eyebrow">Northern Michigan · Offline review desk</div><h1>Which stack earns the sky?</h1></div><div class="status"><span id="progress">Loading…</span><div class="bar"><div class="fill" id="fill"></div></div></div></header><section id="app"></section><footer class="foot"><span>←/→ move · 1 site · 2 ours · S skip</span><span>Choices save locally; the gallery is never overwritten.</span></footer></main>
<script>
let data={pairs:[],summary:{}}, index=0, candidateSelections={};
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){data=await (await fetch('/api/pairs',{cache:'no-store'})).json();render()}
function current(){return data.pairs[index]}
function render(){
 const app=document.querySelector('#app'), total=data.pairs.length, decided=data.summary.decided_count||0;
 document.querySelector('#progress').textContent=`${decided} of ${total} decided`;
 document.querySelector('#fill').style.width=total?`${decided/total*100}%`:'0%';
 if(!total){app.innerHTML=`<div class="empty"><h2>No matched stacks yet</h2><p>Finish at least one local Siril stack that matches a current gallery target, exposure, and filter, then refresh.</p><p>${data.summary.unpaired_gallery_count||0} gallery images are waiting.</p></div>`;return}
 const p=current(), chosen=p.choice?.choice, selected=candidateSelections[p.pair_id]||p.choice?.ours_candidate_id, ours=p.ours.find(o=>o.candidate_id===selected)||p.ours[0];
 app.innerHTML=`<div class="target"><h2>${esc(p.target)}</h2><span class="count">${index+1} / ${total}</span></div><div class="compare">
 <figure><figcaption class="label"><strong>Current gallery</strong><span>${p.site.frames} frames · ${esc(p.site.treatment)}</span></figcaption><div class="image-wrap"><img src="${p.site.media_url}" alt="Current gallery version of ${esc(p.target)}"></div></figure>
 <figure><figcaption class="label"><strong>Our Siril stack</strong><span>${ours.frames} frames · ${esc(ours.settings.preview_style||'preview')}</span></figcaption><div class="image-wrap"><img src="${ours.media_url}" alt="Locally stacked version of ${esc(p.target)}"></div></figure></div>
 <div class="controls"><button onclick="move(-1)">← Previous</button><button class="choose ${chosen==='site'?'selected':''}" onclick="choose('site')">1 · Keep gallery</button>
 ${p.ours.length>1?`<select id="candidate" onchange="selectCandidate(this.value)">${p.ours.map(o=>`<option value="${o.candidate_id}" ${o.candidate_id===ours.candidate_id?'selected':''}>${o.frames} frames · ${esc(o.finished_at||o.filename)}</option>`).join('')}</select>`:''}
 <button class="choose ${chosen==='ours'&&p.choice?.ours_candidate_id===ours.candidate_id?'selected':''}" onclick="choose('ours','${ours.candidate_id}')">2 · Use our stack</button><button class="${chosen==='skip'?'selected':''}" onclick="choose('skip')">S · Decide later</button><button onclick="move(1)">Next →</button></div>`;
}
function move(delta){if(!data.pairs.length)return;index=(index+delta+data.pairs.length)%data.pairs.length;render()}
function selectCandidate(candidateId){candidateSelections[current().pair_id]=candidateId;render()}
async function choose(choice,candidate){const p=current();const chosen=document.querySelector('#candidate')?.value||candidate;const res=await fetch('/api/choice',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({pair_id:p.pair_id,choice,candidate_id:chosen})});if(!res.ok){alert((await res.json()).error||'Could not save choice');return}await load();if(index<data.pairs.length-1)index++;render()}
addEventListener('keydown',event=>{if(event.key==='ArrowLeft')move(-1);else if(event.key==='ArrowRight')move(1);else if(event.key==='1')choose('site');else if(event.key==='2'){const p=current();if(p)choose('ours',document.querySelector('#candidate')?.value||p.ours[0].candidate_id)}else if(event.key.toLowerCase()==='s')choose('skip')});
load();
</script></body></html>'''


class ReviewApplication:
    def __init__(
        self,
        pairs: list[ComparisonPair],
        unpaired_site: list[SiteImage],
        unpaired_local: list[LocalStack],
        choices_path: Path,
    ) -> None:
        self.pairs = pairs
        self.unpaired_site = unpaired_site
        self.unpaired_local = unpaired_local
        self.choices_path = choices_path
        self.pairs_by_id = {pair.pair_id: pair for pair in pairs}
        self.media: dict[str, Path] = {}
        for pair in pairs:
            self.media[f"site-{pair.pair_id}"] = pair.site.path
            for candidate in pair.ours:
                self.media[f"ours-{candidate.candidate_id}"] = candidate.path
        self.lock = threading.Lock()

    def payload(self) -> dict[str, object]:
        with self.lock:
            choices = load_choices(self.choices_path)
        return api_payload(self.pairs, self.unpaired_site, self.unpaired_local, choices)

    def choose(self, request: dict[str, object]) -> dict[str, object]:
        with self.lock:
            return record_choice(
                self.choices_path,
                self.pairs_by_id,
                str(request.get("pair_id", "")),
                str(request.get("choice", "")),
                str(request["candidate_id"]) if request.get("candidate_id") else None,
            )


def make_handler(application: ReviewApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SeestarReview/1.0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", status)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/pairs":
                try:
                    self.send_json(application.payload())
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if parsed.path.startswith("/media/"):
                media_id = parsed.path.removeprefix("/media/")
                path = application.media.get(media_id)
                if path is None or not path.is_file():
                    self.send_json({"error": "media not found"}, HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                try:
                    self.send_bytes(path.read_bytes(), content_type)
                except OSError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/choice":
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST_BYTES:
                    raise ValueError("invalid request size")
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                self.send_json(application.choose(request))
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return Handler


def discover_comparisons(
    gallery: Path, ours: Path, min_frames: int
) -> tuple[list[ComparisonPair], list[SiteImage], list[LocalStack], list[str]]:
    site_images, site_warnings = load_site_images(gallery, min_frames)
    local_stacks, local_warnings = load_local_stacks(ours, min_frames)
    pairs, unpaired_site, unpaired_local = build_pairs(site_images, local_stacks)
    return pairs, unpaired_site, unpaired_local, site_warnings + local_warnings


def command_serve(args: argparse.Namespace) -> int:
    gallery = args.gallery.expanduser().resolve()
    ours = args.ours.expanduser().resolve()
    choices = args.choices.expanduser().resolve()
    try:
        pairs, unpaired_site, unpaired_local, warnings = discover_comparisons(gallery, ours, args.min_frames)
        load_choices(choices)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    print(
        f"Review set: {len(pairs)} matched, {len(unpaired_site)} gallery-only, "
        f"{len(unpaired_local)} local-only."
    )
    application = ReviewApplication(pairs, unpaired_site, unpaired_local, choices)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(application))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Open the stack review at {url}")
    print("Press Ctrl-C to stop it.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview server stopped.")
    finally:
        server.server_close()
    return 0


def safe_filename_component(value: str) -> str:
    """Keep provenance readable without allowing a filename to escape its snapshot."""

    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[^\w .+\-]", "_", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._-")
    return normalized or "Unknown"


def safe_export_name(
    pair: ComparisonPair,
    choice: dict[str, object],
    candidate: LocalStack | None,
) -> str:
    if choice["choice"] == "site":
        return pair.site.filename
    if choice["choice"] != "ours" or candidate is None:
        raise ValueError(f"local export for {pair.target} has no selected stack candidate")

    exposure = candidate.exposure_seconds
    if not re.fullmatch(r"\d+(?:\.\d+)?", exposure):
        exposure = candidate.key.exposure
    if not re.fullmatch(r"\d+(?:\.\d+)?", exposure):
        raise ValueError(f"local export for {pair.target} has an invalid exposure")
    filter_name = candidate.filter_name.upper()
    if filter_name not in {"LP", "IRCUT"}:
        raise ValueError(f"local export for {pair.target} has an unsupported filter")
    parse_capture_timestamp(candidate.capture_first_timestamp)
    extension = candidate.path.suffix.casefold()
    if extension not in {".jpg", ".jpeg"}:
        raise ValueError(f"local export for {pair.target} is not a JPEG preview")

    # Preview refreshes deliberately use generic artifact names. Reconstruct the
    # public filename from immutable capture provenance instead of that basename.
    return (
        f"Stacked_{candidate.frames}_{safe_filename_component(candidate.object_name)}_"
        f"{exposure}s_{filter_name}_{candidate.capture_first_timestamp}_"
        f"hand_processed{extension}"
    )


def public_capture_id(pair: ComparisonPair, candidate: LocalStack) -> str:
    """Return a stable public ID independent of mutable previews and pair IDs."""

    fingerprint = candidate.input_fingerprint.strip()
    if not fingerprint:
        raise ValueError(f"local stack for {pair.target} has no input fingerprint")
    identity = json.dumps(
        {
            "object_id": pair.site.key.object_id,
            "mosaic": pair.site.key.mosaic,
            "exposure": pair.site.key.exposure,
            "filter": pair.site.key.filter_name,
            "input_fingerprint": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"capture-{digest[:24]}"


def resolve_public_default(
    pair: ComparisonPair,
    candidate: LocalStack,
    choices: dict[str, object],
) -> tuple[str, str]:
    """Resolve only an exact visual review; changed candidates default safely."""

    expected = {
        "target": pair.target,
        "site_file": str(pair.site.path),
        "site_sha256": pair.site.sha256,
    }
    matching: list[dict[str, object]] = []
    for raw_record in choices.values():
        if not isinstance(raw_record, dict):
            continue
        if all(raw_record.get(key) == value for key, value in expected.items()):
            matching.append(raw_record)

    exact = [record for record in matching if record.get("pair_id") == pair.pair_id]
    if len(exact) > 1:
        raise ValueError(
            f"ambiguous curated decision for {pair.target}: "
            f"{len(exact)} records claim the current comparison"
        )
    if not exact and matching:
        return "seestar", "candidate-changed-default"
    if not exact:
        if pair.pair_id in choices:
            raise ValueError(
                f"invalid curated decision for {pair.target}: current record does not "
                "match its gallery provenance"
            )
        return "missing", "missing"

    record = exact[0]
    if not isinstance(record.get("selected_at"), str) or not record["selected_at"]:
        raise ValueError(f"invalid curated decision for {pair.target}: selected_at is missing")
    choice = record.get("choice")
    if choice == "skip":
        return "missing", "skip"
    if choice == "site":
        return "seestar", "saved-choice"
    if choice != "ours":
        raise ValueError(f"invalid curated decision for {pair.target}: unrecognized choice")
    if record.get("ours_input_fingerprint") != candidate.input_fingerprint:
        raise ValueError(
            f"stale NightSkyAI decision for {pair.target}: the top stack uses a "
            "different raw-frame inventory"
        )
    return "nightskyai", "saved-choice"


def export_public_comparisons(
    pairs: list[ComparisonPair],
    choices_path: Path,
    destination: Path,
    *,
    allow_incomplete: bool,
) -> Path:
    """Stage public comparison media and metadata, then publish atomically."""

    if not pairs:
        raise ValueError("no eligible comparison pairs were found")
    final = destination.expanduser().resolve()
    temporary = final.with_name(f".{final.name}.tmp")
    if final.exists() or final.is_symlink():
        raise ValueError(f"public export destination already exists: {final}")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"public export staging folder already exists: {temporary}")

    choices_payload = load_choices(choices_path)
    choices = choices_payload["choices"]
    assert isinstance(choices, dict)

    plans: list[tuple[dict[str, object], Path]] = []
    seen_capture_ids: set[str] = set()
    missing: list[str] = []
    defaulted_count = 0
    for pair in pairs:
        if not pair.ours:
            raise ValueError(f"comparison for {pair.target} has no NightSkyAI candidate")
        candidate = pair.ours[0]
        if candidate.frames < DEFAULT_MIN_FRAMES or pair.site.frames < DEFAULT_MIN_FRAMES:
            raise ValueError(f"comparison for {pair.target} does not meet the 50-frame minimum")
        if candidate.path.suffix.casefold() not in {".jpg", ".jpeg"}:
            raise ValueError(f"NightSkyAI comparison for {pair.target} is not a JPEG")
        validate_public_framing(pair.site, candidate)
        if sha256_file(pair.site.path) != pair.site.sha256:
            raise ValueError(f"gallery image changed while exporting: {pair.site.path}")
        if sha256_file(candidate.path) != candidate.sha256:
            raise ValueError(f"NightSkyAI image changed while exporting: {candidate.path}")

        curated_default, decision_source = resolve_public_default(pair, candidate, choices)
        if curated_default == "missing":
            if not allow_incomplete:
                missing.append(pair.target)
                continue
            curated_default = "seestar"
            decision_source = "allow-incomplete-default"
            defaulted_count += 1
        elif decision_source == "candidate-changed-default":
            defaulted_count += 1

        capture_id = public_capture_id(pair, candidate)
        if capture_id in seen_capture_ids:
            raise ValueError(f"public capture ID collision: {capture_id}")
        seen_capture_ids.add(capture_id)
        relative_filename = f"images/{capture_id}.jpg"
        record: dict[str, object] = {
            "captureId": capture_id,
            "object": pair.site.object_name,
            "exposure": pair.site.key.exposure,
            "filter": pair.site.key.filter_name,
            "baseline": {
                "filename": pair.site.filename,
                "frames": pair.site.frames,
                "treatment": pair.site.treatment,
                "sha256": pair.site.sha256,
                "width": pair.site.width,
                "height": pair.site.height,
            },
            "nightSkyAI": {
                "filename": relative_filename,
                "frames": candidate.frames,
                "inputFingerprint": candidate.input_fingerprint,
                "firstTimestamp": candidate.capture_first_timestamp,
                "lastTimestamp": candidate.capture_last_timestamp,
                "nightCount": len(candidate.capture_nights),
                "sha256": candidate.sha256,
                "width": candidate.width,
                "height": candidate.height,
            },
            "curatedDefault": curated_default,
            "curatedDefaultSource": decision_source,
        }
        plans.append((record, candidate.path))

    if missing:
        raise ValueError(
            f"{len(missing)} comparison(s) lack a curated decision: "
            + ", ".join(missing)
        )

    final.parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir()
    try:
        (temporary / "images").mkdir()
        for record, source in plans:
            night_sky = record["nightSkyAI"]
            assert isinstance(night_sky, dict)
            target = temporary / str(night_sky["filename"])
            source_hash = str(night_sky["sha256"])
            shutil.copy2(source, target)
            if sha256_file(source) != source_hash or sha256_file(target) != source_hash:
                raise ValueError(f"NightSkyAI source changed while exporting: {source}")
            night_sky["sha256"] = source_hash
        write_json(
            temporary / "manifest.json",
            {
                "schemaVersion": PUBLIC_EXPORT_SCHEMA_VERSION,
                "tool": TOOL_NAME,
                "createdAt": utc_now(),
                "captureCount": len(plans),
                "defaultedToSeestarCount": defaulted_count,
                "captures": [record for record, _source in plans],
            },
        )
        temporary.replace(final)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final


def export_winners(
    pairs: list[ComparisonPair],
    choices_path: Path,
    destination: Path,
    *,
    allow_incomplete: bool,
    snapshot_name: str | None = None,
) -> Path:
    choices_payload = load_choices(choices_path)
    choices = choices_payload["choices"]
    assert isinstance(choices, dict)
    resolved: dict[str, tuple[str, LocalStack | None]] = {}
    incomplete: list[ComparisonPair] = []
    for pair in pairs:
        status, candidate = resolve_saved_choice(pair, choices.get(pair.pair_id))
        resolved[pair.pair_id] = (status, candidate)
        if status in {"missing", "skip"}:
            incomplete.append(pair)
    if incomplete and not allow_incomplete:
        raise ValueError(f"{len(incomplete)} comparison(s) are still undecided")

    name = snapshot_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("snapshot name may contain only letters, numbers, dots, dashes, and underscores")
    final = destination / name
    temporary = destination / f".{name}.tmp"
    if final.exists() or temporary.exists():
        raise ValueError(f"export snapshot already exists: {final}")
    temporary.mkdir(parents=True)
    records: list[dict[str, object]] = []
    try:
        for pair in pairs:
            raw_choice = choices.get(pair.pair_id)
            status, candidate = resolved[pair.pair_id]
            if status in {"missing", "skip"}:
                continue
            assert isinstance(raw_choice, dict)
            if status == "site":
                source = pair.site.path
                provenance = "current-gallery"
            elif status == "ours":
                assert candidate is not None
                source = candidate.path
                provenance = "local-siril-stack"
            else:
                raise AssertionError(f"unexpected validated choice status: {status}")
            target = temporary / safe_export_name(pair, raw_choice, candidate)
            if target.exists():
                raise ValueError(f"two selections would export as the same file: {target.name}")
            shutil.copy2(source, target)
            records.append(
                {
                    "pair_id": pair.pair_id,
                    "target": pair.target,
                    "choice": raw_choice["choice"],
                    "provenance": provenance,
                    "source": str(source),
                    "file": target.name,
                    "sha256": sha256_file(target),
                }
            )
        write_json(
            temporary / "selection_manifest.json",
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "tool": TOOL_NAME,
                "created_at": utc_now(),
                "choices_file": str(choices_path),
                "selected_count": len(records),
                "comparisons_available": len(pairs),
                "selections": records,
            },
        )
        destination.mkdir(parents=True, exist_ok=True)
        temporary.replace(final)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final


def command_export(args: argparse.Namespace) -> int:
    gallery = args.gallery.expanduser().resolve()
    ours = args.ours.expanduser().resolve()
    choices = args.choices.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    try:
        pairs, _unpaired_site, _unpaired_local, warnings = discover_comparisons(gallery, ours, args.min_frames)
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        snapshot = export_winners(
            pairs,
            choices,
            destination,
            allow_incomplete=args.allow_incomplete,
            snapshot_name=args.snapshot_name,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Selected images exported to: {snapshot}")
    print(f"Manifest: {snapshot / 'selection_manifest.json'}")
    return 0


def command_export_public(args: argparse.Namespace) -> int:
    gallery = args.gallery.expanduser().resolve()
    ours = args.ours.expanduser().resolve()
    choices = args.choices.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    try:
        pairs, _unpaired_site, _unpaired_local, warnings = discover_comparisons(
            gallery, ours, args.min_frames
        )
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        exported = export_public_comparisons(
            pairs,
            choices,
            destination,
            allow_incomplete=args.allow_incomplete,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Public comparison export: {exported}")
    print(f"Manifest: {exported / 'manifest.json'}")
    return 0


def add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gallery", required=True, type=Path, help="Deep Space Field Notes site repository.")
    parser.add_argument("--ours", required=True, type=Path, help="Root containing successful Siril manifests.")
    parser.add_argument("--choices", required=True, type=Path, help="JSON file where human choices are stored.")
    parser.add_argument("--min-frames", type=int, default=DEFAULT_MIN_FRAMES)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare gallery JPEGs with local Siril stacks and choose winners.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Launch the local side-by-side review page.")
    add_source_options(serve)
    serve.add_argument("--port", type=int, default=8765, help="Loopback port; use 0 to choose a free port.")
    serve.add_argument("--open", action="store_true", help="Open the review page in the default browser.")
    serve.set_defaults(func=command_serve)

    export = subparsers.add_parser("export", help="Copy chosen winners into a new review snapshot.")
    add_source_options(export)
    export.add_argument("--destination", required=True, type=Path, help="Parent folder for selection snapshots.")
    export.add_argument("--allow-incomplete", action="store_true", help="Export decided choices before every pair is reviewed.")
    export.add_argument("--snapshot-name", help="Optional stable name for the new snapshot folder.")
    export.set_defaults(func=command_export)

    public_export = subparsers.add_parser(
        "export-public",
        help="Stage 50+ frame Seestar/NightSkyAI comparisons for the public site.",
    )
    add_source_options(public_export)
    public_export.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="New destination folder for manifest.json and NightSkyAI JPEGs.",
    )
    public_export.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Default missing human decisions to Seestar and record that fallback.",
    )
    public_export.set_defaults(func=command_export_public)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.min_frames < 1:
        print("Error: --min-frames must be positive", file=sys.stderr)
        return 2
    if args.command == "export-public" and args.min_frames < DEFAULT_MIN_FRAMES:
        print(
            f"Error: export-public requires --min-frames of at least {DEFAULT_MIN_FRAMES}",
            file=sys.stderr,
        )
        return 2
    if getattr(args, "port", 0) < 0 or getattr(args, "port", 0) > 65535:
        print("Error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
