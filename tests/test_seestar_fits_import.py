import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import seestar_fits_import as importer


def make_frame(path: Path, payload: bytes = b"fits") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class DiscoveryTests(unittest.TestCase):
    def test_discovers_recursive_sub_folders_and_only_immediate_light_fits(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            make_frame(source / "MyWorks" / "M 51_sub" / "Light_M 51_10.0s_IRCUT_20260101-000000.fit")
            make_frame(source / "Other" / "Café_SUB" / "Light_Café_10.0s_LP_20260101-000001.fits")
            make_frame(source / "MyWorks" / "M 51_sub" / "Stacked_50_M 51.fit")
            make_frame(source / "MyWorks" / "M 51_sub" / "Light_M 51.jpg")
            make_frame(source / "MyWorks" / "M 51_sub" / "proc" / "Light_nested.fit")
            make_frame(source / "MyWorks" / "not-a-target" / "Light_wrong.fit")

            frames, warnings = importer.discover_frames(source)

            self.assertEqual(warnings, [])
            self.assertEqual(
                [str(frame.relative_path) for frame in frames],
                [
                    "MyWorks/M 51_sub/Light_M 51_10.0s_IRCUT_20260101-000000.fit",
                    "Other/Café_SUB/Light_Café_10.0s_LP_20260101-000001.fits",
                ],
            )


class ImportTests(unittest.TestCase):
    def test_first_run_copies_atomically_and_second_run_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            original = make_frame(
                source / "MyWorks" / "M 51_sub" / "Light_M 51_10.0s_IRCUT_20260101-000000.fit",
                b"original-fits",
            )
            source_stat = original.stat()

            first = importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"])
            archived = destination / original.relative_to(source)
            archived_mtime = archived.stat().st_mtime_ns
            second = importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"])

            self.assertEqual((first, second), (0, 0))
            self.assertEqual(archived.read_bytes(), b"original-fits")
            self.assertEqual(archived.stat().st_mtime_ns, archived_mtime)
            self.assertEqual((original.stat().st_size, original.stat().st_mtime_ns), (source_stat.st_size, source_stat.st_mtime_ns))
            manifest = json.loads((destination / importer.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["skipped_count"], 1)
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)
            self.assertFalse(list(destination.rglob("*.part")))

    def test_different_existing_file_is_conflict_until_replace_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            relative = Path("MyWorks/M 31_sub/Light_M 31_10.0s_IRCUT_20260101-000000.fit")
            make_frame(source / relative, b"source")
            make_frame(destination / relative, b"local!")

            with contextlib.redirect_stderr(io.StringIO()):
                conflict = importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"])
            self.assertEqual(conflict, 1)
            self.assertEqual((destination / relative).read_bytes(), b"local!")

            replaced = importer.main(
                [
                    "--source",
                    str(source),
                    "--destination",
                    str(destination),
                    "--replace-changed",
                    "--reserve-gib",
                    "0",
                ]
            )
            self.assertEqual(replaced, 0)
            self.assertEqual((destination / relative).read_bytes(), b"source")

    def test_same_size_different_content_is_detected_with_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            relative = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            make_frame(source / relative, b"AAAA")
            make_frame(destination / relative, b"BBBB")

            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    ["--source", str(source), "--destination", str(destination), "--verify", "--reserve-gib", "0"]
                )

            self.assertEqual(result, 1)
            self.assertEqual((destination / relative).read_bytes(), b"BBBB")

    def test_same_size_tampering_is_detected_without_verify_when_metadata_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            relative = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            make_frame(source / relative, b"AAAA")
            self.assertEqual(
                importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )
            archived = destination / relative
            archived.write_bytes(b"BBBB")
            changed_ns = archived.stat().st_mtime_ns + 1_000_000_000
            os.utime(archived, ns=(changed_ns, changed_ns))

            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    ["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"]
                )

            self.assertEqual(result, 1)
            self.assertEqual(archived.read_bytes(), b"BBBB")

    def test_changed_source_root_invalidates_manifest_trust(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_a = root / "source-a"
            source_b = root / "source-b"
            destination = root / "offline"
            relative = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            first = make_frame(source_a / relative, b"AAAA")
            self.assertEqual(
                importer.main(["--source", str(source_a), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )
            second = make_frame(source_b / relative, b"BBBB")
            first_stat = first.stat()
            os.utime(second, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))

            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    ["--source", str(source_b), "--destination", str(destination), "--reserve-gib", "0"]
                )

            self.assertEqual(result, 1)
            self.assertEqual((destination / relative).read_bytes(), b"AAAA")

    def test_changed_source_root_does_not_retrust_retained_records_on_next_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_a = root / "source-a"
            source_b = root / "source-b"
            destination = root / "offline"
            shared = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            other = Path("M 31_sub/Light_M 31_10.0s_IRCUT_20260101-000000.fit")
            first = make_frame(source_a / shared, b"AAAA")
            self.assertEqual(
                importer.main(["--source", str(source_a), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )

            make_frame(source_b / other, b"other")
            self.assertEqual(
                importer.main(["--source", str(source_b), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )
            manifest = json.loads((destination / importer.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertNotIn(str(shared), {record["relative_path"] for record in manifest["files"]})

            replacement = make_frame(source_b / shared, b"BBBB")
            first_stat = first.stat()
            os.utime(replacement, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    ["--source", str(source_b), "--destination", str(destination), "--reserve-gib", "0"]
                )

            self.assertEqual(result, 1)
            self.assertEqual((destination / shared).read_bytes(), b"AAAA")
            manifest = json.loads((destination / importer.MANIFEST_NAME).read_text(encoding="utf-8"))
            shared_record = next(record for record in manifest["files"] if record["relative_path"] == str(shared))
            self.assertEqual(shared_record["last_action"], "conflict")

    def test_rejects_symlinked_intermediate_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside = root / "outside"
            outside.mkdir()
            link = root / "archive-link"
            link.symlink_to(outside, target_is_directory=True)
            relative = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            make_frame(source / relative, b"AAAA")

            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    [
                        "--source",
                        str(source),
                        "--destination",
                        str(link / "offline"),
                        "--reserve-gib",
                        "0",
                    ]
                )

            self.assertEqual(result, 2)
            self.assertFalse((outside / "offline").exists())

    def test_symlinked_target_folder_is_conflict_before_existing_file_is_read_or_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            outside_target = root / "outside" / "M 42_sub"
            destination.mkdir()
            outside_target.mkdir(parents=True)
            relative = Path("M 42_sub/Light_M 42_10.0s_LP_20260101-000000.fit")
            make_frame(source / relative, b"AAAA")
            outside_file = make_frame(outside_target / relative.name, b"AAAA")
            (destination / "M 42_sub").symlink_to(outside_target, target_is_directory=True)

            with contextlib.redirect_stderr(io.StringIO()):
                result = importer.main(
                    [
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--reserve-gib",
                        "0",
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(outside_file.read_bytes(), b"AAAA")
            manifest = json.loads(
                (destination / importer.MANIFEST_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["summary"]["skipped_count"], 0)
            self.assertEqual(manifest["files"][0]["last_action"], "conflict")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            make_frame(source / "M 13_sub" / "Light_M 13_10.0s_IRCUT_20260101-000000.fit")

            result = importer.main(
                ["--source", str(source), "--destination", str(destination), "--dry-run", "--reserve-gib", "0"]
            )

            self.assertEqual(result, 0)
            self.assertFalse(destination.exists())

    def test_retains_offline_manifest_records_missing_from_later_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            first = make_frame(source / "M 13_sub" / "Light_M 13_10.0s_IRCUT_20260101-000000.fit")
            make_frame(source / "M 13_sub" / "Light_M 13_10.0s_IRCUT_20260101-000001.fit")
            self.assertEqual(
                importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )
            first.unlink()

            self.assertEqual(
                importer.main(["--source", str(source), "--destination", str(destination), "--reserve-gib", "0"]),
                0,
            )
            manifest = json.loads((destination / importer.MANIFEST_NAME).read_text(encoding="utf-8"))
            records = {record["relative_path"]: record for record in manifest["files"]}
            self.assertEqual(records[str(first.relative_to(source))]["last_action"], "retained-offline")
            self.assertTrue((destination / first.relative_to(source)).is_file())

    def test_copy_failure_never_promotes_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            frame_path = make_frame(source / "M 92_sub" / "Light_M 92_10.0s_IRCUT_20260101-000000.fit")
            frame = importer.discover_frames(source)[0][0]
            target = destination / frame.relative_path

            with mock.patch.object(os, "fsync", side_effect=OSError("simulated write failure")):
                with self.assertRaises(OSError):
                    importer.copy_verified(frame, target)

            self.assertFalse(target.exists())
            self.assertFalse(list(destination.rglob("*.part")))
            self.assertEqual(frame_path.read_bytes(), b"fits")

    def test_temporary_copy_checksum_mismatch_is_never_promoted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "offline"
            frame_path = make_frame(source / "M 92_sub" / "Light_M 92_10.0s_IRCUT_20260101-000000.fit")
            frame = importer.discover_frames(source)[0][0]
            target = destination / frame.relative_path

            with mock.patch.object(importer, "sha256_file", return_value="0" * 64):
                with self.assertRaisesRegex(RuntimeError, "temporary copy checksum mismatch"):
                    importer.copy_verified(frame, target)

            self.assertFalse(target.exists())
            self.assertFalse(list(destination.rglob("*.part")))
            self.assertEqual(frame_path.read_bytes(), b"fits")

    def test_rejects_overlapping_roots_and_insufficient_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            make_frame(source / "M 3_sub" / "Light_M 3_10.0s_IRCUT_20260101-000000.fit")
            self.assertIsNotNone(importer.validate_roots(source.resolve(), (source / "offline").resolve()))

            with mock.patch.object(importer.shutil, "disk_usage", return_value=shutil_usage(total=10, used=10, free=0)):
                with contextlib.redirect_stderr(io.StringIO()):
                    result = importer.main(
                        ["--source", str(source), "--destination", str(root / "offline"), "--reserve-gib", "0"]
                    )
            self.assertEqual(result, 2)
            self.assertFalse((root / "offline").exists())


def shutil_usage(*, total: int, used: int, free: int):
    return os.statvfs_result((0, 0, 0, 0, 0, 0, total, free, free, 0)) if False else mock.Mock(total=total, used=used, free=free)


if __name__ == "__main__":
    unittest.main()
