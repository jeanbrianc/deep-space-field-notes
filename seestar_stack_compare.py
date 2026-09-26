#!/usr/bin/env python3
"""Review current gallery images beside locally produced Siril stacks.

The local server exposes only discovered images through opaque IDs, stores each
human choice atomically, and exports winners into a new snapshot. A separate
guarded command can apply a complete public review to manifest metadata; no
command here overwrites gallery media or stack output.
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
PUBLIC_REVIEW_TOOL_NAME = "seestar_public_comparison_review"
PUBLIC_REVIEW_CHOICES_SCHEMA_VERSION = 1
PUBLIC_REVIEW_CURATION_SOURCE = "owner-review"
CULL_TOOL_NAME = "seestar_gallery_cull"
CULL_CHOICES_SCHEMA_VERSION = 1
CULL_GROUPS_SCHEMA_VERSION = 1
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
SHA256_VALUE = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True)
class CullGroupDefinition:
    definition_id: str
    label: str
    object_ids: tuple[str, ...]
    candidate_overrides: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CullCandidate:
    catalog_filename: str
    review_file: str
    image: SiteImage
    overridden: bool
    candidate_id: str


@dataclass(frozen=True)
class CullGroup:
    group_id: str
    definition_id: str
    label: str
    candidates: tuple[CullCandidate, ...]


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


def load_cull_group_definitions(path: Path) -> list[CullGroupDefinition]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read cull groups {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CULL_GROUPS_SCHEMA_VERSION:
        raise ValueError(f"invalid cull groups document: {path}")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise ValueError(f"cull groups must be a list: {path}")

    definitions: list[CullGroupDefinition] = []
    seen_definition_ids: set[str] = set()
    object_owners: dict[str, str] = {}
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"cull group {index} must be an object")
        definition_id = raw_group.get("id")
        label = raw_group.get("label")
        raw_objects = raw_group.get("objects")
        raw_overrides = raw_group.get("candidate_overrides", {})
        if not isinstance(definition_id, str) or not definition_id.strip():
            raise ValueError(f"cull group {index} has no valid id")
        if definition_id in seen_definition_ids:
            raise ValueError(f"duplicate cull group id: {definition_id}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"cull group {definition_id} has no valid label")
        if not isinstance(raw_objects, list) or not raw_objects:
            raise ValueError(f"cull group {definition_id} must list at least one object")
        if not isinstance(raw_overrides, dict):
            raise ValueError(f"cull group {definition_id} candidate_overrides must be an object")

        object_ids: list[str] = []
        for raw_object in raw_objects:
            if not isinstance(raw_object, str) or not raw_object.strip():
                raise ValueError(f"cull group {definition_id} contains an invalid object")
            object_id, _mosaic = canonical_object(raw_object)
            owner = object_owners.get(object_id)
            if owner is not None and owner != definition_id:
                raise ValueError(
                    f"cull object {raw_object!r} belongs to both {owner} and {definition_id}"
                )
            object_owners[object_id] = definition_id
            if object_id not in object_ids:
                object_ids.append(object_id)

        overrides: list[tuple[str, str]] = []
        for catalog_filename, review_file in raw_overrides.items():
            if not isinstance(catalog_filename, str) or not catalog_filename:
                raise ValueError(f"cull group {definition_id} has an invalid override filename")
            if not isinstance(review_file, str) or not review_file:
                raise ValueError(
                    f"cull override for {catalog_filename!r} must name a repository-relative file"
                )
            overrides.append((catalog_filename, review_file))

        seen_definition_ids.add(definition_id)
        definitions.append(
            CullGroupDefinition(
                definition_id=definition_id,
                label=label.strip(),
                object_ids=tuple(object_ids),
                candidate_overrides=tuple(sorted(overrides)),
            )
        )
    return definitions


def _review_site_image(gallery: Path, review_file: str) -> SiteImage:
    relative = Path(review_file)
    if relative.is_absolute():
        raise ValueError(f"cull override must be repository-relative: {review_file}")
    unresolved = gallery / relative
    if unresolved.is_symlink():
        raise ValueError(f"cull override may not be a symbolic link: {review_file}")
    resolved_gallery = gallery.resolve()
    resolved = unresolved.resolve()
    if not _is_relative_to(resolved, resolved_gallery):
        raise ValueError(f"cull override resolves outside the gallery: {review_file}")
    if not resolved.is_file():
        raise ValueError(f"cull override file does not exist: {review_file}")
    parsed = SITE_FILENAME.fullmatch(resolved.name)
    if not parsed:
        raise ValueError(f"cull override filename is not recognized: {review_file}")
    frames = int(parsed.group("frames"))
    if frames < 1:
        raise ValueError(f"cull override frame count must be positive: {review_file}")
    object_name = parsed.group("object")
    width, height = image_dimensions(resolved)
    return SiteImage(
        path=resolved,
        filename=resolved.name,
        frames=frames,
        object_name=object_name,
        treatment=parsed.group("treatment").replace("_", " "),
        timestamp=parsed.group("timestamp"),
        key=match_key(object_name, parsed.group("exposure"), parsed.group("filter")),
        sha256=sha256_file(resolved),
        width=width,
        height=height,
    )


def cull_candidate_snapshot(group: CullGroup) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": candidate.candidate_id,
            "catalog_filename": candidate.catalog_filename,
            "review_file": candidate.review_file,
            "review_sha256": candidate.image.sha256,
        }
        for candidate in group.candidates
    ]


def verify_cull_group_files(groups: list[CullGroup] | tuple[CullGroup, ...]) -> None:
    for group in groups:
        for candidate in group.candidates:
            try:
                current_sha256 = sha256_file(candidate.image.path)
            except OSError as exc:
                raise ValueError(
                    f"cull candidate is unavailable: {candidate.image.filename}"
                ) from exc
            if current_sha256 != candidate.image.sha256:
                raise ValueError(
                    f"cull candidate changed while the review desk was open: "
                    f"{candidate.image.filename}"
                )


def _cull_capture_identity(image: SiteImage) -> tuple[object, ...]:
    return (
        image.frames,
        image.key.object_id,
        image.key.mosaic,
        image.key.exposure,
        image.key.filter_name,
        image.timestamp,
    )


def build_cull_groups(
    gallery: Path,
    site_images: list[SiteImage],
    definitions: list[CullGroupDefinition],
) -> list[CullGroup]:
    resolved_gallery = gallery.resolve()
    catalog_by_filename = {image.filename: image for image in site_images}
    groups: list[CullGroup] = []
    for definition in definitions:
        overrides = dict(definition.candidate_overrides)
        for catalog_filename in overrides:
            catalog_image = catalog_by_filename.get(catalog_filename)
            if catalog_image is None:
                raise ValueError(
                    f"cull override does not name a current catalog image: {catalog_filename}"
                )
            if catalog_image.key.object_id not in definition.object_ids:
                raise ValueError(
                    f"cull override {catalog_filename} does not belong to group "
                    f"{definition.definition_id}"
                )

        candidates: list[CullCandidate] = []
        for catalog_image in site_images:
            if catalog_image.key.object_id not in definition.object_ids:
                continue
            review_file = overrides.get(catalog_image.filename)
            if review_file is None:
                image = catalog_image
                relative_review_file = str(
                    catalog_image.path.relative_to(resolved_gallery).as_posix()
                )
                overridden = False
            else:
                image = _review_site_image(resolved_gallery, review_file)
                if _cull_capture_identity(image) != _cull_capture_identity(catalog_image):
                    raise ValueError(
                        f"cull override {review_file} does not match catalog capture identity "
                        f"for {catalog_image.filename}"
                    )
                relative_review_file = str(image.path.relative_to(resolved_gallery).as_posix())
                overridden = True
            identity = json.dumps(
                {
                    "catalog_filename": catalog_image.filename,
                    "review_file": relative_review_file,
                    "review_sha256": image.sha256,
                },
                sort_keys=True,
            )
            candidates.append(
                CullCandidate(
                    catalog_filename=catalog_image.filename,
                    review_file=relative_review_file,
                    image=image,
                    overridden=overridden,
                    candidate_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                )
            )
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: (-item.image.frames, item.catalog_filename.casefold()))
        group_identity = json.dumps(
            {
                "definition_id": definition.definition_id,
                "candidates": [
                    {
                        "candidate_id": candidate.candidate_id,
                        "catalog_filename": candidate.catalog_filename,
                        "review_sha256": candidate.image.sha256,
                    }
                    for candidate in candidates
                ],
            },
            sort_keys=True,
        )
        groups.append(
            CullGroup(
                group_id=hashlib.sha256(group_identity.encode("utf-8")).hexdigest(),
                definition_id=definition.definition_id,
                label=definition.label,
                candidates=tuple(candidates),
            )
        )
    groups.sort(key=lambda group: (group.label.casefold(), group.definition_id))
    return groups


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


def _positive_manifest_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _manifest_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_VALUE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _public_manifest_file(root: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{label} must name a file")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must stay inside {root}")
    unresolved = root / relative
    if unresolved.is_symlink():
        raise ValueError(f"{label} may not be a symbolic link")
    resolved_root = root.resolve()
    resolved = unresolved.resolve()
    if not _is_relative_to(resolved, resolved_root):
        raise ValueError(f"{label} resolves outside {root}")
    if not resolved.is_file():
        raise ValueError(f"{label} is missing: {resolved}")
    return resolved


def _verify_manifest_image(
    path: Path,
    record: dict[str, object],
    label: str,
    *,
    sha_key: str = "sha256",
    width_key: str = "width",
    height_key: str = "height",
) -> tuple[str, int, int]:
    declared_sha256 = _manifest_sha256(record.get(sha_key), f"{label}.{sha_key}")
    declared_width = _positive_manifest_integer(record.get(width_key), f"{label}.{width_key}")
    declared_height = _positive_manifest_integer(record.get(height_key), f"{label}.{height_key}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != declared_sha256:
        raise ValueError(f"{label} checksum does not match its manifest")
    actual_width, actual_height = image_dimensions(path)
    if (actual_width, actual_height) != (declared_width, declared_height):
        raise ValueError(
            f"{label} dimensions do not match its manifest: "
            f"{actual_width}x{actual_height} versus "
            f"{declared_width}x{declared_height}"
        )
    return actual_sha256, actual_width, actual_height


def load_public_comparison_pairs(gallery: Path) -> list[ComparisonPair]:
    """Load the already-published, alignment-verified comparison inventory.

    This deliberately reads the checked-in public manifest rather than
    rediscovering raw Siril runs. Every declared image checksum, dimension, and
    frame count is validated before a review pair is exposed.
    """

    resolved_gallery = gallery.expanduser().resolve()
    manifest_path = resolved_gallery / "public" / "comparisons" / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read public comparison manifest {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"public comparison manifest root must be an object: {manifest_path}")
    if payload.get("schemaVersion") != 2 or payload.get("tool") != TOOL_NAME:
        raise ValueError(f"public comparison manifest tool or schema is not recognized: {manifest_path}")
    captures = payload.get("captures")
    if not isinstance(captures, list):
        raise ValueError("public comparison manifest captures must be a list")
    capture_count = _positive_manifest_integer(
        payload.get("captureCount"), "public comparison manifest captureCount"
    )
    if capture_count != len(captures):
        raise ValueError(
            "public comparison manifest captureCount does not match its captures"
        )

    image_root = resolved_gallery / "public" / "images"
    comparison_root = resolved_gallery / "public" / "comparisons"
    current_catalog = set(site_catalog_filenames(resolved_gallery))
    created_at = str(payload.get("createdAt", ""))
    pairs: list[ComparisonPair] = []
    seen_capture_ids: set[str] = set()
    seen_comparison_ids: set[str] = set()
    seen_baselines: set[str] = set()
    seen_candidate_ids: set[str] = set()

    for position, raw_capture in enumerate(captures, start=1):
        label = f"public comparison capture {position}"
        if not isinstance(raw_capture, dict):
            raise ValueError(f"{label} must be an object")
        capture_id = raw_capture.get("captureId")
        comparison_id = raw_capture.get("comparisonId")
        object_name = raw_capture.get("object")
        if not isinstance(capture_id, str) or not capture_id:
            raise ValueError(f"{label}.captureId must be a non-empty string")
        if capture_id in seen_capture_ids:
            raise ValueError(f"duplicate public comparison captureId: {capture_id}")
        if not isinstance(comparison_id, str) or not comparison_id:
            raise ValueError(f"{label}.comparisonId must be a non-empty string")
        if comparison_id in seen_comparison_ids:
            raise ValueError(f"duplicate public comparison comparisonId: {comparison_id}")
        if not isinstance(object_name, str) or not object_name.strip():
            raise ValueError(f"{label}.object must be a non-empty string")
        exposure = normalize_exposure(raw_capture.get("exposure"))
        filter_name = str(raw_capture.get("filter", "")).strip().upper()
        if filter_name not in {"LP", "IRCUT"}:
            raise ValueError(f"{label}.filter is not supported")
        key = match_key(object_name, exposure, filter_name)

        baseline = raw_capture.get("baseline")
        night_sky = raw_capture.get("nightSkyAI")
        if not isinstance(baseline, dict) or not isinstance(night_sky, dict):
            raise ValueError(f"{label} must contain baseline and nightSkyAI objects")
        baseline_filename = baseline.get("filename")
        if not isinstance(baseline_filename, str) or baseline_filename not in current_catalog:
            raise ValueError(f"{label} baseline is not in the current gallery catalog")
        if baseline_filename in seen_baselines:
            raise ValueError(f"duplicate public comparison baseline: {baseline_filename}")
        parsed = SITE_FILENAME.fullmatch(baseline_filename)
        if parsed is None:
            raise ValueError(f"{label} baseline filename is not recognized: {baseline_filename}")
        baseline_frames = _positive_manifest_integer(
            baseline.get("frames"), f"{label}.baseline.frames"
        )
        if baseline_frames < DEFAULT_MIN_FRAMES:
            raise ValueError(
                f"{label}.baseline.frames must be at least {DEFAULT_MIN_FRAMES}"
            )
        if baseline_frames != int(parsed.group("frames")):
            raise ValueError(f"{label} baseline frame count does not match its filename")
        baseline_key = match_key(
            parsed.group("object"), parsed.group("exposure"), parsed.group("filter")
        )
        if baseline_key != key:
            raise ValueError(f"{label} baseline capture identity does not match its manifest")
        treatment = parsed.group("treatment").replace("_", " ")
        declared_treatment = baseline.get("treatment")
        if (
            not isinstance(declared_treatment, str)
            or declared_treatment.replace("_", " ").casefold() != treatment.casefold()
        ):
            raise ValueError(f"{label} baseline treatment does not match its filename")
        baseline_path = _public_manifest_file(
            image_root, baseline_filename, f"{label}.baseline.filename"
        )
        baseline_sha256, baseline_width, baseline_height = _verify_manifest_image(
            baseline_path, baseline, f"{label}.baseline"
        )
        site = SiteImage(
            path=baseline_path,
            filename=baseline_filename,
            frames=baseline_frames,
            object_name=object_name,
            treatment=treatment,
            timestamp=parsed.group("timestamp"),
            key=key,
            sha256=baseline_sha256,
            width=baseline_width,
            height=baseline_height,
        )

        expected_night_filename = f"aligned-v1/{capture_id}.jpg"
        if night_sky.get("filename") != expected_night_filename:
            raise ValueError(
                f"{label}.nightSkyAI.filename must be {expected_night_filename}"
            )
        night_path = _public_manifest_file(
            comparison_root,
            night_sky.get("filename"),
            f"{label}.nightSkyAI.filename",
        )
        night_sha256, night_width, night_height = _verify_manifest_image(
            night_path, night_sky, f"{label}.nightSkyAI"
        )
        if (night_width, night_height) != (baseline_width, baseline_height):
            raise ValueError(
                f"{label}.nightSkyAI dimensions must match the Gallery baseline: "
                f"{night_width}x{night_height} versus "
                f"{baseline_width}x{baseline_height}"
            )
        night_frames = _positive_manifest_integer(
            night_sky.get("frames"), f"{label}.nightSkyAI.frames"
        )
        if night_frames < DEFAULT_MIN_FRAMES:
            raise ValueError(
                f"{label}.nightSkyAI.frames must be at least {DEFAULT_MIN_FRAMES}"
            )
        first_timestamp = str(night_sky.get("firstTimestamp", ""))
        last_timestamp = str(night_sky.get("lastTimestamp", ""))
        if parse_capture_timestamp(first_timestamp) > parse_capture_timestamp(last_timestamp):
            raise ValueError(f"{label} NightSkyAI timestamps are out of order")
        night_count = _positive_manifest_integer(
            night_sky.get("nightCount"), f"{label}.nightSkyAI.nightCount"
        )
        known_nights = tuple(sorted({first_timestamp[:8], last_timestamp[:8]}))
        if night_count < len(known_nights):
            raise ValueError(f"{label}.nightSkyAI.nightCount contradicts its timestamps")
        input_fingerprint = _manifest_sha256(
            night_sky.get("inputFingerprint"),
            f"{label}.nightSkyAI.inputFingerprint",
        )

        alignment = night_sky.get("alignment")
        if not isinstance(alignment, dict):
            raise ValueError(f"{label}.nightSkyAI.alignment must be an object")
        if alignment.get("referencePolicy") != "gallery-edit-is-immutable":
            raise ValueError(
                f"{label}.nightSkyAI.alignment must use the immutable Gallery edit"
            )
        if alignment.get("mode") != "registered-to-gallery-edit":
            raise ValueError(
                f"{label}.nightSkyAI.alignment must be registered to the Gallery edit"
            )
        verification = alignment.get("verification")
        if not isinstance(verification, dict) or verification.get("passed") is not True:
            raise ValueError(
                f"{label}.nightSkyAI.alignment verification must have passed"
            )
        source_keys = {"sourceFilename", "sourceSha256", "sourceWidth", "sourceHeight"}
        present_source_keys = source_keys.intersection(alignment)
        if present_source_keys and present_source_keys != source_keys:
            raise ValueError(f"{label}.nightSkyAI.alignment source metadata is incomplete")
        if present_source_keys:
            source_path = _public_manifest_file(
                comparison_root,
                alignment["sourceFilename"],
                f"{label}.nightSkyAI.alignment.sourceFilename",
            )
            _verify_manifest_image(
                source_path,
                alignment,
                f"{label}.nightSkyAI.alignment source",
                sha_key="sourceSha256",
                width_key="sourceWidth",
                height_key="sourceHeight",
            )

        candidate_identity = json.dumps(
            {
                "capture_id": capture_id,
                "comparison_id": comparison_id,
                "input_fingerprint": input_fingerprint,
                "night_sky_sha256": night_sha256,
            },
            sort_keys=True,
        )
        candidate_id = hashlib.sha256(candidate_identity.encode("utf-8")).hexdigest()
        if candidate_id in seen_candidate_ids:
            raise ValueError(f"duplicate public comparison NightSkyAI candidate: {capture_id}")
        candidate = LocalStack(
            path=night_path,
            manifest_path=manifest_path.resolve(),
            frames=night_frames,
            object_name=object_name,
            exposure_seconds=exposure,
            filter_name=filter_name,
            capture_first_timestamp=first_timestamp,
            capture_last_timestamp=last_timestamp,
            capture_nights=known_nights,
            finished_at=created_at,
            input_fingerprint=input_fingerprint,
            preview_settings={"preview_style": "aligned"},
            key=key,
            candidate_id=candidate_id,
            sha256=night_sha256,
            width=night_width,
            height=night_height,
        )
        pair_identity = json.dumps(
            {
                "comparison_id": comparison_id,
                "site_filename": baseline_filename,
                "site_sha256": baseline_sha256,
                "candidate_id": candidate_id,
            },
            sort_keys=True,
        )
        pair_id = hashlib.sha256(pair_identity.encode("utf-8")).hexdigest()
        pairs.append(ComparisonPair(pair_id, object_name, site, (candidate,)))
        seen_capture_ids.add(capture_id)
        seen_comparison_ids.add(comparison_id)
        seen_baselines.add(baseline_filename)
        seen_candidate_ids.add(candidate_id)

    return pairs


def verify_public_comparison_pairs(pairs: list[ComparisonPair]) -> None:
    for pair in pairs:
        if len(pair.ours) != 1:
            raise ValueError(f"public comparison for {pair.target} must have one NightSkyAI image")
        for label, path, expected_sha256, expected_dimensions in (
            (
                "Gallery",
                pair.site.path,
                pair.site.sha256,
                (pair.site.width, pair.site.height),
            ),
            (
                "NightSkyAI",
                pair.ours[0].path,
                pair.ours[0].sha256,
                (pair.ours[0].width, pair.ours[0].height),
            ),
        ):
            try:
                current_sha256 = sha256_file(path)
                current_dimensions = image_dimensions(path)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"{label} image for {pair.target} is unavailable or invalid"
                ) from exc
            if current_sha256 != expected_sha256 or current_dimensions != expected_dimensions:
                raise ValueError(
                    f"{label} image for {pair.target} changed while the review desk was open"
                )


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


def load_public_review_choices(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {
            "schema_version": PUBLIC_REVIEW_CHOICES_SCHEMA_VERSION,
            "tool": PUBLIC_REVIEW_TOOL_NAME,
            "updated_at": None,
            "choices": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read public review choices {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != PUBLIC_REVIEW_CHOICES_SCHEMA_VERSION
        or payload.get("tool") != PUBLIC_REVIEW_TOOL_NAME
        or not isinstance(payload.get("choices"), dict)
    ):
        raise ValueError(f"invalid public review choices document: {path}")
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_cull_choices(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {
            "schema_version": CULL_CHOICES_SCHEMA_VERSION,
            "tool": CULL_TOOL_NAME,
            "updated_at": None,
            "choices": {},
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read cull choices {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CULL_CHOICES_SCHEMA_VERSION
        or payload.get("tool") != CULL_TOOL_NAME
        or not isinstance(payload.get("choices"), dict)
    ):
        raise ValueError(f"invalid cull choices document: {path}")
    return payload


def record_cull_choice(
    choices_path: Path,
    groups_by_id: dict[str, CullGroup],
    group_id: str,
    choice: str,
    candidate_id: str | None = None,
) -> dict[str, object]:
    if choice not in {"winner", "skip"}:
        raise ValueError("cull choice must be winner or skip")
    group = groups_by_id.get(group_id)
    if group is None:
        raise ValueError("cull group is stale or unknown; refresh before choosing")
    verify_cull_group_files([group])

    selected: CullCandidate | None = None
    if choice == "winner":
        selected = next(
            (candidate for candidate in group.candidates if candidate.candidate_id == candidate_id),
            None,
        )
        if selected is None:
            raise ValueError("choose one of the available gallery candidates")

    payload = load_cull_choices(choices_path)
    choices = payload["choices"]
    assert isinstance(choices, dict)
    record: dict[str, object] = {
        "group_id": group.group_id,
        "definition_id": group.definition_id,
        "label": group.label,
        "choice": choice,
        "selected_at": utc_now(),
        "candidate_snapshot": cull_candidate_snapshot(group),
    }
    if selected is not None:
        record.update(
            {
                "winner_candidate_id": selected.candidate_id,
                "winner_catalog_filename": selected.catalog_filename,
                "winner_review_file": selected.review_file,
                "winner_sha256": selected.image.sha256,
            }
        )
    choices[group.group_id] = record
    payload["schema_version"] = CULL_CHOICES_SCHEMA_VERSION
    payload["tool"] = CULL_TOOL_NAME
    payload["updated_at"] = utc_now()
    write_json(choices_path, payload)
    return record


def resolve_saved_cull_choice(
    group: CullGroup, raw_choice: object
) -> tuple[str, CullCandidate | None]:
    if raw_choice is None:
        return "missing", None
    if not isinstance(raw_choice, dict):
        raise ValueError(f"invalid cull choice for {group.label}: record is not an object")
    expected: dict[str, object] = {
        "group_id": group.group_id,
        "definition_id": group.definition_id,
        "label": group.label,
        "candidate_snapshot": cull_candidate_snapshot(group),
    }
    for key, value in expected.items():
        if raw_choice.get(key) != value:
            raise ValueError(f"stale cull choice for {group.label}: {key} no longer matches")
    if not isinstance(raw_choice.get("selected_at"), str) or not raw_choice["selected_at"]:
        raise ValueError(f"invalid cull choice for {group.label}: selected_at is missing")
    choice = raw_choice.get("choice")
    if choice == "skip":
        return "skip", None
    if choice != "winner":
        raise ValueError(f"invalid cull choice for {group.label}: unrecognized choice")
    candidate = next(
        (
            item
            for item in group.candidates
            if item.candidate_id == raw_choice.get("winner_candidate_id")
        ),
        None,
    )
    if candidate is None:
        raise ValueError(f"stale cull winner for {group.label}; review it again")
    winner_expected = {
        "winner_catalog_filename": candidate.catalog_filename,
        "winner_review_file": candidate.review_file,
        "winner_sha256": candidate.image.sha256,
    }
    for key, value in winner_expected.items():
        if raw_choice.get(key) != value:
            raise ValueError(f"stale cull winner for {group.label}: {key} no longer matches")
    return "winner", candidate


def cull_group_to_api(group: CullGroup, choice: object) -> dict[str, object]:
    hash_counts: dict[str, int] = {}
    for candidate in group.candidates:
        hash_counts[candidate.image.sha256] = hash_counts.get(candidate.image.sha256, 0) + 1
    return {
        "group_id": group.group_id,
        "definition_id": group.definition_id,
        "label": group.label,
        "choice": choice,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "catalog_filename": candidate.catalog_filename,
                "filename": candidate.image.filename,
                "object": candidate.image.object_name,
                "frames": candidate.image.frames,
                "treatment": candidate.image.treatment,
                "timestamp": candidate.image.timestamp,
                "exposure": candidate.image.key.exposure,
                "filter": candidate.image.key.filter_name,
                "mosaic": candidate.image.key.mosaic,
                "overridden": candidate.overridden,
                "same_pixels": hash_counts[candidate.image.sha256] > 1,
                "media_url": f"/media/cull-{candidate.candidate_id}",
            }
            for candidate in group.candidates
        ],
    }


def cull_api_payload(
    groups: list[CullGroup], choices_payload: dict[str, object]
) -> dict[str, object]:
    verify_cull_group_files(groups)
    choices = choices_payload.get("choices", {})
    assert isinstance(choices, dict)
    api_groups: list[dict[str, object]] = []
    decided = 0
    invalid = 0
    current_group_ids = {group.group_id for group in groups}
    for group in groups:
        raw_choice = choices.get(group.group_id)
        try:
            status, _candidate = resolve_saved_cull_choice(group, raw_choice)
        except ValueError:
            status = "invalid"
            invalid += 1
        if status == "winner":
            decided += 1
        visible_choice = raw_choice if status in {"winner", "skip"} else None
        api_groups.append(cull_group_to_api(group, visible_choice))
    return {
        "groups": api_groups,
        "summary": {
            "group_count": len(groups),
            "decided_count": decided,
            "invalid_choice_count": invalid,
            "orphan_choice_count": len(set(choices) - current_group_ids),
        },
    }


def record_choice(
    choices_path: Path,
    pairs_by_id: dict[str, ComparisonPair],
    pair_id: str,
    choice: str,
    candidate_id: str | None = None,
) -> dict[str, object]:
    return _record_pair_choice(
        choices_path,
        load_choices(choices_path),
        pairs_by_id,
        pair_id,
        choice,
        candidate_id,
        schema_version=CHOICES_SCHEMA_VERSION,
        tool_name=TOOL_NAME,
    )


def record_public_review_choice(
    choices_path: Path,
    pairs_by_id: dict[str, ComparisonPair],
    pair_id: str,
    choice: str,
    candidate_id: str | None = None,
) -> dict[str, object]:
    return _record_pair_choice(
        choices_path,
        load_public_review_choices(choices_path),
        pairs_by_id,
        pair_id,
        choice,
        candidate_id,
        schema_version=PUBLIC_REVIEW_CHOICES_SCHEMA_VERSION,
        tool_name=PUBLIC_REVIEW_TOOL_NAME,
    )


def _record_pair_choice(
    choices_path: Path,
    payload: dict[str, object],
    pairs_by_id: dict[str, ComparisonPair],
    pair_id: str,
    choice: str,
    candidate_id: str | None,
    *,
    schema_version: int,
    tool_name: str,
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
    payload["schema_version"] = schema_version
    payload["tool"] = tool_name
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


def _confined_public_review_choices_path(gallery: Path, requested: Path) -> Path:
    """Resolve a real choices file beneath this checkout's non-symlinked work folder."""

    work_path = gallery / "work"
    if work_path.is_symlink() or not work_path.is_dir():
        raise ValueError("gallery work/ must be an existing, non-symlinked directory")
    work_root = work_path.resolve()
    if not _is_relative_to(work_root, gallery):
        raise ValueError("gallery work/ resolves outside the gallery")

    raw_choices = requested.expanduser()
    if raw_choices.is_symlink():
        raise ValueError("public review choices may not be a symbolic link")
    choices_path = raw_choices.resolve()
    if choices_path.suffix.casefold() != ".json" or not _is_relative_to(
        choices_path, work_root
    ):
        raise ValueError(
            "public review choices must be a JSON file under the gallery work/ folder"
        )
    if not choices_path.is_file():
        raise ValueError(f"public review choices file is missing: {choices_path}")
    return choices_path


