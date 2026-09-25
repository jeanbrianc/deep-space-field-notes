#!/usr/bin/env python3
"""Incrementally archive original Seestar light frames from removable storage.

Only immediate ``Light_*.fit`` and ``Light_*.fits`` children of recursively
discovered ``*_sub`` folders are eligible. The removable source is never
modified, destination paths preserve the source layout, and every completed
copy is checksummed before it is atomically promoted into the offline archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


TOOL_NAME = "seestar_fits_import"
MANIFEST_NAME = ".seestar_fits_import_manifest.json"
SCHEMA_VERSION = 2
COPY_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_RESERVE_GIB = 5.0
LIGHT_NAME = re.compile(r"^Light_.+\.fits?$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceFrame:
    path: Path
    relative_path: Path
    target_folder: str
    size: int
    modified_ns: int


@dataclass(frozen=True)
class PlannedFrame:
    frame: SourceFrame
    destination: Path
    action: str
    source_sha256: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def human_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            return f"{number:.1f} {unit}"
        number /= 1024.0
    return f"{number:.1f} TiB"


def existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_roots(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return f"source is not mounted or does not exist: {source}"
    if source.is_symlink():
        return f"source must be the real mounted directory, not a symlink: {source}"
    if not source.is_dir():
        return f"source is not a directory: {source}"
    if source == destination or is_relative_to(destination, source) or is_relative_to(source, destination):
        return "source and destination must be separate, non-overlapping trees"

    ancestor = existing_ancestor(destination)
    while ancestor != ancestor.parent:
        # macOS exposes canonical system roots through aliases such as
        # /var -> /private/var. Reject user-controlled symlinks below that
        # top-level compatibility alias.
        if ancestor.parent != Path(ancestor.anchor) and ancestor.is_symlink():
            return f"destination ancestor must not be a symlink: {ancestor}"
        ancestor = ancestor.parent
    return None


def validate_destination_path(destination_root: Path, destination: Path) -> None:
    """Reject lexical escapes and symlinked destination components."""

    root = destination_root.absolute()
    target = destination.absolute()
    if not is_relative_to(target, root):
        raise RuntimeError(f"destination escapes the archive root: {destination}")
    candidate = root
    if candidate.is_symlink():
        raise RuntimeError(f"destination path contains a symlink: {candidate}")
    for component in target.parent.relative_to(root).parts:
        candidate /= component
        if candidate.is_symlink():
            raise RuntimeError(f"destination path contains a symlink: {candidate}")


def discover_frames(source: Path) -> tuple[list[SourceFrame], list[str]]:
    """Find eligible top-level light frames while avoiding system metadata."""

    frames: list[SourceFrame] = []
    warnings: list[str] = []

    def onerror(error: OSError) -> None:
        warnings.append(f"Could not scan {error.filename or source}: {error}")

    for root_text, directory_names, file_names in os.walk(source, topdown=True, onerror=onerror, followlinks=False):
        root = Path(root_text)
        directory_names[:] = sorted(
            (
                name
                for name in directory_names
                if not name.startswith(".") and name != "System Volume Information"
            ),
            key=str.casefold,
        )
        if not root.name.casefold().endswith("_sub"):
            continue

        # Frames nested in proc/work directories are intentionally excluded.
        directory_names[:] = []
        for name in sorted(file_names, key=str.casefold):
            if not LIGHT_NAME.match(name):
                continue
            path = root / name
            try:
                if path.is_symlink() or not path.is_file():
                    warnings.append(f"Ignoring non-regular light frame: {path}")
                    continue
                stat = path.stat()
                relative = path.relative_to(source)
            except OSError as exc:
                warnings.append(f"Could not inspect {path}: {exc}")
                continue
            frames.append(
                SourceFrame(
                    path=path,
                    relative_path=relative,
                    target_folder=root.name,
                    size=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )

    frames.sort(key=lambda frame: str(frame.relative_path).casefold())
    return frames, warnings


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read existing manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"existing manifest is not a JSON object: {path}")
    return payload


def manifest_records(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    records = payload.get("files", [])
    if not isinstance(records, list):
        return {}
    return {
        str(record["relative_path"]): record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("relative_path"), str)
    }


def classify_frame(
    frame: SourceFrame,
    destination_root: Path,
    previous: dict[str, dict[str, object]],
    *,
    verify: bool,
) -> PlannedFrame:
    destination = destination_root / frame.relative_path
    try:
        validate_destination_path(destination_root, destination)
    except RuntimeError:
        # A symlinked archive component can make a lexically safe relative path
        # resolve outside the archive. Treat it as a conflict before reading or
        # trusting anything at that location.
        return PlannedFrame(frame, destination, "conflict")
    if not destination.exists():
        return PlannedFrame(frame, destination, "copy")
    if destination.is_symlink() or not destination.is_file():
        return PlannedFrame(frame, destination, "conflict")

    try:
        destination_stat = destination.stat()
        destination_size = destination_stat.st_size
    except OSError:
        return PlannedFrame(frame, destination, "conflict")
    if destination_size != frame.size:
        return PlannedFrame(frame, destination, "changed")

    prior = previous.get(str(frame.relative_path))
    trusted = bool(
        prior
        and prior.get("size") == frame.size
        and prior.get("modified_ns") == frame.modified_ns
        and isinstance(prior.get("sha256"), str)
        and prior.get("destination_size") == destination_size
        and prior.get("destination_modified_ns") == destination_stat.st_mtime_ns
    )
    if trusted and not verify:
        return PlannedFrame(frame, destination, "skip", str(prior["sha256"]))

    source_hash = sha256_file(frame.path)
    destination_hash = sha256_file(destination)
    action = "skip" if source_hash == destination_hash else "changed"
    return PlannedFrame(frame, destination, action, source_hash)


def plan_import(
    frames: Iterable[SourceFrame],
    destination: Path,
    previous: dict[str, dict[str, object]],
    *,
    verify: bool,
) -> list[PlannedFrame]:
    return [classify_frame(frame, destination, previous, verify=verify) for frame in frames]


def copy_verified(
    frame: SourceFrame, destination: Path, *, destination_root: Path | None = None
) -> str:
    """Copy one stable source file and atomically promote it after verification."""

    archive_root = destination.parent if destination_root is None else destination_root
    validate_destination_path(archive_root, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validate_destination_path(archive_root, destination)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    digest = hashlib.sha256()
    before = frame.path.stat()
    try:
        with frame.path.open("rb") as source_handle, temporary.open("xb") as target_handle:
            for chunk in iter(lambda: source_handle.read(COPY_CHUNK_BYTES), b""):
                digest.update(chunk)
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())

        after = frame.path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"source changed while it was being copied: {frame.path}")
        if after.st_size != frame.size or after.st_mtime_ns != frame.modified_ns:
            raise RuntimeError(f"source changed after inventory: {frame.path}")
        if temporary.stat().st_size != frame.size:
            raise RuntimeError(f"incomplete copy for {frame.path}")
        source_hash = digest.hexdigest()
        destination_hash = sha256_file(temporary)
        if destination_hash != source_hash:
            raise RuntimeError(f"temporary copy checksum mismatch for {frame.path}")
        shutil.copystat(frame.path, temporary, follow_symlinks=False)
        validate_destination_path(archive_root, destination)
        temporary.replace(destination)
        return source_hash
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_manifest(
    source: Path,
    destination: Path,
    previous: dict[str, dict[str, object]],
    results: list[dict[str, object]],
    summary: dict[str, object],
) -> dict[str, object]:
    current = {str(record["relative_path"]): record for record in results}
    for relative_path, record in previous.items():
        if relative_path not in current:
            retained = dict(record)
            retained["source_present_last_scan"] = False
            retained["last_action"] = "retained-offline"
            current[relative_path] = retained
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "source_root": str(source),
        "destination_root": str(destination),
        "last_scan_at": utc_now(),
        "summary": summary,
        "files": [current[key] for key in sorted(current, key=str.casefold)],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely copy original Seestar Light_*.fit[s] files into an offline archive."
    )
    parser.add_argument("--source", required=True, type=Path, help="Mounted Seestar or YOTUO root to scan.")
    parser.add_argument("--destination", required=True, type=Path, help="Local offline FITS archive root.")
    parser.add_argument("--dry-run", action="store_true", help="Inventory and plan without writing anything.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Re-hash existing destination files even when their recorded metadata is unchanged.",
    )
    parser.add_argument(
        "--replace-changed",
        action="store_true",
        help="Atomically replace existing destination files whose contents differ from the source.",
    )
    parser.add_argument(
        "--reserve-gib",
        type=float,
        default=DEFAULT_RESERVE_GIB,
        help="Free-space reserve to leave after planned copies (default: 5 GiB).",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every copied file instead of periodic progress.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_input = args.source.expanduser().absolute()
    destination_input = args.destination.expanduser().absolute()
    source = source_input.resolve()
    if args.reserve_gib < 0:
        print("Error: --reserve-gib cannot be negative", file=sys.stderr)
        return 2
    validation_error = validate_roots(source_input, destination_input)
    if validation_error:
        print(f"Error: {validation_error}", file=sys.stderr)
        return 2
    destination = destination_input.resolve()
    resolved_validation_error = validate_roots(source, destination)
    if resolved_validation_error:
        print(f"Error: {resolved_validation_error}", file=sys.stderr)
        return 2

    frames, warnings = discover_frames(source)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if not frames:
        print(f"Error: no top-level Light_*.fit[s] files found in *_sub folders under {source}", file=sys.stderr)
        return 2

    manifest_path = destination / MANIFEST_NAME
    try:
        previous_payload = read_manifest(manifest_path)
        previous = manifest_records(previous_payload)
        trusted_previous = previous if (
            previous_payload.get("tool") == TOOL_NAME
            and previous_payload.get("schema_version") == SCHEMA_VERSION
            and previous_payload.get("source_root") == str(source)
            and previous_payload.get("destination_root") == str(destination)
        ) else {}
        planned = plan_import(frames, destination, trusted_previous, verify=args.verify)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    counts = {action: sum(item.action == action for item in planned) for action in ("copy", "skip", "changed", "conflict")}
    planned_writes = [item for item in planned if item.action == "copy" or (item.action == "changed" and args.replace_changed)]
    planned_bytes = sum(item.frame.size for item in planned_writes)
    reserve_bytes = int(args.reserve_gib * 1024**3)
    free = shutil.disk_usage(existing_ancestor(destination)).free

    print(
        f"Found {len(frames):,} light frames in "
        f"{len({frame.target_folder for frame in frames})} *_sub folders ({human_size(sum(frame.size for frame in frames))})."
    )
    print(
        f"Plan: {counts['copy']:,} new, {counts['skip']:,} already archived, "
        f"{counts['changed']:,} changed, {counts['conflict']:,} conflicts."
    )
    print(f"Copy bytes: {human_size(planned_bytes)}; free locally: {human_size(free)}.")

    unresolved = counts["conflict"] + (0 if args.replace_changed else counts["changed"])
    if free < planned_bytes + reserve_bytes:
        print(
            f"Error: import needs {human_size(planned_bytes)} plus the "
            f"{human_size(reserve_bytes)} reserve, but only {human_size(free)} is free.",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        if counts["changed"] and not args.replace_changed:
            print("Changed destination files would be left untouched; add --replace-changed to refresh them.")
        print("Dry run: no directories, files, or manifest were written.")
        return 0 if not warnings and unresolved == 0 else 1

    results: list[dict[str, object]] = []
    failures = 0
    copied_bytes = 0
    for index, item in enumerate(planned, start=1):
        frame = item.frame
        action = item.action
        record: dict[str, object] = {
            "relative_path": str(frame.relative_path),
            "target_folder": frame.target_folder,
            "size": frame.size,
            "modified_ns": frame.modified_ns,
            "source_present_last_scan": True,
            "last_action": action,
        }
        if action == "skip":
            record["sha256"] = item.source_sha256 or previous[str(frame.relative_path)]["sha256"]
            destination_stat = item.destination.stat()
            record["destination_size"] = destination_stat.st_size
            record["destination_modified_ns"] = destination_stat.st_mtime_ns
        elif action == "conflict" or (action == "changed" and not args.replace_changed):
            failures += 1
            record["last_action"] = "conflict"
            record["error"] = "destination differs; left untouched"
            print(f"Conflict: {frame.relative_path}", file=sys.stderr)
        else:
            verb = "Refreshing" if action == "changed" else "Copying"
            if args.verbose or index == 1 or index % 100 == 0 or index == len(planned):
                print(f"[{index:,}/{len(planned):,}] {verb} {frame.relative_path}")
            try:
                record["sha256"] = copy_verified(
                    frame, item.destination, destination_root=destination
                )
                destination_stat = item.destination.stat()
                record["destination_size"] = destination_stat.st_size
                record["destination_modified_ns"] = destination_stat.st_mtime_ns
                record["last_action"] = "refreshed" if action == "changed" else "copied"
                record["imported_at"] = utc_now()
                copied_bytes += frame.size
            except (OSError, RuntimeError) as exc:
                failures += 1
                record["last_action"] = "error"
                record["error"] = str(exc)
                print(f"Error copying {frame.relative_path}: {exc}", file=sys.stderr)
        results.append(record)

    summary: dict[str, object] = {
        "source_frame_count": len(frames),
        "source_bytes": sum(frame.size for frame in frames),
        "copied_or_refreshed_count": sum(record["last_action"] in {"copied", "refreshed"} for record in results),
        "copied_bytes": copied_bytes,
        "skipped_count": sum(record["last_action"] == "skip" for record in results),
        "failure_count": failures + len(warnings),
        "warning_count": len(warnings),
    }
    try:
        write_manifest(
            manifest_path,
            build_manifest(source, destination, trusted_previous, results, summary),
        )
    except OSError as exc:
        print(f"Error writing manifest: {exc}", file=sys.stderr)
        return 1

    print(
        f"Import complete: {summary['copied_or_refreshed_count']:,} copied/refreshed, "
        f"{summary['skipped_count']:,} skipped, {failures:,} failed."
    )
    print(f"Manifest: {manifest_path}")
    return 0 if failures == 0 and not warnings else 1


if __name__ == "__main__":
    raise SystemExit(main())
