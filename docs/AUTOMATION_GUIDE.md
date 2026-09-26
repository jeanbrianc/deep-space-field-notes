# From Seestar Drive to Deep Space Field Note

This guide covers the complete, repeatable path from a connected Seestar or
YOTUO drive to a reviewed Deep Space Field Notes release. There are two ways to
run it:

- **With Codex skills:** describe the outcome in ordinary language and let the
  skills run the guarded commands, show the review screens, and stop at the
  decisions that need a person.
- **From the terminal:** run the same local processing, review, alignment, and
  validation scripts directly. Every substantial write to the removable-drive
  pipeline is previewed where the tool supports it, and all working files stay
  under the ignored `work/` directory. The terminal route ends at a validated
  Git revision; public hosting still uses the separate Sites release workflow.

Both routes use the same quality rules: one strongest JPEG per capture folder,
at least 50 stacked frames, a preference for Brian's finished `proc` edit, and
the Gallery edit as the immutable alignment reference for public comparisons.

## Choose the route you need

| Goal | Stages to run | Result |
| --- | --- | --- |
| Make the best existing Seestar JPEGs available offline | Collect, then clean | A small offline set and a cleanup manifest |
| Rebuild targets from the original light frames | Stack, then review | Reproducible Siril stacks and human choices |
| Choose which treatment each tracked comparison should display | Review all tracked public pairs | 28 local display decisions; no site changes |
| Choose one public photo when a target was captured twice | Cull repeated targets | One personal winner per configured object |
| Prepare a fair Seestar/NightSkyAI comparison | Export a release candidate, then align | Both treatments show the same sky orientation and framing |
| Update the public experience | Promote the reviewed candidate, validate, then publish | A tested, versioned site release |

The fast JPEG route and the raw-FITS route are complementary. The first finds
the best image the telescope already created. The second builds NightSkyAI's
own stack from the source frames so it can be judged against the Gallery edit.

### Three local review queues

The same review-desk design supports three different decisions. Choose the
queue deliberately:

- `serve` compares the Gallery with newly built Siril/NightSkyAI stacks while
  `work/local_siril_stacks` still exists. Its size follows the eligible local
  stack manifests; it is not the fixed 28-pair public queue. It records the
  choices used for a later stack export in `work/stack_choices.json`.
- `review-public` reads the tracked, already-aligned
  `public/comparisons/manifest.json` and presents all 28 Gallery edit versus
  NightSkyAI pairs. It records the intended final display treatment in
  `work/public_display_choices.json` without changing `public/`, `app/`, or the
  deployed site.
- `cull` contains only the three groups in `gallery_cull_groups.json`:
  Andromeda, Western Veil, and Crescent Nebula. It answers which duplicate
  capture should remain, not which processing treatment should be displayed.

Finishing any queue completes only its local decision file. Applying those
decisions is a separate reviewed change, and publishing that change is a
separate explicit release action.

## Route A: run it with Codex skills

The repository keeps reviewable copies of the four astronomy skills under
`skills/`. When installed in Codex, use them in this order:

1. `$seestar-offline-best-stacks`
2. `$clean-up-seestar-photos`
3. `$seestar-fits-stack-review`
4. `$align-seestar-comparisons`
5. Sites validation and hosting, only after the release candidate is approved

The repository copies are the source-controlled definitions; placing a folder
under `skills/` does not install it automatically. On a fresh Codex setup, ask:

> Install or update the four local skills from this clone's `skills/` folder.
> Show me the differences before replacing any installed copy.

Brian's Codex installation is kept synchronized with these repository copies
when the workflow changes. A newly installed or updated skill is available to
new Codex tasks after the installation is complete.

### A good single starting request

> My Seestar drive is connected at `/Volumes/YOTUO`. Collect the best stack per
> folder, clean the 50-frame-or-better images while preferring finished edits in
> `~/Desktop/astro`, stack the original FITS directly from the drive, and open
> the local comparison review. Keep all generated files under this repository's
> ignored `work/` folder. Stop for my review choices before preparing anything
> public.

Codex can run the early stages together, but the side-by-side review is an
intentional human checkpoint. The public candidate should not be created from
undecided pairs.

### Stage 1 — collect the best telescope JPEGs

Use a request such as:

> Use `$seestar-offline-best-stacks` on `/Volumes/YOTUO`. Show me the dry-run
> selections first, then copy the highest-frame-count full-resolution JPEG from
> every capture folder into `work/offline_best_stacks`.

The skill ignores thumbnails, keeps the source folder structure, and writes
`work/offline_best_stacks/selected_jpegs.txt`. That list records the frame
count, local copy, and original drive path for every winner.

### Stage 2 — prepare the Gallery-quality images

Use a request such as:

> Use `$clean-up-seestar-photos` on `work/offline_best_stacks`. Write results to
> `work/cleaned_photos`, require at least 50 frames, and prefer a finished JPEG
> or PNG from matching `~/Desktop/astro/*_sub/proc` folders. Do not prune the
> previous outputs.

The skill identifies strong red or green backgrounds on its own. It uses a
finished `proc` result when available; otherwise it applies a conservative
color-neutralization and stretch. It does not darken every image by default.
When the collector manifest is present, cleanup treats its current selections
as authoritative and ignores older JPEG winners that remain safely archived.
The originals remain untouched, and the provenance of each result is recorded
in `work/cleaned_photos/cleanup_manifest.json`.

Ask for pruning only when you deliberately want to refresh the derivative set:

> Refresh the cleaned set and use the manifest-owned prune option. Show me what
> is in scope before running it.

Pruning is limited to outputs named by the prior cleanup manifest, but it is
still a deliberate removal step and is never part of the normal recipe.

### Stage 3 — stack the original FITS and review the result with `serve`

Use a request such as:

> Use `$seestar-fits-stack-review` to stack every eligible target directly from
> `/Volumes/YOTUO` into `work/local_siril_stacks`. Start with a dry run, use the
> natural preview, and do not copy the raw FITS archive locally. Then open the
> side-by-side chooser against this Gallery.

Stacking directly from the mounted drive avoids turning a removable archive
into tens of gigabytes of duplicate local data. The skill writes its Siril
work, linear stack, preview, log, and manifest under
`work/local_siril_stacks`; it does not write processing artifacts to the drive.

In the local chooser, decide each pair:

- **Keep gallery** preserves the current Seestar or hand-finished treatment.
- **Use our stack** selects the NightSkyAI/Siril result.
- **Decide later** leaves the pair incomplete and blocks the normal export.

When all pairs are decided, ask:

> Export the reviewed winners as a new immutable snapshot. Do not use the
> incomplete override.

### Stage 4 — choose one Gallery image for repeated targets

Use a request such as:

> Open the Gallery culling queue in the same local review desk. Let me choose
> one image for every repeated target, save my choices locally, and do not
> change or publish the Gallery yet.

This is intentionally separate from Seestar-versus-NightSkyAI review. It may
group alternate catalog names such as C 34 and NGC 6960 because the question is
which single finished photograph should remain public—not whether the source
frames belong to the same capture session. The Andromeda comparison uses the
distinct original 61-frame mosaic rather than the duplicate processed file that
was accidentally substituted during curation.

The culling desk contains exactly three decisions and records one winner per
group in `work/gallery_cull_choices.json`. It never edits `app/page.tsx`,
removes an image, or publishes the site. Apply the completed choices later as
a separate reviewed Gallery change.

### Stage 5 — audit orientation before a public comparison

Use a request such as:

> Use `$align-seestar-comparisons` to audit this fresh, unaligned public release
> candidate. Treat the Gallery edit as immutable. Show me every
> `review-recommended` or `needs-review` result before applying anything, then
> verify the aligned manifest.

The audit checks rotation, reflection, scale, and crop using matched stars. It
creates a separate aligned NightSkyAI derivative; it never rotates, crops, or
overwrites the Gallery edit or the original NightSkyAI export. Black borders
are retained when the NightSkyAI source does not cover the full Gallery field.

The statuses are gates, not decoration:

- `high-confidence` may be applied automatically.
- `review-recommended` requires visual inspection and explicit acceptance.
- `needs-review` blocks automatic application until the source or framing is
  corrected.

The comparison bundle currently checked into this repository is already
aligned. For that bundle, ask the skill to **verify** it. Do not run `audit` on
an aligned manifest; the tool rejects that because the derivative must not be
mistaken for an original source.

### Stage 6 — choose the final treatment for all 28 tracked pairs

Use a request such as:

> Open all 28 tracked, aligned Gallery edit versus NightSkyAI pairs in the
> final display review. Save my choices locally, and do not apply them or
> publish the site.

This uses `review-public`, not the three-item `cull` queue and not the
workspace-dependent `serve` queue. It reads the checked-in public comparison
manifest and saves `work/public_display_choices.json`. Reaching 28 of 28
finishes the decision record only. Applying the winners requires a separate
reviewed Gallery change, and publishing requires another explicit request.

### Stage 7 — validate and publish

Use a final request such as:

> Promote the reviewed candidate in a dedicated change, synchronize the site
> data, run the Python and site validations, show me the diff, and publish with
> Sites. Do not cast a real public vote during QA.

Publishing is a separate release operation. A successful local pipeline does
not silently replace `public/comparisons/` or deploy the website. The promotion
should preserve Git history, include the audit evidence, and be reviewed in the
diff before the public version is created.

## Route B: process and validate entirely from the terminal

The examples below start in the repository root. Substitute the mounted-drive
and processed-photo paths for the machine you are using.

### 1. Prerequisites and portable setup

You need:

- Python 3 with NumPy and Pillow
- Siril CLI 1.4 or newer
- Node.js 22.13 or newer and npm
- Enough free space under `work/` for the selected outputs and Siril's working
  estimate

Create an isolated Python environment and install the bounded automation
dependencies:

```bash
git clone https://github.com/jeanbrianc/deep-space-field-notes.git
cd deep-space-field-notes

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-automation.txt
npm ci
```

Confirm the tool versions:

```bash
python --version
node --version
npm --version
siril-cli --version
```

On macOS, Siril's command-line executable may be inside the app bundle:

```bash
/Applications/Siril.app/Contents/MacOS/siril-cli --version
```

If it is not on `PATH`, add this option to the Siril commands below:

```text
--siril /Applications/Siril.app/Contents/MacOS/siril-cli
```

Set the paths once for the session. Choose a new release-candidate name for
each run:

```bash
export SEESTAR_SOURCE="/Volumes/YOTUO"
export ASTRO_PROCESSED_ROOT="$HOME/Desktop/astro"
export WORK_ROOT="$PWD/work"
export RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export RELEASE_CANDIDATE="$WORK_ROOT/release-candidate-$RELEASE_ID"

mkdir -p "$WORK_ROOT"
```

Everything under `work/` is ignored by Git. Raw captures and local derivatives
therefore do not enter a commit accidentally.

### 2. Collect one best JPEG per folder

Preview the exact source-to-destination list:

```bash
python seestar_best_stack.py \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/offline_best_stacks" \
  --dry-run
```

If the mount and selections look right, run the copy:

```bash
python seestar_best_stack.py \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/offline_best_stacks"
```

Expected artifacts:

- selected full-resolution `Stacked_<frames>_...jpg` files, preserving their
  relative capture folders
- `work/offline_best_stacks/selected_jpegs.txt`

The collector chooses the largest frame count **within each folder**, not one
winner for the entire drive. Distinct older filenames are retained. If the
exact destination already contains identical bytes, it is safely reused; if
it contains different bytes, the run stops before copying or rewriting the
manifest. `selected_jpegs.txt` is the authoritative list for the current run.

### 3. Clean the selected images and prefer finished edits

```bash
python astro_cleanup.py \
  --source "$WORK_ROOT/offline_best_stacks" \
  --destination "$WORK_ROOT/cleaned_photos" \
  --processed-root "$ASTRO_PROCESSED_ROOT" \
  --min-frames 50
```

Expected artifacts:

- `*_hand_processed.jpg` or `.png` when a matching finished `proc` image exists
- otherwise, a conservative `*_cleaned.jpg`
- `work/cleaned_photos/cleanup_manifest.json`, including source, output, frame
  count, target, and provenance

When two selected source folders contain the same basename, both results are
kept and receive stable path-derived suffixes instead of overwriting one
another. A malformed or escaping collector manifest fails before cleanup or
manifest-owned pruning begins.

Do not add `--prune` to a routine run. If a deliberate refresh is needed,
inspect the existing cleanup manifest first; `--prune` removes the derivatives
owned by that manifest before recreating the set.

### 4. Stack original FITS directly from the removable drive

First inspect eligible groups and the disk estimate:

```bash
python seestar_siril_stack.py stack \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/local_siril_stacks" \
  --all \
  --min-frames 50 \
  --dry-run
```

Then run the same selection:

```bash
python seestar_siril_stack.py stack \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/local_siril_stacks" \
  --all \
  --min-frames 50
```

The default preview style is `natural`; no blanket darkening is applied. Each
successful run contains a provenance `manifest.json`, a preserved linear FITS,
a JPEG preview, and processing logs. Large registered intermediates are removed
after success unless `--keep-work` is explicitly supplied.

To process only selected targets, replace `--all` with one or more target
filters:

```bash
python seestar_siril_stack.py stack \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/local_siril_stacks" \
  --target "NGC 6960*" \
  --min-frames 50 \
  --dry-run
```

#### Optional: make an offline raw-FITS archive

This is not required for direct-from-drive stacking and can consume many
gigabytes. Use it only when a disconnected raw archive is explicitly wanted:

```bash
python seestar_fits_import.py \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/offline_fits" \
  --dry-run

python seestar_fits_import.py \
  --source "$SEESTAR_SOURCE" \
  --destination "$WORK_ROOT/offline_fits"
```

The importer writes `work/offline_fits/.seestar_fits_import_manifest.json` and
retains already archived files across later scans. It does not make this copy
unless you run the second command.

### 5. Review newly built Gallery and NightSkyAI stacks with `serve`

Launch the local-only review desk:

```bash
python seestar_stack_compare.py serve \
  --gallery . \
  --ours "$WORK_ROOT/local_siril_stacks" \
  --choices "$WORK_ROOT/stack_choices.json" \
  --port 8765 \
  --open
```

Choices are saved atomically in `work/stack_choices.json`. When every pair has
a final choice, create an immutable winner snapshot:

```bash
python seestar_stack_compare.py export \
  --gallery . \
  --ours "$WORK_ROOT/local_siril_stacks" \
  --choices "$WORK_ROOT/stack_choices.json" \
  --destination "$WORK_ROOT/selected_site_images"
```

The exporter creates a new UTC-named directory containing the chosen files and
`selection_manifest.json`. It refuses to overwrite an existing snapshot. Do
not use `--allow-incomplete` in the normal workflow: a **Decide later** choice
should remain a visible release blocker.

This queue is built from the available manifests under
`work/local_siril_stacks`. It is for reviewing fresh raw-FITS processing while
that workspace exists; it is not the fixed 28-decision public display review.

### 6. Choose one Gallery image for repeated targets

Launch the same local review desk in culling mode:

```bash
python seestar_stack_compare.py cull \
  --gallery . \
  --groups gallery_cull_groups.json \
  --choices "$WORK_ROOT/gallery_cull_choices.json" \
  --port 8765 \
  --open
```

The three configured groups are Andromeda, Western Veil, and Crescent Nebula.
NGC 6992 remains separate because it is the Eastern Veil. The Andromeda pair
uses the distinct original mosaic stored under `review_candidates/`, so both
choices represent real captures. Number keys select a winner; **Decide later**
remains incomplete.

The choice file records the exact candidate filenames and hashes, but this
command never changes the tracked Gallery. Review and apply the completed
choices in a separate Git diff.

### 7. Prepare and align a public comparison candidate

Never export directly over the tracked `public/comparisons/` bundle. Create a
new, isolated candidate root instead. The alignment tool needs the Gallery
baseline images beside that candidate, so copy those immutable references into
the new root:

```bash
mkdir -p "$RELEASE_CANDIDATE/public"
cp -R public/images "$RELEASE_CANDIDATE/public/images"

python seestar_stack_compare.py export-public \
  --gallery . \
  --ours "$WORK_ROOT/local_siril_stacks" \
  --choices "$WORK_ROOT/stack_choices.json" \
  --destination "$RELEASE_CANDIDATE/public/comparisons" \
  --min-frames 50
```

`export-public` requires a destination that does not already exist. If a
candidate path is already present, choose a different release-candidate name;
do not clear or reuse it. A missing or **Decide later** choice blocks export by
default. If a previously reviewed NightSkyAI candidate has changed while the
Gallery image is unchanged, the exporter instead records the safe Gallery
default as `candidate-changed-default`; that pair still needs a new human
review before promotion.

Audit the **fresh, unaligned** candidate. The audit is report-only:

```bash
python scripts/audit_comparison_alignment.py audit \
  --gallery "$RELEASE_CANDIDATE" \
  --report public/comparisons/alignment-audit.json
```

Read the report before continuing:

```bash
python -m json.tool \
  "$RELEASE_CANDIDATE/public/comparisons/alignment-audit.json"
```

If every capture is `high-confidence`, create derivatives and update the candidate
manifest:

```bash
python scripts/audit_comparison_alignment.py apply \
  --gallery "$RELEASE_CANDIDATE" \
  --audit public/comparisons/alignment-audit.json \
  --update-manifest
```

For `review-recommended`, inspect the visual match first. If it is acceptable,
repeat `apply` with `--allow-review-recommended`. A `needs-review` result must
be corrected at the stack/framing stage; it cannot be overridden by the apply
command.

Verify the finished candidate independently:

```bash
python scripts/audit_comparison_alignment.py verify \
  --gallery "$RELEASE_CANDIDATE"
```

Expected candidate artifacts:

- `public/comparisons/manifest.json`
- `public/comparisons/images/`, preserving the original NightSkyAI exports
- `public/comparisons/aligned-v1/`, containing Gallery-registered derivatives
- `public/comparisons/alignment-audit.json`, with hashes, transforms,
  matched-star evidence, review decisions, and verification results

For the already-aligned bundle in the repository, skip `audit` and `apply` and
run only:

```bash
python scripts/audit_comparison_alignment.py verify --gallery .
```

### 8. Review all 28 tracked display treatments with `review-public`

Launch the same local desk against the checked-in, aligned public comparison
manifest:

```bash
python seestar_stack_compare.py review-public \
  --gallery . \
  --choices "$WORK_ROOT/public_display_choices.json" \
  --port 8765 \
  --open
```

This is the 28-decision queue: each choice says whether the Gallery edit or its
aligned NightSkyAI treatment should ultimately be displayed for that tracked
observation. Choices are saved atomically in
`work/public_display_choices.json`. The command verifies and reads the tracked
pair assets, but it never mutates `public/`, edits `app/`, applies a winner, or
publishes the site.

Reaching **28 of 28** completes only the local choice file. Inspect that file,
apply the completed winners in a separate reviewed Git change, validate that
change, and publish it only through a separate explicit Sites release. For a
future comparison candidate, first promote and verify that candidate
deliberately, then rerun this queue against the newly tracked manifest.

### 9. Promote and validate, then hand off for hosting

Promotion is intentionally not an overwrite one-liner. Compare the complete
candidate with the tracked bundle, promote it in a dedicated Git change while
preserving the previous version in history, and review the resulting diff. A
read-only comparison can start with:

```bash
git diff --no-index -- \
  public/comparisons \
  "$RELEASE_CANDIDATE/public/comparisons"
```

That read-only comparison normally exits with status `1` when it finds
differences; in this case, that means there is a candidate change to review,
not that either directory was modified.

After the reviewed candidate has been promoted into
`public/comparisons/`, synchronize the app projection and validate everything:

```bash
npm run comparisons:sync
python -m unittest discover -v
python scripts/audit_comparison_alignment.py self-test
python scripts/audit_comparison_alignment.py verify --gallery .
npm test
npm run lint
git diff --check
```

Review at least these files before committing:

- `public/comparisons/manifest.json`
- `public/comparisons/alignment-audit.json`
- `app/comparisons.generated.json`
- the new original and aligned comparison images

There is no supported Sites deployment command in this repository. The
terminal-only workflow therefore ends at the tested Git revision. Publishing
with Sites happens only after that validation succeeds, using the Sites skill
or Sites product workflow. Keep the release public if that is the site's
intended access level, deploy the exact reviewed Git revision, and open the
deployed URL for a read-only smoke test.
Navigate, blink between treatments, and check mobile/desktop layout, but **do
not cast a real public vote during QA**. A vote changes production data and is
not a harmless rendering check.

## Recovery and decision points

### The dry run finds the wrong drive or no images

- Confirm `SEESTAR_SOURCE` is the mounted root you intended.
- Confirm it contains full-resolution `Stacked_<frames>_...jpg` files or
  top-level `Light_*.fit`/`.fits` files inside the expected capture folders.