def _public_manifest_for_owner_review(gallery: Path) -> Path:
    public_root = gallery / "public"
    comparison_root = public_root / "comparisons"
    manifest_path = comparison_root / "manifest.json"
    for directory, label in (
        (public_root, "public/"),
        (comparison_root, "public/comparisons/"),
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{label} must be an existing, non-symlinked directory")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("public comparison manifest must be an existing, non-symlinked file")
    if not _is_relative_to(manifest_path.resolve(), gallery):
        raise ValueError("public comparison manifest resolves outside the gallery")
    return manifest_path


def _replace_json_atomically(
    path: Path, payload: dict[str, object], expected_bytes: bytes
) -> None:
    """Replace an existing JSON file only if it is still the version we validated."""

    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"public manifest staging file already exists: {temporary}")
    if path.is_symlink() or path.read_bytes() != expected_bytes:
        raise ValueError("public comparison manifest changed while choices were validated")
    try:
        with temporary.open("x", encoding="utf-8") as file_handle:
            json.dump(payload, file_handle, indent=2, sort_keys=True)
            file_handle.write("\n")
        if path.is_symlink() or path.read_bytes() != expected_bytes:
            raise ValueError("public comparison manifest changed while choices were validated")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_public_review_choices(
    gallery: Path, choices_path: Path, *, dry_run: bool
) -> dict[str, object]:
    """Promote one complete owner-reviewed decision set into the public manifest.

    Media, image hashes, comparison IDs, alignment evidence, and the comparison
    bundle's creation timestamp are intentionally left unchanged. The ignored
    local choice records are reduced to portable, non-path provenance fields.
    """

    resolved_gallery = gallery.expanduser().resolve()
    confined_choices = _confined_public_review_choices_path(
        resolved_gallery, choices_path
    )
    manifest_path = _public_manifest_for_owner_review(resolved_gallery)
    original_manifest = manifest_path.read_bytes()
    original_choices = confined_choices.read_bytes()

    try:
        manifest_payload = json.loads(original_manifest)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not read public comparison manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest_payload, dict):
        raise ValueError("public comparison manifest root must be an object")

    pairs = load_public_comparison_pairs(resolved_gallery)
    verify_public_comparison_pairs(pairs)
    choices_payload = load_public_review_choices(confined_choices)
    if confined_choices.read_bytes() != original_choices:
        raise ValueError("public review choices changed while they were validated")
    choices = choices_payload["choices"]
    assert isinstance(choices, dict)

    current_pair_ids = {pair.pair_id for pair in pairs}
    orphan_choice_ids = sorted(set(choices) - current_pair_ids)
    if orphan_choice_ids:
        raise ValueError(
            f"{len(orphan_choice_ids)} public review choice(s) do not belong to "
            "the current comparison manifest"
        )

    resolved: dict[str, tuple[str, LocalStack | None, dict[str, object]]] = {}
    incomplete: list[str] = []
    for pair in pairs:
        raw_choice = choices.get(pair.pair_id)
        status, candidate = resolve_saved_choice(pair, raw_choice)
        if status not in {"site", "ours"}:
            incomplete.append(pair.target)
            continue
        assert isinstance(raw_choice, dict)
        resolved[pair.pair_id] = (status, candidate, raw_choice)
    if incomplete:
        raise ValueError(
            f"{len(incomplete)} public comparison(s) are still undecided: "
            + ", ".join(incomplete)
        )
    if len(choices) != len(pairs):
        raise ValueError("public review choices must contain exactly one current decision per pair")

    choices_updated_at = choices_payload.get("updated_at")
    if not isinstance(choices_updated_at, str) or not choices_updated_at:
        raise ValueError("public review choices updated_at is missing")

    captures = manifest_payload.get("captures")
    if not isinstance(captures, list) or len(captures) != len(pairs):
        raise ValueError("public comparison manifest captures changed during review")
    pairs_by_baseline = {pair.site.filename: pair for pair in pairs}
    if len(pairs_by_baseline) != len(pairs):
        raise ValueError("public comparison baselines are not unique")

    gallery_count = 0
    nightskyai_count = 0
    for position, capture in enumerate(captures, start=1):
        if not isinstance(capture, dict):
            raise ValueError(f"public comparison capture {position} must be an object")
        baseline = capture.get("baseline")
        if not isinstance(baseline, dict):
            raise ValueError(f"public comparison capture {position}.baseline must be an object")
        pair = pairs_by_baseline.get(str(baseline.get("filename", "")))
        if pair is None:
            raise ValueError(
                f"public comparison capture {position} no longer matches its reviewed pair"
            )
        status, _candidate, raw_choice = resolved[pair.pair_id]
        selected_at = raw_choice.get("selected_at")
        if not isinstance(selected_at, str) or not selected_at:
            raise ValueError(f"owner-review timestamp is missing for {pair.target}")
        if status == "site":
            curated_default = "seestar"
            gallery_count += 1
        else:
            curated_default = "nightskyai"
            nightskyai_count += 1
        capture["curatedDefault"] = curated_default
        capture["curatedDefaultSource"] = PUBLIC_REVIEW_CURATION_SOURCE
        capture["curatedDefaultSelectedAt"] = selected_at
        capture["curatedDefaultReviewPairId"] = pair.pair_id

    applied_at = utc_now()
    manifest_payload["defaultedToSeestarCount"] = 0
    manifest_payload["curation"] = {
        "source": PUBLIC_REVIEW_CURATION_SOURCE,
        "tool": PUBLIC_REVIEW_TOOL_NAME,
        "choicesSchemaVersion": PUBLIC_REVIEW_CHOICES_SCHEMA_VERSION,
        "choicesUpdatedAt": choices_updated_at,
        "appliedAt": applied_at,
        "decisionCount": len(pairs),
        "galleryCount": gallery_count,
        "nightSkyAICount": nightskyai_count,
    }

    if confined_choices.read_bytes() != original_choices:
        raise ValueError("public review choices changed while they were validated")
    if manifest_path.read_bytes() != original_manifest:
        raise ValueError("public comparison manifest changed while choices were validated")
    if not dry_run:
        _replace_json_atomically(manifest_path, manifest_payload, original_manifest)

    return {
        "manifest": manifest_path,
        "dry_run": dry_run,
        "decision_count": len(pairs),
        "gallery_count": gallery_count,
        "nightskyai_count": nightskyai_count,
        "applied_at": applied_at,
    }


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
function showLoadError(error){document.querySelector('#progress').textContent='Review unavailable';document.querySelector('#fill').style.width='0%';document.querySelector('#app').innerHTML=`<div class="empty"><h2>Could not load review</h2><p>${esc(error.message||'The comparison files could not be verified.')}</p></div>`}
async function load(){try{const response=await fetch('/api/pairs',{cache:'no-store'});const payload=await response.json();if(!response.ok)throw new Error(payload.error||'Could not load comparisons');data=payload;render();return true}catch(error){showLoadError(error);return false}}
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
async function choose(choice,candidate){const p=current();const chosen=document.querySelector('#candidate')?.value||candidate;const res=await fetch('/api/choice',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({pair_id:p.pair_id,choice,candidate_id:chosen})});if(!res.ok){alert((await res.json()).error||'Could not save choice');return}if(!await load())return;if(index<data.pairs.length-1)index++;render()}
addEventListener('keydown',event=>{if(event.key==='ArrowLeft')move(-1);else if(event.key==='ArrowRight')move(1);else if(event.key==='1')choose('site');else if(event.key==='2'){const p=current();if(p)choose('ours',document.querySelector('#candidate')?.value||p.ours[0].candidate_id)}else if(event.key.toLowerCase()==='s')choose('skip')});
load();
</script></body></html>'''

PUBLIC_REVIEW_HTML = (
    HTML.replace("<title>Seestar Stack Review</title>", "<title>Public Comparison Review</title>")
    .replace("Current gallery", "Gallery edit")
    .replace("Our Siril stack", "NightSkyAI")
    .replace("Locally stacked version", "Aligned NightSkyAI version")
    .replace("1 site · 2 ours", "1 gallery · 2 NightSkyAI")
    .replace("1 · Keep gallery", "1 · Keep Gallery")
    .replace("2 · Use our stack", "2 · Use NightSkyAI")
)


CULL_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gallery Culling Review</title>
  <style>
    :root{color-scheme:dark;--ink:#e9edf0;--muted:#95a0a8;--amber:#d59b55;--line:#273037;--panel:#0c1115}
    *{box-sizing:border-box}body{margin:0;background:#050809;color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif}
    body:before{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(circle at 50% -10%,#23303a99,transparent 42%),linear-gradient(#0000 70%,#07100d)}
    main{position:relative;width:min(1700px,100%);margin:auto;padding:22px}.top{display:flex;gap:22px;justify-content:space-between;align-items:end;margin-bottom:16px}
    .eyebrow{color:var(--amber);font:600 11px/1.2 ui-monospace,monospace;letter-spacing:.18em;text-transform:uppercase}h1{margin:.25rem 0 0;font:400 clamp(26px,4vw,48px)/1.05 Georgia,serif}
    .status{text-align:right;color:var(--muted)}.bar{width:min(360px,38vw);height:3px;background:#253038;margin-top:8px}.fill{height:100%;background:var(--amber);transition:width .2s}
    .target{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--line);border-bottom:0;background:#090d10;padding:12px 14px}.target h2{margin:0;font:400 21px/1.2 Georgia,serif}.count{color:var(--muted);font-family:ui-monospace,monospace}
    .candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(390px,100%),1fr));gap:1px;background:var(--line);border:1px solid var(--line)}.candidates.saving{pointer-events:none;opacity:.65}
    figure{margin:0;background:#020303;display:grid;grid-template-rows:auto 1fr auto;min-width:0;outline:1px solid transparent}figure.selected{outline-color:var(--amber);z-index:1}
    .label{display:flex;justify-content:space-between;gap:12px;padding:10px 13px;background:var(--panel);border-bottom:1px solid var(--line);color:var(--muted)}.label strong{color:var(--ink);font-weight:600}.meta{text-align:right}.badge{color:var(--amber)}
    .image-wrap{display:grid;place-items:center;min-height:420px;overflow:hidden;background:radial-gradient(circle,#10171a,#010202 70%)}img{display:block;max-width:100%;max-height:calc(68vh - 110px);object-fit:contain}
    .pick{border:0;border-top:1px solid var(--line);background:#0d1317;color:var(--ink);padding:12px 16px;cursor:pointer}.pick:hover{color:var(--amber)}figure.selected .pick{background:#6d4b27;color:#fff}
    .controls{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;padding:16px 0}.controls button{border:1px solid #39434a;background:#0d1317;color:var(--ink);padding:11px 16px;border-radius:2px;cursor:pointer}.controls button:hover{border-color:var(--amber)}.controls button.selected{background:#6d4b27;border-color:#e1ad6b}.controls button:disabled,.pick:disabled{cursor:wait;opacity:.6}
    .foot{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.empty{border:1px solid var(--line);padding:48px;text-align:center;color:var(--muted)}
    @media(max-width:760px){main{padding:12px}.top{align-items:start}.status{font-size:12px}.bar{width:30vw}.candidates{grid-template-columns:1fr}.image-wrap{min-height:54vh}img{max-height:54vh}.target{position:sticky;top:0;z-index:2}.foot{display:block}.foot span{display:block;margin-top:4px}}
  </style>
</head>
<body><main><header class="top"><div><div class="eyebrow">Northern Michigan · Offline gallery curation</div><h1>Which capture earns the sky?</h1></div><div class="status"><span id="progress">Loading…</span><div class="bar"><div class="fill" id="fill"></div></div></div></header><section id="app"></section><footer class="foot"><span>←/→ move · number chooses · S decide later</span><span>Choices save locally; the gallery is never overwritten.</span></footer></main>
<script>
let data={groups:[],summary:{}}, index=0, saving=false;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const response=await fetch('/api/cull-groups',{cache:'no-store'});data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load culling groups');render()}
function current(){return data.groups[index]}
function render(){
 const app=document.querySelector('#app'), total=data.groups.length, decided=data.summary.decided_count||0;
 document.querySelector('#progress').textContent=`${decided} of ${total} decided`;
 document.querySelector('#fill').style.width=total?`${decided/total*100}%`:'0%';
 if(!total){app.innerHTML='<div class="empty"><h2>No duplicate groups need review</h2><p>The configured targets do not currently contain two or more gallery candidates.</p></div>';return}
 const group=current(), winner=group.choice?.choice==='winner'?group.choice.winner_candidate_id:null, disabled=saving?' disabled':'';
 const cards=group.candidates.map((candidate,position)=>`<figure class="${winner===candidate.candidate_id?'selected':''}"><figcaption class="label"><div><strong>${esc(candidate.object)}${candidate.mosaic?' · Mosaic':''}</strong><br><span>${esc(candidate.filename)}</span></div><div class="meta">${candidate.frames} frames · ${esc(candidate.filter)}<br>${esc(candidate.treatment)}${candidate.overridden?' · review candidate':''}${candidate.same_pixels?'<br><span class="badge">Same pixels as another entry</span>':''}</div></figcaption><div class="image-wrap"><img src="${candidate.media_url}" alt="Candidate ${position+1} for ${esc(group.label)}"></div><button class="pick" onclick="choose('winner','${candidate.candidate_id}')"${disabled}>${position+1} · Keep this capture</button></figure>`).join('');
 app.innerHTML=`<div class="target"><h2>${esc(group.label)}</h2><span class="count">${index+1} / ${total}</span></div><div class="candidates${saving?' saving':''}">${cards}</div><div class="controls"><button onclick="move(-1)"${disabled}>← Previous</button><button class="${group.choice?.choice==='skip'?'selected':''}" onclick="choose('skip')"${disabled}>S · Decide later</button><button onclick="move(1)"${disabled}>Next →</button></div>`;
}
function move(delta){if(saving||!data.groups.length)return;index=(index+delta+data.groups.length)%data.groups.length;render()}
async function choose(choice,candidate){if(saving)return;const group=current();if(!group)return;saving=true;render();try{const response=await fetch('/api/cull-choice',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({group_id:group.group_id,choice,candidate_id:candidate})});const payload=await response.json();if(!response.ok){alert(payload.error||'Could not save choice');return}const oldIndex=index;await load();index=Math.min(oldIndex,data.groups.length-1);if(index<data.groups.length-1)index++}catch(error){alert(error.message||'Could not save choice')}finally{saving=false;render()}}
addEventListener('keydown',event=>{if(event.repeat||saving)return;if(event.key==='ArrowLeft')move(-1);else if(event.key==='ArrowRight')move(1);else if(event.key.toLowerCase()==='s')choose('skip');else if(/^[1-9]$/.test(event.key)){const group=current(),candidate=group?.candidates[Number(event.key)-1];if(candidate)choose('winner',candidate.candidate_id)}});
load().catch(error=>{document.querySelector('#app').innerHTML=`<div class="empty"><h2>Could not open review</h2><p>${esc(error.message)}</p></div>`});
</script></body></html>'''


