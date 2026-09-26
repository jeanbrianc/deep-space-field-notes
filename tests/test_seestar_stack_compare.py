import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import seestar_stack_compare as compare


def jpeg_bytes(
    *, width: int = 1080, height: int = 1920, payload: bytes = b""
) -> bytes:
    """Return a minimal JPEG header sufficient for dimension/integrity tests."""

    frame = (
        b"\xff\xc0\x00\x11\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    )
    return b"\xff\xd8" + frame + b"\xff\xd9" + payload


def make_gallery(root: Path, filenames: list[str]) -> Path:
    gallery = root / "gallery"
    images = gallery / "public" / "images"
    images.mkdir(parents=True)
    (gallery / "app").mkdir()
    quoted = ",\n".join(f'  "{filename}"' for filename in filenames)
    (gallery / "app" / "page.tsx").write_text(f"const imageFiles = [\n{quoted}\n];\n", encoding="utf-8")
    for filename in filenames:
        (images / filename).write_bytes(jpeg_bytes(payload=f"site:{filename}".encode()))
    return gallery


def write_cull_groups(gallery: Path, groups: list[dict[str, object]]) -> Path:
    path = gallery / "gallery_cull_groups.json"
    path.write_text(
        json.dumps({"schema_version": 1, "groups": groups}),
        encoding="utf-8",
    )
    return path


def write_public_comparison_manifest(gallery: Path) -> Path:
    comparison_root = gallery / "public" / "comparisons"
    aligned_root = comparison_root / "aligned-v1"
    aligned_root.mkdir(parents=True)
    captures: list[dict[str, object]] = []
    for position, filename in enumerate(compare.site_catalog_filenames(gallery), start=1):
        parsed = compare.SITE_FILENAME.fullmatch(filename)
        assert parsed is not None
        baseline_path = gallery / "public" / "images" / filename
        baseline_width, baseline_height = compare.image_dimensions(baseline_path)
        capture_id = f"capture-{position:024x}"
        night_path = aligned_root / f"{capture_id}.jpg"
        night_frames = max(1, int(parsed.group("frames")) - 7)
        night_path.write_bytes(
            jpeg_bytes(
                width=baseline_width,
                height=baseline_height,
                payload=f"aligned:{filename}".encode(),
            )
        )
        night_width, night_height = compare.image_dimensions(night_path)
        captures.append(
            {
                "captureId": capture_id,
                "comparisonId": f"comparison-{position:024x}",
                "object": parsed.group("object"),
                "exposure": compare.normalize_exposure(parsed.group("exposure")),
                "filter": parsed.group("filter").upper(),
                "baseline": {
                    "filename": filename,
                    "frames": int(parsed.group("frames")),
                    "treatment": parsed.group("treatment").replace("_", " "),
                    "sha256": compare.sha256_file(baseline_path),
                    "width": baseline_width,
                    "height": baseline_height,
                },
                "nightSkyAI": {
                    "filename": f"aligned-v1/{capture_id}.jpg",
                    "frames": night_frames,
                    "inputFingerprint": f"{position:064x}",
                    "firstTimestamp": parsed.group("timestamp"),
                    "lastTimestamp": parsed.group("timestamp"),
                    "nightCount": 1,
                    "sha256": compare.sha256_file(night_path),
                    "width": night_width,
                    "height": night_height,
                    "alignment": {
                        "mode": "registered-to-gallery-edit",
                        "referencePolicy": "gallery-edit-is-immutable",
                        "verification": {"passed": True},
                    },
                },
            }
        )
    manifest = comparison_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "tool": compare.TOOL_NAME,
                "createdAt": "2026-09-25T18:27:58Z",
                "captureCount": len(captures),
                "captures": captures,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def make_stack(
    root: Path,
    *,
    target: str,
    exposure: str = "10.0",
    filter_name: str = "LP",
    frames: int = 75,
    finished_at: str = "2026-09-25T12:00:00Z",
    first_timestamp: str = "20260925-210000",
    last_timestamp: str = "20260925-215000",
    preview_timestamp: str | None = None,
    preview_filename: str | None = None,
    input_fingerprint: str | None = None,
    preview_payload: bytes | None = None,
    preview_width: int = 1080,
    preview_height: int = 1920,
    input_timestamps: list[str] | None = None,
) -> Path:
    run = root / target.replace(" ", "_") / f"run-{frames}-{exposure}-{first_timestamp}"
    run.mkdir(parents=True)
    preview_timestamp = preview_timestamp or first_timestamp
    preview = run / (
        preview_filename
        or f"Stacked_{frames}_{target}_{exposure}s_{filter_name}_{preview_timestamp}.jpg"
    )
    preview.write_bytes(
        jpeg_bytes(
            width=preview_width,
            height=preview_height,
            payload=(
                preview_payload
                if preview_payload is not None
                else f"ours:{target}:{frames}:{exposure}:{first_timestamp}".encode()
            ),
        )
    )
    timestamps = input_timestamps or [first_timestamp, last_timestamp]
    manifest = {
        "schema_version": 1,
        "tool": "seestar_siril_stack",
        "status": "success",
        "capture": {
            "object": target,
            "exposure_seconds": exposure,
            "filter": filter_name,
            "first_timestamp": first_timestamp,
        },
        "stacked_count": frames,
        "registered_count": frames,
        "input_count": frames,
        "input_fingerprint": input_fingerprint
        or f"fingerprint-{target}-{frames}-{exposure}-{first_timestamp}",
        "inputs": [
            {"path": f"Light_{target}_{timestamp}.fit", "timestamp": timestamp}
            for timestamp in timestamps
        ],
        "finished_at": finished_at,
        "settings": {
            "preview_style": "dark",
            "preview_brightness": 0.08,
            "preview_shadow_sigma": 2.5,
            "preview_rotation_degrees": 0,
            "preview_flip": "none",
            "jpeg_quality": 95,
        },
        "outputs": {"preview_jpeg": str(preview)},
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return preview


class MatchingTests(unittest.TestCase):
    def test_canonicalizes_catalog_spacing_without_aliasing_or_losing_mosaic(self):
        self.assertEqual(compare.canonical_object("M31"), ("m:31", False))
        self.assertEqual(compare.canonical_object("NGC 6960"), ("ngc:6960", False))
        self.assertEqual(compare.canonical_object("mosaic_M 31"), ("m:31", True))
        self.assertNotEqual(compare.canonical_object("C 34")[0], compare.canonical_object("NGC 6960")[0])

    def test_uses_site_catalog_and_matches_exact_capture_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg",
                    "Stacked_100_M 106_10.0s_LP_20260925-215000_cleaned.jpg",
                    "Stacked_100_Unknown_10.0s_LP_20260925-215000_cleaned.jpg",
                ],
            )
            # A public leftover absent from page.tsx must not enter review.
            (gallery / "public" / "images" / "Stacked_100_M 92_10.0s_LP_20260925-215000_cleaned.jpg").write_bytes(b"stale")
            ours = root / "ours"
            make_stack(ours, target="M51", frames=90)
            make_stack(ours, target="M 51", exposure="20.0", frames=120)
            make_stack(ours, target="Unknown", frames=90)

            pairs, unpaired_site, unpaired_local, warnings = compare.discover_comparisons(gallery, ours, 50)

            self.assertEqual(warnings, [])
            self.assertEqual([pair.target for pair in pairs], ["M 51"])
            self.assertEqual([image.object_name for image in unpaired_site], ["M 106", "Unknown"])
            self.assertEqual(len(unpaired_local), 2)
            self.assertTrue(all("M 92" not in pair.target for pair in pairs))

    def test_ranks_multiple_local_runs_by_frame_count_then_finish_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=70, finished_at="2026-09-25T10:00:00Z")
            make_stack(ours, target="M51", frames=90, finished_at="2026-09-25T09:00:00Z")

            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)

            self.assertEqual([candidate.frames for candidate in pairs[0].ours], [90, 70])

    def test_pairs_site_timestamp_with_capture_session_and_processing_grace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 51_10.0s_LP_20260925-220015_cleaned.jpg",
                    "Stacked_100_M 51_10.0s_LP_20260926-010000_cleaned.jpg",
                ],
            )
            ours = root / "ours"
            make_stack(
                ours,
                target="M 51",
                frames=120,
                first_timestamp="20260925-190000",
                last_timestamp="20260925-200000",
            )
            matching = make_stack(
                ours,
                target="M51",
                frames=80,
                first_timestamp="20260925-210000",
                last_timestamp="20260925-220000",
            )
            make_stack(
                ours,
                target="M 51",
                frames=140,
                first_timestamp="20260925-230000",
                last_timestamp="20260925-235959",
            )

            pairs, unpaired_site, unpaired_local, warnings = compare.discover_comparisons(gallery, ours, 50)

            self.assertEqual(warnings, [])
            self.assertEqual(len(pairs), 1)
            self.assertEqual([candidate.path for candidate in pairs[0].ours], [matching.resolve()])
            self.assertEqual(
                [image.timestamp for image in unpaired_site],
                ["20260926-010000"],
            )
            self.assertEqual(len(unpaired_local), 2)

    def test_malformed_manifest_shapes_are_warned_and_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            valid_preview = make_stack(ours, target="M 51", frames=90)

            list_manifest = ours / "bad-list" / "manifest.json"
            list_manifest.parent.mkdir(parents=True)
            list_manifest.write_text("[]", encoding="utf-8")

            invalid_settings_manifest = valid_preview.parent / "manifest.json"
            invalid_settings = json.loads(
                invalid_settings_manifest.read_text(encoding="utf-8")
            )
            invalid_settings["settings"] = []
            invalid_settings_manifest.write_text(
                json.dumps(invalid_settings), encoding="utf-8"
            )

            pairs, unpaired_site, unpaired_local, warnings = (
                compare.discover_comparisons(gallery, ours, 50)
            )

            self.assertEqual(pairs, [])
            self.assertEqual(len(unpaired_site), 1)
            self.assertEqual(unpaired_local, [])
            self.assertEqual(len(warnings), 2)
            self.assertTrue(any("root must be an object" in warning for warning in warnings))
            self.assertTrue(any("settings must be an object" in warning for warning in warnings))

    def test_unreadable_preview_dimensions_are_warned_and_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            preview = make_stack(ours, target="M 51", frames=90)
            preview.write_bytes(b"not-an-image")

            pairs, unpaired_site, unpaired_local, warnings = (
                compare.discover_comparisons(gallery, ours, 50)
            )

            self.assertEqual(pairs, [])
            self.assertEqual(len(unpaired_site), 1)
            self.assertEqual(unpaired_local, [])
            self.assertEqual(len(warnings), 1)
            self.assertIn("not a recognized JPEG or PNG", warnings[0])


