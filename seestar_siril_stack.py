#!/usr/bin/env python3
"""Safely stack Seestar light frames with Siril's command-line interface.

The source capture folders are treated as read-only. Each run stages symlinks to
only the selected FITS files, preserves a linear 32-bit FITS stack, and creates a
separate, replaceable JPEG preview.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TOOL_NAME = "seestar_siril_stack"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 2
WORK_MARKER = ".owned-by-seestar-siril-stack"
DEFAULT_SIRIL_MAC = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
DEFAULT_MIN_FRAMES = 50
DEFAULT_DISK_MULTIPLIER = 10.0

FRAME_NAME = re.compile(
    r"^Light_(?P<object>.+)_(?P<exposure>\d+(?:\.\d+)?)s_"
    r"(?P<filter>[^_]+)_(?P<timestamp>\d{8}-\d{6})\.fits?$",
    re.IGNORECASE,
)
STACKED_COUNT_PATTERNS = (
    re.compile(r"(\d+)\s+images?\s+have\s+been\s+stacked", re.IGNORECASE),
    re.compile(r"stack(?:ed|ing).*?\b(\d+)\s+images?", re.IGNORECASE),
)
REGISTERED_COUNT_PATTERNS = (
    re.compile(r"registration finished:\s*(\d+)\s+images?", re.IGNORECASE),
    re.compile(r"(\d+)\s+images?\s+registered", re.IGNORECASE),
    re.compile(r"\b(\d+)\s+registered\b", re.IGNORECASE),
)

PREVIEW_STYLES = {
    # Siril's default target background is 0.25. These deliberately keep a
    # night-sky background while preserving faint nebula detail.
    "natural": (0.18, 2.8),
    "dark": (0.08, 2.5),
    "dramatic": (0.06, 2.3),
}
PREVIEW_ROTATIONS = (0, 90, 180, 270)
PREVIEW_FLIPS = ("none", "top-bottom", "left-right")
FRAMING_MODES = ("min", "cog", "current")
RETRY_FRAMING_MODES = ("cog", "current")
RETRY_DISK_MULTIPLIER = 2.0
# A public comparison should still show essentially the full Seestar field.
# Treat more than a 10% loss on either axis or in total area as a crop and
# retry with a centered full-field registration mode.
MIN_FRAMING_AREA_RETENTION = 0.90
MIN_FRAMING_AXIS_RETENTION = 0.90
FITS_BLOCK_SIZE = 2880
FITS_CARD_SIZE = 80


@dataclass(frozen=True)
class CaptureFrame:
    path: Path
    object_name: str
    exposure: str
    filter_name: str
    timestamp: str
    size: int
    modified_ns: int

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.object_name, self.exposure, self.filter_name)


@dataclass(frozen=True)
class CaptureGroup:
    target_name: str
    source_folder: Path
    object_name: str
    exposure: str
    filter_name: str
    frames: tuple[CaptureFrame, ...]

    @property
    def total_bytes(self) -> int:
        return sum(frame.size for frame in self.frames)

    @property
    def first_timestamp(self) -> str:
        return min(frame.timestamp for frame in self.frames)


@dataclass(frozen=True)
class FramingRetry:
    run_dir: Path
    manifest_path: Path
    manifest: dict[str, object]
    process_dir: Path
    sequence_path: Path
    converted_frames: tuple[Path, ...]
    normalized_settings: dict[str, object]
    quality_mode: str
    brightness: float
    shadow_sigma: float
    jpeg_quality: int
    preview_rotation: int
    preview_flip: str
    minimum_stacked_frames: int
    replaces_framing_mode: str
    input_count: int
    required_bytes: int


@dataclass(frozen=True)
class PreviewRefresh:
    root: Path
    run_dir: Path
    manifest_path: Path
    manifest_sha256: str
    manifest: dict[str, object]
    schema_version: int
    settings: dict[str, object]
    linear_fits: Path
    preview_jpeg: Path
    linear_sha256: str
    preview_sha256: str
    style: str
    brightness: float
    shadow_sigma: float
    jpeg_quality: int
    rotation: int
    flip: str
    framing_mode: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_capture_frame(path: Path) -> CaptureFrame | None:
    match = FRAME_NAME.match(path.name)
    if not match or not path.is_file():
        return None
    stat = path.stat()
    return CaptureFrame(
        path=path.resolve(),
        object_name=match.group("object"),
        exposure=match.group("exposure"),
        filter_name=match.group("filter"),
        timestamp=match.group("timestamp"),
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


def target_name_from_folder(folder: Path) -> str:
    name = folder.name
    return name[:-4] if name.casefold().endswith("_sub") else name


def _matches_target(folder: Path, patterns: Iterable[str]) -> bool:
    names = (folder.name.casefold(), target_name_from_folder(folder).casefold())
    return any(
        fnmatch.fnmatchcase(name, pattern.casefold())
        for pattern in patterns
        for name in names
    )


def discover_target_folders(
    source: Path,
    target_patterns: Iterable[str] = (),
    select_all: bool = False,
) -> tuple[list[Path], list[str]]:
    """Find recursive ``*_sub`` folders, or accept one such folder directly."""

    patterns = tuple(target_patterns)
    warnings: list[str] = []
    if source.name.casefold().endswith("_sub"):
        folders = [source]
    else:
        folders = []

        def onerror(error: OSError) -> None:
            warnings.append(f"Could not scan {error.filename or source}: {error}")

        for root_text, directory_names, _file_names in os.walk(
            source, topdown=True, onerror=onerror, followlinks=False
        ):
            directory_names[:] = sorted(
                (
                    name
                    for name in directory_names
                    if not name.startswith(".") and name != "System Volume Information"
                ),
                key=str.casefold,
            )
            root = Path(root_text)
            if root.name.casefold().endswith("_sub"):
                folders.append(root)
                # Do not mistake processing folders beneath a capture for
                # additional targets.
                directory_names[:] = []
        folders.sort(key=lambda path: str(path.relative_to(source)).casefold())

    if patterns:
        folders = [folder for folder in folders if _matches_target(folder, patterns)]
    elif not select_all and not source.name.casefold().endswith("_sub"):
        return [], ["Choose at least one --target, or use --all."]

    selected: list[Path] = []
    for folder in folders:
        if folder.name.casefold().endswith("_mosaic_sub"):
            warnings.append(
                f"Skipping mosaic folder {folder.name}; mosaics need a separate registration workflow."
            )
            continue
        selected.append(folder)
    return selected, warnings


def collect_capture_groups(
    folder: Path,
    source_root: Path | None = None,
) -> tuple[list[CaptureGroup], list[str]]:
    """Group top-level Seestar FITS by object, exposure, and filter."""

    warnings: list[str] = []
    by_signature: dict[tuple[str, str, str], list[CaptureFrame]] = {}
    resolved_source_root = (source_root or folder).resolve()
    for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if not path.name.casefold().startswith("light_"):
            continue
        if path.suffix.casefold() not in {".fit", ".fits"}:
            continue
        if path.is_symlink():
            warnings.append(
                f"Skipping {path}: symbolic links are not accepted as light frames."
            )
            continue
        try:
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(resolved_source_root)
        except ValueError:
            warnings.append(
                f"Skipping {path}: light frame resolves outside the selected "
                f"source root {resolved_source_root}."
            )
            continue
        except OSError as exc:
            warnings.append(f"Could not inspect {path}: {exc}")
            continue
        if not resolved_path.is_file():
            continue
        try:
            frame = parse_capture_frame(resolved_path)
        except OSError as exc:
            warnings.append(f"Could not inspect {path}: {exc}")
            continue
        if frame is None:
            warnings.append(f"Ignoring unrecognized Seestar light filename: {path.name}")
            continue
        by_signature.setdefault(frame.signature, []).append(frame)

    groups = [
        CaptureGroup(
            target_name=target_name_from_folder(folder),
            source_folder=folder.resolve(),
            object_name=signature[0],
            exposure=signature[1],
            filter_name=signature[2],
            frames=tuple(sorted(frames, key=lambda frame: (frame.timestamp, frame.path.name))),
        )
        for signature, frames in by_signature.items()
    ]
    groups.sort(key=lambda group: (group.object_name.casefold(), group.exposure, group.filter_name.casefold()))
    return groups, warnings


def inventory_fingerprint(group: CaptureGroup) -> str:
    digest = hashlib.sha256()
    for frame in group.frames:
        digest.update(frame.path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(frame.size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(frame.modified_ns).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def manifest_input_records(group: CaptureGroup) -> list[dict[str, object]]:
    """Return the exact ordered source identity recorded for resume checks."""

    return [
        {
            "path": str(frame.path),
            "size": frame.size,
            "modified_ns": frame.modified_ns,
            "timestamp": frame.timestamp,
        }
        for frame in group.frames
    ]


def safe_component(value: str, *, spaces: bool = False) -> str:
    replacement = " " if spaces else "_"
    value = re.sub(r"[^\w .+\-]", replacement, value, flags=re.UNICODE)
    value = re.sub(r"\s+", replacement, value).strip(" ._-")
    return value or "unknown"


def siril_quote(value: Path | str) -> str:
    text = str(value)
    if "\n" in text or "\r" in text:
        raise ValueError("Siril paths cannot contain line breaks")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def resolve_preview_settings(
    style: str,
    brightness: float | None,
    shadow_sigma: float | None,
) -> tuple[float, float]:
    default_brightness, default_shadow_sigma = PREVIEW_STYLES[style]
    actual_brightness = default_brightness if brightness is None else brightness
    actual_shadow_sigma = default_shadow_sigma if shadow_sigma is None else shadow_sigma
    if not 0.0 < actual_brightness < 1.0:
        raise ValueError("preview brightness must be between 0 and 1")
    if not 0.5 <= actual_shadow_sigma <= 10.0:
        raise ValueError("preview shadow sigma must be between 0.5 and 10")
    return actual_brightness, actual_shadow_sigma


def preview_transform_commands(rotation: int, flip: str) -> list[str]:
    """Return lossless/reversible presentation transforms for a JPEG preview.

    The flip is applied first, followed by rotation. The linear FITS on disk is
    never overwritten by these commands.
    """

    if rotation not in PREVIEW_ROTATIONS:
        raise ValueError(f"preview rotation must be one of {PREVIEW_ROTATIONS}")
    if flip not in PREVIEW_FLIPS:
        raise ValueError(f"preview flip must be one of {PREVIEW_FLIPS}")

    commands: list[str] = []
    if flip == "top-bottom":
        commands.append("mirrorx")
    elif flip == "left-right":
        commands.append("mirrory")

    if rotation == 180:
        commands.append("rotatePi")
    elif rotation in {90, 270}:
        # -nocrop lets a portrait image become landscape without losing its
        # sides. Siril 1.4.3 swaps the dimensions for these right angles.
        commands.append(f"rotate {rotation} -nocrop")
    return commands


def render_stack_script(
    run_dir: Path,
    *,
    quality_mode: str,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int,
    framing_mode: str = "min",
    preview_rotation: int = 0,
    preview_flip: str = "none",
) -> str:
    lights = run_dir / "_work" / "lights"
    process = run_dir / "_work" / "process"
    linear_base = run_dir / "stacked_linear"
    preview_base = run_dir / "preview"
    quality_filter = {
        "balanced": " -filter-round=2.5k",
        "keep-all": "",
        "sharpest-75": " -filter-fwhm=75%",
    }[quality_mode]
    commands = [
            "requires 1.4.0",
            "setext fit",
            "setfindstar reset",
            f"cd {siril_quote(lights)}",
            # Siril 1.4 parses quotes after ``-out=`` as literal path text.
            # Relative option values avoid that parser edge case even when the
            # outer run directory contains spaces.
            "convert light_ -debayer -out=../process",
            f"cd {siril_quote(process)}",
            "register light_ -2pass -transf=homography",
            f"seqapplyreg light_ -framing={framing_mode}{quality_filter}",
            (
                "stack r_light_ rej 3 3 -norm=addscale -weight=nbstars "
                "-output_norm -rgb_equal -32b -out=../../stacked_linear"
            ),
            f"load {siril_quote(linear_base.with_suffix('.fit'))}",
            *preview_transform_commands(preview_rotation, preview_flip),
            f"autostretch -{shadow_sigma:.6f} {brightness:.6f}",
            f"savejpg {siril_quote(preview_base)} {jpeg_quality}",
            "close",
            "",
        ]
    return "\n".join(commands)


def render_retry_framing_script(
    run_dir: Path,
    *,
    output_prefix: str,
    quality_mode: str,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int,
    framing_mode: str,
    preview_rotation: int,
    preview_flip: str,
) -> str:
    """Render a framing-only retry that reuses converted and registered data.

    The original ``pipeline.ssf`` converted and registered ``light_`` already.
    A retry therefore starts at ``seqapplyreg`` and deliberately writes
    separate top-level temporary outputs so the failed attempt's diagnostics
    are not overwritten.
    """

    if framing_mode not in RETRY_FRAMING_MODES:
        raise ValueError(f"retry framing must be one of {RETRY_FRAMING_MODES}")
    process = run_dir / "_work" / "process"
    linear_base = run_dir / f"{output_prefix}-linear"
    preview_base = run_dir / f"{output_prefix}-preview"
    quality_filter = {
        "balanced": " -filter-round=2.5k",
        "keep-all": "",
        "sharpest-75": " -filter-fwhm=75%",
    }[quality_mode]
    commands = [
        "requires 1.4.0",
        "setext fit",
        "setfindstar reset",
        f"cd {siril_quote(process)}",
        f"seqapplyreg light_ -framing={framing_mode}{quality_filter}",
        (
            "stack r_light_ rej 3 3 -norm=addscale -weight=nbstars "
            f"-output_norm -rgb_equal -32b -out=../../{output_prefix}-linear"
        ),
        f"load {siril_quote(linear_base.with_suffix('.fit'))}",
        *preview_transform_commands(preview_rotation, preview_flip),
        f"autostretch -{shadow_sigma:.6f} {brightness:.6f}",
        f"savejpg {siril_quote(preview_base)} {jpeg_quality}",
        "close",
        "",
    ]
    return "\n".join(commands)


def render_preview_script(
    input_fits: Path,
    output_jpeg: Path,
    *,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int,
    linked: bool,
    rotation: int = 0,
    flip: str = "none",
) -> str:
    linked_flag = " -linked" if linked else ""
    commands = [
            "requires 1.4.0",
            f"load {siril_quote(input_fits)}",
            *preview_transform_commands(rotation, flip),
            f"autostretch{linked_flag} -{shadow_sigma:.6f} {brightness:.6f}",
            f"savejpg {siril_quote(output_jpeg.with_suffix(''))} {jpeg_quality}",
            "close",
            "",
        ]
    return "\n".join(commands)


def parse_last_count(log_text: str, patterns: Iterable[re.Pattern[str]]) -> int | None:
    values: list[int] = []
    for pattern in patterns:
        values.extend(int(match.group(1)) for match in pattern.finditer(log_text))
    return values[-1] if values else None


def fits_image_dimensions(path: Path) -> tuple[int, int]:
    """Read primary-image dimensions from a FITS header without dependencies.

    FITS headers consist of 80-byte ASCII cards padded to 2880-byte blocks.
    Only the primary HDU's ``NAXIS1`` and ``NAXIS2`` cards are needed for the
    framing guard, so the potentially very large pixel payload is never read.
    """

    values: dict[str, int] = {}
    found_end = False
    try:
        with path.open("rb") as handle:
            while not found_end:
                block = handle.read(FITS_BLOCK_SIZE)
                if len(block) != FITS_BLOCK_SIZE:
                    raise ValueError("header ended before a complete FITS block")
                for offset in range(0, FITS_BLOCK_SIZE, FITS_CARD_SIZE):
                    raw_card = block[offset : offset + FITS_CARD_SIZE]
                    try:
                        card = raw_card.decode("ascii")
                    except UnicodeDecodeError as exc:
                        raise ValueError("header contains non-ASCII data") from exc
                    keyword = card[:8].strip()
                    if keyword == "END":
                        found_end = True
                        break
                    if keyword not in {"NAXIS", "NAXIS1", "NAXIS2"} or card[8:10] != "= ":
                        continue
                    raw_value = card[10:].split("/", 1)[0].strip()
                    match = re.match(r"^[+-]?\d+", raw_value)
                    if match is None:
                        raise ValueError(f"{keyword} is not an integer")
                    values[keyword] = int(match.group(0))
    except OSError as exc:
        raise ValueError(f"could not read FITS header: {exc}") from exc

    if values.get("NAXIS", 0) < 2:
        raise ValueError("primary FITS image has fewer than two axes")
    width = values.get("NAXIS1")
    height = values.get("NAXIS2")
    if width is None or height is None or width <= 0 or height <= 0:
        raise ValueError("primary FITS image lacks positive NAXIS1/NAXIS2 dimensions")
    return width, height


def framing_quality(
    source_dimensions: tuple[int, int],
    output_dimensions: tuple[int, int],
) -> dict[str, object]:
    """Describe how much of the source field remains in a registered stack."""

    source_width, source_height = source_dimensions
    output_width, output_height = output_dimensions
    if min(source_width, source_height, output_width, output_height) <= 0:
        raise ValueError("FITS dimensions must be positive")
    width_ratio = output_width / source_width
    height_ratio = output_height / source_height
    area_ratio = (output_width * output_height) / (source_width * source_height)
    below_threshold = (
        area_ratio < MIN_FRAMING_AREA_RETENTION
        or width_ratio < MIN_FRAMING_AXIS_RETENTION
        or height_ratio < MIN_FRAMING_AXIS_RETENTION
    )
    return {
        "source_dimensions": {"width": source_width, "height": source_height},
        "output_dimensions": {"width": output_width, "height": output_height},
        "retained_width_ratio": round(width_ratio, 6),
        "retained_height_ratio": round(height_ratio, 6),
        "retained_area_ratio": round(area_ratio, 6),
        "thresholds": {
            "minimum_axis_ratio": MIN_FRAMING_AXIS_RETENTION,
            "minimum_area_ratio": MIN_FRAMING_AREA_RETENTION,
        },
        "below_threshold": below_threshold,
    }


def inspect_framing(source_fits: Path, output_fits: Path) -> dict[str, object]:
    report = framing_quality(
        fits_image_dimensions(source_fits),
        fits_image_dimensions(output_fits),
    )
    report["dimension_reference"] = str(source_fits)
    return report


def _retry_source_dimensions(retry: FramingRetry) -> tuple[tuple[int, int], str]:
    """Recover a trustworthy source canvas for an in-place framing retry."""

    prior_quality = retry.manifest.get("framing_quality")
    if isinstance(prior_quality, dict):
        prior_source = prior_quality.get("source_dimensions")
        if isinstance(prior_source, dict):
            width = prior_source.get("width")
            height = prior_source.get("height")
            if (
                isinstance(width, int)
                and not isinstance(width, bool)
                and isinstance(height, int)
                and not isinstance(height, bool)
                and width > 0
                and height > 0
            ):
                reference = prior_quality.get("dimension_reference", "prior manifest")
                return (width, height), str(reference)

    inputs = retry.manifest.get("inputs")
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            source_path = Path(item["path"]).expanduser()
            try:
                return fits_image_dimensions(source_path), str(source_path)
            except ValueError:
                continue

    # Explicit retries are designed to work from their retained cache even if
    # the removable source drive is no longer mounted. Converted frames retain
    # the same pixel canvas as the originals.
    converted = retry.converted_frames[0]
    return fits_image_dimensions(converted), f"{converted} (converted source canvas)"


def find_siril(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    found = shutil.which("siril-cli")
    if found:
        candidates.append(Path(found))
    candidates.append(DEFAULT_SIRIL_MAC)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Could not find siril-cli. Install Siril, or pass --siril /path/to/siril-cli."
    )


def siril_version(siril: Path) -> str:
    completed = subprocess.run(
        [str(siril), "--version"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"\bsiril\s+\d+(?:\.\d+)+", line, re.IGNORECASE):
            return line
    return lines[-1] if lines else "unknown"


def run_siril(siril: Path, script_path: Path, working_directory: Path, log_path: Path) -> int:
    command = [str(siril), "-o", "-d", str(working_directory), "-s", str(script_path)]
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log_file.write(line)
        return process.wait()


def existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def disk_preflight(destination: Path, required_bytes: int) -> tuple[bool, int]:
    free = shutil.disk_usage(existing_ancestor(destination)).free
    return free >= required_bytes, free


def human_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{number:.1f} TiB"


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def remove_owned_work_directory(work_dir: Path) -> None:
    marker = work_dir / WORK_MARKER
    if work_dir.name != "_work" or not marker.is_file():
        raise RuntimeError(f"Refusing to remove unrecognized work directory: {work_dir}")
    shutil.rmtree(work_dir)


def output_stem(group: CaptureGroup, stacked_count: int) -> str:
    object_name = safe_component(group.object_name, spaces=True)
    filter_name = safe_component(group.filter_name, spaces=True)
    return (
        f"Stacked_{stacked_count}_{object_name}_{group.exposure}s_"
        f"{filter_name}_{group.first_timestamp}"
    )


def stack_settings(
    *,
    quality_mode: str,
    preview_style: str,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int,
    preview_rotation: int,
    preview_flip: str,
    framing_mode: str = "min",
    minimum_stacked_frames: int = DEFAULT_MIN_FRAMES,
) -> dict[str, object]:
    framing_description = {
        "min": "minimum common area",
        "cog": "center of gravity",
        "current": "reference image",
    }[framing_mode]
    return {
        "pipeline": "seestar-lights-only direct debayer conversion",
        "registration": "two-pass homography",
        "framing": framing_description,
        "framing_mode": framing_mode,
        "minimum_stacked_frames": minimum_stacked_frames,
        "quality_mode": quality_mode,
        "stack": "average, winsorized rejection 3/3, additive scaling, star-count weighting",
        "output_bits": 32,
        "preview_style": preview_style,
        "preview_brightness": brightness,
        "preview_shadow_sigma": shadow_sigma,
        "preview_rotation_degrees": preview_rotation,
        "preview_flip": preview_flip,
        "jpeg_quality": jpeg_quality,
    }


def resolve_manifest_output(manifest_path: Path, raw_path: object) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def output_is_confined(path: Path, run_dir: Path) -> bool:
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        return False
    return True


def validate_new_output_path(destination: Path, target: Path) -> None:
    """Reject lexical escapes and symlinked components before creating a run."""

    root = destination.absolute()
    candidate_target = target.absolute()
    try:
        relative = candidate_target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output path escapes the destination: {target}") from exc

    candidate = root
    if candidate.is_symlink():
        raise ValueError(f"output path contains a symlink: {candidate}")
    for component in relative.parts:
        candidate /= component
        if candidate.is_symlink():
            raise ValueError(f"output path contains a symlink: {candidate}")


def valid_stack_counts(
    input_count: int,
    registered_count: object,
    stacked_count: object,
    minimum_stacked_frames: int = 1,
) -> bool:
    return (
        isinstance(registered_count, int)
        and not isinstance(registered_count, bool)
        and isinstance(stacked_count, int)
        and not isinstance(stacked_count, bool)
        and minimum_stacked_frames <= stacked_count <= registered_count <= input_count
    )


def _confined_regular_file(path: Path, run_dir: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file, not a symlink: {path}")
    resolved = path.resolve(strict=True)
    if not output_is_confined(resolved, run_dir):
        raise ValueError(f"{label} escapes the run folder: {path}")
    return resolved


def _confined_directory(path: Path, run_dir: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be a real directory, not a symlink: {path}")
    resolved = path.resolve(strict=True)
    if not output_is_confined(resolved, run_dir):
        raise ValueError(f"{label} escapes the run folder: {path}")
    return resolved


def _integer_setting(value: object, label: str, minimum: int, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{label} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be no more than {maximum}")
    return value


def _sequence_image_count(sequence_path: Path) -> int:
    try:
        lines = sequence_path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read Siril sequence {sequence_path}: {exc}") from exc
    pattern = re.compile(r"^S\s+(?:'light_'|\"light_\"|light_)\s+\d+\s+(\d+)\b")
    for line in lines:
        match = pattern.match(line.strip())
        if match:
            return int(match.group(1))
    raise ValueError(f"Siril sequence does not describe the expected light_ sequence: {sequence_path}")


def load_framing_retry(run_path: Path, framing_mode: str) -> FramingRetry:
    """Validate a failed, marker-owned run before reusing any intermediates."""

    if framing_mode not in RETRY_FRAMING_MODES:
        raise ValueError(f"retry framing must be one of {RETRY_FRAMING_MODES}")
    run_dir = run_path.expanduser().resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run is not a directory: {run_path}")

    manifest_path = _confined_regular_file(run_dir / MANIFEST_NAME, run_dir, "manifest")
    try:
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read manifest {manifest_path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"manifest must contain a JSON object: {manifest_path}")
    manifest: dict[str, object] = loaded
    if manifest.get("tool") != TOOL_NAME:
        raise ValueError(f"manifest is not owned by {TOOL_NAME}: {manifest_path}")
    if manifest.get("schema_version") not in {1, MANIFEST_SCHEMA_VERSION}:
        raise ValueError(f"manifest schema must be 1 or {MANIFEST_SCHEMA_VERSION}: {manifest_path}")
    if manifest.get("status") != "failed":
        raise ValueError(f"only failed runs can be retried in place: {manifest_path}")
    attempts = manifest.get("attempts", [])
    if not isinstance(attempts, list):
        raise ValueError(f"manifest attempts must be a list: {manifest_path}")

    work_dir = _confined_directory(run_dir / "_work", run_dir, "work folder")
    marker = _confined_regular_file(work_dir / WORK_MARKER, run_dir, "work ownership marker")
    try:
        marker_text = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read work ownership marker {marker}: {exc}") from exc
    if marker_text != f"Owned by {TOOL_NAME}":
        raise ValueError(f"work ownership marker does not name {TOOL_NAME}: {marker}")

    process_dir = _confined_directory(work_dir / "process", run_dir, "process folder")
    sequence_path = _confined_regular_file(process_dir / "light_.seq", run_dir, "Siril sequence")
    converted_candidates = sorted(
        (
            path
            for path in process_dir.iterdir()
            if path.name.casefold().startswith("light_")
            and path.suffix.casefold() in {".fit", ".fits"}
        ),
        key=lambda path: path.name.casefold(),
    )
    if not converted_candidates:
        raise ValueError(f"no converted light_ FITS remain in {process_dir}")
    converted_frames = tuple(
        _confined_regular_file(path, run_dir, "converted FITS")
        for path in converted_candidates
    )

    input_count = _integer_setting(manifest.get("input_count"), "manifest input_count", 1)
    sequence_count = _sequence_image_count(sequence_path)
    if sequence_count != len(converted_frames):
        raise ValueError(
            "Siril sequence/conversion mismatch: "
            f"sequence={sequence_count}, converted FITS={len(converted_frames)}"
        )
    if sequence_count > input_count:
        raise ValueError(
            f"Siril sequence has {sequence_count} images but manifest has only {input_count} inputs"
        )

    settings_value = manifest.get("settings")
    if not isinstance(settings_value, dict):
        raise ValueError(f"manifest settings must be an object: {manifest_path}")
    settings: dict[str, object] = dict(settings_value)
    quality_mode = settings.get("quality_mode")
    if quality_mode not in {"balanced", "keep-all", "sharpest-75"}:
        raise ValueError(f"manifest has unsupported quality_mode: {quality_mode!r}")
    preview_style = settings.get("preview_style", "dark")
    if not isinstance(preview_style, str) or preview_style not in PREVIEW_STYLES:
        raise ValueError(f"manifest has unsupported preview_style: {preview_style!r}")
    raw_brightness = settings.get("preview_brightness")
    raw_shadow_sigma = settings.get("preview_shadow_sigma")
    if raw_brightness is not None and not isinstance(raw_brightness, (int, float)):
        raise ValueError("manifest preview_brightness must be numeric")
    if raw_shadow_sigma is not None and not isinstance(raw_shadow_sigma, (int, float)):
        raise ValueError("manifest preview_shadow_sigma must be numeric")
    brightness, shadow_sigma = resolve_preview_settings(
        preview_style,
        float(raw_brightness) if raw_brightness is not None else None,
        float(raw_shadow_sigma) if raw_shadow_sigma is not None else None,
    )
    jpeg_quality = _integer_setting(settings.get("jpeg_quality", 95), "manifest jpeg_quality", 1, 100)
    preview_rotation = settings.get("preview_rotation_degrees", 0)
    if not isinstance(preview_rotation, int) or preview_rotation not in PREVIEW_ROTATIONS:
        raise ValueError(f"manifest has unsupported preview rotation: {preview_rotation!r}")
    preview_flip = settings.get("preview_flip", "none")
    if not isinstance(preview_flip, str) or preview_flip not in PREVIEW_FLIPS:
        raise ValueError(f"manifest has unsupported preview flip: {preview_flip!r}")
    minimum_stacked_frames = _integer_setting(
        settings.get("minimum_stacked_frames", DEFAULT_MIN_FRAMES),
        "manifest minimum_stacked_frames",
        1,
    )
    if minimum_stacked_frames > input_count:
        raise ValueError(
            f"minimum stacked frames {minimum_stacked_frames} exceeds input count {input_count}"
        )
    replaces_framing_mode = settings.get("framing_mode", "min")
    if replaces_framing_mode not in FRAMING_MODES:
        raise ValueError(
            f"manifest has unsupported framing_mode: {replaces_framing_mode!r}"
        )

    framing_description = {
        "cog": "center of gravity",
        "current": "reference image",
    }[framing_mode]
    settings.update(
        {
            "preview_style": preview_style,
            "preview_brightness": brightness,
            "preview_shadow_sigma": shadow_sigma,
            "jpeg_quality": jpeg_quality,
            "preview_rotation_degrees": preview_rotation,
            "preview_flip": preview_flip,
            "minimum_stacked_frames": minimum_stacked_frames,
            "framing": framing_description,
            "framing_mode": framing_mode,
        }
    )
    converted_bytes = sum(path.stat().st_size for path in converted_frames)
    required_bytes = max(1, int(converted_bytes * RETRY_DISK_MULTIPLIER))
    return FramingRetry(
        run_dir=run_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        process_dir=process_dir,
        sequence_path=sequence_path,
        converted_frames=converted_frames,
        normalized_settings=settings,
        quality_mode=quality_mode,
        brightness=brightness,
        shadow_sigma=shadow_sigma,
        jpeg_quality=jpeg_quality,
        preview_rotation=preview_rotation,
        preview_flip=preview_flip,
        minimum_stacked_frames=minimum_stacked_frames,
        replaces_framing_mode=replaces_framing_mode,
        input_count=input_count,
        required_bytes=required_bytes,
    )


def retry_output_stem(manifest: dict[str, object], stacked_count: int) -> str:
    capture = manifest.get("capture")
    if not isinstance(capture, dict):
        raise ValueError("manifest capture must be an object")
    object_name = capture.get("object")
    exposure = capture.get("exposure_seconds")
    filter_name = capture.get("filter")
    first_timestamp = capture.get("first_timestamp")
    if first_timestamp is None:
        inputs = manifest.get("inputs")
        if isinstance(inputs, list):
            timestamps = [
                item.get("timestamp")
                for item in inputs
                if isinstance(item, dict) and isinstance(item.get("timestamp"), str)
            ]
            first_timestamp = min(timestamps) if timestamps else None
    values = (object_name, exposure, filter_name, first_timestamp)
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("manifest capture lacks object, exposure, filter, or first timestamp")
    return (
        f"Stacked_{stacked_count}_{safe_component(object_name, spaces=True)}_"
        f"{safe_component(exposure, spaces=True)}s_"
        f"{safe_component(filter_name, spaces=True)}_"
        f"{safe_component(first_timestamp, spaces=True)}"
    )


def retry_artifact_paths(run_dir: Path, framing_mode: str) -> tuple[str, Path, Path, Path, Path]:
    """Choose new retry artifacts without replacing earlier diagnostics."""

    suffix = 1
    while True:
        output_prefix = f"retry-{framing_mode}" if suffix == 1 else f"retry-{framing_mode}-{suffix}"
        script_path = run_dir / f"{output_prefix}.ssf"
        log_path = run_dir / f"{output_prefix}.log"
        linear = run_dir / f"{output_prefix}-linear.fit"
        preview = run_dir / f"{output_prefix}-preview.jpg"
        paths = (script_path, log_path, linear, preview)
        if not any(path.exists() or path.is_symlink() for path in paths):
            return output_prefix, script_path, log_path, linear, preview
        suffix += 1


def load_preview_refresh(
    manifest_path: Path,
    root: Path,
    *,
    style: str,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int | None = None,
) -> PreviewRefresh:
    """Validate one successful run before deriving a replacement preview.

    Refreshing is limited to owned successful schema-1/schema-2 manifests with
    verified, run-confined outputs. A schema-1 run is migrated only after the
    new preview succeeds. The original linear FITS remains the source of truth.
    """

    if style not in PREVIEW_STYLES:
        raise ValueError(f"preview style must be one of {tuple(sorted(PREVIEW_STYLES))}")
    brightness, shadow_sigma = resolve_preview_settings(style, brightness, shadow_sigma)
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise ValueError(f"refresh root is not a directory: {root}")
    resolved_manifest = _confined_regular_file(
        manifest_path.expanduser(), resolved_root, "manifest"
    )
    run_dir = resolved_manifest.parent
    try:
        manifest_bytes = resolved_manifest.read_bytes()
        loaded = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read manifest {resolved_manifest}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"manifest must contain a JSON object: {resolved_manifest}")
    manifest: dict[str, object] = loaded
    if manifest.get("tool") != TOOL_NAME:
        raise ValueError(f"manifest is not owned by {TOOL_NAME}: {resolved_manifest}")
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in {1, MANIFEST_SCHEMA_VERSION}
    ):
        raise ValueError(
            f"preview refresh requires schema 1 or {MANIFEST_SCHEMA_VERSION}: {resolved_manifest}"
        )
    if manifest.get("status") != "success":
        raise ValueError(f"preview refresh requires a successful run: {resolved_manifest}")

    settings_value = manifest.get("settings")
    outputs_value = manifest.get("outputs")
    hashes_value = manifest.get("output_sha256")
    if not isinstance(settings_value, dict):
        raise ValueError(f"manifest settings must be an object: {resolved_manifest}")
    if not isinstance(outputs_value, dict):
        raise ValueError(f"manifest outputs must be an object: {resolved_manifest}")
    settings: dict[str, object] = dict(settings_value)
    outputs: dict[str, object] = outputs_value
    if schema_version == MANIFEST_SCHEMA_VERSION:
        if not isinstance(hashes_value, dict):
            raise ValueError(f"manifest output hashes must be an object: {resolved_manifest}")
        hashes: dict[str, object] | None = hashes_value
    elif hashes_value is None:
        hashes = None
    elif isinstance(hashes_value, dict):
        hashes = hashes_value
    else:
        raise ValueError(
            f"schema-1 output hashes must be absent or an object: {resolved_manifest}"
        )

    raw_linear = outputs.get("linear_fits")
    raw_preview = outputs.get("preview_jpeg")
    if not isinstance(raw_linear, str) or not raw_linear:
        raise ValueError("manifest linear_fits output must be a non-empty path")
    if not isinstance(raw_preview, str) or not raw_preview:
        raise ValueError("manifest preview_jpeg output must be a non-empty path")
    linear = _confined_regular_file(
        resolve_manifest_output(resolved_manifest, raw_linear), run_dir, "linear FITS"
    )
    preview = _confined_regular_file(
        resolve_manifest_output(resolved_manifest, raw_preview), run_dir, "JPEG preview"
    )
    if linear == preview:
        raise ValueError("manifest linear FITS and JPEG preview cannot be the same file")
    if linear.suffix.casefold() not in {".fit", ".fits"}:
        raise ValueError(f"linear output is not a FITS file: {linear}")
    if preview.suffix.casefold() not in {".jpg", ".jpeg"}:
        raise ValueError(f"preview output is not a JPEG: {preview}")

    actual_linear_hash = sha256_file(linear)
    actual_preview_hash = sha256_file(preview)
    if hashes is not None:
        if hashes.get("linear_fits") != actual_linear_hash:
            raise ValueError(f"linear FITS checksum does not match manifest: {linear}")
        if hashes.get("preview_jpeg") != actual_preview_hash:
            raise ValueError(f"JPEG preview checksum does not match manifest: {preview}")

    input_count = _integer_setting(manifest.get("input_count"), "manifest input_count", 1)
    minimum = _integer_setting(
        settings.get("minimum_stacked_frames", DEFAULT_MIN_FRAMES),
        "manifest minimum_stacked_frames",
        1,
    )
    if not valid_stack_counts(
        input_count,
        manifest.get("registered_count"),
        manifest.get("stacked_count"),
        minimum,
    ):
        raise ValueError(f"manifest has invalid stack frame counts: {resolved_manifest}")

    framing_mode = settings.get("framing_mode")
    if framing_mode is None and schema_version == 1:
        framing_mode = {
            "minimum common area": "min",
            "center of gravity": "cog",
            "reference image": "current",
        }.get(settings.get("framing"))
    if not isinstance(framing_mode, str) or framing_mode not in FRAMING_MODES:
        raise ValueError(f"manifest has unsupported framing_mode: {framing_mode!r}")
    settings["framing_mode"] = framing_mode
    settings["minimum_stacked_frames"] = minimum
    rotation = settings.get("preview_rotation_degrees", 0)
    if not isinstance(rotation, int) or isinstance(rotation, bool) or rotation not in PREVIEW_ROTATIONS:
        raise ValueError(f"manifest has unsupported preview rotation: {rotation!r}")
    flip = settings.get("preview_flip", "none")
    if not isinstance(flip, str) or flip not in PREVIEW_FLIPS:
        raise ValueError(f"manifest has unsupported preview flip: {flip!r}")
    current_quality = _integer_setting(
        settings.get("jpeg_quality", 95), "manifest jpeg_quality", 1, 100
    )
    selected_quality = (
        current_quality
        if jpeg_quality is None
        else _integer_setting(jpeg_quality, "JPEG quality", 1, 100)
    )
    refreshes = manifest.get("preview_refreshes", [])
    if not isinstance(refreshes, list) or any(not isinstance(item, dict) for item in refreshes):
        raise ValueError(f"manifest preview_refreshes must be a list of objects: {resolved_manifest}")

    return PreviewRefresh(
        root=resolved_root,
        run_dir=run_dir,
        manifest_path=resolved_manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        manifest=manifest,
        schema_version=schema_version,
        settings=settings,
        linear_fits=linear,
        preview_jpeg=preview,
        linear_sha256=actual_linear_hash,
        preview_sha256=actual_preview_hash,
        style=style,
        brightness=brightness,
        shadow_sigma=shadow_sigma,
        jpeg_quality=selected_quality,
        rotation=rotation,
        flip=flip,
        framing_mode=framing_mode,
    )


def preview_refresh_artifact_paths(
    refresh: PreviewRefresh,
) -> tuple[Path, Path, Path, Path]:
    """Choose new artifacts without replacing a prior preview or diagnostic."""

    suffix = 1
    while True:
        label = f"preview-refresh-{refresh.style}"
        if suffix > 1:
            label = f"{label}-{suffix}"
        script_path = refresh.run_dir / f"{label}.ssf"
        log_path = refresh.run_dir / f"{label}.log"
        pending_preview = refresh.run_dir / f".{label}.pending.jpg"
        final_preview = refresh.run_dir / f"{label}.jpg"
        paths = (script_path, log_path, pending_preview, final_preview)
        if not any(path.exists() or path.is_symlink() for path in paths):
            return paths
        suffix += 1


def _latest_refresh_matches_outputs(
    manifest: dict[str, object],
    *,
    framing_mode: object,
    outputs: dict[str, object],
    hashes: dict[str, object],
) -> bool:
    """Verify that the current preview was refreshed from the current linear stack."""

    refreshes = manifest.get("preview_refreshes")
    if not isinstance(refreshes, list) or not refreshes:
        return False
    refresh = refreshes[-1]
    if not isinstance(refresh, dict):
        return False
    source = refresh.get("source")
    refresh_outputs = refresh.get("outputs")
    refresh_hashes = refresh.get("output_sha256")
    if not all(isinstance(value, dict) for value in (source, refresh_outputs, refresh_hashes)):
        return False
    assert isinstance(source, dict)
    assert isinstance(refresh_outputs, dict)
    assert isinstance(refresh_hashes, dict)
    return (
        refresh.get("kind") == "preview_refresh"
        and refresh.get("status") == "success"
        and refresh.get("framing_mode") == framing_mode
        and source.get("linear_fits") == outputs.get("linear_fits")
        and source.get("linear_fits_sha256") == hashes.get("linear_fits")
        and refresh_outputs.get("preview_jpeg") == outputs.get("preview_jpeg")
        and refresh_hashes.get("preview_jpeg") == hashes.get("preview_jpeg")
    )


def resume_settings_rank(
    manifest: dict[str, object],
    existing_settings: object,
    requested_settings: dict[str, object],
) -> int:
    """Rank exact settings above a proven one-way framing fallback."""

    if not isinstance(existing_settings, dict):
        return 0
    if existing_settings == requested_settings:
        return 2
    existing_framing = existing_settings.get("framing_mode")
    requested_framing = requested_settings.get("framing_mode")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or requested_framing != "min"
        or existing_framing not in RETRY_FRAMING_MODES
    ):
        return 0

    existing_non_framing = {
        key: value
        for key, value in existing_settings.items()
        if key not in {"framing", "framing_mode"}
    }
    requested_non_framing = {
        key: value
        for key, value in requested_settings.items()
        if key not in {"framing", "framing_mode"}
    }
    if existing_non_framing != requested_non_framing:
        return 0

    outputs = manifest.get("outputs")
    hashes = manifest.get("output_sha256")
    if not isinstance(outputs, dict) or not isinstance(hashes, dict):
        return 0

    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return 0
    for attempt in reversed(attempts):
        if not isinstance(attempt, dict):
            continue
        replaces_framing = attempt.get("replaces_framing_mode", "min")
        if (
            attempt.get("kind") != "framing_retry"
            or attempt.get("status") != "success"
            or attempt.get("framing_mode") != existing_framing
            or replaces_framing != requested_framing
        ):
            continue
        attempt_outputs = attempt.get("outputs")
        attempt_hashes = attempt.get("output_sha256")
        if not isinstance(attempt_outputs, dict) or not isinstance(attempt_hashes, dict):
            continue
        linear_matches = (
            attempt_outputs.get("linear_fits") == outputs.get("linear_fits")
            and attempt_hashes.get("linear_fits") == hashes.get("linear_fits")
        )
        if not linear_matches:
            continue
        preview_matches = (
            attempt_outputs.get("preview_jpeg") == outputs.get("preview_jpeg")
            and attempt_hashes.get("preview_jpeg") == hashes.get("preview_jpeg")
        )
        if preview_matches or _latest_refresh_matches_outputs(
            manifest,
            framing_mode=existing_framing,
            outputs=outputs,
            hashes=hashes,
        ):
            return 1
    return 0


def find_completed_run(
    group: CaptureGroup,
    destination: Path,
    fingerprint: str,
    settings: dict[str, object],
) -> Path | None:
    """Return a successful matching manifest so interrupted batches can resume."""

    target_root = destination / safe_component(group.target_name)
    try:
        validate_new_output_path(destination, target_root)
    except ValueError:
        return None
    if not target_root.is_dir():
        return None
    resolved_target_root = target_root.resolve()
    matches: list[tuple[int, str, str, Path]] = []
    for manifest_path in target_root.rglob(MANIFEST_NAME):
        try:
            manifest_path.resolve().relative_to(resolved_target_root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            outputs = manifest["outputs"]
            linear = resolve_manifest_output(manifest_path, outputs["linear_fits"])
            preview = resolve_manifest_output(manifest_path, outputs["preview_jpeg"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        existing_settings = manifest.get("settings")
        if isinstance(existing_settings, dict) and "framing_mode" not in existing_settings:
            # Manifests produced before framing became configurable used min.
            existing_settings = dict(existing_settings, framing_mode="min")
        if isinstance(existing_settings, dict) and "minimum_stacked_frames" not in existing_settings:
            # Older batches used the default 50-frame input threshold.
            existing_settings = dict(existing_settings, minimum_stacked_frames=DEFAULT_MIN_FRAMES)
        settings_rank = resume_settings_rank(manifest, existing_settings, settings)
        if settings_rank == 0:
            continue
        capture = manifest.get("capture")
        capture_matches = isinstance(capture, dict) and (
            capture.get("object") == group.object_name
            and str(capture.get("exposure_seconds")) == group.exposure
            and capture.get("filter") == group.filter_name
        )
        source_matches = manifest.get("source_folder") == str(group.source_folder)
        inputs_match = manifest.get("inputs") == manifest_input_records(group)
        run_dir = manifest_path.parent.resolve()
        paths_are_safe = output_is_confined(linear, run_dir) and output_is_confined(preview, run_dir)
        counts_are_sane = valid_stack_counts(
            len(group.frames),
            manifest.get("registered_count"),
            manifest.get("stacked_count"),
            int(settings.get("minimum_stacked_frames", DEFAULT_MIN_FRAMES)),
        )
        hashes = manifest.get("output_sha256")
        hashes_match = False
        if manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION:
            try:
                hashes_match = (
                    isinstance(hashes, dict)
                    and hashes.get("linear_fits") == sha256_file(linear)
                    and hashes.get("preview_jpeg") == sha256_file(preview)
                ) if linear.is_file() and preview.is_file() else False
            except OSError:
                hashes_match = False
        framing_is_safe = True
        if settings.get("framing_mode") == "min" and linear.is_file():
            try:
                framing_is_safe = not bool(
                    inspect_framing(group.frames[0].path, linear)["below_threshold"]
                )
            except (OSError, ValueError, IndexError):
                # A default-min resume must prove that it did not collapse the
                # field. This also audits successful manifests created before
                # the framing guard recorded dimensions itself.
                framing_is_safe = False
        if (
            manifest.get("tool") == TOOL_NAME
            and manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
            and manifest.get("status") == "success"
            and manifest.get("target") == group.target_name
            and capture_matches
            and source_matches
            and inputs_match
            and manifest.get("input_count") == len(group.frames)
            and manifest.get("input_fingerprint") == fingerprint
            and paths_are_safe
            and counts_are_sane
            and linear.is_file()
            and linear.stat().st_size > 0
            and preview.is_file()
            and preview.stat().st_size > 0
            and hashes_match
            and framing_is_safe
        ):
            matches.append(
                (
                    settings_rank,
                    str(manifest.get("finished_at", "")),
                    str(manifest_path),
                    manifest_path,
                )
            )
    return max(matches)[3] if matches else None


def find_retained_failed_runs(
    group: CaptureGroup,
    destination: Path,
    fingerprint: str,
) -> tuple[Path, ...]:
    """Find matching failures whose marker-owned work cache is still present."""

    target_root = destination / safe_component(group.target_name)
    try:
        validate_new_output_path(destination, target_root)
    except ValueError:
        return ()
    if not target_root.is_dir():
        return ()
    resolved_target_root = target_root.resolve()
    matches: list[Path] = []
    for manifest_path in target_root.rglob(MANIFEST_NAME):
        try:
            if manifest_path.is_symlink():
                continue
            resolved_manifest = manifest_path.resolve(strict=True)
            resolved_manifest.relative_to(resolved_target_root)
            manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
            run_dir = resolved_manifest.parent
            work_dir = run_dir / "_work"
            marker = work_dir / WORK_MARKER
            if work_dir.is_symlink() or marker.is_symlink():
                continue
            resolved_work = work_dir.resolve(strict=True)
            resolved_marker = marker.resolve(strict=True)
            if not work_dir.is_dir() or not marker.is_file():
                continue
            if not output_is_confined(resolved_work, run_dir):
                continue
            if not output_is_confined(resolved_marker, run_dir):
                continue
            if marker.read_text(encoding="utf-8").strip() != f"Owned by {TOOL_NAME}":
                continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        capture = manifest.get("capture")
        capture_matches = isinstance(capture, dict) and (
            capture.get("object") == group.object_name
            and str(capture.get("exposure_seconds")) == group.exposure
            and capture.get("filter") == group.filter_name
        )
        source_matches = manifest.get("source_folder") == str(group.source_folder)
        inputs_match = manifest.get("inputs") == manifest_input_records(group)
        if (
            manifest.get("tool") == TOOL_NAME
            and manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
            and manifest.get("status") == "failed"
            and manifest.get("target") == group.target_name
            and capture_matches
            and source_matches
            and inputs_match
            and manifest.get("input_count") == len(group.frames)
            and manifest.get("input_fingerprint") == fingerprint
        ):
            matches.append(run_dir)
    return tuple(sorted(matches, key=str))


def retry_framing_command(run_dir: Path, *, dry_run: bool = True) -> str:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "retry-framing",
        "--run",
        str(run_dir),
        "--framing",
        "cog",
    ]
    if dry_run:
        command.append("--dry-run")
    return shlex.join(command)


def initial_manifest(
    group: CaptureGroup,
    *,
    fingerprint: str,
    siril: Path,
    version: str,
    settings: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "running",
        "created_at": utc_now(),
        "target": group.target_name,
        "source_folder": str(group.source_folder),
        "capture": {
            "object": group.object_name,
            "exposure_seconds": group.exposure,
            "filter": group.filter_name,
            "first_timestamp": group.first_timestamp,
            "last_timestamp": max(frame.timestamp for frame in group.frames),
        },
        "input_count": len(group.frames),
        "input_bytes": group.total_bytes,
        "input_fingerprint": fingerprint,
        "inputs": manifest_input_records(group),
        "siril": {"executable": str(siril), "version": version},
        "settings": settings,
    }


def run_group(
    group: CaptureGroup,
    destination: Path,
    siril: Path,
    version: str,
    *,
    quality_mode: str,
    preview_style: str,
    brightness: float,
    shadow_sigma: float,
    jpeg_quality: int,
    preview_rotation: int,
    preview_flip: str,
    framing_mode: str = "min",
    keep_work: bool,
    minimum_stacked_frames: int = 1,
) -> bool:
    fingerprint = inventory_fingerprint(group)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    group_slug = safe_component(
        f"{group.object_name}_{group.exposure}s_{group.filter_name}"
    )
    group_root = destination / safe_component(group.target_name) / group_slug
    run_dir = group_root / f"{timestamp}-{fingerprint[:10]}"
    try:
        validate_new_output_path(destination, run_dir)
    except ValueError as exc:
        print(f"Error: refusing unsafe stack output path: {exc}", file=sys.stderr)
        return False
    suffix = 2
    while run_dir.exists():
        run_dir = run_dir.with_name(f"{timestamp}-{fingerprint[:10]}-{suffix}")
        suffix += 1
        try:
            validate_new_output_path(destination, run_dir)
        except ValueError as exc:
            print(f"Error: refusing unsafe stack output path: {exc}", file=sys.stderr)
            return False

    work_dir = run_dir / "_work"
    lights_dir = work_dir / "lights"
    process_dir = work_dir / "process"
    try:
        lights_dir.mkdir(parents=True)
        validate_new_output_path(destination, lights_dir)
        process_dir.mkdir()
        validate_new_output_path(destination, process_dir)
    except (OSError, ValueError) as exc:
        print(f"Error: could not create a confined stack work folder: {exc}", file=sys.stderr)
        return False
    (work_dir / WORK_MARKER).write_text(f"Owned by {TOOL_NAME}\n", encoding="utf-8")

    for index, frame in enumerate(group.frames, start=1):
        suffix_name = ".fits" if frame.path.suffix.casefold() == ".fits" else ".fit"
        (lights_dir / f"light_{index:05d}{suffix_name}").symlink_to(frame.path)

    settings = stack_settings(
        quality_mode=quality_mode,
        preview_style=preview_style,
        brightness=brightness,
        shadow_sigma=shadow_sigma,
        jpeg_quality=jpeg_quality,
        preview_rotation=preview_rotation,
        preview_flip=preview_flip,
        framing_mode=framing_mode,
        minimum_stacked_frames=minimum_stacked_frames,
    )
    manifest = initial_manifest(
        group,
        fingerprint=fingerprint,
        siril=siril,
        version=version,
        settings=settings,
    )
    manifest_path = run_dir / MANIFEST_NAME
    script_path = run_dir / "pipeline.ssf"
    log_path = run_dir / "siril.log"
    script_path.write_text(
        render_stack_script(
            run_dir,
            quality_mode=quality_mode,
            brightness=brightness,
            shadow_sigma=shadow_sigma,
            jpeg_quality=jpeg_quality,
            framing_mode=framing_mode,
            preview_rotation=preview_rotation,
            preview_flip=preview_flip,
        ),
        encoding="utf-8",
    )
    write_json(manifest_path, manifest)

    print(f"\nStacking {group.target_name}: {len(group.frames)} frames")
    print(f"Run folder: {run_dir}")
    exit_code = run_siril(siril, script_path, run_dir, log_path)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    linear = run_dir / "stacked_linear.fit"
    preview = run_dir / "preview.jpg"
    stacked_count = parse_last_count(log_text, STACKED_COUNT_PATTERNS)
    registered_count = parse_last_count(log_text, REGISTERED_COUNT_PATTERNS)
    counts_are_sane = valid_stack_counts(
        len(group.frames), registered_count, stacked_count, minimum_stacked_frames
    )
    success = (
        exit_code == 0
        and "Script execution finished successfully" in log_text
        and linear.is_file()
        and linear.stat().st_size > 0
        and preview.is_file()
        and preview.stat().st_size > 0
        and counts_are_sane
    )

    framing_inspection_error: str | None = None
    if success:
        try:
            quality = inspect_framing(group.frames[0].path, linear)
            quality["framing_mode"] = framing_mode
            manifest["framing_quality"] = quality
        except ValueError as exc:
            # A successful Siril exit is not enough to select an output whose
            # canvas cannot be verified. Retain the registered cache so the
            # run can be diagnosed or explicitly retried without recopying.
            success = False
            framing_inspection_error = f"Could not verify stack framing: {exc}"

    manifest["finished_at"] = utc_now()
    manifest["exit_code"] = exit_code
    manifest["registered_count"] = registered_count
    manifest["stacked_count"] = stacked_count
    if success:
        assert stacked_count is not None
        quality = manifest["framing_quality"]
        assert isinstance(quality, dict)

        if framing_mode == "min" and quality.get("below_threshold") is True:
            initial_linear = run_dir / "initial-min-linear.fit"
            initial_preview = run_dir / "initial-min-preview.jpg"
            if any(
                path.exists() or path.is_symlink()
                for path in (initial_linear, initial_preview)
            ):
                manifest["status"] = "failed"
                manifest["error"] = (
                    "Minimum-area stack was too narrowly cropped, but diagnostic "
                    "output names already exist; automatic framing fallback was not run."
                )
                manifest["work_files_retained"] = True
                write_json(manifest_path, manifest)
                return False
            try:
                linear.replace(initial_linear)
                preview.replace(initial_preview)
            except OSError as exc:
                manifest["status"] = "failed"
                manifest["error"] = (
                    "Minimum-area stack was too narrowly cropped, but its diagnostic "
                    f"outputs could not be preserved: {exc}"
                )
                manifest["work_files_retained"] = True
                write_json(manifest_path, manifest)
                return False

            reason = (
                "minimum-area framing retained "
                f"{float(quality['retained_area_ratio']):.1%} of the source area "
                f"({float(quality['retained_width_ratio']):.1%} width, "
                f"{float(quality['retained_height_ratio']):.1%} height)"
            )
            initial_hashes = {
                "linear_fits": sha256_file(initial_linear),
                "preview_jpeg": sha256_file(initial_preview),
            }
            manifest["attempts"] = [
                {
                    "attempt": 1,
                    "kind": "initial_stack",
                    "status": "rejected_by_framing_guard",
                    "started_at": manifest["created_at"],
                    "finished_at": manifest["finished_at"],
                    "framing_mode": "min",
                    "reason": reason,
                    "exit_code": exit_code,
                    "registered_count": registered_count,
                    "stacked_count": stacked_count,
                    "settings": dict(settings),
                    "framing_quality": quality,
                    "pipeline_script": str(script_path),
                    "siril_log": str(log_path),
                    "outputs": {
                        "linear_fits": str(initial_linear),
                        "preview_jpeg": str(initial_preview),
                    },
                    "output_sha256": initial_hashes,
                }
            ]
            manifest["status"] = "failed"
            manifest["error"] = (
                f"{reason}; automatically retrying the retained registration with "
                "center-of-gravity framing."
            )
            manifest["framing_fallback"] = {
                "triggered": True,
                "from": "min",
                "to": "cog",
                "reason": reason,
            }
            manifest["work_files_retained"] = True
            write_json(manifest_path, manifest)
            print(f"Framing guard: {reason}; retrying with framing=cog.")
            try:
                retry = load_framing_retry(run_dir, "cog")
            except ValueError as exc:
                manifest["error"] = f"Automatic framing fallback could not start: {exc}"
                write_json(manifest_path, manifest)
                return False
            return run_framing_retry(
                retry,
                siril,
                version,
                framing_mode="cog",
                keep_work=keep_work,
            )

        actual_count = stacked_count
        final_stem = output_stem(group, actual_count)
        final_linear = run_dir / f"{final_stem}.fit"
        final_preview = run_dir / f"{final_stem}.jpg"
        linear.replace(final_linear)
        preview.replace(final_preview)
        manifest["status"] = "success"
        manifest["outputs"] = {
            "linear_fits": str(final_linear),
            "preview_jpeg": str(final_preview),
            "siril_log": str(log_path),
            "pipeline_script": str(script_path),
        }
        manifest["output_sha256"] = {
            "linear_fits": sha256_file(final_linear),
            "preview_jpeg": sha256_file(final_preview),
        }
        if not keep_work:
            remove_owned_work_directory(work_dir)
        manifest["work_files_retained"] = keep_work
        print(f"Linear stack: {final_linear}")
        print(f"Preview: {final_preview}")
    else:
        manifest["status"] = "failed"
        if framing_inspection_error is not None:
            manifest["error"] = framing_inspection_error
        elif not counts_are_sane:
            manifest["error"] = (
                "Siril reported invalid frame counts: "
                f"stacked={stacked_count!r}, registered={registered_count!r}, "
                f"inputs={len(group.frames)}, minimum={minimum_stacked_frames}."
            )
        else:
            manifest["error"] = "Siril did not produce both a valid linear FITS and JPEG preview."
        manifest["work_files_retained"] = True
        print(f"Stacking failed; diagnostic files were preserved in {run_dir}", file=sys.stderr)
    write_json(manifest_path, manifest)
    return success


def _retry_error(
    retry: FramingRetry,
    attempt: dict[str, object],
    message: str,
) -> bool:
    attempt["status"] = "failed"
    attempt["finished_at"] = utc_now()
    attempt["error"] = message
    retry.manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    retry.manifest["status"] = "failed"
    retry.manifest["last_retry_at"] = attempt["finished_at"]
    retry.manifest["last_retry_error"] = message
    retry.manifest["work_files_retained"] = True
    write_json(retry.manifest_path, retry.manifest)
    print(f"Framing retry failed for {retry.run_dir}: {message}", file=sys.stderr)
    return False


def run_framing_retry(
    retry: FramingRetry,
    siril: Path,
    version: str,
    *,
    framing_mode: str,
    keep_work: bool,
) -> bool:
    """Retry only registered-frame application, stacking, and preview."""

    output_prefix, script_path, log_path, temporary_linear, temporary_preview = (
        retry_artifact_paths(retry.run_dir, framing_mode)
    )
    script_path.write_text(
        render_retry_framing_script(
            retry.run_dir,
            output_prefix=output_prefix,
            quality_mode=retry.quality_mode,
            brightness=retry.brightness,
            shadow_sigma=retry.shadow_sigma,
            jpeg_quality=retry.jpeg_quality,
            framing_mode=framing_mode,
            preview_rotation=retry.preview_rotation,
            preview_flip=retry.preview_flip,
        ),
        encoding="utf-8",
    )

    attempts_value = retry.manifest.setdefault("attempts", [])
    assert isinstance(attempts_value, list)
    attempt: dict[str, object] = {
        "attempt": len(attempts_value) + 1,
        "kind": "framing_retry",
        "status": "running",
        "started_at": utc_now(),
        "framing_mode": framing_mode,
        "replaces_framing_mode": retry.replaces_framing_mode,
        "siril": {"executable": str(siril), "version": version},
        "pipeline_script": str(script_path),
        "siril_log": str(log_path),
        "reused_sequence": str(retry.sequence_path),
        "reused_sequence_sha256": sha256_file(retry.sequence_path),
        "reused_converted_fits_count": len(retry.converted_frames),
        "reused_converted_fits_bytes": sum(path.stat().st_size for path in retry.converted_frames),
        "minimum_stacked_frames": retry.minimum_stacked_frames,
    }
    attempts_value.append(attempt)
    retry.manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    retry.manifest["last_retry_at"] = attempt["started_at"]
    write_json(retry.manifest_path, retry.manifest)

    print(f"\nRetrying framing={framing_mode}: {retry.run_dir}")
    try:
        exit_code = run_siril(siril, script_path, retry.run_dir, log_path)
    except OSError as exc:
        attempt["exit_code"] = None
        return _retry_error(retry, attempt, f"Could not launch Siril: {exc}")

    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        attempt["exit_code"] = exit_code
        return _retry_error(retry, attempt, f"Could not read retry log: {exc}")
    registered_count = parse_last_count(log_text, REGISTERED_COUNT_PATTERNS)
    registered_count_source = "retry_log"
    if registered_count is None:
        prior_registered = retry.manifest.get("registered_count")
        if (
            isinstance(prior_registered, int)
            and not isinstance(prior_registered, bool)
            and 1 <= prior_registered <= len(retry.converted_frames)
            and prior_registered <= retry.input_count
        ):
            registered_count = prior_registered
            registered_count_source = "prior_manifest"
        else:
            # seqapplyreg reuses the registration records already stored in
            # light_.seq and does not necessarily repeat a registration-count
            # line. The validated sequence/conversion count is the safe upper
            # bound when an older manifest did not retain that count.
            registered_count = len(retry.converted_frames)
            registered_count_source = "validated_sequence"
    stacked_count = parse_last_count(log_text, STACKED_COUNT_PATTERNS)
    attempt["exit_code"] = exit_code
    attempt["registered_count"] = registered_count
    attempt["registered_count_source"] = registered_count_source
    attempt["stacked_count"] = stacked_count
    counts_are_sane = valid_stack_counts(
        retry.input_count,
        registered_count,
        stacked_count,
        retry.minimum_stacked_frames,
    )
    if exit_code != 0:
        return _retry_error(retry, attempt, f"Siril exited with status {exit_code}; see {log_path}")
    if "Script execution finished successfully" not in log_text:
        return _retry_error(retry, attempt, f"Siril did not report successful completion; see {log_path}")
    if not counts_are_sane:
        return _retry_error(
            retry,
            attempt,
            "Siril reported invalid frame counts: "
            f"stacked={stacked_count!r}, registered={registered_count!r}, "
            f"inputs={retry.input_count}, minimum={retry.minimum_stacked_frames}.",
        )
    if not temporary_linear.is_file() or temporary_linear.stat().st_size == 0:
        return _retry_error(retry, attempt, f"Siril did not create a non-empty linear FITS: {temporary_linear}")
    if not temporary_preview.is_file() or temporary_preview.stat().st_size == 0:
        return _retry_error(retry, attempt, f"Siril did not create a non-empty JPEG preview: {temporary_preview}")

    try:
        source_dimensions, dimension_reference = _retry_source_dimensions(retry)
        quality = framing_quality(
            source_dimensions,
            fits_image_dimensions(temporary_linear),
        )
    except ValueError as exc:
        return _retry_error(retry, attempt, f"Could not verify retry stack framing: {exc}")
    quality["dimension_reference"] = dimension_reference
    quality["framing_mode"] = framing_mode
    attempt["framing_quality"] = quality
    if retry.replaces_framing_mode == "min" and quality["below_threshold"]:
        return _retry_error(
            retry,
            attempt,
            "Retry still failed the full-field framing guard: "
            f"retained width={quality['retained_width_ratio']}, "
            f"height={quality['retained_height_ratio']}, "
            f"area={quality['retained_area_ratio']}.",
        )

    assert stacked_count is not None
    try:
        final_stem = retry_output_stem(retry.manifest, stacked_count)
    except ValueError as exc:
        return _retry_error(retry, attempt, str(exc))
    final_linear = retry.run_dir / f"{final_stem}.fit"
    final_preview = retry.run_dir / f"{final_stem}.jpg"
    if not output_is_confined(final_linear, retry.run_dir) or not output_is_confined(
        final_preview, retry.run_dir
    ):
        return _retry_error(retry, attempt, "derived output path escapes the run folder")
    if any(path.exists() or path.is_symlink() for path in (final_linear, final_preview)):
        return _retry_error(
            retry,
            attempt,
            f"refusing to replace an existing final output for {final_stem}",
        )

    temporary_linear.replace(final_linear)
    temporary_preview.replace(final_preview)
    hashes = {
        "linear_fits": sha256_file(final_linear),
        "preview_jpeg": sha256_file(final_preview),
    }
    finished_at = utc_now()
    attempt.update(
        {
            "status": "success",
            "finished_at": finished_at,
            "outputs": {
                "linear_fits": str(final_linear),
                "preview_jpeg": str(final_preview),
            },
            "output_sha256": hashes,
        }
    )
    retry.manifest.update(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "status": "success",
            "finished_at": finished_at,
            "exit_code": exit_code,
            "registered_count": registered_count,
            "stacked_count": stacked_count,
            "settings": retry.normalized_settings,
            "siril": {"executable": str(siril), "version": version},
            "framing_quality": quality,
            "outputs": {
                "linear_fits": str(final_linear),
                "preview_jpeg": str(final_preview),
                "siril_log": str(log_path),
                "pipeline_script": str(script_path),
                "original_siril_log": str(retry.run_dir / "siril.log"),
                "original_pipeline_script": str(retry.run_dir / "pipeline.ssf"),
            },
            "output_sha256": hashes,
            "work_files_retained": True,
        }
    )
    retry.manifest.pop("last_retry_error", None)
    retry.manifest.pop("error", None)
    write_json(retry.manifest_path, retry.manifest)

    if not keep_work:
        try:
            remove_owned_work_directory(retry.run_dir / "_work")
        except (OSError, RuntimeError) as exc:
            retry.manifest["cleanup_error"] = str(exc)
            write_json(retry.manifest_path, retry.manifest)
            print(
                f"Stack succeeded but safe work cleanup failed for {retry.run_dir}: {exc}",
                file=sys.stderr,
            )
            return False
        retry.manifest["work_files_retained"] = False
        retry.manifest.pop("cleanup_error", None)
        write_json(retry.manifest_path, retry.manifest)

    print(f"Linear stack: {final_linear}")
    print(f"Preview: {final_preview}")
    return True


def command_retry_framing(args: argparse.Namespace) -> int:
    resolved_runs: list[Path] = []
    seen: set[Path] = set()
    for raw_run in args.run:
        run_dir = raw_run.expanduser().resolve()
        if run_dir in seen:
            print(f"Error: duplicate --run path: {run_dir}", file=sys.stderr)
            return 2
        seen.add(run_dir)
        resolved_runs.append(run_dir)

    retries: list[FramingRetry] = []
    try:
        for run_dir in resolved_runs:
            retries.append(load_framing_retry(run_dir, args.framing))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        disk_ok = True
        for retry in retries:
            okay, free = disk_preflight(retry.run_dir, retry.required_bytes)
            disk_ok = disk_ok and okay
            print(
                f"Would reuse {len(retry.converted_frames)} converted FITS in {retry.run_dir} "
                f"with framing={args.framing}; about {human_size(retry.required_bytes)} "
                f"working space ({human_size(free)} free)."
            )
            if not okay:
                print(
                    f"Error: insufficient free space for retry in {retry.run_dir}",
                    file=sys.stderr,
                )
        if disk_ok:
            print("Dry run: no manifest, scripts, logs, outputs, or work files were changed.")
            return 0
        return 2

    try:
        siril = find_siril(args.siril)
        version = siril_version(siril)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Using {version}: {siril}")

    results: list[bool] = []
    for planned in retries:
        # Revalidate immediately before mutation in case the run changed while
        # another explicit retry was executing.
        try:
            retry = load_framing_retry(planned.run_dir, args.framing)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            results.append(False)
            continue
        okay, free = disk_preflight(retry.run_dir, retry.required_bytes)
        if not okay:
            print(
                f"Error: only {human_size(free)} is free for {retry.run_dir}; "
                f"about {human_size(retry.required_bytes)} is required.",
                file=sys.stderr,
            )
            results.append(False)
            continue
        results.append(
            run_framing_retry(
                retry,
                siril,
                version,
                framing_mode=args.framing,
                keep_work=args.keep_work,
            )
        )
    return 0 if all(results) else 1


def run_preview_refresh(
    refresh: PreviewRefresh,
    siril: Path,
    version: str,
) -> bool:
    """Render and atomically select one new JPEG without touching the FITS."""

    script_path, log_path, pending_preview, final_preview = (
        preview_refresh_artifact_paths(refresh)
    )
    started_at = utc_now()
    try:
        script_path.write_text(
            render_preview_script(
                refresh.linear_fits,
                pending_preview,
                brightness=refresh.brightness,
                shadow_sigma=refresh.shadow_sigma,
                jpeg_quality=refresh.jpeg_quality,
                linked=False,
                rotation=refresh.rotation,
                flip=refresh.flip,
            ),
            encoding="utf-8",
        )
        exit_code = run_siril(siril, script_path, refresh.run_dir, log_path)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Preview refresh failed for {refresh.run_dir}: {exc}", file=sys.stderr)
        return False
    if (
        exit_code != 0
        or "Script execution finished successfully" not in log_text
        or not pending_preview.is_file()
        or pending_preview.is_symlink()
        or pending_preview.stat().st_size == 0
    ):
        print(f"Preview refresh failed for {refresh.run_dir}; see {log_path}", file=sys.stderr)
        return False
    try:
        pending_preview = _confined_regular_file(
            pending_preview, refresh.run_dir, "refreshed JPEG preview"
        )
        if sha256_file(refresh.linear_fits) != refresh.linear_sha256:
            raise RuntimeError("linear FITS changed while Siril was rendering; leaving manifest unchanged")
        if sha256_file(refresh.manifest_path) != refresh.manifest_sha256:
            raise RuntimeError("manifest changed while Siril was rendering; leaving it unchanged")
        if final_preview.exists() or final_preview.is_symlink():
            raise RuntimeError(f"refusing to replace an unexpected file: {final_preview}")
        pending_preview.replace(final_preview)
        new_preview_hash = sha256_file(final_preview)
        if sha256_file(refresh.manifest_path) != refresh.manifest_sha256:
            raise RuntimeError("manifest changed before preview selection; leaving it unchanged")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Preview refresh failed for {refresh.run_dir}: {exc}", file=sys.stderr)
        return False

    updated = copy.deepcopy(refresh.manifest)
    settings = dict(refresh.settings)
    settings.update(
        {
            "preview_style": refresh.style,
            "preview_brightness": refresh.brightness,
            "preview_shadow_sigma": refresh.shadow_sigma,
            "preview_rotation_degrees": refresh.rotation,
            "preview_flip": refresh.flip,
            "jpeg_quality": refresh.jpeg_quality,
        }
    )
    outputs = dict(updated["outputs"])
    prior_hashes = updated.get("output_sha256")
    hashes = dict(prior_hashes) if isinstance(prior_hashes, dict) else {}
    prior_preview_value = outputs["preview_jpeg"]
    outputs["preview_jpeg"] = str(final_preview)
    hashes["linear_fits"] = refresh.linear_sha256
    hashes["preview_jpeg"] = new_preview_hash
    finished_at = utc_now()
    refreshes = list(updated.get("preview_refreshes", []))
    refreshes.append(
        {
            "refresh": len(refreshes) + 1,
            "kind": "preview_refresh",
            "status": "success",
            "started_at": started_at,
            "finished_at": finished_at,
            "manifest_schema_version_before": refresh.schema_version,
            "framing_mode": refresh.framing_mode,
            "siril": {"executable": str(siril), "version": version},
            "settings": {
                "preview_style": refresh.style,
                "preview_brightness": refresh.brightness,
                "preview_shadow_sigma": refresh.shadow_sigma,
                "preview_rotation_degrees": refresh.rotation,
                "preview_flip": refresh.flip,
                "jpeg_quality": refresh.jpeg_quality,
                "linked": False,
            },
            "source": {
                "linear_fits": updated["outputs"]["linear_fits"],
                "linear_fits_sha256": refresh.linear_sha256,
            },
            "replaces": {
                "preview_jpeg": prior_preview_value,
                "preview_jpeg_sha256": refresh.preview_sha256,
            },
            "outputs": {"preview_jpeg": str(final_preview)},
            "output_sha256": {"preview_jpeg": new_preview_hash},
            "pipeline_script": str(script_path),
            "siril_log": str(log_path),
        }
    )
    updated["settings"] = settings
    updated["outputs"] = outputs
    updated["output_sha256"] = hashes
    updated["preview_refreshes"] = refreshes
    updated["preview_refreshed_at"] = finished_at
    updated["schema_version"] = MANIFEST_SCHEMA_VERSION
    try:
        # Re-check both durable inputs at the commit boundary so a concurrent
        # stack/refresh cannot be silently overwritten with stale provenance.
        if sha256_file(refresh.linear_fits) != refresh.linear_sha256:
            raise RuntimeError("linear FITS changed before manifest commit; leaving manifest unchanged")
        if sha256_file(final_preview) != new_preview_hash:
            raise RuntimeError("refreshed JPEG changed before manifest commit; leaving manifest unchanged")
        if sha256_file(refresh.manifest_path) != refresh.manifest_sha256:
            raise RuntimeError("manifest changed before commit; leaving it unchanged")
        write_json(refresh.manifest_path, updated)
    except (OSError, RuntimeError) as exc:
        print(
            f"Preview rendered but manifest selection failed for {refresh.run_dir}: {exc}",
            file=sys.stderr,
        )
        return False
    print(f"Refreshed preview: {final_preview}")
    return True


def command_refresh_previews(args: argparse.Namespace) -> int:
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"Error: refresh root is not a directory: {root}", file=sys.stderr)
        return 2
    try:
        brightness, shadow_sigma = resolve_preview_settings(
            args.style, args.brightness, args.shadow_sigma
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    plans: list[PreviewRefresh] = []
    invalid_manifests: list[Path] = []
    for manifest_path in sorted(root.rglob(MANIFEST_NAME), key=lambda path: str(path).casefold()):
        claims_owned_success = False
        indeterminate_manifest = False
        try:
            basic_path = _confined_regular_file(manifest_path, root, "manifest")
            basic = json.loads(basic_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            basic = None
            indeterminate_manifest = True
        if isinstance(basic, dict):
            if basic.get("tool") != TOOL_NAME:
                # Foreign manifests are outside this tool's ownership.
                continue
            if basic.get("status") in {"failed", "running"}:
                # Failed/in-progress runs are expected to remain untouched by
                # a preview-only refresh.
                continue
            if basic.get("status") == "success":
                claims_owned_success = True
                stacked_count = basic.get("stacked_count")
                if (
                    isinstance(stacked_count, int)
                    and not isinstance(stacked_count, bool)
                    and stacked_count < args.min_frames
                ):
                    print(
                        f"Skipping {manifest_path}: {stacked_count} stacked frames is below "
                        f"--min-frames {args.min_frames}."
                    )
                    continue
            else:
                indeterminate_manifest = True
        else:
            indeterminate_manifest = True
        try:
            plans.append(
                load_preview_refresh(
                    manifest_path,
                    root,
                    style=args.style,
                    brightness=brightness,
                    shadow_sigma=shadow_sigma,
                    jpeg_quality=args.jpeg_quality,
                )
            )
        except ValueError as exc:
            print(f"Warning: skipping {manifest_path}: {exc}", file=sys.stderr)
            if claims_owned_success or indeterminate_manifest:
                invalid_manifests.append(manifest_path)
    if not plans:
        print("No valid successful schema-1 or schema-2 stack manifests were found.", file=sys.stderr)
        return 1 if invalid_manifests else 2

    print(
        f"Found {len(plans)} preview(s) to refresh with style={args.style}, "
        f"brightness={brightness:.3f}, shadow sigma={shadow_sigma:.2f}."
    )
    for plan in plans:
        print(f"  {plan.preview_jpeg} from {plan.linear_fits}")
    if invalid_manifests:
        print(
            f"Error: {len(invalid_manifests)} manifest(s) failed validation; "
            "the refresh will be incomplete.",
            file=sys.stderr,
        )
    if args.dry_run:
        print("Dry run: no scripts, logs, previews, FITS files, or manifests were changed.")
        return 1 if invalid_manifests else 0

    try:
        siril = find_siril(args.siril)
        version = siril_version(siril)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Using {version}: {siril}")

    results: list[bool] = []
    for planned in plans:
        try:
            refresh = load_preview_refresh(
                planned.manifest_path,
                root,
                style=args.style,
                brightness=brightness,
                shadow_sigma=shadow_sigma,
                jpeg_quality=args.jpeg_quality,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            results.append(False)
            continue
        results.append(run_preview_refresh(refresh, siril, version))
    return 0 if all(results) and not invalid_manifests else 1


def validate_source_destination(source: Path, destination: Path) -> str | None:
    if not source.is_dir():
        return f"source is not a directory: {source}"
    if destination == source or source in destination.parents or destination in source.parents:
        return "destination must be separate from the source capture tree"
    return None


def command_stack(args: argparse.Namespace) -> int:
    source = args.source.expanduser().resolve()
    destination = args.destination.expanduser().resolve()
    validation_error = validate_source_destination(source, destination)
    if validation_error:
        print(f"Error: {validation_error}", file=sys.stderr)
        return 2

    try:
        brightness, shadow_sigma = resolve_preview_settings(
            args.preview_style, args.preview_brightness, args.preview_shadow_sigma
        )
        siril = find_siril(args.siril)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    folders, warnings = discover_target_folders(source, args.target, args.all)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if not folders:
        print("No eligible target folders selected.", file=sys.stderr)
        return 2

    groups: list[CaptureGroup] = []
    for folder in folders:
        found, group_warnings = collect_capture_groups(folder, source)
        for warning in group_warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        if not found:
            print(f"Warning: no top-level Seestar Light_*.fit[s] files in {folder}", file=sys.stderr)
        groups.extend(found)

    if args.exposure is not None:
        groups = [group for group in groups if float(group.exposure) == args.exposure]
    if args.filter_name is not None:
        groups = [
            group for group in groups if group.filter_name.casefold() == args.filter_name.casefold()
        ]

    eligible = [
        group
        for group in groups
        if len(group.frames) >= args.min_frames
        and (args.include_unknown or group.object_name.casefold() != "unknown")
    ]
    for group in groups:
        if len(group.frames) < args.min_frames:
            print(
                f"Skipping {group.target_name} ({group.object_name}, {group.filter_name}): "
                f"{len(group.frames)} frames is below --min-frames {args.min_frames}."
            )
        elif group.object_name.casefold() == "unknown" and not args.include_unknown:
            print(
                f"Skipping {group.target_name}: unidentified captures are not combined "
                "automatically; use --include-unknown only after inspecting the sessions."
            )
    if not eligible:
        print("No capture groups have enough frames to stack.", file=sys.stderr)
        return 2

    settings = stack_settings(
        quality_mode=args.quality,
        preview_style=args.preview_style,
        brightness=brightness,
        shadow_sigma=shadow_sigma,
        jpeg_quality=args.jpeg_quality,
        preview_rotation=args.preview_rotate,
        preview_flip=args.preview_flip,
        framing_mode=args.framing,
        minimum_stacked_frames=args.min_frames,
    )
    pending: list[CaptureGroup] = []
    completed: list[tuple[CaptureGroup, Path]] = []
    retained_failures: list[tuple[CaptureGroup, tuple[Path, ...]]] = []
    for group in eligible:
        fingerprint = inventory_fingerprint(group)
        existing = None if args.force else find_completed_run(
            group, destination, fingerprint, settings
        )
        if existing:
            completed.append((group, existing))
        else:
            pending.append(group)
            failed_runs = find_retained_failed_runs(group, destination, fingerprint)
            if failed_runs:
                retained_failures.append((group, failed_runs))

    allow_fresh_after_failure = args.force or args.fresh_after_failure
    if retained_failures and not allow_fresh_after_failure:
        print(
            "Error: refusing to create a fresh multi-GB work cache while matching failed "
            "work is retained.",
            file=sys.stderr,
        )
        for group, failed_runs in retained_failures:
            print(f"  {group.target_name}:", file=sys.stderr)
            for run_dir in failed_runs:
                print(f"    Retained run: {run_dir}", file=sys.stderr)
                try:
                    load_framing_retry(run_dir, "cog")
                except ValueError as exc:
                    print(
                        f"    Framing-only retry is not currently safe: {exc}",
                        file=sys.stderr,
                    )
                else:
                    print("    Validate and reuse it with:", file=sys.stderr)
                    print(f"      {retry_framing_command(run_dir)}", file=sys.stderr)
        print(
            "Use --fresh-after-failure on the same stack command only when you "
            "intentionally want another work cache; the retained cache is not deleted.",
            file=sys.stderr,
        )
        return 2
    if retained_failures:
        print(
            "Warning: starting fresh while matching failed work remains on disk; "
            "additional working space will be used.",
            file=sys.stderr,
        )

    version = siril_version(siril)
    print(f"Using {version}: {siril}")
    print(
        f"Found {len(eligible)} eligible capture group(s): "
        f"{len(completed)} already complete, {len(pending)} pending."
    )
    for group, manifest_path in completed:
        print(f"  Complete {group.target_name}: {len(group.frames)} frames ({manifest_path})")
    disk_ok = True
    for group in pending:
        required = int(group.total_bytes * DEFAULT_DISK_MULTIPLIER)
        okay, free = disk_preflight(destination, required)
        disk_ok = disk_ok and okay
        print(
            f"  {group.target_name}: {len(group.frames)} frames, "
            f"{human_size(group.total_bytes)} source, about {human_size(required)} working space"
        )
        if not okay:
            print(
                f"Error: only {human_size(free)} is free for {group.target_name}; "
                f"about {human_size(required)} is required.",
                file=sys.stderr,
            )
    if not disk_ok:
        return 2
    if args.dry_run:
        print("Dry run: no folders were created and Siril was not launched.")
        return 0

    if not pending:
        print("All eligible capture groups already have matching successful runs.")
        return 0

    results: list[bool] = []
    for group in pending:
        required = int(group.total_bytes * DEFAULT_DISK_MULTIPLIER)
        okay, free = disk_preflight(destination, required)
        if not okay:
            print(
                f"Error: stopping before {group.target_name}; only {human_size(free)} is free "
                f"and about {human_size(required)} is required.",
                file=sys.stderr,
            )
            results.append(False)
            break
        success = run_group(
            group,
            destination,
            siril,
            version,
            quality_mode=args.quality,
            preview_style=args.preview_style,
            brightness=brightness,
            shadow_sigma=shadow_sigma,
            jpeg_quality=args.jpeg_quality,
            preview_rotation=args.preview_rotate,
            preview_flip=args.preview_flip,
            framing_mode=args.framing,
            keep_work=args.keep_work,
            minimum_stacked_frames=args.min_frames,
        )
        results.append(success)
        if not success:
            print(
                f"Error: stopping the batch after {group.target_name} failed; "
                "its diagnostic work was retained.",
                file=sys.stderr,
            )
            break
    return 0 if all(results) else 1


def command_preview(args: argparse.Namespace) -> int:
    input_fits = args.input.expanduser().resolve()
    output_jpeg = args.output.expanduser().resolve()
    if not input_fits.is_file():
        print(f"Error: input FITS does not exist: {input_fits}", file=sys.stderr)
        return 2
    if output_jpeg.suffix.casefold() not in {".jpg", ".jpeg"}:
        print("Error: preview output must end in .jpg or .jpeg", file=sys.stderr)
        return 2
    # Siril's savejpg command consistently writes the .jpg extension.
    if output_jpeg.suffix.casefold() == ".jpeg":
        output_jpeg = output_jpeg.with_suffix(".jpg")
    if output_jpeg.exists() and not args.force:
        print(f"Error: output already exists (use --force to replace it): {output_jpeg}", file=sys.stderr)
        return 2
    try:
        brightness, shadow_sigma = resolve_preview_settings(
            args.style, args.brightness, args.shadow_sigma
        )
        siril = find_siril(args.siril)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(
            f"Would render {input_fits} -> {output_jpeg} with "
            f"brightness {brightness:.3f}, shadow sigma {shadow_sigma:.2f}."
        )
        return 0

    output_jpeg.parent.mkdir(parents=True, exist_ok=True)
    script_path = output_jpeg.with_name(f"{output_jpeg.stem}.preview.ssf")
    log_path = output_jpeg.with_name(f"{output_jpeg.stem}.preview.log")
    script_path.write_text(
        render_preview_script(
            input_fits,
            output_jpeg,
            brightness=brightness,
            shadow_sigma=shadow_sigma,
            jpeg_quality=args.jpeg_quality,
            linked=args.linked,
            rotation=args.rotate,
            flip=args.flip,
        ),
        encoding="utf-8",
    )
    exit_code = run_siril(siril, script_path, output_jpeg.parent, log_path)
    if exit_code != 0 or not output_jpeg.is_file() or output_jpeg.stat().st_size == 0:
        print(f"Preview failed; see {log_path}", file=sys.stderr)
        return 1
    print(f"Preview written to: {output_jpeg}")
    return 0


def add_preview_options(parser: argparse.ArgumentParser, *, prefix: str = "") -> None:
    option_prefix = f"{prefix}-" if prefix else ""
    parser.add_argument(
        f"--{option_prefix}style",
        choices=sorted(PREVIEW_STYLES),
        default="natural",
        help="Preview look; natural is the brighter, balanced default for Seestar stacks.",
    )
    parser.add_argument(
        f"--{option_prefix}brightness",
        type=float,
        help="Target background from 0 to 1; lower values make the preview darker.",
    )
    parser.add_argument(
        f"--{option_prefix}shadow-sigma",
        type=float,
        help="Shadow clipping strength; a smaller value increases contrast (default 2.8 for natural).",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stack Seestar FITS safely with Siril CLI and create a natural JPEG preview."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    stack = subparsers.add_parser("stack", help="Register and stack one or more Seestar capture folders.")
    stack.add_argument("--source", required=True, type=Path, help="An astro folder or one *_sub folder.")
    stack.add_argument("--destination", required=True, type=Path, help="Separate folder for run outputs.")
    selection = stack.add_mutually_exclusive_group()
    selection.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target folder/name or wildcard to process; repeat for more than one.",
    )
    selection.add_argument("--all", action="store_true", help="Process every eligible *_sub folder sequentially.")
    stack.add_argument("--min-frames", type=int, default=DEFAULT_MIN_FRAMES)
    stack.add_argument(
        "--exposure",
        type=float,
        help="Process only one exposure duration in seconds (useful for bounded retries).",
    )
    stack.add_argument(
        "--filter",
        dest="filter_name",
        help="Process only one filter name, such as LP or IRCUT.",
    )
    stack.add_argument(
        "--include-unknown",
        action="store_true",
        help="Allow unidentified captures; inspect sessions first because they may be unrelated fields.",
    )
    stack.add_argument(
        "--quality",
        choices=("balanced", "keep-all", "sharpest-75"),
        default="balanced",
        help="Frame selection: balanced rejects unusually elongated stars.",
    )
    stack.add_argument(
        "--framing",
        choices=FRAMING_MODES,
        default="min",
        help=(
            "Registered-frame crop: min removes borders; cog is a fallback when "
            "mixed sessions have no common intersection."
        ),
    )
    add_preview_options(stack, prefix="preview")
    stack.add_argument(
        "--preview-rotate",
        type=int,
        choices=PREVIEW_ROTATIONS,
        default=0,
        help="Rotate only the JPEG preview; the linear FITS remains unchanged.",
    )
    stack.add_argument(
        "--preview-flip",
        choices=PREVIEW_FLIPS,
        default="none",
        help="Flip only the JPEG preview before rotation.",
    )
    stack.add_argument("--jpeg-quality", type=int, choices=range(1, 101), default=95)
    stack.add_argument("--siril", type=Path, help="Path to siril-cli if it is not auto-detected.")
    stack.add_argument("--keep-work", action="store_true", help="Keep large calibrated/registered intermediates.")
    stack.add_argument("--force", action="store_true", help="Restack even when a matching successful manifest exists.")
    stack.add_argument(
        "--fresh-after-failure",
        action="store_true",
        help=(
            "Start a new run even when matching marker-owned failed work is retained; "
            "this can consume substantial additional disk space."
        ),
    )
    stack.add_argument("--dry-run", action="store_true", help="Show selected groups and disk estimate only.")
    stack.set_defaults(func=command_stack)

    retry = subparsers.add_parser(
        "retry-framing",
        help="Reuse a failed run's converted FITS and registration data with safer framing.",
    )
    retry.add_argument(
        "--run",
        action="append",
        required=True,
        type=Path,
        help="Explicit failed run folder containing manifest.json; repeat for more than one.",
    )
    retry.add_argument(
        "--framing",
        choices=RETRY_FRAMING_MODES,
        default="cog",
        help="Retry crop mode; cog is the normal fallback when min has no common intersection.",
    )
    retry.add_argument("--siril", type=Path, help="Path to siril-cli if it is not auto-detected.")
    retry.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep marker-owned converted and registered intermediates after success.",
    )
    retry.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate runs and disk space without writing or launching Siril.",
    )
    retry.set_defaults(func=command_retry_framing)

    refresh = subparsers.add_parser(
        "refresh-previews",
        help="Re-render all verified stack previews from their preserved linear FITS.",
    )
    refresh.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Local stack root containing successful schema-1 or schema-2 manifests.",
    )
    add_preview_options(refresh)
    refresh.add_argument(
        "--min-frames",
        type=int,
        default=DEFAULT_MIN_FRAMES,
        help="Refresh only stacks retaining at least this many frames (default: 50).",
    )
    refresh.add_argument(
        "--jpeg-quality",
        type=int,
        choices=range(1, 101),
        help="Override each manifest's existing JPEG quality.",
    )
    refresh.add_argument("--siril", type=Path, help="Path to siril-cli if it is not auto-detected.")
    refresh.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and list previews without writing or launching Siril.",
    )
    refresh.set_defaults(func=command_refresh_previews)

    preview = subparsers.add_parser(
        "preview", help="Render a new JPEG from an existing linear FITS without restacking."
    )
    preview.add_argument("--input", required=True, type=Path, help="Existing linear .fit/.fits stack.")
    preview.add_argument("--output", required=True, type=Path, help="Destination .jpg path.")
    add_preview_options(preview)
    preview.add_argument(
        "--rotate",
        type=int,
        choices=PREVIEW_ROTATIONS,
        default=0,
        help="Rotate the derived JPEG; the input FITS remains unchanged.",
    )
    preview.add_argument(
        "--flip",
        choices=PREVIEW_FLIPS,
        default="none",
        help="Flip the derived JPEG before rotation.",
    )
    preview.add_argument("--jpeg-quality", type=int, choices=range(1, 101), default=95)
    preview.add_argument("--linked", action="store_true", help="Use a linked RGB stretch to preserve channel ratios.")
    preview.add_argument("--siril", type=Path, help="Path to siril-cli if it is not auto-detected.")
    preview.add_argument("--force", action="store_true", help="Replace an existing JPEG output.")
    preview.add_argument("--dry-run", action="store_true")
    preview.set_defaults(func=command_preview)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if getattr(args, "min_frames", DEFAULT_MIN_FRAMES) < 2:
        print("Error: --min-frames must be at least 2", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
