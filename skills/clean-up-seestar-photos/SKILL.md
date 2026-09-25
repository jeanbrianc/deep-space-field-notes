---
name: clean-up-seestar-photos
description: Conservatively clean up downloaded Seestar astrophotography JPEGs by neutralizing colored sky backgrounds, stretching the histogram, preserving stellar highlights and nebula color, reducing chroma noise, and writing non-destructive outputs plus a manifest. Use when Brian asks to clean up, color-correct, enhance, process, fix red or purple casts, histogram-align, or touch up Seestar photos after download.
---

# Clean Up Seestar Photos

Use `astro_cleanup.py` from the current Deep Space Field Notes Git checkout.
Resolve `<gallery-root>` by preferring the current Git root when it contains
both `.openai/hosting.json` and `astro_cleanup.py`; otherwise locate a nearby
`deep-space-gallery` checkout with those files. Ask for the clone path if there
is more than one plausible checkout. Never download or substitute an
unreviewed processor.

Choose `<work-root>` without splitting an existing archive:

- For a fresh clone, use the Git-ignored `<gallery-root>/work` directory.
- When continuing Brian's established archive, use `<gallery-root>/..` if its
  existing `offline_best_stacks/selected_jpegs.txt` or
  `cleaned_photos/cleanup_manifest.json` proves that it is the prior workspace.

Run with a Python environment containing Pillow and NumPy. In Codex desktop,
load the bundled workspace dependencies and use the returned Python executable
rather than modifying the user's global Python installation.

## Workflow

1. Default the input to `<work-root>/offline_best_stacks` when the user asks to process the downloaded or offline collection without naming a path. When `selected_jpegs.txt` exists there, treat it as the authoritative current selection; fail closed if it is malformed or escapes the offline root, and do not rescan older retained winners.
2. Require at least 50 stacked frames by default; exclude lower-frame captures instead of trying to rescue them.
3. When `~/Desktop/astro` exists, inspect its `<object>_sub/proc` folders for matching finished JPEG or PNG exports. Prefer those hand-processed files over automatic cleanup; when duplicates exist, favor stretched GraXpert results and lossless PNGs. Omit `--processed-root` when that archive is unavailable; do not invent a replacement.
4. Write every result directly into the single flat folder `<work-root>/cleaned_photos`; never overwrite inputs. Preserve familiar filenames when unique, and use deterministic path-derived suffixes when two selected folders would otherwise collide.
5. Run the processor with `--source`, `--destination`, and `--min-frames 50`;
   add `--processed-root ~/Desktop/astro` when that archive exists. Do not use
   `--prune` in a routine run. Add it only for an explicitly
   requested refresh after verifying the destination and its manifest: pruning
   may remove only derivatives owned by the previous cleanup manifest, never
   input images.
6. Inspect representative outputs visually. Compare sky neutrality, white-star color, clipped highlights, nebula preservation, and noise.
7. If the result is too dark or harsh, lower `--stretch`; if too colorful, lower `--saturation`. Keep changes conservative.
8. Report the processed count, excluded low-frame count when known, hand-processed preference count, flat output folder, and generated `cleanup_manifest.json`.

For a single image:

```bash
<python> <gallery-root>/astro_cleanup.py \
  --source 'Stacked_153_IC 5146_10.0s_LP_20251009-214559.jpg' \
  --destination <work-root>/cleaned_photos
```

Single-file input still requires the canonical Seestar stacked filename, and
the encoded frame count must meet the default 50-frame gate. The processor
does not infer missing capture metadata from an arbitrary JPEG name.

Read [open-source-tools.md](references/open-source-tools.md) before recommending stacking, raw-data processing, AI gradient removal, or external installations.

Do not use generative fill or synthesize astronomical detail by default. Distinguish presentational AI denoising from scientifically faithful calibration. Prefer FITS/raw inputs and Siril when stacking or photometric color calibration is requested; JPEG cleanup is a finishing pass, not a replacement for calibration.