class GalleryCullTests(unittest.TestCase):
    def test_configured_groups_match_aliases_ignore_mosaic_and_apply_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            m31 = "Stacked_201_M 31_10.0s_IRCUT_20251118-191752_hand_processed.png"
            mosaic = "Stacked_61_mosaic_M 31_10.0s_IRCUT_20251223-200629_hand_processed.png"
            western_caldwell = "Stacked_136_C 34_20.0s_IRCUT_20260924-001000_cleaned.jpg"
            western_ngc = "Stacked_277_NGC 6960_10.0s_LP_20260922-221229_cleaned.jpg"
            eastern_veil = "Stacked_240_NGC 6992_10.0s_LP_20250925-213428_cleaned.jpg"
            gallery = make_gallery(
                root, [m31, mosaic, western_caldwell, western_ngc, eastern_veil]
            )
            override_relative = (
                "work/review/Stacked_61_mosaic_M 31_10.0s_IRCUT_"
                "20251223-200629_cleaned.jpg"
            )
            override = gallery / override_relative
            override.parent.mkdir(parents=True)
            override.write_bytes(jpeg_bytes(payload=b"actual mosaic review pixels"))
            groups_path = write_cull_groups(
                gallery,
                [
                    {
                        "id": "andromeda",
                        "label": "Andromeda Galaxy",
                        "objects": ["M 31"],
                        "candidate_overrides": {mosaic: override_relative},
                    },
                    {
                        "id": "western-veil",
                        "label": "Western Veil Nebula",
                        "objects": ["C 34", "NGC 6960"],
                    },
                ],
            )

            images, warnings = compare.load_site_images(gallery, 1)
            definitions = compare.load_cull_group_definitions(groups_path)
            groups = compare.build_cull_groups(gallery, images, definitions)

            self.assertEqual(warnings, [])
            self.assertEqual([group.label for group in groups], ["Andromeda Galaxy", "Western Veil Nebula"])
            andromeda = groups[0]
            self.assertEqual(len(andromeda.candidates), 2)
            overridden = next(item for item in andromeda.candidates if item.catalog_filename == mosaic)
            self.assertTrue(overridden.overridden)
            self.assertEqual(overridden.image.frames, 61)
            self.assertEqual(overridden.image.object_name, "mosaic_M 31")
            self.assertEqual(overridden.image.treatment, "cleaned")
            self.assertEqual(overridden.review_file, override_relative)
            all_catalog_files = {
                candidate.catalog_filename for group in groups for candidate in group.candidates
            }
            self.assertNotIn(eastern_veil, all_catalog_files)

    def test_only_configured_groups_with_two_candidates_are_built(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg",
                    "Stacked_100_M 42_10.0s_LP_20260925-215000_cleaned.jpg",
                ],
            )
            groups_path = write_cull_groups(
                gallery,
                [
                    {"id": "andromeda", "label": "Andromeda", "objects": ["M 31"]},
                    {"id": "orion", "label": "Orion", "objects": ["M 42"]},
                ],
            )

            images, _warnings = compare.load_site_images(gallery, 1)
            groups = compare.build_cull_groups(
                gallery, images, compare.load_cull_group_definitions(groups_path)
            )

            self.assertEqual(groups, [])

    def test_override_must_match_every_catalog_capture_identity_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"
            second = "Stacked_90_mosaic_M 31_10.0s_IRCUT_20260925-220000_cleaned.jpg"
            gallery = make_gallery(root, [first, second])
            cases = {
                "frames": "Stacked_89_mosaic_M 31_10.0s_IRCUT_20260925-220000_hand_processed.png",
                "object": "Stacked_90_mosaic_M 42_10.0s_IRCUT_20260925-220000_hand_processed.png",
                "mosaic": "Stacked_90_M 31_10.0s_IRCUT_20260925-220000_hand_processed.png",
                "exposure": "Stacked_90_mosaic_M 31_20.0s_IRCUT_20260925-220000_hand_processed.png",
                "filter": "Stacked_90_mosaic_M 31_10.0s_LP_20260925-220000_hand_processed.png",
                "timestamp": "Stacked_90_mosaic_M 31_10.0s_IRCUT_20260925-220001_hand_processed.png",
            }
            override_root = gallery / "work" / "identity-cases"
            override_root.mkdir(parents=True)
            images, _warnings = compare.load_site_images(gallery, 1)
            for case_name, filename in cases.items():
                with self.subTest(case=case_name):
                    override = override_root / filename
                    override.write_bytes(jpeg_bytes(payload=case_name.encode()))
                    override_relative = str(override.relative_to(gallery).as_posix())
                    groups_path = write_cull_groups(
                        gallery,
                        [
                            {
                                "id": "andromeda",
                                "label": "Andromeda",
                                "objects": ["M 31"],
                                "candidate_overrides": {second: override_relative},
                            }
                        ],
                    )
                    with self.assertRaisesRegex(
                        ValueError, "does not match catalog capture identity"
                    ):
                        compare.build_cull_groups(
                            gallery,
                            images,
                            compare.load_cull_group_definitions(groups_path),
                        )

    def test_winner_resumes_and_changed_pixels_make_the_saved_group_orphaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"
            second = "Stacked_90_mosaic_M 31_10.0s_IRCUT_20260925-220000_cleaned.jpg"
            gallery = make_gallery(root, [first, second])
            groups_path = write_cull_groups(
                gallery,
                [{"id": "andromeda", "label": "Andromeda", "objects": ["M 31"]}],
            )
            images, _warnings = compare.load_site_images(gallery, 1)
            definitions = compare.load_cull_group_definitions(groups_path)
            group = compare.build_cull_groups(gallery, images, definitions)[0]
            choices = gallery / "work" / "gallery_cull_choices.json"
            winner = group.candidates[0]

            record = compare.record_cull_choice(
                choices,
                {group.group_id: group},
                group.group_id,
                "winner",
                winner.candidate_id,
            )
            payload = compare.cull_api_payload([group], compare.load_cull_choices(choices))

            self.assertEqual(record["winner_catalog_filename"], winner.catalog_filename)
            self.assertEqual(payload["summary"]["decided_count"], 1)
            self.assertEqual(
                payload["groups"][0]["choice"]["winner_candidate_id"],
                winner.candidate_id,
            )

            winner.image.path.write_bytes(
                jpeg_bytes(payload=b"changed after the review desk opened")
            )
            with self.assertRaisesRegex(ValueError, "changed while the review desk was open"):
                compare.record_cull_choice(
                    choices,
                    {group.group_id: group},
                    group.group_id,
                    "skip",
                )

            refreshed_images, _warnings = compare.load_site_images(gallery, 1)
            refreshed_group = compare.build_cull_groups(
                gallery, refreshed_images, definitions
            )[0]
            refreshed_payload = compare.cull_api_payload(
                [refreshed_group], compare.load_cull_choices(choices)
            )
            self.assertNotEqual(refreshed_group.group_id, group.group_id)
            self.assertEqual(refreshed_payload["summary"]["decided_count"], 0)
            self.assertEqual(refreshed_payload["summary"]["orphan_choice_count"], 1)
            self.assertIsNone(refreshed_payload["groups"][0]["choice"])

    def test_live_api_and_media_reject_changed_or_deleted_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"
            second = "Stacked_90_mosaic_M 31_10.0s_IRCUT_20260925-220000_cleaned.jpg"
            gallery = make_gallery(root, [first, second])
            groups_path = write_cull_groups(
                gallery,
                [{"id": "andromeda", "label": "Andromeda", "objects": ["M 31"]}],
            )
            images, _warnings = compare.load_site_images(gallery, 1)
            group = compare.build_cull_groups(
                gallery, images, compare.load_cull_group_definitions(groups_path)
            )[0]
            choices = gallery / "work" / "gallery_cull_choices.json"
            selected = group.candidates[0]
            compare.record_cull_choice(
                choices,
                {group.group_id: group},
                group.group_id,
                "winner",
                selected.candidate_id,
            )
            application = compare.GalleryCullApplication([group], choices)
            selected_media_id = f"cull-{selected.candidate_id}"
            selected_bytes = selected.image.path.read_bytes()

            self.assertEqual(application.payload()["summary"]["decided_count"], 1)
            self.assertEqual(application.read_media(selected_media_id)[1], selected_bytes)

            selected.image.path.write_bytes(jpeg_bytes(payload=b"changed live pixels"))
            with self.assertRaisesRegex(ValueError, "changed while the review desk was open"):
                application.payload()
            with self.assertRaisesRegex(ValueError, "changed; refresh"):
                application.read_media(selected_media_id)

            selected.image.path.write_bytes(selected_bytes)
            deleted = group.candidates[1]
            deleted_media_id = f"cull-{deleted.candidate_id}"
            deleted.image.path.unlink()
            with self.assertRaisesRegex(ValueError, "candidate is unavailable"):
                application.payload()
            with self.assertRaises(FileNotFoundError):
                application.read_media(deleted_media_id)

    def test_skip_is_undecided_and_tampered_snapshot_is_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_C 34_10.0s_LP_20260925-215000_cleaned.jpg",
                    "Stacked_90_NGC 6960_10.0s_LP_20260925-220000_cleaned.jpg",
                ],
            )
            groups_path = write_cull_groups(
                gallery,
                [
                    {
                        "id": "western-veil",
                        "label": "Western Veil",
                        "objects": ["C 34", "NGC 6960"],
                    }
                ],
            )
            images, _warnings = compare.load_site_images(gallery, 1)
            group = compare.build_cull_groups(
                gallery, images, compare.load_cull_group_definitions(groups_path)
            )[0]
            choices = root / "choices.json"
            compare.record_cull_choice(
                choices, {group.group_id: group}, group.group_id, "skip"
            )

            payload = compare.cull_api_payload([group], compare.load_cull_choices(choices))
            self.assertEqual(payload["summary"]["decided_count"], 0)
            self.assertEqual(payload["groups"][0]["choice"]["choice"], "skip")

            tampered = compare.load_cull_choices(choices)
            tampered["choices"][group.group_id]["candidate_snapshot"] = []
            compare.write_json(choices, tampered)
            payload = compare.cull_api_payload([group], compare.load_cull_choices(choices))
            self.assertEqual(payload["summary"]["invalid_choice_count"], 1)
            self.assertIsNone(payload["groups"][0]["choice"])

            stack_choices = root / "stack_choices.json"
            compare.write_json(
                stack_choices,
                {"schema_version": 1, "tool": compare.TOOL_NAME, "choices": {}},
            )
            with self.assertRaisesRegex(ValueError, "invalid cull choices document"):
                compare.load_cull_choices(stack_choices)

    def test_choice_write_does_not_mutate_app_or_public_and_media_urls_are_opaque(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg",
                    "Stacked_90_mosaic_M 31_10.0s_IRCUT_20260925-220000_cleaned.jpg",
                ],
            )
            groups_path = write_cull_groups(
                gallery,
                [{"id": "andromeda", "label": "Andromeda", "objects": ["M 31"]}],
            )
            before = {
                path.relative_to(gallery): path.read_bytes()
                for folder in (gallery / "app", gallery / "public")
                for path in folder.rglob("*")
                if path.is_file()
            }
            images, _warnings = compare.load_site_images(gallery, 1)
            group = compare.build_cull_groups(
                gallery, images, compare.load_cull_group_definitions(groups_path)
            )[0]
            choices = gallery / "work" / "gallery_cull_choices.json"
            compare.record_cull_choice(
                choices,
                {group.group_id: group},
                group.group_id,
                "winner",
                group.candidates[1].candidate_id,
            )
            after = {
                path.relative_to(gallery): path.read_bytes()
                for folder in (gallery / "app", gallery / "public")
                for path in folder.rglob("*")
                if path.is_file()
            }
            api = compare.cull_api_payload([group], compare.load_cull_choices(choices))
            application = compare.GalleryCullApplication([group], choices)

            self.assertEqual(after, before)
            self.assertTrue(choices.is_file())
            for candidate in api["groups"][0]["candidates"]:
                self.assertRegex(candidate["media_url"], r"^/media/cull-[0-9a-f]{64}$")
                self.assertNotIn(candidate["filename"], candidate["media_url"])
            self.assertEqual(set(application.media), {
                f"cull-{candidate.candidate_id}" for candidate in group.candidates
            })

    def test_cli_has_separate_cull_contract_and_protects_public_and_app(self):
        args = compare.parse_args(
            [
                "cull",
                "--gallery",
                "/tmp/gallery",
                "--groups",
                "/tmp/gallery/gallery_cull_groups.json",
                "--choices",
                "/tmp/gallery/work/gallery_cull_choices.json",
            ]
        )
        self.assertEqual(args.command, "cull")
        self.assertEqual(args.port, 8765)
        self.assertFalse(hasattr(args, "ours"))
        self.assertEqual(compare.ReviewApplication.payload_path, "/api/pairs")
        self.assertEqual(compare.ReviewApplication.choice_path, "/api/choice")
        self.assertEqual(compare.GalleryCullApplication.payload_path, "/api/cull-groups")
        self.assertEqual(compare.GalleryCullApplication.choice_path, "/api/cull-choice")
        self.assertIn("Which capture earns the sky?", compare.CULL_HTML)
        self.assertIn("Number(event.key)-1", compare.CULL_HTML)
        self.assertIn("if(saving)return", compare.CULL_HTML)
        self.assertIn("event.repeat||saving", compare.CULL_HTML)
        self.assertIn("disabled=saving?' disabled':''", compare.CULL_HTML)
        self.assertIn("candidates${saving?' saving':''}", compare.CULL_HTML)

        with contextlib.redirect_stderr(io.StringIO()):
            result = compare.main(
                [
                    "cull",
                    "--gallery",
                    "/tmp/gallery",
                    "--groups",
                    "/tmp/does-not-matter.json",
                    "--choices",
                    "/tmp/gallery/public/choices.json",
                ]
            )
        self.assertEqual(result, 2)
        with contextlib.redirect_stderr(io.StringIO()):
            result = compare.main(
                [
                    "cull",
                    "--gallery",
                    "/tmp/gallery",
                    "--groups",
                    "/tmp/does-not-matter.json",
                    "--choices",
                    "/tmp/gallery/app/choices.json",
                ]
            )
        self.assertEqual(result, 2)


