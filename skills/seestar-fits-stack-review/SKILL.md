---
name: seestar-fits-stack-review
description: Stack original Seestar FIT/FITS light frames directly from Brian's connected YOTUO, Seestar, or removable astronomy drive without duplicating the raw archive; optionally make an offline FITS copy when explicitly requested; and launch the correct local review desk for raw-stack comparisons, all tracked Gallery-versus-NightSkyAI display decisions, or duplicate-target curation. Use when Brian says the YOTUO or Seestar drive is connected, asks to stack all targets himself, compare Seestar and Siril results, personally choose which of the 28 aligned versions the public gallery should show, choose among repeated gallery targets, sync raw FITS for offline use, or prepare winners for the gallery.
---

# Stack and Review Seestar FITS

Process the removable drive in place by default. Treat every source FITS as
read-only, write all work and durable outputs locally, and use a human choice
to decide whether the telescope image or local Siril stack should advance.

Resolve `<gallery-root>` before doing any drive work. Prefer the current Deep
Space Field Notes Git root when it contains `.openai/hosting.json` and all
three automation scripts. Otherwise locate a nearby `deep-space-gallery`
checkout containing them. Ask for the path when the checkout is ambiguous;
never download or substitute unreviewed scripts.

Use these tested project tools and locations:

- Stacker: `<gallery-root>/seestar_siril_stack.py`
- Reviewer: `<gallery-root>/seestar_stack_compare.py`
- Optional importer: `<gallery-root>/seestar_fits_import.py`
- Gallery: `<gallery-root>`

Choose `<work-root>` without splitting a prior review:

- For a fresh clone, use the Git-ignored `<gallery-root>/work` directory.
- When continuing Brian's established archive, use `<gallery-root>/..` if
  existing `local_siril_stacks/`, `stack_choices.json`, or
  `offline_fits/.seestar_fits_import_manifest.json` proves it is the prior
  workspace.
  Preserve those paths so stack resumes and review choices remain continuous.

Put local stacks, raw-stack decisions, duplicate-target decisions, winner
snapshots, and an optional offline FITS archive under `<work-root>` as
`local_siril_stacks`, `stack_choices.json`, `gallery_cull_choices.json`,
`selected_site_images`, and `offline_fits` respectively. Keep final public
treatment decisions at `<gallery-root>/work/public_display_choices.json` so a
fresh clone can review the tracked bundle without the old stacking workspace.
These decision files serve different queues and must never be substituted for
one another.

## 1. Resolve and inspect the removable source

1. Inspect `/Volumes/YOTUO` and `/Volumes/Seestar` without changing either.
2. Prefer the drive named by the user. If only one exists, use it. If both
   exist and the user did not choose, compare eligible FITS inventories; use
   one only when they are identical, otherwise ask which is authoritative.
3. Count and total only immediate `Light_*.fit` or `Light_*.fits` children of
   recursively discovered `*_sub` folders. Exclude `Stacked_*.fit`, JPEGs,
   thumbnails, `proc` outputs, nested work files, and symbolic links. Every
   accepted regular frame must resolve beneath the selected source root.
4. Confirm local free space can hold the largest target's temporary processing
   estimate. Do not require enough space for a duplicate of the complete raw
   drive when direct processing is available.

## 2. Plan and resume direct stacking

Require Siril 1.4 or newer. Keep the removable drive connected for the full
duration of each target. Preview the direct batch first:

```bash
python3 <gallery-root>/seestar_siril_stack.py stack \
  --source <mounted-drive> \
  --destination <work-root>/local_siril_stacks \
  --all \
  --dry-run
```

The stacker reads source lights through local symlinks, never writes in the
source tree, and puts converted/registered work under `local_siril_stacks`. Keep the default
50-frame minimum and `natural` JPEG preview (background target 0.18, shadow
sigma 2.8). `dark` and `dramatic` are opt-in looks. Expect mosaics and targets
below 50 frames to be skipped. The finished stack must also retain at least 50 frames.
Skip `Unknown` by default because different unidentified sessions may be
unrelated sky fields. Different exposure times and filters become separate
jobs.

Successful schema-2 manifests make the batch resumable. A later run is skipped
only when the source folder, ordered input paths, input fingerprints, and
settings all match. Switching between a removable-drive folder and an offline
copy is intentionally treated as a new source identity rather than silently
reusing a similarly named run. A validated `cog` or `current` framing retry
also satisfies a later default `min` batch for the same source, inputs, and
non-framing settings. When Brian asks to stack/process everything, remove
`--dry-run` and monitor the sequential batch. Add `--force` only for an
intentional restack.

