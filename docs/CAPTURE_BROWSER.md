# Capture and print browser

The observatory header provides **Browse captures** and **Browse prints** before telescope entry and while viewing a capture. The telescope and cinematic navigation remain available. Both entries open the same native modal dialog; Browse prints starts with **Available prints only** checked.

Cards are a projection of the existing `captures` collection in `app/page.tsx`, not a separate catalog. They show the selected photograph, common name, catalog object, capture date (or selected NightSkyAI observation span), frame count and exposure. Repeated Andromeda, Western Veil and Crescent observations remain separate cards. Capture links use the permanent #15 identities. Preview links come from the existing guarded print registry and exact source filename; the browser never guesses a supplier destination or activates a product.

Search is case-insensitive and normalizes Unicode punctuation and whitespace. Common-name words may be entered in different spacing/case. Compact catalog numbers such as `M31` and `NGC6960` work; exact catalog matching prevents `M 3` from matching M 31/M 33. Messier/Caldwell long names are supported. C 27/NGC 6888 and C 34/NGC 6960 aliases find their distinct observations. Mosaic is separately searchable. Count, Clear search and a no-results suggestion remain available with either filter state.

The search field receives focus on opening. Native dialog semantics trap keyboard focus and make the observatory inert. Close and Escape return focus to the triggering button without changing the current capture. Escape closes even when the native search field contains text. Opening interrupts any unfinished sky travel at its currently displayed capture; history navigation closes the modal and follows the capture-link contract. Dialog inputs, blank areas and thumbnails cannot trigger background arrow/swipe navigation.

## Bounded image delivery

`npm run captures:thumbnails` creates proportional JPEG derivatives no larger than 240 × 360 pixels, with no enlargement, from the same selected full-field sources as the gallery. Presentation rotation is applied to the photograph. Original images, selected treatments and poster assets are untouched. Fingerprinted filenames and `public/capture-thumbnails/manifest.json` record source hashes, selected paths, rotations and output dimensions. A source/rotation mismatch does not display a stale thumbnail. Regenerate and review thumbnails after a selected-source change; regression tests fail on stale hashes.

The complete 33-thumbnail set is 483,082 bytes. Cards mount only while the browser is open; IntersectionObserver supplies image URLs only near the viewport, and images also use native lazy loading/async decoding. At first opening developer QA observed 10 requested thumbnails on desktop, four at 390 pixels and two at 320 pixels, with no new full-resolution gallery requests from the browser. Card captions stay readable when a thumbnail is not loaded.

## Validation

Run `npm test`, `npm run lint` and the declared Python regression suite. New unit tests exercise common-name/catalog aliases, whitespace/case/punctuation, repeated observations, print availability through existing guards, missing mappings and all 33 derivative source hashes/dimensions/rotation contracts.

Start the isolated preview with `npm run dev -- --port 5173` and use its reported localhost URL. With Playwright/Chromium available, run:

```sh
PREVIEW_URL=http://localhost:5173 node tests/browser/capture-browser.mjs
PREVIEW_URL=http://localhost:5173 node tests/browser/capture-links.mjs
```

The optional `PLAYWRIGHT_MODULE`, `CHROME_PATH` and `BROWSER_ARTIFACT_DIR` settings follow [Capture links](CAPTURE_LINKS.md). The discovery runner checks all 33 canonical/preview mappings, initial thumbnail bounds, search and aliases, empty/clear/filter behavior, keyboard/focus/Escape, repeated cancellation, selection, exact print return and history at 1440, 390 and 320 pixel widths, plus modal opening/closing during normal-motion travel. It also records local scripted discovery steps/times under ignored `outputs/capture-browser/`. It does not enter supplier checkout or collect visitor telemetry. The previous capture-link runner remains a regression gate.

Standalone TypeScript checking retains the same five baseline errors documented in the capture-link runbook. Project lint has no errors; it reports the two existing full-image warnings and one analogous bounded-thumbnail warning. Physical print quality and original-source evidence remain governed by #3/#13; this browser adds no quality approval.

## Representative local discovery comparison

One developer run compares the retained sequential gallery path with browse + search + select in the same isolated checkout, desktop Chromium and reduced motion. Timing starts after root navigation. It includes automated interactions; cache, machine and browser conditions limit comparisons. This is a flow check, not a historical production-performance measurement or revenue evidence. In particular, the near-first Andromeda mosaic takes fewer steps sequentially.

| Capture | Sequential actions | Browser actions | Sequential ms | Browser ms |
| --- | ---: | ---: | ---: | ---: |
| Pelican | 24 | 3 | 1168 | 285 |
| Orion | 21 | 3 | 1188 | 343 |
| Triangulum | 28 | 3 | 1411 | 349 |
| Andromeda mosaic | 2 | 3 | 224 | 266 |

Actions count telescope entry plus Next clicks, versus opening the browser, entering a search and selecting the capture. Real conversion measurement belongs to #10; these timings do not establish customer demand or sales uplift.
