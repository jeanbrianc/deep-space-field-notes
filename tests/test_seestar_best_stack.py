import tempfile
import unittest
from pathlib import Path
from unittest import mock

import seestar_best_stack as collector


class CollectorTests(unittest.TestCase):
    def test_selects_best_stack_in_each_folder_and_ignores_thumbnails(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            first = source / "MyWorks" / "M 51"
            second = source / "MyWorks" / "M 101"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "Stacked_12_M 51.jpg").write_bytes(b"12")
            (first / "Stacked_40_M 51.jpg").write_bytes(b"40")
            (first / "Stacked_999_M 51_thn.jpg").write_bytes(b"thumbnail")
            (first / "Light_M 51.jpg").write_bytes(b"light")
            (second / "Stacked_22_M 101.jpg").write_bytes(b"22")

            candidates, warnings = collector.find_candidates(source)
            winners = collector.best_per_folder(candidates)

            self.assertEqual(warnings, [])
            self.assertEqual(
                [(item.path.name, item.frames) for item in winners],
                [("Stacked_22_M 101.jpg", 22), ("Stacked_40_M 51.jpg", 40)],
            )

    def test_copies_winners_preserves_folders_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            (source / "A").mkdir(parents=True)
            (source / "B").mkdir()
            (source / "A" / "Stacked_4_A.jpg").write_bytes(b"four")
            (source / "A" / "Stacked_20_A.jpg").write_bytes(b"twenty")
            (source / "B" / "Stacked_8_B.jpg").write_bytes(b"eight")

            result = collector.main(["--source", str(source), "--destination", str(destination)])

            self.assertEqual(result, 0)
            self.assertEqual((destination / "A" / "Stacked_20_A.jpg").read_bytes(), b"twenty")
            self.assertEqual((destination / "B" / "Stacked_8_B.jpg").read_bytes(), b"eight")
            manifest = (destination / collector.MANIFEST_NAME).read_text()
            self.assertIn("20\t", manifest)
            self.assertIn("Stacked_8_B.jpg", manifest)
            self.assertNotIn("Stacked_4_A.jpg", manifest)

    def test_rejects_selected_source_symlink_that_escapes_source_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            outside = root / "outside.jpg"
            (source / "A").mkdir(parents=True)
            outside.write_bytes(b"outside")
            (source / "A" / "Stacked_100_A.jpg").symlink_to(outside)

            result = collector.main(
                ["--source", str(source), "--destination", str(destination)]
            )

            self.assertEqual(result, 2)
            self.assertFalse(destination.exists())
            self.assertEqual(outside.read_bytes(), b"outside")

    def test_rejects_selected_source_symlink_even_when_target_stays_inside_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            real_folder = source / "real"
            linked_folder = source / "linked"
            real_folder.mkdir(parents=True)
            linked_folder.mkdir()
            real = real_folder / "Stacked_100_A.jpg"
            real.write_bytes(b"inside")
            (linked_folder / "Stacked_100_A.jpg").symlink_to(real)

            result = collector.main(
                ["--source", str(source), "--destination", str(destination)]
            )

            self.assertEqual(result, 2)
            self.assertFalse(destination.exists())
            self.assertEqual(real.read_bytes(), b"inside")

    def test_rejects_destination_ancestor_symlink_before_any_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            outside = root / "outside"
            (source / "0-safe").mkdir(parents=True)
            (source / "A").mkdir()
            destination.mkdir()
            outside.mkdir()
            (source / "0-safe" / "Stacked_10_safe.jpg").write_bytes(b"safe")
            (source / "A" / "Stacked_100_A.jpg").write_bytes(b"winner")
            escaped_target = outside / "Stacked_100_A.jpg"
            escaped_target.write_bytes(b"sentinel")
            (destination / "A").symlink_to(outside, target_is_directory=True)

            result = collector.main(
                ["--source", str(source), "--destination", str(destination)]
            )

            self.assertEqual(result, 2)
            self.assertEqual(escaped_target.read_bytes(), b"sentinel")
            self.assertFalse(
                (destination / "0-safe" / "Stacked_10_safe.jpg").exists()
            )
            self.assertFalse((destination / collector.MANIFEST_NAME).exists())

    def test_rejects_non_directory_destination_ancestor_before_any_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            (source / "0-safe").mkdir(parents=True)
            (source / "A").mkdir()
            destination.mkdir()
            (source / "0-safe" / "Stacked_10_safe.jpg").write_bytes(b"safe")
            (source / "A" / "Stacked_100_A.jpg").write_bytes(b"winner")
            (destination / "A").write_bytes(b"not a directory")

            result = collector.main(
                ["--source", str(source), "--destination", str(destination)]
            )

            self.assertEqual(result, 2)
            self.assertEqual((destination / "A").read_bytes(), b"not a directory")
            self.assertFalse(
                (destination / "0-safe" / "Stacked_10_safe.jpg").exists()
            )
            self.assertFalse((destination / collector.MANIFEST_NAME).exists())

    def test_reuses_identical_existing_target_without_copying_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            source_target = source / "A" / "Stacked_100_A.jpg"
            existing_target = destination / "A" / "Stacked_100_A.jpg"
            source_target.parent.mkdir(parents=True)
            existing_target.parent.mkdir(parents=True)
            source_target.write_bytes(b"same image")
            existing_target.write_bytes(b"same image")
            existing_stat = existing_target.stat()

            with mock.patch.object(collector.shutil, "copy2") as copy2:
                result = collector.main(
                    ["--source", str(source), "--destination", str(destination)]
                )

            self.assertEqual(result, 0)
            copy2.assert_not_called()
            self.assertEqual(existing_target.read_bytes(), b"same image")
            self.assertEqual(existing_target.stat().st_ino, existing_stat.st_ino)
            manifest = (destination / collector.MANIFEST_NAME).read_text()
            self.assertIn("Stacked_100_A.jpg", manifest)

    def test_different_existing_target_conflicts_before_copy_or_manifest_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            (source / "0-new").mkdir(parents=True)
            (source / "Z-conflict").mkdir()
            destination.mkdir()
            (source / "0-new" / "Stacked_10_new.jpg").write_bytes(b"new")
            (source / "Z-conflict" / "Stacked_20_conflict.jpg").write_bytes(
                b"source"
            )
            conflict = destination / "Z-conflict" / "Stacked_20_conflict.jpg"
            conflict.parent.mkdir()
            conflict.write_bytes(b"different")
            manifest = destination / collector.MANIFEST_NAME
            manifest.write_bytes(b"original manifest\n")

            result = collector.main(
                ["--source", str(source), "--destination", str(destination)]
            )

            self.assertNotEqual(result, 0)
            self.assertFalse(
                (destination / "0-new" / "Stacked_10_new.jpg").exists()
            )
            self.assertEqual(conflict.read_bytes(), b"different")
            self.assertEqual(manifest.read_bytes(), b"original manifest\n")

    def test_copy_failure_leaves_no_final_or_temporary_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            source_target = source / "A" / "Stacked_100_A.jpg"
            source_target.parent.mkdir(parents=True)
            source_target.write_bytes(b"complete image")
            destination.mkdir()
            manifest = destination / collector.MANIFEST_NAME
            manifest.write_bytes(b"original manifest\n")

            def fail_after_partial_write(_source: Path, temporary: Path) -> None:
                Path(temporary).write_bytes(b"partial")
                raise OSError("simulated copy failure")

            with mock.patch.object(
                collector.shutil, "copy2", side_effect=fail_after_partial_write
            ):
                result = collector.main(
                    ["--source", str(source), "--destination", str(destination)]
                )

            target_parent = destination / "A"
            self.assertEqual(result, 1)
            self.assertFalse((target_parent / source_target.name).exists())
            self.assertEqual(list(target_parent.glob(f".{source_target.name}.*.tmp")), [])
            self.assertEqual(manifest.read_bytes(), b"original manifest\n")

    def test_late_created_target_is_never_clobbered_during_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            destination = root / "offline"
            source_target = source / "A" / "Stacked_100_A.jpg"
            final_target = destination / "A" / source_target.name
            source_target.parent.mkdir(parents=True)
            source_target.write_bytes(b"source image")
            destination.mkdir()
            manifest = destination / collector.MANIFEST_NAME
            manifest.write_bytes(b"original manifest\n")

            def create_racing_target(_temporary: Path, target: Path) -> None:
                Path(target).write_bytes(b"racing writer")
                raise FileExistsError("simulated no-clobber race")

            with mock.patch.object(
                collector.os, "link", side_effect=create_racing_target
            ):
                result = collector.main(
                    ["--source", str(source), "--destination", str(destination)]
                )

            self.assertEqual(result, 1)
            self.assertEqual(final_target.read_bytes(), b"racing writer")
            self.assertEqual(
                list(final_target.parent.glob(f".{source_target.name}.*.tmp")), []
            )
            self.assertEqual(manifest.read_bytes(), b"original manifest\n")


if __name__ == "__main__":
    unittest.main()