On macOS, Siril may falsely report `0 bytes available` when launched inside a
restricted process sandbox. If Python's disk preflight shows adequate space but
the Siril log shows that exact error, rerun the same bounded stack command with
the permission needed for Siril to read the real filesystem capacity. Do not
weaken source protections or change algorithms to mask it.

Preserve every successful linear FITS and manifest. Do not keep large work
directories unless diagnosing a failed target. A failed group retains its work
and log; report the failure clearly.

Minimum-common-area framing is guarded automatically. After Siril succeeds, the
stacker reads only the small FITS headers and rejects a result that retains less
than 90% of the source area or less than 90% of either source dimension. It
preserves that first attempt and reuses the converted and registered cache for
an automatic `cog` retry; it does not repeat conversion or registration. The
fallback must pass the same full-field guard before it is accepted or cleanup
can remove the reusable work cache.

When framing fails outright, or an automatic retry needs a different framing
choice, reuse the explicit failed run after a dry run:

```bash
python3 <gallery-root>/seestar_siril_stack.py retry-framing \
  --run <failed-run-folder> \
  --framing cog \
  --dry-run
```

Remove `--dry-run` after validation. Multiple explicit `--run` arguments may
be supplied. This preserves the original script/log, writes a separate retry
attempt, and removes only marker-owned `_work` after a validated success.
Failed retries retain their work. Inspect every `cog` preview, especially runs
spanning several nights or large rotations. If needed, `--framing current` is a
second fallback that uses the registration reference frame's footprint; inspect
it visually too. Confirm published comparison JPEGs retain the expected full
1080 x 1920 Seestar field before release. Use `--exposure` and `--filter` when a source target has
multiple capture groups and a full restack is needed.

Use `preview` later to adjust brightness, contrast, rotation, or flip without
restacking or altering the linear FITS. When the default look changes, validate
and refresh every eligible saved preview without rebuilding a stack:

```bash
python3 <gallery-root>/seestar_siril_stack.py refresh-previews \
  --root <work-root>/local_siril_stacks \
  --style natural \
  --dry-run
```

Remove `--dry-run` after the inventory is correct. The refresh must verify
owned, confined outputs; preserve the linear FITS and prior JPEG; record the new
preview checksum and provenance atomically; and safely upgrade verified
schema-1 manifests. It excludes stacks below the 50-frame gate. Restart the
reviewer after a refresh because the preview hashes and candidate IDs change.
Archive and reset choices made against the old previews rather than silently
carrying them forward.

## 3. Make an offline FITS archive only on request

Do not copy the complete raw library merely because the drive is connected. If
Brian explicitly needs to process after disconnecting the drive, dry-run and
then run:

```bash
python3 <gallery-root>/seestar_fits_import.py \
  --source <mounted-drive> \
  --destination <work-root>/offline_fits \
  --dry-run
```

Remove `--dry-run` only after reporting the total bytes. The importer preserves
the hierarchy, verifies SHA-256 while copying, resumes incrementally, and never
deletes source or offline files. Do not use `--replace-changed` unless the
differing files have been inspected and Brian confirms which copy to trust.

After any import, verify manifest/source counts and bytes, zero failures, and no
remaining `.part` files.

For ordinary offline tests in Brian's established workspace, prefer its
existing `<gallery-root>/../example_fits` subset: complete light-frame folders
for M 42, NGC 6960, and NGC 6992. Do not copy those fixtures into a fresh clone,
expand that subset, or recreate the full `offline_fits` archive unless Brian
explicitly requests the extra local storage. The fixtures are local-only,
Git-ignored, and total about 6 GiB. Preview them without processing:

```bash
python3 <gallery-root>/seestar_siril_stack.py stack \
  --source <gallery-root>/../example_fits \
  --destination <work-root>/local_siril_stacks \
  --all \
  --dry-run
```

A real fixture stack still requires the temporary capacity reported by the dry
run.

## 4. Review newly built raw-stack comparisons

Start the loopback-only reviewer:

```bash
python3 <gallery-root>/seestar_stack_compare.py serve \
  --gallery <gallery-root> \
  --ours <work-root>/local_siril_stacks \
  --choices <work-root>/stack_choices.json \
  --port 8765
```

