import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import seestar_siril_stack as stacker


def fits_bytes(width: int = 1080, height: int = 1920) -> bytes:
    def card(keyword: str, value: str | None = None) -> str:
        text = keyword if value is None else f"{keyword:<8}= {value:>20}"
        return text.ljust(80)

    header = "".join(
        (
            card("SIMPLE", "T"),
            card("BITPIX", "16"),
            card("NAXIS", "2"),
            card("NAXIS1", str(width)),
            card("NAXIS2", str(height)),
            card("END"),
        )
    ).encode("ascii")
    return header.ljust(stacker.FITS_BLOCK_SIZE, b" ")


def make_light(folder: Path, name: str, payload: bytes | None = None) -> Path:
    path = folder / name
    path.write_bytes(fits_bytes() if payload is None else payload)
    return path


def make_failed_retry_run(root: Path, *, schema_version: int = 1) -> Path:
    run = root / "M_51" / "M_51_10.0s_IRCUT" / "failed-run"
    process = run / "_work" / "process"
    process.mkdir(parents=True)
    (run / "_work" / stacker.WORK_MARKER).write_text(
        f"Owned by {stacker.TOOL_NAME}\n", encoding="utf-8"
    )
    (process / "light_00001.fit").write_bytes(fits_bytes())
    (process / "light_00002.fit").write_bytes(fits_bytes())
    (process / "light_.seq").write_text(
        "#Siril sequence file\nS 'light_' 1 2 2 5 1 6 0 0 0\n",
        encoding="utf-8",
    )
    (run / "pipeline.ssf").write_text("original pipeline\n", encoding="utf-8")
    (run / "siril.log").write_text("original log\n", encoding="utf-8")
    settings = stacker.stack_settings(
        quality_mode="balanced",
        preview_style="dark",
        brightness=0.08,
        shadow_sigma=2.5,
        jpeg_quality=95,
        preview_rotation=180,
        preview_flip="none",
        minimum_stacked_frames=2,
    )
    (run / stacker.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "tool": stacker.TOOL_NAME,
                "status": "failed",
                "error": "minimum common area was empty",
                "target": "M 51",
                "capture": {
                    "object": "M 51",
                    "exposure_seconds": "10.0",
                    "filter": "IRCUT",
                    "first_timestamp": "20260728-233258",
                },
                "input_count": 2,
                "input_bytes": 8,
                "registered_count": 2,
                "settings": settings,
            }
        ),
        encoding="utf-8",
    )
    return run