class ReviewApplication:
    index_html = HTML
    payload_path = "/api/pairs"
    choice_path = "/api/choice"

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

    def read_media(self, media_id: str) -> tuple[Path, bytes]:
        path = self.media.get(media_id)
        if path is None:
            raise FileNotFoundError("media not found")
        return path, path.read_bytes()


class PublicComparisonReviewApplication(ReviewApplication):
    index_html = PUBLIC_REVIEW_HTML

    def __init__(self, pairs: list[ComparisonPair], choices_path: Path) -> None:
        super().__init__(pairs, [], [], choices_path)
        self.media_sha256 = {
            f"site-{pair.pair_id}": pair.site.sha256 for pair in pairs
        }
        self.media_sha256.update(
            {
                f"ours-{candidate.candidate_id}": candidate.sha256
                for pair in pairs
                for candidate in pair.ours
            }
        )

    def payload(self) -> dict[str, object]:
        with self.lock:
            verify_public_comparison_pairs(self.pairs)
            choices = load_public_review_choices(self.choices_path)
        return api_payload(self.pairs, [], [], choices)

    def choose(self, request: dict[str, object]) -> dict[str, object]:
        with self.lock:
            verify_public_comparison_pairs(self.pairs)
            return record_public_review_choice(
                self.choices_path,
                self.pairs_by_id,
                str(request.get("pair_id", "")),
                str(request.get("choice", "")),
                str(request["candidate_id"]) if request.get("candidate_id") else None,
            )

    def read_media(self, media_id: str) -> tuple[Path, bytes]:
        path = self.media.get(media_id)
        expected_sha256 = self.media_sha256.get(media_id)
        if path is None or expected_sha256 is None:
            raise FileNotFoundError("media not found")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise ValueError("public comparison image changed; refresh the review desk")
        return path, body


