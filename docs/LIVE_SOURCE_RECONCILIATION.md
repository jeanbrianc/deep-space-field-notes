# Published v22 source reconciliation

GitHub main was `8e01757d3802f4a0b79b3984b8812024da453dc0` on October 3, 2026. Sites version 22 identifies `2129e8e99b47353f2060bbefe906b97fac53b30d`; its publish deployment reports `succeeded` (October 1). A clean existing source checkout retained that exact commit. Work uses an isolated clone; the owner checkout was read only.

Main is an ancestor of v22. Five preserved commits (`9785717`, `044dcba`, `defb6c4`, `ad1fe01`, `2129e8e`) add 33 poster previews, their catalog, price updates, and the Pelican presentation correction. No force push or replacement of main is needed. `LIVE_V22_MANIFEST.json` records the original changed paths and Git blob identities of every protected photograph, comparison, preview, catalog and rotation file. Selected capture images and comparison provenance have no changes between main and v22. The reconciliation adds documentation and replaces the print page's internal anchor with Next Link to fix existing lint; its root-return behavior is unchanged.

## Validation and limits

- Published-source production build and 12 JavaScript tests pass.
- Declared NumPy/Pillow dependencies in an isolated virtual environment: all 135 Python tests pass. System Python initially lacked NumPy; no application change was needed.
- Lint initially failed on the print page internal anchor. After correction it passes with two existing `no-img-element` warnings.
- No GitHub Actions workflow exists in this source baseline; local checks are distinct from remote CI.
- Browser QA must independently verify telescope entry, previous/next, print preview/return and Pelican image/label orientation at desktop and phone sizes. Native UI automation could not initialize in this execution environment. Headless Chrome starts, but the local preview reports a listening URL that browser/curl cannot reach. This is an outstanding behavioral-QA gate, not a passing UI claim.
- Existing root print return resets the telescope entry. Issue #15 owns restoring exact observation context.
- Existing owner launch-approval records are preserved as historical source records. Physical print quality and original-exposure evidence remain unresolved under #3/#13; this reconciliation does not certify those claims.

## Release and rollback

This is a draft source-restoration PR, not a deployment. Retain v22 as the production reference. Downstream branches should name this reconciliation PR's exact reviewed head and retain all protected blob identities. Before a later authorized release, recheck main and Sites versions for concurrent work, run behavioral QA, and save only the exact pushed source state. A rollback must use the retained v22 saved version/source, never rebuild older GitHub main as though it contains the live poster collection. No source credential or access change is necessary for this restoration.