def bind_failed_run_to_group(
    run: Path,
    group: stacker.CaptureGroup,
    *,
    schema_version: int = stacker.MANIFEST_SCHEMA_VERSION,
) -> None:
    manifest_path = run / stacker.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "schema_version": schema_version,
            "source_folder": str(group.source_folder),
            "input_count": len(group.frames),
            "input_fingerprint": stacker.inventory_fingerprint(group),
            "inputs": stacker.manifest_input_records(group),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def make_successful_run(
    destination: Path,
    group: stacker.CaptureGroup,
    settings: dict[str, object],
    *,
    run_name: str,
    finished_at: str,
    retry_for: str | None = None,
    legacy_retry_provenance: bool = False,
    output_dimensions: tuple[int, int] = (1080, 1920),
) -> Path:
    run = destination / stacker.safe_component(group.target_name) / "group" / run_name
    run.mkdir(parents=True)
    linear = run / "stack.fit"
    preview = run / "preview.jpg"
    linear.write_bytes(fits_bytes(*output_dimensions))
    preview.write_bytes(f"preview-{run_name}".encode())
    outputs = {"linear_fits": str(linear), "preview_jpeg": str(preview)}
    hashes = {
        "linear_fits": stacker.sha256_file(linear),
        "preview_jpeg": stacker.sha256_file(preview),
    }
    manifest: dict[str, object] = {
        "schema_version": stacker.MANIFEST_SCHEMA_VERSION,
        "tool": stacker.TOOL_NAME,
        "status": "success",
        "target": group.target_name,
        "source_folder": str(group.source_folder),
        "capture": {
            "object": group.object_name,
            "exposure_seconds": group.exposure,
            "filter": group.filter_name,
        },
        "input_count": len(group.frames),
        "registered_count": len(group.frames),
        "stacked_count": len(group.frames),
        "input_fingerprint": stacker.inventory_fingerprint(group),
        "inputs": stacker.manifest_input_records(group),
        "finished_at": finished_at,
        "settings": settings,
        "outputs": outputs,
        "output_sha256": hashes,
    }
    if retry_for is not None:
        attempt: dict[str, object] = {
            "attempt": 1,
            "kind": "framing_retry",
            "status": "success",
            "framing_mode": settings["framing_mode"],
            "outputs": outputs,
            "output_sha256": hashes,
        }
        if not legacy_retry_provenance:
            attempt["replaces_framing_mode"] = retry_for
        manifest["attempts"] = [attempt]
    (run / stacker.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return run / stacker.MANIFEST_NAME


class DiscoveryTests(unittest.TestCase):
    def test_groups_only_top_level_light_fits_by_capture_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "NGC 6960_sub"
            folder.mkdir()
            make_light(folder, "Light_NGC 6960_10.0s_LP_20250916-213258.fit", b"one")
            make_light(folder, "Light_NGC 6960_10.0s_LP_20250916-213309.fit", b"two")
            make_light(folder, "Light_NGC 6960_20.0s_LP_20250916-213320.fits", b"three")
            (folder / "Light_NGC 6960_10.0s_LP_20250916-213258.jpg").write_bytes(b"jpeg")
            (folder / "Stacked_99_NGC 6960.fit").write_bytes(b"stack")
            proc = folder / "proc"
            proc.mkdir()
            make_light(proc, "Light_NGC 6960_10.0s_LP_20250916-213331.fit", b"nested")

            groups, warnings = stacker.collect_capture_groups(folder)

            self.assertEqual(warnings, [])
            self.assertEqual(len(groups), 2)
            self.assertEqual([(group.exposure, len(group.frames)) for group in groups], [("10.0", 2), ("20.0", 1)])
            self.assertEqual(groups[0].target_name, "NGC 6960")
            self.assertEqual(groups[0].filter_name, "LP")
            self.assertNotIn("nested", [frame.path.name for group in groups for frame in group.frames])

    def test_skips_light_symlink_resolving_outside_selected_source_before_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            folder = source / "M 51_sub"
            outside = root / "outside"
            folder.mkdir(parents=True)
            outside.mkdir()
            safe = make_light(
                folder,
                "Light_M 51_10.0s_IRCUT_20260728-233258.fit",
            )
            escaped = make_light(
                outside,
                "outside.fit",
            )
            symlink = folder / "Light_M 51_10.0s_IRCUT_20260728-233309.fit"
            symlink.symlink_to(escaped)

            original_parse = stacker.parse_capture_frame
            with mock.patch.object(
                stacker,
                "parse_capture_frame",
                wraps=original_parse,
            ) as parse_capture_frame:
                groups, warnings = stacker.collect_capture_groups(folder, source)

            self.assertEqual(len(groups), 1)
            self.assertEqual([frame.path for frame in groups[0].frames], [safe.resolve()])
            self.assertEqual(
                [call.args[0] for call in parse_capture_frame.call_args_list],
                [safe.resolve()],
            )
            self.assertEqual(len(warnings), 1)
            self.assertIn("symbolic links are not accepted", warnings[0])

    def test_skips_light_symlink_resolving_inside_selected_source_before_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            folder = source / "M 51_sub"
            shared = source / "shared"
            folder.mkdir(parents=True)
            shared.mkdir()
            safe = make_light(
                folder,
                "Light_M 51_10.0s_IRCUT_20260728-233258.fit",
            )
            target = make_light(shared, "shared.fit")
            symlink = folder / "Light_M 51_10.0s_IRCUT_20260728-233309.fit"
            symlink.symlink_to(target)

            original_parse = stacker.parse_capture_frame
            with mock.patch.object(
                stacker,
                "parse_capture_frame",
                wraps=original_parse,
            ) as parse_capture_frame:
                groups, warnings = stacker.collect_capture_groups(folder, source)

            self.assertEqual(len(groups), 1)
            self.assertEqual([frame.path for frame in groups[0].frames], [safe.resolve()])
            self.assertEqual(
                [call.args[0] for call in parse_capture_frame.call_args_list],
                [safe.resolve()],
            )
            self.assertEqual(len(warnings), 1)
            self.assertIn("symbolic links are not accepted", warnings[0])

    def test_skips_resolved_regular_light_outside_source_before_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            outside_folder = root / "outside" / "M 51_sub"
            source.mkdir()
            outside_folder.mkdir(parents=True)
            make_light(
                outside_folder,
                "Light_M 51_10.0s_IRCUT_20260728-233258.fit",
            )
            linked_folder = source / "M 51_sub"
            linked_folder.symlink_to(outside_folder, target_is_directory=True)

            with mock.patch.object(stacker, "parse_capture_frame") as parse_capture_frame:
                groups, warnings = stacker.collect_capture_groups(linked_folder, source)

            self.assertEqual(groups, [])
            parse_capture_frame.assert_not_called()
            self.assertEqual(len(warnings), 1)
            self.assertIn("resolves outside the selected source root", warnings[0])
            self.assertIn(str(source.resolve()), warnings[0])

    def test_discovers_recursive_targets_and_skips_mosaics(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "MyWorks" / "M 51_sub").mkdir(parents=True)
            (source / "MyWorks" / "M 31_mosaic_sub").mkdir()
            (source / "Second device" / "M 51_sub").mkdir(parents=True)
            (source / "not-a-target").mkdir()

            folders, warnings = stacker.discover_target_folders(source, ["M*"], False)

            self.assertEqual(
                [str(folder.relative_to(source)) for folder in folders],
                ["MyWorks/M 51_sub", "Second device/M 51_sub"],
            )
            self.assertEqual(len(warnings), 1)
            self.assertIn("mosaic", warnings[0])

    def test_parent_source_requires_target_or_all(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "M 51_sub").mkdir()

            folders, warnings = stacker.discover_target_folders(source)

            self.assertEqual(folders, [])
            self.assertIn("--target", warnings[0])


class ScriptTests(unittest.TestCase):
    def test_stack_script_uses_isolated_two_pass_pipeline_and_dark_preview(self):
        run_dir = Path("/tmp/target with spaces/run")

        script = stacker.render_stack_script(
            run_dir,
            quality_mode="balanced",
            brightness=0.08,
            shadow_sigma=2.5,
            jpeg_quality=95,
            preview_rotation=180,
            preview_flip="none",
        )

        self.assertIn("convert light_ -debayer -out=../process", script)
        self.assertIn("register light_ -2pass -transf=homography", script)
        self.assertIn("seqapplyreg light_ -framing=min -filter-round=2.5k", script)
        self.assertIn("-norm=addscale -weight=nbstars -output_norm -rgb_equal -32b", script)
        self.assertIn("-out=../../stacked_linear", script)
        self.assertIn("rotatePi", script)
        self.assertIn("autostretch -2.500000 0.080000", script)
        self.assertLess(script.index("load "), script.index("rotatePi"))
        self.assertLess(script.index("rotatePi"), script.index("autostretch"))

    def test_stack_script_can_use_center_of_gravity_framing(self):
        script = stacker.render_stack_script(
            Path("/tmp/run"),
            quality_mode="keep-all",
            brightness=0.08,
            shadow_sigma=2.5,
            jpeg_quality=95,
            framing_mode="cog",
        )

        self.assertIn("seqapplyreg light_ -framing=cog", script)

    def test_retry_script_skips_conversion_and_registration(self):
        script = stacker.render_retry_framing_script(
            Path("/tmp/run"),
            output_prefix="retry-cog",
            quality_mode="balanced",
            brightness=0.08,
            shadow_sigma=2.5,
            jpeg_quality=95,
            framing_mode="cog",
            preview_rotation=180,
            preview_flip="none",
        )

        self.assertIn("seqapplyreg light_ -framing=cog -filter-round=2.5k", script)
        self.assertIn("stack r_light_", script)
        self.assertIn("rotatePi", script)
        self.assertFalse(any(line.startswith("convert ") for line in script.splitlines()))
        self.assertFalse(any(line.startswith("register ") for line in script.splitlines()))

    def test_preview_script_can_link_channels(self):
        script = stacker.render_preview_script(
            Path("/tmp/source.fit"),
            Path("/tmp/darker.jpg"),
            brightness=0.07,
            shadow_sigma=2.4,
            jpeg_quality=90,
            linked=True,
            rotation=180,
            flip="top-bottom",
        )

        self.assertIn("mirrorx\nrotatePi\nautostretch", script)
        self.assertIn("autostretch -linked -2.400000 0.070000", script)
        self.assertIn('savejpg "/tmp/darker" 90', script)

    def test_preview_transform_commands_cover_rotation_and_flip_choices(self):
        self.assertEqual(stacker.preview_transform_commands(0, "none"), [])
        self.assertEqual(stacker.preview_transform_commands(180, "none"), ["rotatePi"])
        self.assertEqual(
            stacker.preview_transform_commands(90, "left-right"),
            ["mirrory", "rotate 90 -nocrop"],
        )
        with self.assertRaises(ValueError):
            stacker.preview_transform_commands(45, "none")

    def test_preview_cli_accepts_supported_orientation_and_rejects_other_angles(self):
        args = stacker.parse_args(
            [
                "preview",
                "--input",
                "/tmp/source.fit",
                "--output",
                "/tmp/preview.jpg",
                "--rotate",
                "180",
                "--flip",
                "top-bottom",
            ]
        )
        self.assertEqual(args.rotate, 180)
        self.assertEqual(args.flip, "top-bottom")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                stacker.parse_args(
                    [
                        "preview",
                        "--input",
                        "/tmp/source.fit",
                        "--output",
                        "/tmp/preview.jpg",
                        "--rotate",
                        "45",
                    ]
                )

    def test_parses_stacked_and_registered_counts(self):
        log = "Total: 7 failed, 459 registered.\nRejection stacking complete. 457 images have been stacked.\n"

        stacked = stacker.parse_last_count(log, stacker.STACKED_COUNT_PATTERNS)
        registered = stacker.parse_last_count(log, stacker.REGISTERED_COUNT_PATTERNS)

        self.assertEqual(stacked, 457)
        self.assertEqual(registered, 459)

    def test_reads_fits_dimensions_from_primary_header_without_pixel_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "frame.fit"
            path.write_bytes(fits_bytes(1080, 1920))

            self.assertEqual(stacker.fits_image_dimensions(path), (1080, 1920))

    def test_framing_threshold_accepts_boundary_and_rejects_any_lower_axis(self):
        boundary = stacker.framing_quality((1000, 2000), (900, 2000))
        narrow = stacker.framing_quality((1000, 2000), (899, 2000))

        self.assertEqual(boundary["retained_area_ratio"], 0.9)
        self.assertEqual(boundary["retained_width_ratio"], 0.9)
        self.assertFalse(boundary["below_threshold"])
        self.assertTrue(narrow["below_threshold"])

    def test_dark_style_matches_tested_values(self):
        self.assertEqual(stacker.resolve_preview_settings("dark", None, None), (0.08, 2.5))
        with self.assertRaises(ValueError):
            stacker.resolve_preview_settings("dark", 0.0, None)

    def test_natural_style_is_the_brighter_default_for_every_preview_command(self):
        self.assertEqual(stacker.resolve_preview_settings("natural", None, None), (0.18, 2.8))
        stack_args = stacker.parse_args(
            ["stack", "--source", "/tmp/source", "--destination", "/tmp/output", "--all"]
        )
        preview_args = stacker.parse_args(
            ["preview", "--input", "/tmp/source.fit", "--output", "/tmp/preview.jpg"]
        )
        refresh_args = stacker.parse_args(
            ["refresh-previews", "--root", "/tmp/local_siril_stacks"]
        )

        self.assertEqual(stack_args.preview_style, "natural")
        self.assertEqual(preview_args.style, "natural")
        self.assertEqual(refresh_args.style, "natural")


class ExecutionTests(unittest.TestCase):
    def test_retry_dry_run_validates_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = make_failed_retry_run(Path(temporary))
            before = {
                path.relative_to(run): path.read_bytes()
                for path in run.rglob("*")
                if path.is_file()
            }

            with (
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)) as disk,
                mock.patch.object(stacker, "find_siril") as find_siril,
                mock.patch.object(stacker, "run_siril") as run_siril,
            ):
                result = stacker.main(
                    ["retry-framing", "--run", str(run), "--framing", "cog", "--dry-run"]
                )

            after = {
                path.relative_to(run): path.read_bytes()
                for path in run.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result, 0)
            self.assertEqual(after, before)
            disk.assert_called_once()
            find_siril.assert_not_called()
            run_siril.assert_not_called()

    def test_retry_success_reuses_work_preserves_originals_and_updates_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = make_failed_retry_run(Path(temporary))

            def fake_run(_siril, script_path, working_directory, log_path):
                script = script_path.read_text(encoding="utf-8")
                self.assertNotIn("\nconvert ", script)
                self.assertNotIn("\nregister ", script)
                self.assertIn("seqapplyreg light_ -framing=cog", script)
                (working_directory / "retry-cog-linear.fit").write_bytes(fits_bytes())
                (working_directory / "retry-cog-preview.jpg").write_bytes(b"jpeg")
                log_path.write_text(
                    "2 images have been stacked\n"
                    "Script execution finished successfully.\n",
                    encoding="utf-8",
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
            ):
                result = stacker.main(["retry-framing", "--run", str(run), "--framing", "cog"])

            manifest = json.loads((run / stacker.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 0)
            self.assertEqual((run / "pipeline.ssf").read_text(), "original pipeline\n")
            self.assertEqual((run / "siril.log").read_text(), "original log\n")
            self.assertEqual((run / "retry-cog.ssf").is_file(), True)
            self.assertEqual((run / "retry-cog.log").is_file(), True)
            self.assertFalse((run / "_work").exists())
            self.assertEqual(manifest["schema_version"], stacker.MANIFEST_SCHEMA_VERSION)
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["settings"]["framing_mode"], "cog")
            self.assertEqual(manifest["stacked_count"], 2)
            self.assertEqual(manifest["attempts"][-1]["status"], "success")
            self.assertEqual(manifest["attempts"][-1]["replaces_framing_mode"], "min")
            self.assertEqual(manifest["attempts"][-1]["reused_converted_fits_count"], 2)
            self.assertEqual(manifest["attempts"][-1]["registered_count_source"], "prior_manifest")
            self.assertEqual(
                manifest["attempts"][-1]["framing_quality"]["retained_area_ratio"],
                1.0,
            )
            self.assertEqual(
                manifest["framing_quality"]["output_dimensions"],
                {"width": 1080, "height": 1920},
            )
            self.assertEqual(set(manifest["output_sha256"]), {"linear_fits", "preview_jpeg"})
            self.assertEqual(len(list(run.glob("Stacked_2_M 51_10.0s_IRCUT_*.fit"))), 1)
            self.assertEqual(len(list(run.glob("Stacked_2_M 51_10.0s_IRCUT_*.jpg"))), 1)

    def test_retry_bad_counts_retains_work_and_appends_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = make_failed_retry_run(Path(temporary), schema_version=2)

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / "retry-cog-linear.fit").write_bytes(b"linear")
                (working_directory / "retry-cog-preview.jpg").write_bytes(b"jpeg")
                log_path.write_text(
                    "2 images registered\n1 images have been stacked\n"
                    "Script execution finished successfully.\n",
                    encoding="utf-8",
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
            ):
                result = stacker.main(["retry-framing", "--run", str(run)])

            manifest = json.loads((run / stacker.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(result, 1)
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("invalid frame counts", manifest["last_retry_error"])
            self.assertEqual(manifest["attempts"][-1]["status"], "failed")
            self.assertTrue((run / "_work" / stacker.WORK_MARKER).is_file())
            self.assertEqual((run / "pipeline.ssf").read_text(), "original pipeline\n")
            self.assertEqual((run / "siril.log").read_text(), "original log\n")

    def test_retry_rejects_process_symlink_outside_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = make_failed_retry_run(root)
            outside = root / "outside"
            (run / "_work" / "process").rename(outside)
            (run / "_work" / "process").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "real directory"):
                stacker.load_framing_retry(run, "cog")

    def test_stack_refuses_duplicate_cache_and_prints_exact_retry_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = source / "M 51_sub"
            destination = root / "output"
            target.mkdir(parents=True)
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(target)[0][0]
            failed_run = make_failed_retry_run(destination)
            bind_failed_run_to_group(failed_run, group)
            base_args = [
                "stack",
                "--source",
                str(source),
                "--destination",
                str(destination),
                "--all",
                "--min-frames",
                "2",
            ]
            stderr = io.StringIO()

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version") as siril_version,
                mock.patch.object(stacker, "disk_preflight") as disk_preflight,
                mock.patch.object(stacker, "run_group") as run_group,
                contextlib.redirect_stderr(stderr),
            ):
                result = stacker.main(base_args)

            self.assertEqual(result, 2)
            siril_version.assert_not_called()
            disk_preflight.assert_not_called()
            run_group.assert_not_called()
            self.assertEqual(len(list(destination.rglob(stacker.MANIFEST_NAME))), 1)
            guidance = stderr.getvalue()
            self.assertIn("refusing to create a fresh multi-GB work cache", guidance)
            self.assertIn(stacker.retry_framing_command(failed_run.resolve()), guidance)
            self.assertIn("--fresh-after-failure", guidance)

    def test_fresh_after_failure_explicitly_allows_new_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = source / "M 51_sub"
            destination = root / "output"
            target.mkdir(parents=True)
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(target)[0][0]
            failed_run = make_failed_retry_run(destination)
            bind_failed_run_to_group(failed_run, group)
            stderr = io.StringIO()

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_group", return_value=True) as run_group,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
            ):
                result = stacker.main(
                    [
                        "stack",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--all",
                        "--min-frames",
                        "2",
                        "--fresh-after-failure",
                    ]
                )

            self.assertEqual(result, 0)
            run_group.assert_called_once()
            self.assertIn("additional working space will be used", stderr.getvalue())

    def test_unowned_failed_work_does_not_trigger_storage_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            failed_run = make_failed_retry_run(destination)
            bind_failed_run_to_group(failed_run, group)
            (failed_run / "_work" / stacker.WORK_MARKER).write_text(
                "not owned by this tool\n", encoding="utf-8"
            )

            self.assertEqual(
                stacker.find_retained_failed_runs(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                ),
                (),
            )

    def test_retained_failure_requires_same_source_and_ordered_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "output"
            source_a = root / "source-a" / "M 51_sub"
            source_b = root / "source-b" / "M 51_sub"
            source_a.mkdir(parents=True)
            source_b.mkdir(parents=True)
            names = (
                "Light_M 51_10.0s_IRCUT_20260728-233258.fit",
                "Light_M 51_10.0s_IRCUT_20260728-233309.fit",
            )
            for name in names:
                first = make_light(source_a, name)
                second = make_light(source_b, name)
                first_stat = first.stat()
                os.utime(second, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))

            group_a = stacker.collect_capture_groups(source_a)[0][0]
            group_b = stacker.collect_capture_groups(source_b)[0][0]
            fingerprint = stacker.inventory_fingerprint(group_a)
            self.assertEqual(fingerprint, stacker.inventory_fingerprint(group_b))
            failed_run = make_failed_retry_run(destination)
            bind_failed_run_to_group(failed_run, group_a)

            self.assertEqual(
                stacker.find_retained_failed_runs(group_a, destination, fingerprint),
                (failed_run.resolve(),),
            )
            self.assertEqual(
                stacker.find_retained_failed_runs(group_b, destination, fingerprint),
                (),
            )
            manifest_path = failed_run / stacker.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["inputs"] = list(reversed(manifest["inputs"]))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(
                stacker.find_retained_failed_runs(group_a, destination, fingerprint),
                (),
            )

    def test_schema_one_retained_failure_never_matches_automatically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            failed_run = make_failed_retry_run(destination)
            bind_failed_run_to_group(failed_run, group, schema_version=1)

            self.assertEqual(
                stacker.find_retained_failed_runs(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                ),
                (),
            )

    def test_dry_run_writes_nothing_and_does_not_launch_siril(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "astro"
            target = source / "M 51_sub"
            destination = root / "outputs"
            target.mkdir(parents=True)
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_siril") as run_siril,
            ):
                result = stacker.main(
                    [
                        "stack",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--target",
                        "M 51",
                        "--min-frames",
                        "2",
                        "--dry-run",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertFalse(destination.exists())
            run_siril.assert_not_called()

    def test_successful_group_run_keeps_outputs_and_removes_only_owned_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            first_payload = fits_bytes() + b"original-one"
            second_payload = fits_bytes() + b"original-two"
            first = make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit", first_payload)
            second = make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit", second_payload)
            groups, _ = stacker.collect_capture_groups(source)

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / "stacked_linear.fit").write_bytes(fits_bytes())
                (working_directory / "preview.jpg").write_bytes(b"jpeg")
                log_path.write_text(
                    "2 images registered\n2 images have been stacked\n"
                    "Script execution finished successfully.\n",
                    encoding="utf-8",
                )
                return 0

            with mock.patch.object(stacker, "run_siril", side_effect=fake_run) as run_siril:
                success = stacker.run_group(
                    groups[0],
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="balanced",
                    preview_style="dark",
                    brightness=0.08,
                    shadow_sigma=2.5,
                    jpeg_quality=95,
                    preview_rotation=180,
                    preview_flip="none",
                    keep_work=False,
                )

            self.assertTrue(success)
            run_dirs = list(destination.glob("M_51/*/*"))
            self.assertEqual(len(run_dirs), 1)
            run_dir = run_dirs[0]
            self.assertFalse((run_dir / "_work").exists())
            self.assertEqual(len(list(run_dir.glob("Stacked_2_*.fit"))), 1)
            self.assertEqual(len(list(run_dir.glob("Stacked_2_*.jpg"))), 1)
            manifest = json.loads((run_dir / stacker.MANIFEST_NAME).read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["stacked_count"], 2)
            self.assertEqual(manifest["schema_version"], stacker.MANIFEST_SCHEMA_VERSION)
            self.assertEqual(set(manifest["output_sha256"]), {"linear_fits", "preview_jpeg"})
            self.assertEqual(manifest["settings"]["preview_rotation_degrees"], 180)
            self.assertEqual(run_siril.call_count, 1)
            self.assertEqual(
                manifest["framing_quality"]["source_dimensions"],
                {"width": 1080, "height": 1920},
            )
            self.assertEqual(manifest["framing_quality"]["retained_area_ratio"], 1.0)
            self.assertFalse(manifest["framing_quality"]["below_threshold"])
            self.assertNotIn("framing_fallback", manifest)
            self.assertEqual(first.read_bytes(), first_payload)
            self.assertEqual(second.read_bytes(), second_payload)

    def test_severely_cropped_min_stack_automatically_reuses_work_with_cog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "SH2-142_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            first = make_light(source, "Light_SH2-142_10.0s_LP_20260920-210000.fit")
            second = make_light(source, "Light_SH2-142_10.0s_LP_20260920-210010.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            calls: list[str] = []

            def fake_run(_siril, script_path, working_directory, log_path):
                calls.append(script_path.name)
                if script_path.name == "pipeline.ssf":
                    process = working_directory / "_work" / "process"
                    (process / "light_00001.fit").write_bytes(fits_bytes())
                    (process / "light_00002.fit").write_bytes(fits_bytes())
                    (process / "light_.seq").write_text(
                        "#Siril sequence file\nS 'light_' 1 2 2 5 1 6 0 0 0\n",
                        encoding="utf-8",
                    )
                    (working_directory / "stacked_linear.fit").write_bytes(
                        fits_bytes(183, 1239)
                    )
                    (working_directory / "preview.jpg").write_bytes(b"cropped jpeg")
                    log_path.write_text(
                        "2 images registered\n2 images have been stacked\n"
                        "Script execution finished successfully.\n",
                        encoding="utf-8",
                    )
                else:
                    self.assertEqual(script_path.name, "retry-cog.ssf")
                    (working_directory / "retry-cog-linear.fit").write_bytes(fits_bytes())
                    (working_directory / "retry-cog-preview.jpg").write_bytes(b"cog jpeg")
                    log_path.write_text(
                        "2 images have been stacked\n"
                        "Script execution finished successfully.\n",
                        encoding="utf-8",
                    )
                return 0

            with mock.patch.object(stacker, "run_siril", side_effect=fake_run):
                success = stacker.run_group(
                    group,
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="balanced",
                    preview_style="natural",
                    brightness=0.18,
                    shadow_sigma=2.8,
                    jpeg_quality=95,
                    preview_rotation=0,
                    preview_flip="none",
                    keep_work=False,
                )

            self.assertTrue(success)
            self.assertEqual(calls, ["pipeline.ssf", "retry-cog.ssf"])
            manifest_path = next(destination.rglob(stacker.MANIFEST_NAME))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_dir = manifest_path.parent
            self.assertEqual(manifest["status"], "success")
            self.assertEqual(manifest["settings"]["framing_mode"], "cog")
            self.assertEqual(manifest["framing_fallback"]["from"], "min")
            self.assertEqual(manifest["framing_fallback"]["to"], "cog")
            self.assertEqual(
                [attempt["status"] for attempt in manifest["attempts"]],
                ["rejected_by_framing_guard", "success"],
            )
            self.assertLess(
                manifest["attempts"][0]["framing_quality"]["retained_area_ratio"],
                stacker.MIN_FRAMING_AREA_RETENTION,
            )
            self.assertEqual(manifest["framing_quality"]["retained_area_ratio"], 1.0)
            self.assertTrue((run_dir / "initial-min-linear.fit").is_file())
            self.assertTrue((run_dir / "initial-min-preview.jpg").is_file())
            self.assertFalse((run_dir / "_work").exists())
            self.assertEqual(first.read_bytes(), fits_bytes())
            self.assertEqual(second.read_bytes(), fits_bytes())

    def test_automatic_framing_retry_that_is_still_cropped_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "SH2-142_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_SH2-142_10.0s_LP_20260920-210000.fit")
            make_light(source, "Light_SH2-142_10.0s_LP_20260920-210010.fit")
            group = stacker.collect_capture_groups(source)[0][0]

            def fake_run(_siril, script_path, working_directory, log_path):
                if script_path.name == "pipeline.ssf":
                    process = working_directory / "_work" / "process"
                    (process / "light_00001.fit").write_bytes(fits_bytes())
                    (process / "light_00002.fit").write_bytes(fits_bytes())
                    (process / "light_.seq").write_text(
                        "#Siril sequence file\nS 'light_' 1 2 2 5 1 6 0 0 0\n",
                        encoding="utf-8",
                    )
                    (working_directory / "stacked_linear.fit").write_bytes(
                        fits_bytes(183, 1239)
                    )
                    (working_directory / "preview.jpg").write_bytes(b"cropped jpeg")
                    log_path.write_text(
                        "2 images registered\n2 images have been stacked\n"
                        "Script execution finished successfully.\n",
                        encoding="utf-8",
                    )
                else:
                    (working_directory / "retry-cog-linear.fit").write_bytes(
                        fits_bytes(850, 1800)
                    )
                    (working_directory / "retry-cog-preview.jpg").write_bytes(
                        b"still cropped jpeg"
                    )
                    log_path.write_text(
                        "2 images have been stacked\n"
                        "Script execution finished successfully.\n",
                        encoding="utf-8",
                    )
                return 0

            with (
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                success = stacker.run_group(
                    group,
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="balanced",
                    preview_style="natural",
                    brightness=0.18,
                    shadow_sigma=2.8,
                    jpeg_quality=95,
                    preview_rotation=0,
                    preview_flip="none",
                    keep_work=False,
                )

            manifest_path = next(destination.rglob(stacker.MANIFEST_NAME))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_dir = manifest_path.parent
            self.assertFalse(success)
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(
                [attempt["status"] for attempt in manifest["attempts"]],
                ["rejected_by_framing_guard", "failed"],
            )
            self.assertTrue(manifest["attempts"][-1]["framing_quality"]["below_threshold"])
            self.assertIn("full-field framing guard", manifest["last_retry_error"])
            self.assertTrue((run_dir / "_work").is_dir())
            self.assertTrue((run_dir / "retry-cog-linear.fit").is_file())
            self.assertTrue((run_dir / "retry-cog-preview.jpg").is_file())
            self.assertEqual(list(run_dir.glob("Stacked_*.fit")), [])
            self.assertEqual(list(run_dir.glob("Stacked_*.jpg")), [])

    def test_non_min_stack_records_narrow_dimensions_without_auto_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / "stacked_linear.fit").write_bytes(fits_bytes(400, 1000))
                (working_directory / "preview.jpg").write_bytes(b"jpeg")
                log_path.write_text(
                    "2 images registered\n2 images have been stacked\n"
                    "Script execution finished successfully.\n",
                    encoding="utf-8",
                )
                return 0

            with mock.patch.object(stacker, "run_siril", side_effect=fake_run) as run_siril:
                success = stacker.run_group(
                    group,
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="keep-all",
                    preview_style="natural",
                    brightness=0.18,
                    shadow_sigma=2.8,
                    jpeg_quality=95,
                    preview_rotation=0,
                    preview_flip="none",
                    framing_mode="current",
                    keep_work=False,
                )

            manifest_path = next(destination.rglob(stacker.MANIFEST_NAME))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(success)
            self.assertEqual(run_siril.call_count, 1)
            self.assertTrue(manifest["framing_quality"]["below_threshold"])
            self.assertEqual(manifest["framing_quality"]["framing_mode"], "current")
            self.assertNotIn("framing_fallback", manifest)

    def test_refuses_to_remove_unmarked_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "_work"
            work.mkdir()
            (work / "valuable.fit").write_bytes(b"keep")

            with self.assertRaises(RuntimeError):
                stacker.remove_owned_work_directory(work)

            self.assertTrue((work / "valuable.fit").exists())

    def test_impossible_siril_counts_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / "stacked_linear.fit").write_bytes(b"linear")
                (working_directory / "preview.jpg").write_bytes(b"jpeg")
                log_path.write_text(
                    "459 images registered\n459 images have been stacked\n"
                    "Script execution finished successfully.\n",
                    encoding="utf-8",
                )
                return 0

            with mock.patch.object(stacker, "run_siril", side_effect=fake_run):
                success = stacker.run_group(
                    group,
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="balanced",
                    preview_style="dark",
                    brightness=0.08,
                    shadow_sigma=2.5,
                    jpeg_quality=95,
                    preview_rotation=0,
                    preview_flip="none",
                    keep_work=False,
                )

            self.assertFalse(success)
            manifest_path = next(destination.rglob(stacker.MANIFEST_NAME))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("invalid frame counts", manifest["error"])
            self.assertTrue((manifest_path.parent / "_work").is_dir())

    def test_completed_matching_run_is_found_and_changed_settings_are_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            fingerprint = stacker.inventory_fingerprint(group)
            settings = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="dark",
                brightness=0.08,
                shadow_sigma=2.5,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                minimum_stacked_frames=2,
            )
            manifest_path = make_successful_run(
                destination,
                group,
                settings,
                run_name="run",
                finished_at="2026-09-25T12:00:00Z",
            )

            found = stacker.find_completed_run(group, destination, fingerprint, settings)
            changed = dict(settings, preview_brightness=0.06)

            self.assertEqual(found, manifest_path)
            self.assertIsNone(stacker.find_completed_run(group, destination, fingerprint, changed))
            legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
            legacy["settings"].pop("framing_mode")
            manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(
                stacker.find_completed_run(group, destination, fingerprint, settings),
                manifest_path,
            )

    def test_successful_framing_retry_resumes_default_min_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            retried = dict(
                requested,
                framing="center of gravity",
                framing_mode="cog",
            )
            manifest_path = make_successful_run(
                destination,
                group,
                retried,
                run_name="retry-cog",
                finished_at="2026-09-25T13:00:00Z",
                retry_for="min",
                legacy_retry_provenance=True,
            )
            fingerprint = stacker.inventory_fingerprint(group)

            self.assertEqual(
                stacker.find_completed_run(group, destination, fingerprint, requested),
                manifest_path,
            )
            changed = dict(requested, preview_brightness=0.06)
            current = dict(requested, framing="reference image", framing_mode="current")
            self.assertIsNone(
                stacker.find_completed_run(group, destination, fingerprint, changed)
            )
            self.assertIsNone(
                stacker.find_completed_run(group, destination, fingerprint, current)
            )

    def test_direct_or_tampered_cog_run_does_not_resume_min(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="dark",
                brightness=0.08,
                shadow_sigma=2.5,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            cog = dict(requested, framing="center of gravity", framing_mode="cog")
            make_successful_run(
                destination,
                group,
                cog,
                run_name="direct-cog",
                finished_at="2026-09-25T13:00:00Z",
            )
            tampered = make_successful_run(
                destination,
                group,
                cog,
                run_name="tampered-retry",
                finished_at="2026-09-25T14:00:00Z",
                retry_for="min",
            )
            payload = json.loads(tampered.read_text(encoding="utf-8"))
            payload["attempts"][-1]["output_sha256"]["preview_jpeg"] = "0" * 64
            tampered.write_text(json.dumps(payload), encoding="utf-8")

            self.assertIsNone(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                )
            )

    def test_exact_framing_match_is_preferred_over_newer_retry_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="dark",
                brightness=0.08,
                shadow_sigma=2.5,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            exact = make_successful_run(
                destination,
                group,
                requested,
                run_name="older-exact-min",
                finished_at="2026-09-25T12:00:00Z",
            )
            cog = dict(requested, framing="center of gravity", framing_mode="cog")
            make_successful_run(
                destination,
                group,
                cog,
                run_name="newer-retry-cog",
                finished_at="2026-09-25T14:00:00Z",
                retry_for="min",
            )

            self.assertEqual(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                ),
                exact,
            )

    def test_cropped_exact_min_is_ignored_in_favor_of_safe_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "SH2-142_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_SH2-142_20.0s_LP_20260923-220139.fit")
            make_light(source, "Light_SH2-142_20.0s_LP_20260923-220159.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            make_successful_run(
                destination,
                group,
                requested,
                run_name="cropped-exact-min",
                finished_at="2026-09-25T12:00:00Z",
                output_dimensions=(183, 1239),
            )
            fallback_settings = dict(
                requested,
                framing="center of gravity",
                framing_mode="cog",
            )
            fallback = make_successful_run(
                destination,
                group,
                fallback_settings,
                run_name="safe-retry-cog",
                finished_at="2026-09-25T13:00:00Z",
                retry_for="min",
            )

            self.assertEqual(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                ),
                fallback,
            )

    def test_default_min_resume_fails_closed_when_dimensions_are_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            manifest_path = make_successful_run(
                destination,
                group,
                requested,
                run_name="legacy-unreadable-linear",
                finished_at="2026-09-25T12:00:00Z",
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            Path(payload["outputs"]["linear_fits"]).write_bytes(b"not a FITS header")
            payload["schema_version"] = 1
            payload.pop("output_sha256")
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            self.assertIsNone(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                )
            )

    def test_force_bypasses_resume_compatible_framing_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = source / "M 51_sub"
            destination = root / "output"
            target.mkdir(parents=True)
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(target, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(target)[0][0]
            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            cog = dict(requested, framing="center of gravity", framing_mode="cog")
            make_successful_run(
                destination,
                group,
                cog,
                run_name="retry-cog",
                finished_at="2026-09-25T14:00:00Z",
                retry_for="min",
            )
            base_args = [
                "stack",
                "--source",
                str(source),
                "--destination",
                str(destination),
                "--all",
                "--min-frames",
                "2",
            ]

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_group", return_value=True) as run_group,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(stacker.main(base_args), 0)
                run_group.assert_not_called()
                self.assertEqual(stacker.main([*base_args, "--force"]), 0)
                run_group.assert_called_once()

    def test_resume_requires_the_same_source_folder_and_ordered_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "output"
            source_a = root / "source-a" / "M 51_sub"
            source_b = root / "source-b" / "M 51_sub"
            source_a.mkdir(parents=True)
            source_b.mkdir(parents=True)
            names = (
                "Light_M 51_10.0s_IRCUT_20260728-233258.fit",
                "Light_M 51_10.0s_IRCUT_20260728-233309.fit",
            )
            for index, name in enumerate(names):
                first = make_light(source_a, name, fits_bytes() + bytes([65 + index]))
                second = make_light(source_b, name, fits_bytes() + bytes([67 + index]))
                first_stat = first.stat()
                os.utime(second, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))

            group_a = stacker.collect_capture_groups(source_a)[0][0]
            group_b = stacker.collect_capture_groups(source_b)[0][0]
            fingerprint = stacker.inventory_fingerprint(group_a)
            self.assertEqual(fingerprint, stacker.inventory_fingerprint(group_b))
            settings = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                minimum_stacked_frames=2,
            )
            manifest_path = make_successful_run(
                destination,
                group_a,
                settings,
                run_name="source-a",
                finished_at="2026-09-25T12:00:00Z",
            )

            self.assertEqual(
                stacker.find_completed_run(group_a, destination, fingerprint, settings),
                manifest_path,
            )
            self.assertIsNone(
                stacker.find_completed_run(group_b, destination, fingerprint, settings)
            )

    def test_schema_one_success_never_resumes_automatically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            source.mkdir(parents=True)
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            settings = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                minimum_stacked_frames=2,
            )
            manifest_path = make_successful_run(
                destination,
                group,
                settings,
                run_name="legacy",
                finished_at="2026-09-25T12:00:00Z",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 1
            manifest.pop("output_sha256")
            Path(manifest["outputs"]["preview_jpeg"]).write_bytes(b"altered but nonempty")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            self.assertIsNone(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    settings,
                )
            )

    def test_fresh_stack_rejects_symlinked_target_output_before_launching_siril(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "M 51_sub"
            destination = root / "output"
            outside = root / "outside"
            source.mkdir(parents=True)
            destination.mkdir()
            outside.mkdir()
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
            make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
            group = stacker.collect_capture_groups(source)[0][0]
            (destination / "M_51").symlink_to(outside, target_is_directory=True)

            with (
                mock.patch.object(stacker, "run_siril") as run_siril,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                success = stacker.run_group(
                    group,
                    destination,
                    Path("/fake/siril-cli"),
                    "siril 1.4.3",
                    quality_mode="balanced",
                    preview_style="natural",
                    brightness=0.18,
                    shadow_sigma=2.8,
                    jpeg_quality=95,
                    preview_rotation=0,
                    preview_flip="none",
                    keep_work=False,
                    minimum_stacked_frames=2,
                )

            self.assertFalse(success)
            run_siril.assert_not_called()
            self.assertEqual(list(outside.iterdir()), [])

    def test_batch_stops_after_first_group_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "output"
            for target in ("M 31", "M 51"):
                folder = source / f"{target}_sub"
                folder.mkdir(parents=True)
                make_light(folder, f"Light_{target}_10.0s_IRCUT_20260728-233258.fit")
                make_light(folder, f"Light_{target}_10.0s_IRCUT_20260728-233309.fit")

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", return_value=(True, 10**12)),
                mock.patch.object(stacker, "run_group", side_effect=[False, True]) as run_group,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    [
                        "stack",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--all",
                        "--min-frames",
                        "2",
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(run_group.call_count, 1)

    def test_batch_rechecks_disk_before_each_group_and_stops_when_space_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "output"
            for target in ("M 31", "M 51"):
                folder = source / f"{target}_sub"
                folder.mkdir(parents=True)
                make_light(folder, f"Light_{target}_10.0s_IRCUT_20260728-233258.fit")
                make_light(folder, f"Light_{target}_10.0s_IRCUT_20260728-233309.fit")

            disk_results = [
                (True, 10**12),
                (True, 10**12),
                (True, 10**12),
                (False, 0),
            ]
            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "disk_preflight", side_effect=disk_results) as disk,
                mock.patch.object(stacker, "run_group", return_value=True) as run_group,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    [
                        "stack",
                        "--source",
                        str(source),
                        "--destination",
                        str(destination),
                        "--all",
                        "--min-frames",
                        "2",
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(disk.call_count, 4)
            self.assertEqual(run_group.call_count, 1)


class PreviewRefreshTests(unittest.TestCase):
    def make_retry_success(self, root: Path):
        source = root / "source" / "M 51_sub"
        destination = root / "output"
        source.mkdir(parents=True)
        make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233258.fit")
        make_light(source, "Light_M 51_10.0s_IRCUT_20260728-233309.fit")
        group = stacker.collect_capture_groups(source)[0][0]
        requested = stacker.stack_settings(
            quality_mode="balanced",
            preview_style="dark",
            brightness=0.08,
            shadow_sigma=2.5,
            jpeg_quality=95,
            preview_rotation=180,
            preview_flip="none",
            framing_mode="min",
            minimum_stacked_frames=2,
        )
        cog = dict(requested, framing="center of gravity", framing_mode="cog")
        manifest_path = make_successful_run(
            destination,
            group,
            cog,
            run_name="retry-cog",
            finished_at="2026-09-25T14:00:00Z",
            retry_for="min",
        )
        return destination, group, manifest_path

    def test_refresh_dry_run_validates_everything_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, _manifest_path = self.make_retry_success(Path(temporary))
            before = {
                path.relative_to(destination): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file()
            }

            with (
                mock.patch.object(stacker, "find_siril") as find_siril,
                mock.patch.object(stacker, "run_siril") as run_siril,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2", "--dry-run"]
                )

            after = {
                path.relative_to(destination): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(result, 0)
            self.assertEqual(after, before)
            find_siril.assert_not_called()
            run_siril.assert_not_called()

    def test_refresh_switches_preview_preserves_linear_and_retry_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, group, manifest_path = self.make_retry_success(Path(temporary))
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
            linear = Path(original["outputs"]["linear_fits"])
            old_preview = Path(original["outputs"]["preview_jpeg"])
            linear_bytes = linear.read_bytes()

            def fake_run(_siril, script_path, working_directory, log_path):
                script = script_path.read_text(encoding="utf-8")
                self.assertIn(str(linear), script)
                self.assertIn("rotatePi", script)
                self.assertIn("autostretch -2.800000 0.180000", script)
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(
                    b"brighter natural preview"
                )
                log_path.write_text(
                    "Script execution finished successfully.\n", encoding="utf-8"
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                )

            refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
            new_preview = Path(refreshed["outputs"]["preview_jpeg"])
            self.assertEqual(result, 0)
            self.assertEqual(linear.read_bytes(), linear_bytes)
            self.assertTrue(old_preview.is_file())
            self.assertEqual(new_preview.name, "preview-refresh-natural.jpg")
            self.assertEqual(new_preview.read_bytes(), b"brighter natural preview")
            self.assertEqual(refreshed["settings"]["preview_style"], "natural")
            self.assertEqual(refreshed["settings"]["preview_brightness"], 0.18)
            self.assertEqual(refreshed["settings"]["preview_shadow_sigma"], 2.8)
            provenance = refreshed["preview_refreshes"][-1]
            self.assertEqual(provenance["kind"], "preview_refresh")
            self.assertEqual(provenance["framing_mode"], "cog")
            self.assertEqual(
                provenance["source"]["linear_fits_sha256"],
                stacker.sha256_file(linear),
            )
            self.assertEqual(
                refreshed["output_sha256"]["preview_jpeg"],
                stacker.sha256_file(new_preview),
            )

            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=180,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            self.assertEqual(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                ),
                manifest_path,
            )

            refreshed["preview_refreshes"][-1]["source"]["linear_fits_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(refreshed), encoding="utf-8")
            self.assertIsNone(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                )
            )

    def test_refresh_migrates_a_verified_schema_one_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, group, manifest_path = self.make_retry_success(Path(temporary))
            legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
            legacy["schema_version"] = 1
            legacy.pop("output_sha256")
            legacy.pop("attempts")
            legacy["settings"]["framing"] = "minimum common area"
            legacy["settings"].pop("framing_mode")
            manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
            linear = Path(legacy["outputs"]["linear_fits"])
            old_preview = Path(legacy["outputs"]["preview_jpeg"])

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(
                    b"natural schema-one preview"
                )
                log_path.write_text(
                    "Script execution finished successfully.\n", encoding="utf-8"
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                )

            migrated = json.loads(manifest_path.read_text(encoding="utf-8"))
            new_preview = Path(migrated["outputs"]["preview_jpeg"])
            self.assertEqual(result, 0)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(migrated["settings"]["framing_mode"], "min")
            self.assertEqual(migrated["settings"]["minimum_stacked_frames"], 2)
            self.assertEqual(
                migrated["output_sha256"]["linear_fits"], stacker.sha256_file(linear)
            )
            self.assertEqual(
                migrated["output_sha256"]["preview_jpeg"], stacker.sha256_file(new_preview)
            )
            self.assertEqual(
                migrated["preview_refreshes"][-1]["manifest_schema_version_before"], 1
            )
            self.assertTrue(old_preview.is_file())

            requested = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="natural",
                brightness=0.18,
                shadow_sigma=2.8,
                jpeg_quality=95,
                preview_rotation=180,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            self.assertEqual(
                stacker.find_completed_run(
                    group,
                    destination,
                    stacker.inventory_fingerprint(group),
                    requested,
                ),
                manifest_path,
            )

    def test_refresh_failure_leaves_manifest_and_linear_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, manifest_path = self.make_retry_success(Path(temporary))
            before_manifest = manifest_path.read_bytes()
            payload = json.loads(before_manifest)
            linear = Path(payload["outputs"]["linear_fits"])
            before_linear = linear.read_bytes()

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(b"partial")
                log_path.write_text("Siril failed\n", encoding="utf-8")
                return 1

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                )

            self.assertEqual(result, 1)
            self.assertEqual(manifest_path.read_bytes(), before_manifest)
            self.assertEqual(linear.read_bytes(), before_linear)

    def test_refresh_manifest_write_failure_keeps_the_previous_selection_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, manifest_path = self.make_retry_success(Path(temporary))
            before_manifest = manifest_path.read_bytes()
            old_preview = Path(json.loads(before_manifest)["outputs"]["preview_jpeg"])

            def fake_run(_siril, _script_path, working_directory, log_path):
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(
                    b"unselected new preview"
                )
                log_path.write_text(
                    "Script execution finished successfully.\n", encoding="utf-8"
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=fake_run),
                mock.patch.object(stacker, "write_json", side_effect=OSError("disk error")),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                )

            self.assertEqual(result, 1)
            self.assertEqual(manifest_path.read_bytes(), before_manifest)
            self.assertTrue(old_preview.is_file())
            self.assertEqual(
                stacker.sha256_file(old_preview),
                json.loads(before_manifest)["output_sha256"]["preview_jpeg"],
            )

    def test_refresh_rechecks_linear_and_manifest_before_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, manifest_path = self.make_retry_success(Path(temporary))
            before_manifest = manifest_path.read_bytes()
            linear = Path(json.loads(before_manifest)["outputs"]["linear_fits"])

            def mutate_linear(_siril, _script_path, working_directory, log_path):
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(b"new")
                linear.write_bytes(b"concurrent linear change")
                log_path.write_text(
                    "Script execution finished successfully.\n", encoding="utf-8"
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=mutate_linear),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    stacker.main(
                        ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                    ),
                    1,
                )
            self.assertEqual(manifest_path.read_bytes(), before_manifest)

        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, manifest_path = self.make_retry_success(Path(temporary))

            def mutate_manifest(_siril, _script_path, working_directory, log_path):
                (working_directory / ".preview-refresh-natural.pending.jpg").write_bytes(b"new")
                changed = json.loads(manifest_path.read_text(encoding="utf-8"))
                changed["concurrent_update"] = True
                manifest_path.write_text(json.dumps(changed), encoding="utf-8")
                log_path.write_text(
                    "Script execution finished successfully.\n", encoding="utf-8"
                )
                return 0

            with (
                mock.patch.object(stacker, "find_siril", return_value=Path("/fake/siril-cli")),
                mock.patch.object(stacker, "siril_version", return_value="siril 1.4.3"),
                mock.patch.object(stacker, "run_siril", side_effect=mutate_manifest),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(
                    stacker.main(
                        ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                    ),
                    1,
                )
            preserved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(preserved["concurrent_update"])
            self.assertNotIn("preview_refreshes", preserved)

    def test_refresh_excludes_low_frame_and_failed_legacy_runs_without_failing_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, group, _manifest_path = self.make_retry_success(Path(temporary))
            settings = stacker.stack_settings(
                quality_mode="balanced",
                preview_style="dark",
                brightness=0.08,
                shadow_sigma=2.5,
                jpeg_quality=95,
                preview_rotation=0,
                preview_flip="none",
                framing_mode="min",
                minimum_stacked_frames=2,
            )
            low_path = make_successful_run(
                destination,
                group,
                settings,
                run_name="legacy-low",
                finished_at="2026-09-25T15:00:00Z",
            )
            low = json.loads(low_path.read_text(encoding="utf-8"))
            low["schema_version"] = 1
            low["stacked_count"] = 1
            low.pop("output_sha256")
            low_path.write_text(json.dumps(low), encoding="utf-8")

            failed_path = make_successful_run(
                destination,
                group,
                settings,
                run_name="legacy-failed",
                finished_at="2026-09-25T16:00:00Z",
            )
            failed = json.loads(failed_path.read_text(encoding="utf-8"))
            failed["schema_version"] = 1
            failed["status"] = "failed"
            failed.pop("output_sha256")
            failed_path.write_text(json.dumps(failed), encoding="utf-8")
            low_before = low_path.read_bytes()
            failed_before = failed_path.read_bytes()

            with (
                mock.patch.object(stacker, "find_siril") as find_siril,
                mock.patch.object(stacker, "run_siril") as run_siril,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    [
                        "refresh-previews",
                        "--root",
                        str(destination),
                        "--min-frames",
                        "2",
                        "--dry-run",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(low_path.read_bytes(), low_before)
            self.assertEqual(failed_path.read_bytes(), failed_before)
            find_siril.assert_not_called()
            run_siril.assert_not_called()

    def test_refresh_reports_malformed_manifest_as_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination, _group, _manifest_path = self.make_retry_success(Path(temporary))
            malformed = destination / "broken" / stacker.MANIFEST_NAME
            malformed.parent.mkdir()
            malformed.write_text("{not-json", encoding="utf-8")

            with (
                mock.patch.object(stacker, "find_siril") as find_siril,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    [
                        "refresh-previews",
                        "--root",
                        str(destination),
                        "--min-frames",
                        "2",
                        "--dry-run",
                    ]
                )

            self.assertEqual(result, 1)
            find_siril.assert_not_called()

    def test_refresh_rejects_an_output_outside_its_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination, _group, manifest_path = self.make_retry_success(root)
            outside = root / "outside.fit"
            outside.write_bytes(b"outside")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 1
            manifest.pop("output_sha256")
            manifest["outputs"]["linear_fits"] = str(outside)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with (
                mock.patch.object(stacker, "find_siril") as find_siril,
                mock.patch.object(stacker, "run_siril") as run_siril,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = stacker.main(
                    ["refresh-previews", "--root", str(destination), "--min-frames", "2"]
                )

            self.assertEqual(result, 1)
            find_siril.assert_not_called()
            run_siril.assert_not_called()


if __name__ == "__main__":
    unittest.main()
