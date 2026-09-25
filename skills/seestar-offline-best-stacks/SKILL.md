---
name: seestar-offline-best-stacks
description: Copy the highest-frame-count full-resolution Seestar stacked JPEG from every image folder into Brian's local offline collection, preserving folders and producing a manifest. Use when Brian says his Seestar drive, external astronomy drive, YOTUO drive, or image storage is connected and asks to collect, import, sync, grab, or refresh the best Seestar stacks, including shorthand such as "the drive is connected."
---

# Collect Seestar Best Stacks

Use `seestar_best_stack.py` from the current Deep Space Field Notes Git
checkout. Resolve `<gallery-root>` before doing any drive work:

1. Prefer the current Git root when it contains both
   `.openai/hosting.json` and `seestar_best_stack.py`.
2. Otherwise use the unique nearby `deep-space-gallery` checkout containing
   those files.
3. If no checkout can be identified unambiguously, ask for its path. Never
   download or substitute an unreviewed collector.

Choose `<work-root>` without splitting an existing archive:

- For a fresh clone, use the Git-ignored `<gallery-root>/work` directory.
- When continuing Brian's established archive, use `<gallery-root>/..` if its
  existing `offline_best_stacks/selected_jpegs.txt` confirms that this is the
  prior destination. Do not duplicate or silently reset that archive.

Write offline copies to `<work-root>/offline_best_stacks`.

## Workflow

1. Inspect `/Volumes/Seestar` and `/Volumes/YOTUO` without modifying either.
2. If exactly one exists, use it as `--source`.
3. If both exist and the user did not identify one, compare them read-only. If their relevant Seestar JPEG inventory is identical, use `/Volumes/YOTUO`; otherwise ask which drive to use before copying.
4. If neither exists, report that no recognized storage is mounted and stop.
5. Preview the exact selections without writing anything:

   ```bash
   python3 <gallery-root>/seestar_best_stack.py \
     --source <mounted-source> \
     --destination <work-root>/offline_best_stacks \
     --dry-run
   ```

6. Confirm the selected source paths, folder winners, and destination, then
   run:

   ```bash
   python3 <gallery-root>/seestar_best_stack.py \
     --source <mounted-source> \
     --destination <work-root>/offline_best_stacks
   ```

7. Verify `selected_jpegs.txt` exists and its data rows equal the reported
   selected total (new copies plus byte-identical files safely reused).
8. Report the source used, number of folder winners selected, copied and
   reused counts, offline destination, and manifest path.

Never delete source or destination images. Never combine two source drives in
one run. The collector ignores thumbnails and individual light frames; rejects
selected symbolic links and unsafe destination paths before copying; chooses
the largest `Stacked_<frames>_...jpg` count per folder; and preserves relative
folders locally. It reuses an existing exact-destination file only when its
bytes match and stops on different existing content. A refresh may retain a
distinct older offline copy, while the newly written `selected_jpegs.txt`
remains the authoritative current selection.