- Do not switch to another connected volume merely because it looks similar;
  rerun the dry run with the explicit path.

### A stack fails or the default framing crops too much

The default `min` framing removes registration borders but may have no useful
common intersection across mixed sessions. Retry the existing failed run with
`cog` before restacking all source frames:

```bash
python seestar_siril_stack.py retry-framing \
  --run "$WORK_ROOT/local_siril_stacks/PATH_TO_FAILED_RUN" \
  --framing cog \
  --dry-run

python seestar_siril_stack.py retry-framing \
  --run "$WORK_ROOT/local_siril_stacks/PATH_TO_FAILED_RUN" \
  --framing cog
```

If `cog` still produces an unacceptable field, inspect the run and try
`--framing current`. Do not accept a severely cropped image merely to make the
pipeline finish.

### A preview looks too dark, bright, or incorrectly oriented

- `natural` is the standard preview and the default. Dark or dramatic looks
  are opt-in.
- Brightness, contrast, rotation, and flip controls create or refresh a JPEG
  derivative; they do not alter the linear FITS.
- Prefer the automatic star-field alignment for the final public comparison
  rather than guessing orientation by eye.

### A saved decision no longer matches

The review records image hashes and source provenance. If the Gallery image,
NightSkyAI stack, or raw-frame inventory changes, reopen the review desk and
decide that pair again. A stale preference should not silently follow new
pixels into publication.

### Alignment requests review

- Inspect `alignment-audit.json` and the candidate images.
- Accept `review-recommended` only after a visual check of the matched field.
- Return `needs-review` to the stack/framing stage.
- Keep the Gallery edit unchanged. Correct NightSkyAI or create a new
  derivative instead.

## What the workflow guarantees

- **The mounted archive is a source, not a workspace.** Collection and
  stacking write to the explicit local destination, not back to the drive.
- **Original images remain intact.** Cleanup creates derivatives. Alignment
  creates separate NightSkyAI derivatives. Neither changes the Gallery edit.
- **Refreshes have one current winner per folder.** Distinct older offline
  copies may be retained. Exact-path identical files are reused; different
  content fails closed. Cleanup consumes only the collector's current manifest.
- **Low-signal captures stay out.** The prepared and public paths enforce the
  50-frame minimum.
- **Finished human work wins when available.** Matching `proc` JPEG/PNG files
  are preferred over automated cleanup.
- **Reviews are durable and auditable.** Choices, stack manifests, selection
  snapshots, source hashes, and alignment evidence identify exactly which
  pixels were approved.
- **Existing releases are not silently replaced.** Winner snapshots and public
  candidate exports require new destinations, and promotion is a separate Git
  change.
- **Missing coverage remains honest.** Alignment uses black borders rather
  than inventing sky that was not captured.
- **Votes stay tied to a pixel pair.** Changing either public image creates a
  new comparison identity instead of mixing old votes with new treatments.

## Artifact map

| Stage | Artifact | Purpose |
| --- | --- | --- |
| Collect | `work/offline_best_stacks/selected_jpegs.txt` | Best-stack inventory and source paths |
| Clean | `work/cleaned_photos/cleanup_manifest.json` | Output provenance and processing method |
| Optional FITS copy | `work/offline_fits/.seestar_fits_import_manifest.json` | Incremental raw archive inventory |
| Stack | `work/local_siril_stacks/**/manifest.json` | Inputs, settings, outputs, hashes, and run status |
| Raw-stack review (`serve`) | `work/stack_choices.json` | Human choice for each eligible local Siril pair |
| Public display review (`review-public`) | `work/public_display_choices.json` | Final treatment choice for each of the 28 tracked aligned pairs |
| Duplicate cull (`cull`) | `work/gallery_cull_choices.json` | One personal winner for each of the three configured repeated targets |
| Winner export | `work/selected_site_images/<UTC>/selection_manifest.json` | Immutable chosen-image snapshot |
| Candidate | `work/release-candidate-*/public/comparisons/` | Isolated public comparison proposal |
| Published source | `public/comparisons/` | Reviewed originals, derivatives, and audit evidence |
| Site projection | `app/comparisons.generated.json` | Build-safe data generated from the public manifest |

That separation is the heart of the workflow: the drive remains an archive,
`work/` remains a private laboratory, Git records the reviewed release, and
Sites publishes only the exact version that passed validation.
