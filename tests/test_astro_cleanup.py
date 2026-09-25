import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

import astro_cleanup


def save_jpeg(path: Path, color: tuple[int, int, int] = (50, 20, 45)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 20), color).save(path, format="JPEG")


def write_collector_manifest(source: Path, rows: list[tuple[object, object, object]]) -> None:
    lines = ["frames\tlocal_file\tsource_file"]
    lines.extend("\t".join(str(value) for value in row) for row in rows)
    (source / astro_cleanup.COLLECTOR_MANIFEST_NAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


class CleanupTests(unittest.TestCase):
    def test_neutralizes_colored_background_without_clipping_bright_object(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:] = [90, 35, 80]
        image[45:55, 45:55] = [220, 100, 120]

        cleaned, metrics = astro_cleanup.cleanup_array(image)
        background = np.median(cleaned[:40, :40], axis=(0, 1))

        self.assertLess(float(background.max() - background.min()), 3.0)
        self.assertGreater(float(cleaned[50, 50].mean()), 200.0)
        self.assertEqual(len(metrics["background_lab"]), 3)

    def test_neutralizes_bright_white_stars(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[:] = [90, 35, 80]
        image[::5, ::5] = [245, 245, 245]

        cleaned, _ = astro_cleanup.cleanup_array(image)
        star = cleaned[0, 0].astype(float)

        self.assertLess(star.max() - star.min(), 8.0)

    def test_directory_outputs_are_flat(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            output = root / "cleaned"
            nested = source / "MyWorks" / "M 51"
            nested.mkdir(parents=True)
            Image.new("RGB", (20, 20), (50, 20, 45)).save(nested / "Stacked_10_M 51_10.0s_LP_20250101-000000.jpg")

            result = astro_cleanup.main(["--source", str(source), "--destination", str(output), "--min-frames", "0"])

            self.assertEqual(result, 0)
            self.assertTrue((output / "Stacked_10_M 51_10.0s_LP_20250101-000000_cleaned.jpg").is_file())
            self.assertFalse((output / "MyWorks").exists())

    def test_prefers_finished_proc_image_and_filters_low_frame_stacks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "gallery"
            proc = root / "astro" / "M 51_sub" / "proc"
            source.mkdir()
            proc.mkdir(parents=True)
            Image.new("RGB", (20, 20), (20, 20, 20)).save(source / "Stacked_40_M 42_10.0s_LP_20250101-000000.jpg")
            Image.new("RGB", (20, 20), (40, 40, 40)).save(source / "Stacked_100_M 51_10.0s_LP_20250101-000000.jpg")
            Image.new("RGB", (30, 30), (90, 70, 60)).save(proc / "m51_stretched_GraXpert.png")

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output),
                "--processed-root", str(root / "astro"), "--min-frames", "50",
            ])

            self.assertEqual(result, 0)
            images = list(output.glob("*.*g"))
            self.assertEqual(len(images), 1)
            self.assertIn("hand_processed", images[0].name)

    def test_collector_manifest_selects_only_the_new_current_winner(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            old = source / "M 51" / "old" / "Stacked_60_M 51_10.0s_LP_20250101-000000.jpg"
            new = source / "M 51" / "new" / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(old, (30, 20, 20))
            save_jpeg(new, (60, 30, 45))
            write_collector_manifest(source, [(100, new, "/Volumes/Seestar/M 51/new.jpg")])

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output), "--min-frames", "0",
            ])

            self.assertEqual(result, 0)
            records = json.loads((output / "cleanup_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([record["source"] for record in records], [str(new.resolve())])
            self.assertTrue((output / f"{new.stem}_cleaned.jpg").is_file())
            self.assertFalse((output / f"{old.stem}_cleaned.jpg").exists())

    def test_invalid_manifest_is_fully_rejected_before_output_or_prune(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            selected = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(selected)
            write_collector_manifest(source, [
                (100, selected, "/Volumes/Seestar/M 51/current.jpg"),
                ("not-a-number", selected, "/Volumes/Seestar/M 51/bad.jpg"),
            ])
            output.mkdir()
            prior = output / "prior-cleaned.jpg"
            prior.write_bytes(b"keep")
            (output / "cleanup_manifest.json").write_text(
                json.dumps([{"output": str(prior)}]), encoding="utf-8"
            )

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output), "--prune",
            ])

            self.assertEqual(result, 2)
            self.assertEqual(prior.read_bytes(), b"keep")
            self.assertFalse((output / f"{selected.stem}_cleaned.jpg").exists())
            self.assertEqual(
                json.loads((output / "cleanup_manifest.json").read_text(encoding="utf-8")),
                [{"output": str(prior)}],
            )

    def test_manifest_rejects_bad_header_and_malformed_row(self):
        cases = {
            "bad header": "frame\tlocal_file\tsource_file\n",
            "missing column": "frames\tlocal_file\tsource_file\n100\tonly-two-columns\n",
        }
        for label, manifest_text in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "offline"
                output = root / "cleaned"
                source.mkdir()
                (source / astro_cleanup.COLLECTOR_MANIFEST_NAME).write_text(
                    manifest_text, encoding="utf-8"
                )

                result = astro_cleanup.main(["--source", str(source), "--destination", str(output)])

                self.assertEqual(result, 2)
                self.assertFalse(output.exists())

    def test_manifest_rejects_untrusted_local_files_before_prune(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / "outside" / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(outside)

            cases: dict[str, Callable[[Path], Path]] = {
                "absolute escape": lambda source: outside,
                "symlink escape": lambda source: self._escaping_symlink(source, outside),
                "missing": lambda source: source / "Stacked_100_missing.jpg",
                "not a file": lambda source: self._jpeg_named_directory(source),
                "non JPEG extension": lambda source: self._png_file(source),
                "non JPEG contents": lambda source: self._fake_jpeg(source),
            }
            for label, make_local_file in cases.items():
                with self.subTest(label=label):
                    source = root / label.replace(" ", "-")
                    output = root / f"output-{label.replace(' ', '-')}"
                    source.mkdir()
                    local_file = make_local_file(source)
                    write_collector_manifest(source, [(100, local_file, "/Volumes/Seestar/source.jpg")])
                    output.mkdir()
                    prior = output / "prior-cleaned.jpg"
                    prior.write_bytes(b"keep")
                    (output / "cleanup_manifest.json").write_text(
                        json.dumps([{"output": str(prior)}]), encoding="utf-8"
                    )

                    result = astro_cleanup.main([
                        "--source", str(source), "--destination", str(output), "--prune",
                    ])

                    self.assertEqual(result, 2)
                    self.assertEqual(prior.read_bytes(), b"keep")

    @staticmethod
    def _escaping_symlink(source: Path, outside: Path) -> Path:
        link = source / outside.name
        link.symlink_to(outside)
        return link

    @staticmethod
    def _jpeg_named_directory(source: Path) -> Path:
        directory = source / "Stacked_100_directory.jpg"
        directory.mkdir()
        return directory

    @staticmethod
    def _png_file(source: Path) -> Path:
        path = source / "Stacked_100_image.png"
        Image.new("RGB", (20, 20), (20, 30, 40)).save(path, format="PNG")
        return path

    @staticmethod
    def _fake_jpeg(source: Path) -> Path:
        path = source / "Stacked_100_fake.jpg"
        path.write_text("not actually a JPEG", encoding="utf-8")
        return path

    def test_duplicate_flat_output_names_get_stable_distinct_suffixes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            first_output = root / "cleaned-one"
            second_output = root / "cleaned-two"
            name = "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(source / "one" / name, (45, 25, 30))
            save_jpeg(source / "two" / name, (65, 35, 50))

            first_result = astro_cleanup.main([
                "--source", str(source), "--destination", str(first_output), "--min-frames", "0",
            ])
            second_result = astro_cleanup.main([
                "--source", str(source), "--destination", str(second_output), "--min-frames", "0",
            ])

            self.assertEqual((first_result, second_result), (0, 0))
            first_records = json.loads(
                (first_output / "cleanup_manifest.json").read_text(encoding="utf-8")
            )
            second_records = json.loads(
                (second_output / "cleanup_manifest.json").read_text(encoding="utf-8")
            )
            first_names = {Path(record["output"]).name for record in first_records}
            second_names = {Path(record["output"]).name for record in second_records}
            self.assertEqual(len(first_names), 2)
            self.assertEqual(first_names, second_names)
            self.assertNotIn(f"{Path(name).stem}_cleaned.jpg", first_names)
            self.assertEqual(len({record["source"] for record in first_records}), 2)
            self.assertTrue(all((first_output / filename).is_file() for filename in first_names))

    def test_automatic_output_symlink_fails_before_prune_or_any_image_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            outside = root / "outside"
            safe = source / "Stacked_100_M 31_10.0s_LP_20250101-000000.jpg"
            attacked = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(safe)
            save_jpeg(attacked)
            output.mkdir()
            outside.mkdir()
            victim = outside / "victim.jpg"
            victim.write_bytes(b"outside must remain unchanged")
            attacked_output = output / f"{attacked.stem}_cleaned.jpg"
            attacked_output.symlink_to(victim)
            prior = output / "prior-cleaned.jpg"
            prior.write_bytes(b"keep prior")
            manifest = output / astro_cleanup.CLEANUP_MANIFEST_NAME
            manifest.write_text(
                json.dumps([{"output": str(prior)}]), encoding="utf-8"
            )
            manifest_before = manifest.read_bytes()

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output),
                "--min-frames", "0", "--prune",
            ])

            self.assertEqual(result, 2)
            self.assertEqual(victim.read_bytes(), b"outside must remain unchanged")
            self.assertTrue(attacked_output.is_symlink())
            self.assertFalse((output / f"{safe.stem}_cleaned.jpg").exists())
            self.assertEqual(prior.read_bytes(), b"keep prior")
            self.assertEqual(manifest.read_bytes(), manifest_before)

    def test_hand_processed_output_symlink_fails_before_prune_or_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            outside = root / "outside"
            proc = root / "astro" / "M 51_sub" / "proc"
            selected = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            finished = proc / "m51_stretched_GraXpert.png"
            save_jpeg(selected)
            finished.parent.mkdir(parents=True)
            Image.new("RGB", (30, 30), (90, 70, 60)).save(finished)
            output.mkdir()
            outside.mkdir()
            victim = outside / "victim.png"
            victim.write_bytes(b"outside must remain unchanged")
            attacked_output = output / f"{selected.stem}_hand_processed.png"
            attacked_output.symlink_to(victim)
            prior = output / "prior-cleaned.jpg"
            prior.write_bytes(b"keep prior")
            manifest = output / astro_cleanup.CLEANUP_MANIFEST_NAME
            manifest.write_text(
                json.dumps([{"output": str(prior)}]), encoding="utf-8"
            )
            manifest_before = manifest.read_bytes()

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output),
                "--processed-root", str(root / "astro"), "--min-frames", "0",
                "--prune",
            ])

            self.assertEqual(result, 2)
            self.assertEqual(victim.read_bytes(), b"outside must remain unchanged")
            self.assertTrue(attacked_output.is_symlink())
            self.assertEqual(prior.read_bytes(), b"keep prior")
            self.assertEqual(manifest.read_bytes(), manifest_before)

    def test_cleanup_manifest_symlink_fails_before_prune_or_image_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            outside = root / "outside"
            selected = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(selected)
            output.mkdir()
            outside.mkdir()
            victim = outside / "victim.json"
            victim.write_text("[]\n", encoding="utf-8")
            manifest = output / astro_cleanup.CLEANUP_MANIFEST_NAME
            manifest.symlink_to(victim)

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output),
                "--min-frames", "0", "--prune",
            ])

            self.assertEqual(result, 2)
            self.assertEqual(victim.read_text(encoding="utf-8"), "[]\n")
            self.assertTrue(manifest.is_symlink())
            self.assertFalse((output / f"{selected.stem}_cleaned.jpg").exists())

    def test_escaping_previous_prune_target_fails_before_delete_or_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            output = root / "cleaned"
            outside = root / "outside"
            selected = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(selected)
            output.mkdir()
            outside.mkdir()
            victim = outside / "victim.jpg"
            victim.write_bytes(b"keep outside")
            linked_parent = output / "linked-parent"
            linked_parent.symlink_to(outside, target_is_directory=True)
            manifest = output / astro_cleanup.CLEANUP_MANIFEST_NAME
            manifest.write_text(
                json.dumps([{"output": str(linked_parent / victim.name)}]),
                encoding="utf-8",
            )
            manifest_before = manifest.read_bytes()

            result = astro_cleanup.main([
                "--source", str(source), "--destination", str(output),
                "--min-frames", "0", "--prune",
            ])

            self.assertEqual(result, 2)
            self.assertEqual(victim.read_bytes(), b"keep outside")
            self.assertEqual(manifest.read_bytes(), manifest_before)
            self.assertFalse((output / f"{selected.stem}_cleaned.jpg").exists())

    def test_destination_root_symlink_and_file_are_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "offline"
            selected = source / "Stacked_100_M 51_10.0s_LP_20250102-000000.jpg"
            save_jpeg(selected)

            outside = root / "outside"
            outside.mkdir()
            linked_destination = root / "linked-cleaned"
            linked_destination.symlink_to(outside, target_is_directory=True)
            linked_result = astro_cleanup.main([
                "--source", str(source),
                "--destination", str(linked_destination),
                "--min-frames", "0",
            ])

            file_destination = root / "not-a-directory"
            file_destination.write_bytes(b"keep")
            file_result = astro_cleanup.main([
                "--source", str(source),
                "--destination", str(file_destination),
                "--min-frames", "0",
            ])

            self.assertEqual((linked_result, file_result), (2, 2))
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(file_destination.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