Open `http://127.0.0.1:8765/` for Brian. Pair only exact target, exposure, and
filter matches from the same capture session, allowing only a short post-capture
delay for the telescope to save its JPEG. Require at least 50 finished frames.
Do not alias objects such as C 34 and NGC 6960, do not pair `Unknown`, and keep
mosaics separate. Brian chooses
**Keep gallery**, **Use our stack**, or **Decide later**; store each decision
with exact provenance.

The comparison inventory is fixed when `serve` starts. Start the reviewer after
stacking completes, or restart it when new manifests arrive. **Decide later** is
not a completed choice: normal export must stop until every pair has a gallery
or local winner. Use `--allow-incomplete` only when Brian intentionally wants an
export that omits undecided pairs.

Use filenames declared in `<gallery-root>/app/page.tsx` as the current
gallery source of truth. Do not reintroduce a removed image just because a stale
copy remains elsewhere.

## 5. Review every tracked public treatment

When Brian asks to personally choose which Gallery edit or NightSkyAI version
the public site should show, use the tracked public comparison bundle rather
than `serve` or `cull`. This queue is independent of the removed raw FITS
workspace and currently contains all 28 aligned comparisons:

```bash
python3 <gallery-root>/seestar_stack_compare.py review-public \
  --gallery <gallery-root> \
  --choices <gallery-root>/work/public_display_choices.json \
  --port 8765
```

Open `http://127.0.0.1:8765/` for Brian. Require one pair for every capture in
`<gallery-root>/public/comparisons/manifest.json`, verify the declared paths,
dimensions, frame counts, and SHA-256 values before serving them, and preserve
the Gallery edit as the alignment reference. The desk must show the Gallery
edit beside its aligned NightSkyAI treatment and let Brian select either one or
**Decide later**. Resume only from the dedicated
`<gallery-root>/work/public_display_choices.json`; do not mix these 28
treatment decisions with raw-stack or duplicate-target choices.

This mode is review-only. It must not edit `public/`, `app/`, comparison
metadata, or the deployed site. Finishing all 28 decisions does not authorize
applying them, removing the public comparison controls, committing generated
image changes, or publishing. Those are later, explicit steps after Brian says
the review is complete.

## 6. Choose one public image for repeated targets

Keep target curation separate from the exact-session stack comparison above.
The stack reviewer must still never alias C 34 with NGC 6960, C 27 with
NGC 6888, or a mosaic with a standard capture. The culling queue may group
those intentionally because its purpose is to select one public Gallery entry,
not to claim that their source frames match.

Start the same loopback-only review desk in culling mode:

```bash
python3 <gallery-root>/seestar_stack_compare.py cull \
  --gallery <gallery-root> \
  --groups <gallery-root>/gallery_cull_groups.json \
  --choices <work-root>/gallery_cull_choices.json \
  --port 8765
```

Open `http://127.0.0.1:8765/` for Brian. Show every configured repeated target
with its real capture metadata and let Brian choose exactly one candidate or
**Decide later**. The checked-in review candidate for the 61-frame Andromeda
mosaic is its distinct original image, not the accidentally duplicated finished
M 31 edit. Store culling decisions separately from `stack_choices.json`.

The culling desk never edits `app/page.tsx` or `public/images`. After every
group has a winner, use the exact saved filenames and hashes to prepare a
separate, reviewable Gallery diff. Do not publish merely because the local
choice file is complete.

## 7. Export reviewed stack winners

After all available pairs are decided, create a new selection snapshot:

```bash
python3 <gallery-root>/seestar_stack_compare.py export \
  --gallery <gallery-root> \
  --ours <work-root>/local_siril_stacks \
  --choices <work-root>/stack_choices.json \
  --destination <work-root>/selected_site_images
```

Never copy directly into `<gallery-root>/public/images` from this skill.
The snapshot and checksum manifest are the review boundary. Site publication is
a separate explicit action after Brian approves the selected set.

## Completion report

Report the removable source, discovered FITS count/bytes, eligible/completed/
failed stack groups, matched/undecided comparisons, review URL, decisions file,
and winner snapshot when exported. For the public treatment queue, report its
manifest count and decided/undecided totals from `public_display_choices.json`.
When culling repeated targets, also report the distinct culling choice file and
decided/undecided groups. Call out low-frame
targets, mosaics, conflicts, and unpaired images. If an optional offline archive
exists, report its size without deleting it unless Brian explicitly asks.