class GalleryCullApplication:
    index_html = CULL_HTML
    payload_path = "/api/cull-groups"
    choice_path = "/api/cull-choice"

    def __init__(self, groups: list[CullGroup], choices_path: Path) -> None:
        self.groups = groups
        self.choices_path = choices_path
        self.groups_by_id = {group.group_id: group for group in groups}
        self.media: dict[str, Path] = {}
        self.media_sha256: dict[str, str] = {}
        for group in groups:
            for candidate in group.candidates:
                media_id = f"cull-{candidate.candidate_id}"
                self.media[media_id] = candidate.image.path
                self.media_sha256[media_id] = candidate.image.sha256
        self.lock = threading.Lock()

    def payload(self) -> dict[str, object]:
        with self.lock:
            choices = load_cull_choices(self.choices_path)
        return cull_api_payload(self.groups, choices)

    def choose(self, request: dict[str, object]) -> dict[str, object]:
        with self.lock:
            return record_cull_choice(
                self.choices_path,
                self.groups_by_id,
                str(request.get("group_id", "")),
                str(request.get("choice", "")),
                str(request["candidate_id"]) if request.get("candidate_id") else None,
            )

    def read_media(self, media_id: str) -> tuple[Path, bytes]:
        path = self.media.get(media_id)
        expected_sha256 = self.media_sha256.get(media_id)
        if path is None or expected_sha256 is None:
            raise FileNotFoundError("media not found")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise ValueError("cull candidate changed; refresh the review desk")
        return path, body