class PublicComparisonReviewTests(unittest.TestCase):
    def test_manifest_builds_exactly_one_verified_pair_per_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filenames = [
                "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg",
                "Stacked_144_SH2-142_10.0s_LP_20260926-220000_hand_processed.png",
            ]
            gallery = make_gallery(root, filenames)
            write_public_comparison_manifest(gallery)

            pairs = compare.load_public_comparison_pairs(gallery)

            self.assertEqual(len(pairs), 2)
            self.assertEqual([pair.target for pair in pairs], ["M 31", "SH2-142"])
            self.assertEqual([pair.site.filename for pair in pairs], filenames)
            self.assertTrue(all(len(pair.ours) == 1 for pair in pairs))
            self.assertEqual([pair.ours[0].frames for pair in pairs], [93, 137])
            self.assertEqual(len({pair.pair_id for pair in pairs}), 2)
            self.assertEqual(len({pair.ours[0].candidate_id for pair in pairs}), 2)
            payload = compare.api_payload(
                pairs,
                [],
                [],
                compare.load_public_review_choices(gallery / "work" / "choices.json"),
            )
            self.assertEqual(payload["summary"]["pair_count"], 2)
            self.assertEqual(payload["summary"]["unpaired_gallery_count"], 0)
            self.assertEqual(payload["summary"]["unpaired_local_count"], 0)

    def test_manifest_rejects_each_declared_integrity_mismatch(self):
        cases = [
            (
                "unaligned schema",
                lambda payload: payload.__setitem__("schemaVersion", 1),
                "tool or schema",
            ),
            (
                "capture count",
                lambda payload: payload.__setitem__("captureCount", 2),
                "captureCount",
            ),
            (
                "baseline checksum",
                lambda payload: payload["captures"][0]["baseline"].__setitem__(
                    "sha256", "0" * 64
                ),
                "baseline checksum",
            ),
            (
                "baseline dimensions",
                lambda payload: payload["captures"][0]["baseline"].__setitem__(
                    "width", 1079
                ),
                "baseline dimensions",
            ),
            (
                "baseline frames",
                lambda payload: payload["captures"][0]["baseline"].__setitem__(
                    "frames", 99
                ),
                "baseline frame count",
            ),
            (
                "baseline below frame floor",
                lambda payload: payload["captures"][0]["baseline"].__setitem__(
                    "frames", 49
                ),
                "baseline.frames must be at least 50",
            ),
            (
                "NightSkyAI checksum",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "sha256", "0" * 64
                ),
                "nightSkyAI checksum",
            ),
            (
                "NightSkyAI wrong directory",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "filename",
                    f"images/{payload['captures'][0]['captureId']}.jpg",
                ),
                "nightSkyAI.filename must be aligned-v1",
            ),
            (
                "NightSkyAI wrong capture filename",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "filename", "aligned-v1/capture-not-this-one.jpg"
                ),
                "nightSkyAI.filename must be aligned-v1",
            ),
            (
                "NightSkyAI dimensions",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "height", 1919
                ),
                "nightSkyAI dimensions",
            ),
            (
                "NightSkyAI frames",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "frames", 0
                ),
                "nightSkyAI.frames",
            ),
            (
                "NightSkyAI below frame floor",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "frames", 49
                ),
                "nightSkyAI.frames must be at least 50",
            ),
            (
                "missing alignment",
                lambda payload: payload["captures"][0]["nightSkyAI"].__setitem__(
                    "alignment", None
                ),
                "alignment must be an object",
            ),
            (
                "mutable alignment reference",
                lambda payload: payload["captures"][0]["nightSkyAI"][
                    "alignment"
                ].__setitem__("referencePolicy", "candidate-is-reference"),
                "immutable Gallery edit",
            ),
            (
                "wrong alignment mode",
                lambda payload: payload["captures"][0]["nightSkyAI"][
                    "alignment"
                ].__setitem__("mode", "unaligned"),
                "registered to the Gallery edit",
            ),
            (
                "failed alignment verification",
                lambda payload: payload["captures"][0]["nightSkyAI"][
                    "alignment"
                ]["verification"].__setitem__("passed", False),
                "verification must have passed",
            ),
        ]
        for case_name, mutate, expected_error in cases:
            with self.subTest(case_name=case_name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    gallery = make_gallery(
                        root,
                        ["Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"],
                    )
                    manifest = write_public_comparison_manifest(gallery)
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                    mutate(payload)
                    manifest.write_text(json.dumps(payload), encoding="utf-8")

                    with self.assertRaisesRegex(ValueError, expected_error):
                        compare.load_public_comparison_pairs(gallery)

    def test_manifest_rejects_self_consistent_cropped_nightskyai_canvas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"],
            )
            manifest = write_public_comparison_manifest(gallery)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            night_sky = payload["captures"][0]["nightSkyAI"]
            night_path = gallery / "public" / "comparisons" / night_sky["filename"]
            night_path.write_bytes(jpeg_bytes(width=540, height=960, payload=b"cropped"))
            night_sky["sha256"] = compare.sha256_file(night_path)
            night_sky["width"] = 540
            night_sky["height"] = 960
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "dimensions must match the Gallery baseline"
            ):
                compare.load_public_comparison_pairs(gallery)

    def test_manifest_verifies_declared_alignment_source_and_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"],
            )
            manifest = write_public_comparison_manifest(gallery)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            source = gallery / "public" / "comparisons" / "images" / "source.jpg"
            source.parent.mkdir()
            source.write_bytes(jpeg_bytes(payload=b"unaligned source"))
            width, height = compare.image_dimensions(source)
            payload["captures"][0]["nightSkyAI"]["alignment"].update(
                {
                    "sourceFilename": "images/source.jpg",
                    "sourceSha256": compare.sha256_file(source),
                    "sourceWidth": width,
                    "sourceHeight": height,
                }
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(len(compare.load_public_comparison_pairs(gallery)), 1)
            source.write_bytes(jpeg_bytes(payload=b"changed source"))
            with self.assertRaisesRegex(ValueError, "alignment source checksum"):
                compare.load_public_comparison_pairs(gallery)

            payload["captures"][0]["nightSkyAI"]["alignment"][
                "sourceFilename"
            ] = "../../images/escape.jpg"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must stay inside"):
                compare.load_public_comparison_pairs(gallery)

    def test_public_review_choices_are_separate_and_never_mutate_site_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg",
                    "Stacked_100_M 42_10.0s_LP_20260925-220000_cleaned.jpg",
                ],
            )
            write_public_comparison_manifest(gallery)
            pairs = compare.load_public_comparison_pairs(gallery)
            before = {
                path.relative_to(gallery): path.read_bytes()
                for folder in (gallery / "app", gallery / "public")
                for path in folder.rglob("*")
                if path.is_file()
            }
            choices = gallery / "work" / "public_review_choices.json"
            application = compare.PublicComparisonReviewApplication(pairs, choices)

            application.choose({"pair_id": pairs[0].pair_id, "choice": "site"})
            application.choose(
                {
                    "pair_id": pairs[1].pair_id,
                    "choice": "ours",
                    "candidate_id": pairs[1].ours[0].candidate_id,
                }
            )

            after = {
                path.relative_to(gallery): path.read_bytes()
                for folder in (gallery / "app", gallery / "public")
                for path in folder.rglob("*")
                if path.is_file()
            }
            saved = json.loads(choices.read_text(encoding="utf-8"))
            self.assertEqual(after, before)
            self.assertEqual(saved["tool"], compare.PUBLIC_REVIEW_TOOL_NAME)
            self.assertEqual(application.payload()["summary"]["decided_count"], 2)
            self.assertIn("Gallery edit", application.index_html)
            self.assertIn("Use NightSkyAI", application.index_html)
            media_id = f"ours-{pairs[0].ours[0].candidate_id}"
            self.assertEqual(
                application.read_media(media_id)[1], pairs[0].ours[0].path.read_bytes()
            )

            pairs[0].ours[0].path.write_bytes(jpeg_bytes(payload=b"changed live"))
            with self.assertRaisesRegex(ValueError, "changed while the review desk was open"):
                application.payload()
            with self.assertRaisesRegex(ValueError, "changed; refresh"):
                application.read_media(media_id)

            ordinary_choices = gallery / "work" / "ordinary_choices.json"
            compare.write_json(
                ordinary_choices,
                {"schema_version": 1, "tool": compare.TOOL_NAME, "choices": {}},
            )
            with self.assertRaisesRegex(ValueError, "invalid public review choices"):
                compare.load_public_review_choices(ordinary_choices)

    def test_review_public_cli_uses_work_choices_and_no_raw_stack_root(self):
        args = compare.parse_args(
            [
                "review-public",
                "--gallery",
                "/tmp/gallery",
                "--choices",
                "/tmp/gallery/work/public_review_choices.json",
            ]
        )
        self.assertEqual(args.command, "review-public")
        self.assertEqual(args.port, 8765)
        self.assertFalse(hasattr(args, "ours"))

        with contextlib.redirect_stderr(io.StringIO()):
            result = compare.main(
                [
                    "review-public",
                    "--gallery",
                    "/tmp/gallery",
                    "--choices",
                    "/tmp/outside-work.json",
                ]
            )
        self.assertEqual(result, 2)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"],
            )
            external_work = root / "external-work"
            external_work.mkdir()
            (gallery / "work").symlink_to(external_work, target_is_directory=True)
            with contextlib.redirect_stderr(io.StringIO()):
                result = compare.main(
                    [
                        "review-public",
                        "--gallery",
                        str(gallery),
                        "--choices",
                        str(gallery / "work" / "public_review_choices.json"),
                    ]
                )
            self.assertEqual(result, 2)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 31_10.0s_IRCUT_20260925-215000_cleaned.jpg"],
            )
            write_public_comparison_manifest(gallery)
            choices = gallery / "work" / "public_review_choices.json"
            server = mock.Mock()
            server.server_port = 43123
            with (
                mock.patch.object(compare, "ThreadingHTTPServer", return_value=server),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = compare.main(
                    [
                        "review-public",
                        "--gallery",
                        str(gallery),
                        "--choices",
                        str(choices),
                    ]
                )
            self.assertEqual(result, 0)
            server.serve_forever.assert_called_once_with()
            server.server_close.assert_called_once_with()
            self.assertFalse(choices.exists())


class ChoiceAndExportTests(unittest.TestCase):
    def test_review_html_surfaces_api_load_failures(self):
        self.assertIn("if(!response.ok)throw new Error", compare.HTML)
        self.assertIn("showLoadError(error)", compare.HTML)
        self.assertIn("Review unavailable", compare.HTML)
        self.assertIn("if(!await load())return", compare.HTML)
        self.assertIn("Could not load review", compare.PUBLIC_REVIEW_HTML)

    def test_picker_only_marks_the_exact_saved_candidate_as_selected(self):
        self.assertIn(
            "chosen==='ours'&&p.choice?.ours_candidate_id===ours.candidate_id",
            compare.HTML,
        )

    def test_records_a_valid_choice_and_rejects_stale_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            choices = root / "choices.json"

            record = compare.record_choice(
                choices,
                {pairs[0].pair_id: pairs[0]},
                pairs[0].pair_id,
                "ours",
                pairs[0].ours[0].candidate_id,
            )

            self.assertEqual(record["choice"], "ours")
            self.assertTrue(choices.is_file())
            with self.assertRaises(ValueError):
                compare.record_choice(choices, {pairs[0].pair_id: pairs[0]}, "stale", "site")

    def test_export_creates_new_snapshot_without_touching_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            local = make_stack(ours, target="M 51", frames=90)
            local_before = local.read_bytes()
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {pairs[0].pair_id: pairs[0]},
                pairs[0].pair_id,
                "ours",
                pairs[0].ours[0].candidate_id,
            )

            snapshot = compare.export_winners(
                pairs,
                choices,
                root / "exports",
                allow_incomplete=False,
                snapshot_name="review-one",
            )

            manifest = json.loads((snapshot / "selection_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected_count"], 1)
            self.assertEqual(manifest["selections"][0]["provenance"], "local-siril-stack")
            self.assertEqual(
                manifest["selections"][0]["file"],
                "Stacked_90_M 51_10.0s_LP_20260925-210000_hand_processed.jpg",
            )
            self.assertEqual(local.read_bytes(), local_before)
            self.assertEqual(
                [path.name for path in snapshot.glob("*.jpg")],
                ["Stacked_90_M 51_10.0s_LP_20260925-210000_hand_processed.jpg"],
            )

    def test_export_names_multiple_generic_refreshed_previews_from_manifest_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 42_10.0s_LP_20260925-215000_cleaned.jpg",
                    "Stacked_100_M 51_20.0s_IRCUT_20260926-215000_cleaned.jpg",
                ],
            )
            ours = root / "ours"
            make_stack(
                ours,
                target="M 42",
                exposure="10.0",
                filter_name="LP",
                frames=88,
                first_timestamp="20260925-210000",
                last_timestamp="20260925-215000",
                preview_filename="preview-refresh-natural.jpg",
            )
            make_stack(
                ours,
                target="M 51",
                exposure="20.0",
                filter_name="IRCUT",
                frames=144,
                first_timestamp="20260926-210000",
                last_timestamp="20260926-215000",
                preview_filename="preview-refresh-natural.jpg",
            )
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            self.assertEqual(len(pairs), 2)
            choices = root / "choices.json"
            for pair in pairs:
                compare.record_choice(
                    choices,
                    {pair.pair_id: pair},
                    pair.pair_id,
                    "ours",
                    pair.ours[0].candidate_id,
                )

            snapshot = compare.export_winners(
                pairs,
                choices,
                root / "exports",
                allow_incomplete=False,
                snapshot_name="generic-previews",
            )

            expected = {
                "Stacked_88_M 42_10.0s_LP_20260925-210000_hand_processed.jpg",
                "Stacked_144_M 51_20.0s_IRCUT_20260926-210000_hand_processed.jpg",
            }
            self.assertEqual({path.name for path in snapshot.glob("*.jpg")}, expected)
            manifest = json.loads(
                (snapshot / "selection_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {selection["file"] for selection in manifest["selections"]}, expected
            )

    def test_export_requires_all_pairs_decided_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            choices = root / "choices.json"
            compare.write_json(choices, {"choices": {}})

            with self.assertRaises(ValueError):
                compare.export_winners(pairs, choices, root / "exports", allow_incomplete=False)

    def test_skip_is_undecided_and_only_allow_incomplete_may_omit_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {pairs[0].pair_id: pairs[0]},
                pairs[0].pair_id,
                "skip",
            )

            api = compare.api_payload(pairs, [], [], compare.load_choices(choices))
            self.assertEqual(api["summary"]["decided_count"], 0)
            self.assertEqual(api["pairs"][0]["choice"]["choice"], "skip")
            with self.assertRaisesRegex(ValueError, "still undecided"):
                compare.export_winners(pairs, choices, root / "exports", allow_incomplete=False)

            snapshot = compare.export_winners(
                pairs,
                choices,
                root / "exports",
                allow_incomplete=True,
                snapshot_name="skip-omitted",
            )
            manifest = json.loads((snapshot / "selection_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected_count"], 0)
            self.assertEqual(list(snapshot.glob("*.jpg")), [])

    def test_malformed_and_stale_choices_are_not_counted_or_exported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(root, ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"])
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pairs, *_ = compare.discover_comparisons(gallery, ours, 50)
            pair = pairs[0]
            choices = root / "choices.json"
            compare.write_json(choices, {"choices": {pair.pair_id: {"choice": "site"}}})

            api = compare.api_payload(pairs, [], [], compare.load_choices(choices))
            self.assertEqual(api["summary"]["decided_count"], 0)
            self.assertEqual(api["summary"]["invalid_choice_count"], 1)
            self.assertIsNone(api["pairs"][0]["choice"])
            with self.assertRaisesRegex(ValueError, "saved choice"):
                compare.export_winners(
                    pairs,
                    choices,
                    root / "exports",
                    allow_incomplete=True,
                )

            compare.record_choice(
                choices,
                {pair.pair_id: pair},
                pair.pair_id,
                "ours",
                pair.ours[0].candidate_id,
            )
            stale = compare.load_choices(choices)
            stale["choices"][pair.pair_id]["ours_candidate_id"] = "obsolete-candidate"
            compare.write_json(choices, stale)
            api = compare.api_payload(pairs, [], [], stale)
            self.assertEqual(api["summary"]["decided_count"], 0)
            self.assertEqual(api["summary"]["invalid_choice_count"], 1)
            with self.assertRaisesRegex(ValueError, "stale local choice"):
                compare.export_winners(
                    pairs,
                    choices,
                    root / "exports",
                    allow_incomplete=True,
                )


class PublicExportTests(unittest.TestCase):
    def test_capture_id_ignores_pair_id_and_preview_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            preview = make_stack(
                ours,
                target="M 51",
                frames=90,
                input_fingerprint="immutable-raw-inventory",
            )
            first_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            first_id = compare.public_capture_id(first_pair, first_pair.ours[0])

            preview.write_bytes(
                jpeg_bytes(payload=b"same raw frames, newly rendered preview")
            )
            second_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            second_id = compare.public_capture_id(second_pair, second_pair.ours[0])

            self.assertNotEqual(first_pair.pair_id, second_pair.pair_id)
            self.assertNotEqual(
                first_pair.ours[0].candidate_id,
                second_pair.ours[0].candidate_id,
            )
            self.assertEqual(first_id, second_id)

    def test_changed_candidate_defaults_to_seestar_even_with_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            make_stack(
                ours,
                target="M 51",
                frames=75,
                input_fingerprint="same-raw-inventory",
                preview_payload=b"old preview",
            )
            old_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {old_pair.pair_id: old_pair},
                old_pair.pair_id,
                "ours",
                old_pair.ours[0].candidate_id,
            )

            new_preview = make_stack(
                ours,
                target="M 51",
                frames=120,
                finished_at="2026-09-25T13:00:00Z",
                input_fingerprint="same-raw-inventory",
                preview_payload=b"new top preview",
                input_timestamps=[
                    "20260923-210000",
                    "20260924-210000",
                    "20260925-215000",
                ],
                first_timestamp="20260923-210000",
                last_timestamp="20260925-215000",
            )
            new_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            self.assertNotEqual(old_pair.pair_id, new_pair.pair_id)
            self.assertEqual(new_pair.ours[0].path, new_preview.resolve())

            destination = root / "public-comparisons"
            exported = compare.export_public_comparisons(
                [new_pair],
                choices,
                destination,
                allow_incomplete=False,
            )

            manifest = json.loads((exported / "manifest.json").read_text(encoding="utf-8"))
            capture = manifest["captures"][0]
            self.assertEqual(capture["curatedDefault"], "seestar")
            self.assertEqual(
                capture["curatedDefaultSource"], "candidate-changed-default"
            )
            self.assertEqual(manifest["defaultedToSeestarCount"], 1)
            self.assertEqual(capture["nightSkyAI"]["frames"], 120)
            self.assertEqual(
                (exported / capture["nightSkyAI"]["filename"]).read_bytes(),
                jpeg_bytes(payload=b"new top preview"),
            )
            self.assertEqual(capture["nightSkyAI"]["firstTimestamp"], "20260923-210000")
            self.assertEqual(capture["nightSkyAI"]["lastTimestamp"], "20260925-215000")
            self.assertEqual(capture["nightSkyAI"]["nightCount"], 3)
            self.assertEqual(
                (capture["nightSkyAI"]["width"], capture["nightSkyAI"]["height"]),
                (1080, 1920),
            )
            self.assertEqual(
                (capture["baseline"]["width"], capture["baseline"]["height"]),
                (1080, 1920),
            )
            self.assertEqual(
                capture["nightSkyAI"]["sha256"],
                compare.sha256_file(new_preview),
            )
            self.assertEqual(capture["baseline"]["filename"], new_pair.site.filename)
            self.assertEqual(capture["baseline"]["frames"], 100)
            self.assertEqual(capture["baseline"]["treatment"], "cleaned")
            self.assertEqual(
                capture["baseline"]["sha256"],
                compare.sha256_file(new_pair.site.path),
            )

    def test_changed_candidate_invalidates_a_saved_site_choice_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=75)
            old_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {old_pair.pair_id: old_pair},
                old_pair.pair_id,
                "site",
            )

            make_stack(
                ours,
                target="M 51",
                frames=120,
                finished_at="2026-09-25T13:00:00Z",
            )
            new_pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            self.assertNotEqual(old_pair.pair_id, new_pair.pair_id)

            destination = root / "public-comparisons"
            compare.export_public_comparisons(
                [new_pair], choices, destination, allow_incomplete=False
            )
            capture = json.loads(
                (destination / "manifest.json").read_text(encoding="utf-8")
            )["captures"][0]
            self.assertEqual(capture["curatedDefault"], "seestar")
            self.assertEqual(
                capture["curatedDefaultSource"], "candidate-changed-default"
            )

    def test_visibly_cropped_candidate_is_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            make_stack(
                ours,
                target="M 51",
                frames=90,
                preview_width=1015,
                preview_height=1805,
            )
            pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {pair.pair_id: pair},
                pair.pair_id,
                "ours",
                pair.ours[0].candidate_id,
            )
            destination = root / "public"

            with self.assertRaisesRegex(ValueError, "not the full vertical|visibly cropped"):
                compare.export_public_comparisons(
                    [pair], choices, destination, allow_incomplete=False
                )

            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(".public.tmp").exists())

    def test_dimension_changes_after_discovery_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            preview = make_stack(ours, target="M 51", frames=90)
            pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "choices.json"
            compare.record_choice(
                choices,
                {pair.pair_id: pair},
                pair.pair_id,
                "site",
            )
            preview.write_bytes(jpeg_bytes(width=400, height=1400))

            with self.assertRaisesRegex(ValueError, "dimensions changed"):
                compare.export_public_comparisons(
                    [pair], choices, root / "public", allow_incomplete=False
                )

    def test_missing_decision_fails_closed_or_is_recorded_as_seestar_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "missing-choices.json"
            strict_destination = root / "strict-public"

            with self.assertRaisesRegex(ValueError, "lack a curated decision"):
                compare.export_public_comparisons(
                    [pair],
                    choices,
                    strict_destination,
                    allow_incomplete=False,
                )
            self.assertFalse(strict_destination.exists())

            destination = root / "allowed-public"
            compare.export_public_comparisons(
                [pair],
                choices,
                destination,
                allow_incomplete=True,
            )
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            capture = manifest["captures"][0]
            self.assertEqual(manifest["defaultedToSeestarCount"], 1)
            self.assertEqual(capture["curatedDefault"], "seestar")
            self.assertEqual(
                capture["curatedDefaultSource"],
                "allow-incomplete-default",
            )
            self.assertTrue((destination / capture["nightSkyAI"]["filename"]).is_file())

    def test_capture_id_collision_is_rejected_before_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 51_10.0s_LP_20260925-214500_cleaned.jpg",
                    "Stacked_110_M 51_10.0s_LP_20260925-215000_cleaned.jpg",
                ],
            )
            ours = root / "ours"
            make_stack(
                ours,
                target="M 51",
                frames=90,
                input_fingerprint="one-shared-capture",
            )
            pairs = compare.discover_comparisons(gallery, ours, 50)[0]
            self.assertEqual(len(pairs), 2)
            choices = root / "choices.json"
            for pair in pairs:
                compare.record_choice(
                    choices,
                    {pair.pair_id: pair},
                    pair.pair_id,
                    "site",
                )
            destination = root / "public"

            with self.assertRaisesRegex(ValueError, "capture ID collision"):
                compare.export_public_comparisons(
                    pairs,
                    choices,
                    destination,
                    allow_incomplete=False,
                )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(".public.tmp").exists())

    def test_ambiguous_decisions_fail_closed_and_existing_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                ["Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg"],
            )
            ours = root / "ours"
            make_stack(ours, target="M 51", frames=90)
            pair = compare.discover_comparisons(gallery, ours, 50)[0][0]
            choices = root / "choices.json"
            record = compare.record_choice(
                choices,
                {pair.pair_id: pair},
                pair.pair_id,
                "site",
            )
            payload = compare.load_choices(choices)
            payload["choices"]["older-pair-id"] = dict(record)
            compare.write_json(choices, payload)
            destination = root / "public"

            with self.assertRaisesRegex(ValueError, "ambiguous curated decision"):
                compare.export_public_comparisons(
                    [pair],
                    choices,
                    destination,
                    allow_incomplete=True,
                )
            self.assertFalse(destination.exists())

            destination.mkdir()
            marker = destination / "owner-file.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                compare.export_public_comparisons(
                    [pair],
                    choices,
                    destination,
                    allow_incomplete=True,
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_copy_failure_removes_staging_and_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gallery = make_gallery(
                root,
                [
                    "Stacked_100_M 42_10.0s_LP_20260925-215000_cleaned.jpg",
                    "Stacked_100_M 51_10.0s_LP_20260925-215000_cleaned.jpg",
                ],
            )
            ours = root / "ours"
            make_stack(ours, target="M 42", frames=80)
            make_stack(ours, target="M 51", frames=90)
            pairs = compare.discover_comparisons(gallery, ours, 50)[0]
            choices = root / "choices.json"
            for pair in pairs:
                compare.record_choice(
                    choices,
                    {pair.pair_id: pair},
                    pair.pair_id,
                    "site",
                )
            destination = root / "public"
            real_copy = compare.shutil.copy2
            copy_count = 0

            def fail_second_copy(source, target):
                nonlocal copy_count
                copy_count += 1
                if copy_count == 2:
                    raise OSError("simulated copy failure")
                return real_copy(source, target)

            with mock.patch.object(compare.shutil, "copy2", side_effect=fail_second_copy):
                with self.assertRaisesRegex(OSError, "simulated copy failure"):
                    compare.export_public_comparisons(
                        pairs,
                        choices,
                        destination,
                        allow_incomplete=False,
                    )

            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_name(".public.tmp").exists())

    def test_export_public_cli_is_fixed_at_fifty_or_more_frames(self):
        args = compare.parse_args(
            [
                "export-public",
                "--gallery",
                "/tmp/gallery",
                "--ours",
                "/tmp/ours",
                "--choices",
                "/tmp/choices.json",
                "--destination",
                "/tmp/public",
            ]
        )
        self.assertEqual(args.min_frames, 50)

        with (
            mock.patch.object(compare, "command_export_public") as command,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = compare.main(
                [
                    "export-public",
                    "--gallery",
                    "/tmp/gallery",
                    "--ours",
                    "/tmp/ours",
                    "--choices",
                    "/tmp/choices.json",
                    "--destination",
                    "/tmp/public",
                    "--min-frames",
                    "49",
                ]
            )
        self.assertEqual(result, 2)
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
