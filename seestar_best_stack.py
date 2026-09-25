#!/usr/bin/env python3
"""Copy the highest-frame-count Seestar JPEG from every folder."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


STACKED_NAME = re.compile(r"^Stacked_(\d+)_.*\.jpe?g$", re.IGNORECASE)
MANIFEST_NAME = "selected_jpegs.txt"


@dataclass(frozen=True)
class Candidate:
    path: Path
    frames: int
    modified_ns: int


@dataclass(frozen=True)
class PlannedCopy:
    candidate: Candidate
    source: Path
    target: Path
    reuse_existing: bool


def find_candidates(source: Path) -> tuple[list[Candidate], list[str]]:
    candidates: list[Candidate] = []
    warnings: list[str] = []
    try:
        for path in source.rglob("*"):
            if not path.is_file() or path.name.lower().endswith(("_thn.jpg", "_thn.jpeg")):
                continue
            match = STACKED_NAME.match(path.name)
            if not match:
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                warnings.append(f"Could not inspect {path}: {exc}")
                continue
            candidates.append(Candidate(path, int(match.group(1)), stat.st_mtime_ns))
    except OSError as exc:
        warnings.append(f"Could not scan {source}: {exc}")
    return candidates, warnings


def best_per_folder(candidates: list[Candidate]) -> list[Candidate]:
    winners: dict[Path, Candidate] = {}
    for candidate in candidates:
        current = winners.get(candidate.path.parent)
        # Prefer more frames, then the newer file, then a deterministic filename.
        rank = (candidate.frames, candidate.modified_ns, candidate.path.name)
        if current is None or rank > (current.frames, current.modified_ns, current.path.name):
            winners[candidate.path.parent] = candidate
    return sorted(winners.values(), key=lambda item: str(item.path).casefold())


def destination_for(source_root: Path, destination_root: Path, source_file: Path) -> Path:
    return destination_root / source_file.relative_to(source_root)


def validated_source_file(source_root: Path, source_file: Path) -> Path:
    """Return a selected source's canonical path, rejecting root escapes."""
    if source_file.is_symlink():
        raise ValueError(f"selected source must not be a symbolic link: {source_file}")
    root = source_root.absolute()
    candidate = source_file.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"selected source escapes the source root: {source_file}") from exc
    if ".." in relative.parts:
        raise ValueError(f"selected source escapes the source root: {source_file}")

    try:
        resolved = source_file.resolve(strict=True)
        resolved.relative_to(source_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"selected source escapes the source root: {source_file}") from exc
    if not resolved.is_file():
        raise ValueError(f"selected source is not a regular file: {source_file}")
    return resolved


def validate_destination_path(destination_root: Path, target: Path) -> None:
    """Reject output escapes and symlinked components below the destination."""
    root = destination_root.absolute()
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


def files_are_identical(first: Path, second: Path) -> bool:
    """Compare two regular files byte-for-byte without trusting timestamps."""
    if first.stat().st_size != second.stat().st_size:
        return False

    with first.open("rb") as first_handle, second.open("rb") as second_handle:
        while True:
            first_chunk = first_handle.read(1024 * 1024)
            second_chunk = second_handle.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def atomic_copy(source: Path, target: Path, destination_root: Path) -> bool:
    """Copy through a confined sibling temporary file and atomically install it.

    Return ``True`` when a new target was installed. If an identical target
    appeared after preflight, leave it untouched and return ``False``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_destination_path(destination_root, target)
    if target.exists():
        if files_are_identical(source, target):
            return False
        raise FileExistsError(f"destination file changed after preflight: {target}")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        validate_destination_path(destination_root, temporary)
        shutil.copy2(source, temporary)
        validate_destination_path(destination_root, temporary)
        validate_destination_path(destination_root, target)
        try:
            os.link(temporary, target)
        except FileExistsError:
            validate_destination_path(destination_root, target)
            if files_are_identical(source, target):
                return False
            raise FileExistsError(
                f"destination file changed after preflight: {target}"
            )
        return True
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def write_manifest(destination: Path, copied: list[tuple[Candidate, Path]]) -> Path:
    manifest = destination / MANIFEST_NAME
    lines = [
        "frames\tlocal_file\tsource_file",
        *(
            f"{candidate.frames}\t{target}\t{candidate.path}"
            for candidate, target in copied
        ),
    ]
    temporary = destination / f".{MANIFEST_NAME}.tmp"
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(manifest)
    return manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy the Seestar stacked JPEG with the most frames from each source folder."
    )
    parser.add_argument("--source", required=True, type=Path, help="Connected storage folder to scan recursively.")
    parser.add_argument("--destination", required=True, type=Path, help="Local root folder for the offline copies.")
    parser.add_argument("--dry-run", action="store_true", help="List selections without copying files.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.source.expanduser().resolve()
    requested_destination = args.destination.expanduser().absolute()
    if requested_destination.is_symlink():
        print(
            f"Error: destination root must not be a symlink: {requested_destination}",
            file=sys.stderr,
        )
        return 2
    destination = requested_destination.resolve()

    if not source.is_dir():
        print(f"Error: source is not mounted or is not a directory: {source}", file=sys.stderr)
        return 2
    if destination == source or source in destination.parents:
        print("Error: destination must not be inside the source folder.", file=sys.stderr)
        return 2

    candidates, warnings = find_candidates(source)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    winners = best_per_folder(candidates)

    if not winners:
        print(f"No full-resolution stacked JPEGs found under {source}")
        return 0

    print(f"Selected {len(winners)} JPEG(s), one best stack per folder:")
    planned: list[PlannedCopy] = []
    for candidate in winners:
        try:
            copy_source = validated_source_file(source, candidate.path)
            target = destination_for(source, destination, candidate.path)
            validate_destination_path(destination, target)
            reuse_existing = target.exists()
            if reuse_existing and not files_are_identical(copy_source, target):
                print(
                    f"Error: destination already contains different content: {target}",
                    file=sys.stderr,
                )
                return 2
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        planned.append(PlannedCopy(candidate, copy_source, target, reuse_existing))
        suffix = "  (reuse identical file)" if reuse_existing else ""
        print(f"{candidate.frames:>6} frames  {candidate.path}  ->  {target}{suffix}")

    try:
        validate_destination_path(destination, destination / MANIFEST_NAME)
        validate_destination_path(destination, destination / f".{MANIFEST_NAME}.tmp")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print("Dry run: nothing was copied.")
        return 0

    copied_count = 0
    reused_count = 0
    try:
        for item in planned:
            validate_destination_path(destination, item.target)
            if item.reuse_existing:
                if not item.target.exists() or not files_are_identical(
                    item.source, item.target
                ):
                    raise FileExistsError(
                        f"destination file changed after preflight: {item.target}"
                    )
                reused_count += 1
                continue
            if atomic_copy(item.source, item.target, destination):
                copied_count += 1
            else:
                reused_count += 1
        destination.mkdir(parents=True, exist_ok=True)
        manifest = write_manifest(
            destination,
            [(item.candidate, item.target) for item in planned],
        )
    except (OSError, ValueError) as exc:
        print(f"Error while copying: {exc}", file=sys.stderr)
        return 1

    print(
        f"Copied {copied_count} JPEG(s); reused {reused_count} identical JPEG(s). "
        f"List written to: {manifest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
