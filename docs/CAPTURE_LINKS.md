# Capture links and print return

Every published observation has a permanent identity in `app/capture-identities.json`, bound to its exact capture filename. The IDs are frozen values, initially matching existing poster slugs. Do not regenerate them from titles, target aliases, product names, alphabetical position or collection indexes. Keep an existing identity when its display title or processing treatment changes. A new observation needs a new unique ID and filename; standard and mosaic Andromeda, both Western Veil records and both Crescent records remain separate observations.

The canonical gallery URL is `/?capture=<id>`, for example `/?capture=pelican-nebula-ic-5070`. A fresh load selects its reviewed treatment and matching poster preview. The plain `/` retains the telescope entry. Unknown, malformed, empty or repeated `capture` parameters fail safely to that entry. Extra redirect-like parameters are never followed.

Entering the telescope and moving previous/next creates browser history entries. During sky travel the URL changes when the destination photograph becomes current. Back/Forward restores the observation immediately and cancels any pending travel timer. Returning to the original root history entry restores the empty telescope. Rapid repeated clicks cannot enqueue competing travel transitions. The telescope button stays disabled until browser navigation is ready, avoiding interactions before hydration.

Each print preview derives its return URL from the product's exact capture filename and the permanent identity registry. It never accepts a supplied return URL or another capture from query parameters. Existing preview paths, supplier destinations, crop disclosures, prices, source pixels and selected treatments remain unchanged.

`Capture link` is a standard internal anchor. `Copy capture link` attempts the browser clipboard API and announces success or failure. When clipboard access is unavailable or denied, a labeled, read-only URL field provides a selectable fallback. Buttons, links and that field do not trigger gallery arrow-key or swipe navigation. No social SDK, tracker or supplier checkout integration is added.

## Validation

Run the production build/JavaScript tests and lint with `npm test` and `npm run lint`. Identity tests cover all 33 mappings, repeated object aliases, mosaic distinction and invalid IDs. Run Python regressions in an environment with `requirements-automation.txt` installed.

The browser runner requires Playwright and a Chromium browser. It uses only the isolated preview and does not follow supplier checkout links. Start `npm run dev -- --port 5173`, using the exact reported localhost URL (Vinext may bind IPv6 localhost rather than IPv4). In a separate terminal:

```sh
PREVIEW_URL=http://localhost:5173 node tests/browser/capture-links.mjs
```

If Playwright is installed outside the project, set `PLAYWRIGHT_MODULE` to its module path. Set `CHROME_PATH` to an installed Chrome executable when using that browser instead of Playwright's bundled Chromium. `BROWSER_ARTIFACT_DIR` optionally changes the default ignored `outputs/capture-links` screenshot directory. Developer validation on this Mac used Playwright 1.48.2 with installed Chrome; none of that temporary tooling is shipped as an application dependency.

The runner checks every capture's selected source, direct load, reload, matching preview and exact print return at desktop/phone sizes; history, fresh tabs, rapid clicks, clipboard success/denial, invalid inputs and keyboard/touch isolation; and Back during normal-motion travel before and after URL commit. It fails on page errors and checks mobile horizontal overflow. Clipboard outcomes are deterministic injected browser fixtures, not a claim about every browser's permission UI.

The source baseline has five existing standalone `tsc --noEmit` errors: DOM fallback narrowing in the unchanged IntersectionObserver fallback and missing Cloudflare runtime declarations. The same errors reproduce on reconciliation head `f8ef6c977b27bd74276832a67b197f2ffe81638f`. Production build and project lint remain the existing required checks; this navigation change introduces no additional standalone type errors.

## Collection discovery

The observatory header’s Browse captures and Browse prints entries use these same permanent capture URLs and exact print-return mappings. See [Capture and print browser](CAPTURE_BROWSER.md) for the searchable index, verified-print filter, modal focus behavior and bounded thumbnails.