def make_handler(application: Any):
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
                self.send_bytes(application.index_html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == application.payload_path:
                try:
                    self.send_json(application.payload())
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if parsed.path.startswith("/media/"):
                media_id = parsed.path.removeprefix("/media/")
                try:
                    path, body = application.read_media(media_id)
                except FileNotFoundError:
                    self.send_json({"error": "media not found"}, HTTPStatus.NOT_FOUND)
                    return
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
                    return
                except OSError as exc:
                    self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_bytes(body, content_type)
                return
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if urlparse(self.path).path != application.choice_path:
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


def command_review_public(args: argparse.Namespace) -> int:
    gallery = args.gallery.expanduser().resolve()
    choices = args.choices.expanduser().resolve()
    work_path = gallery / "work"
    work_root = work_path.resolve()
    public_root = (gallery / "public").resolve()
    app_root = (gallery / "app").resolve()
    if (
        work_path.is_symlink()
        or not _is_relative_to(work_root, gallery)
        or choices.suffix.casefold() != ".json"
        or not _is_relative_to(choices, work_root)
        or _is_relative_to(choices, public_root)
        or _is_relative_to(choices, app_root)
    ):
        print(
            "Error: public review choices must be a JSON file under the gallery work/ folder",
            file=sys.stderr,
        )
        return 2
    try:
        pairs = load_public_comparison_pairs(gallery)
        load_public_review_choices(choices)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Public comparison review set: {len(pairs)} Gallery versus NightSkyAI decisions.")
    application = PublicComparisonReviewApplication(pairs, choices)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(application))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Open the public comparison review at {url}")
    print("Press Ctrl-C to stop it.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPublic comparison review server stopped.")
    finally:
        server.server_close()
    return 0


