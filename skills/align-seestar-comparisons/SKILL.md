---
name: align-seestar-comparisons
description: Audit and correct orientation, parity, rotation, scale, and crop mismatches between Deep Space Field Notes Gallery edits and NightSkyAI astrophotography comparisons, always treating the Gallery edit as the immutable alignment reference. Use when Brian says comparison orientations differ, asks to make Seestar versus NightSkyAI voting fair, wants every pair star-field aligned, or needs an alignment audit before publishing new comparison images.
---

# Align Seestar Comparisons

Use deterministic star-pattern registration to make each NightSkyAI comparison
match its Gallery edit's sky orientation and field. Preserve both inputs,
produce a derivative plus an audit manifest, and fail closed on ambiguity.

## Project defaults

Resolve `<gallery-root>` by preferring the current Deep Space Field Notes Git
root when it contains `.openai/hosting.json` and
`scripts/audit_comparison_alignment.py`. Otherwise locate a nearby
`deep-space-gallery` checkout containing those files. Ask for the path if the
checkout is ambiguous.

- Public manifest: `public/comparisons/manifest.json`
- Audit: `public/comparisons/alignment-audit.json`
- Aligned derivatives: `public/comparisons/aligned-v1`
- Project script: `<gallery-root>/scripts/audit_comparison_alignment.py`
- Bundled fallback: this skill package's `scripts/align_comparison_pairs.py`

Prefer the project script when it exists so repository history records the
exact algorithm used for publication. Use the bundled copy for another gallery
with the same manifest structure.

Run with a Python environment containing NumPy and Pillow. In Codex desktop,
load the bundled workspace dependencies and use its Python executable rather
than modifying the user's global Python installation. For a standalone skill
installation, the bounded versions are listed in this skill package's
`requirements-alignment.txt`.

## 1. Establish immutable inputs

1. Read the manifest and resolve every Gallery and NightSkyAI file beneath its
   declared public root.
2. Verify both SHA-256 hashes before analysis.
3. Treat `public/images/<baseline>` as the immutable reference. Never rotate,
   crop, rewrite, or replace it.
4. Treat the current NightSkyAI file as immutable source material. Never
   overwrite it, the linear FITS stack, or raw light frames.
5. Stop if paths escape their roots, hashes differ, a capture ID is malformed,
   or dependencies are unavailable.
6. Inspect alignment metadata before choosing a command. If every displayed
   NightSkyAI file is already a verified derivative, run `verify`; do not call
   that identity check a new pre-alignment audit. Use `audit` on a fresh
   unaligned export. Re-audit preserved originals only through their recorded
   `sourceFilename` and `sourceSha256`, never by guessing from Git history or a
   stale temporary file.

## 2. Audit before changing pixels

Run the report-only audit:

```bash
python3 <script> audit \
  --gallery <gallery-root> \
  --report public/comparisons/alignment-audit.json
```

The script extracts stars after background removal, builds translation-,
rotation-, reflection-, and scale-invariant triangle asterisms, scores
similarity transforms deterministically, and records the winner and alternate.
It does not use raw RGB correlation because color, stretch, resolution, and
crop can differ substantially between treatments.

Automatic acceptance requires all of these:

- at least 25 unique matched stars;
- at least 60% of stars in the shared footprint matched;
- audit RMS no greater than 2 detection pixels;
- matches spread across at least 3 of 9 reference regions; and
- at least twice as many matched stars as the strongest distinct alternative.

`needs-review` must never be applied automatically. Inspect every
`review-recommended` pair visually before using the bounded override. Report
the reference/candidate hashes, transform, inlier counts, ratio, RMS, coverage,
and alternate margin.

## 3. Apply only after review

Create registered derivatives and update the manifest only when the audit is
current and every pair is accepted:

```bash
python3 <script> apply \
  --gallery <gallery-root> \
  --audit public/comparisons/alignment-audit.json \
  --output-dir public/comparisons/aligned-v1 \
  --update-manifest
```

Add `--allow-review-recommended` only after manually checking those named
pairs. Never add an override for `needs-review`.

Application uses the complete fitted similarity transform to register
NightSkyAI into the Gallery edit's pixel coordinate system. This makes the
orientation, handedness, scale, and crop directly comparable. Missing source
coverage remains black rather than being invented; explain that black borders
mark non-overlapping sky.

The operation must:

- write a new derivative under `public/comparisons`, never over an input;
- preserve the original full-field filename, dimensions, and hash in alignment
  metadata;
- record the forward affine matrix and match evidence;
- re-audit the derivative and require normal parity, residual rotation within
  0.5 degrees, scale within 1%, bounded translation, and low RMS;
- default changed pairs to the Gallery edit until reviewed again; and
- derive a new comparison ID from the stable capture ID and both image hashes.

Hash-versioned comparison IDs preserve old vote rows as history without
counting votes cast on obsolete pixels. Do not delete or rewrite vote records.

## 4. Inspect and verify

Create contact sheets outside the source tree showing Gallery, original
NightSkyAI, and aligned NightSkyAI. Inspect every pair, with extra attention to
large rotations, cropped hand-processed references, black non-overlap borders,
and any review-recommended result.

Run the independent re-audit:

```bash
python3 <script> verify --gallery <gallery-root>
```

A second audit must classify the displayed NightSkyAI derivative as normal
parity and effectively identity orientation against the Gallery edit. Also
verify output hashes, dimensions, and manifest confinement.

After the comparison manifest changes, refresh the site's generated registry,
run the site tests/build, and publish through the Sites workflow. Never cast a
real public vote during QA.

## Completion report

Report pair count, confidence counts, reflected versus parity-preserving
solutions, manually reviewed pairs, verification results, original preservation,
vote-version reset behavior, and whether the public site was republished.
