---
name: seestar-fits-stack-review
description: Stack original Seestar FIT/FITS light frames directly from Brian's connected YOTUO, Seestar, or removable astronomy drive without duplicating the raw archive; optionally make an offline FITS copy when explicitly requested; and launch a side-by-side chooser comparing current Deep Space Field Notes images with locally stacked versions. Use when Brian says the YOTUO or Seestar drive is connected, asks to stack all targets himself, compare Seestar and Siril results, choose the cooler image, sync raw FITS for offline use, or prepare winners for the gallery.
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

Put local stacks, decisions, winner snapshots, and an optional offline FITS
archive under `<work-root>` as `local_siril_stacks`, `stack_choices.json`,
`selected_site_images`, and `offline_fits` respectively.

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

## 4. Launch the visual choice mode

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

## 5. Export reviewed winners

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
and winner snapshot when exported. Call out low-frame targets, mosaics,
conflicts, and unpaired images. If an optional offline archive exists, report
its size without deleting it unless Brian explicitly asks.