def command_apply_public(args: argparse.Namespace) -> int:
    try:
        result = apply_public_review_choices(
            args.gallery,
            args.choices,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    action = "validated" if args.dry_run else "applied"
    print(
        f"Public display choices {action}: {result['decision_count']} decisions "
        f"({result['gallery_count']} Gallery, "
        f"{result['nightskyai_count']} NightSkyAI)."
    )
    if args.dry_run:
        print("Dry run complete; the public comparison manifest was not changed.")
    else:
        print(f"Manifest updated: {result['manifest']}")
    return 0


def command_cull(args: argparse.Namespace) -> int:
    gallery = args.gallery.expanduser().resolve()
    groups_path = args.groups.expanduser().resolve()
    choices = args.choices.expanduser().resolve()
    public_root = (gallery / "public").resolve()
    app_root = (gallery / "app").resolve()
    if _is_relative_to(choices, public_root) or _is_relative_to(choices, app_root):
        print("Error: cull choices may not be stored under public/ or app/", file=sys.stderr)
        return 2
    try:
        site_images, warnings = load_site_images(gallery, 1)
        definitions = load_cull_group_definitions(groups_path)
        groups = build_cull_groups(gallery, site_images, definitions)
        load_cull_choices(choices)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    print(
        f"Gallery culling set: {len(groups)} duplicate groups from "
        f"{len(definitions)} configured groups."
    )
    application = GalleryCullApplication(groups, choices)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(application))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Open the gallery culling review at {url}")
    print("Press Ctrl-C to stop it.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGallery culling server stopped.")
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

    public_review = subparsers.add_parser(
        "review-public",
        help="Review every published Gallery versus aligned NightSkyAI pair.",
    )
    public_review.add_argument(
        "--gallery", required=True, type=Path, help="Deep Space Field Notes site repository."
    )
    public_review.add_argument(
        "--choices",
        required=True,
        type=Path,
        help="Separate JSON choice file under the gallery work/ folder.",
    )
    public_review.add_argument(
        "--port", type=int, default=8765, help="Loopback port; use 0 to choose a free port."
    )
    public_review.add_argument(
        "--open", action="store_true", help="Open the review page in the default browser."
    )
    public_review.set_defaults(func=command_review_public)

    apply_public = subparsers.add_parser(
        "apply-public",
        help="Apply one complete public display review to the comparison manifest.",
    )
    apply_public.add_argument(
        "--gallery", required=True, type=Path, help="Deep Space Field Notes site repository."
    )
    apply_public.add_argument(
        "--choices",
        required=True,
        type=Path,
        help="Completed public review JSON file under the gallery work/ folder.",
    )
    apply_public.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize every choice without changing the manifest.",
    )
    apply_public.set_defaults(func=command_apply_public)

    cull = subparsers.add_parser(
        "cull", help="Launch the local chooser for configured duplicate gallery captures."
    )
    cull.add_argument("--gallery", required=True, type=Path, help="Deep Space Field Notes site repository.")
    cull.add_argument("--groups", required=True, type=Path, help="Checked-in gallery culling groups JSON.")
    cull.add_argument("--choices", required=True, type=Path, help="Separate JSON file for culling choices.")
    cull.add_argument("--port", type=int, default=8765, help="Loopback port; use 0 to choose a free port.")
    cull.add_argument("--open", action="store_true", help="Open the culling page in the default browser.")
    cull.set_defaults(func=command_cull)

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
    if getattr(args, "min_frames", 1) < 1:
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
