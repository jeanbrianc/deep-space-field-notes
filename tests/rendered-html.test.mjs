import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("gallery ships the complete capture collection and interactions", async () => {
  const page = await readFile(new URL("app/page.tsx", root), "utf8");
  const css = await readFile(new URL("app/globals.css", root), "utf8");
  const layout = await readFile(new URL("app/layout.tsx", root), "utf8");
  const readme = await readFile(new URL("README.md", root), "utf8");

  const referencedImages = new Set(page.match(/Stacked_[^"\n]+_(?:cleaned|hand_processed)\.(?:jpg|png)/g) ?? []);
  assert.equal(referencedImages.size, 33);
  assert.doesNotMatch(page, /M 106|Messier 106/);
  assert.match(page, /ArrowLeft/);
  assert.match(page, /ArrowRight/);
  assert.match(page, /onTouchStart/);
  assert.match(page, /Observation ·/);
  assert.match(page, /Northern Michigan/);
  assert.match(page, /"traveling"/);
  assert.match(page, /raDeg/);
  assert.match(page, /Apparent position · J2000/);
  assert.match(page, /imageRatios/);
  assert.match(page, /photo-watermark/);
  assert.match(page, /© Brian Jean/);
  assert.match(page, /Processing view/);
  assert.match(page, /Gallery edit/);
  assert.doesNotMatch(page, /Seestar edit/);
  assert.match(page, /Which treatment earns the sky/);
  assert.match(page, /Informal browser poll/);
  assert.match(page, /Observation span/);
  assert.match(page, /not a controlled same-light test/);
  assert.match(page, /disabled=\{!voteReady \|\| voteBusy\}/);
  assert.match(page, /fetch\("\/api\/votes"/);
  assert.match(css, /\.processing-switch button:focus-visible/);
  assert.match(css, /min-height: 40px/);
  assert.match(readme, /counts represent browsers, not verified people/);
  assert.doesNotMatch(readme, /same captured light/);
  assert.match(css, /@keyframes skyTravel/);
  assert.match(css, /\.phase-traveling/);
  assert.match(css, /northern-michigan-night\.png/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(layout, /Deep Space Field Notes/);
  assert.doesNotMatch(layout, /codex-preview|_sites-preview/);

  const factBlock = page.match(/const facts:[\s\S]*?= \{([\s\S]*?)\n\};/)?.[1] ?? "";
  const descriptions = [...factBlock.matchAll(/^  (?:"[^"]+"|\w+): "([^"]+)",$/gm)].map((match) => match[1]);
  assert.equal(descriptions.length, 32);
  descriptions.forEach((description) => assert.ok(description.length >= 190 && description.length <= 255));
});

test("all gallery image assets and social card are present", async () => {
  const { readdir, access } = await import("node:fs/promises");
  const images = await readdir(new URL("public/images/", root));
  assert.equal(images.filter((name) => /\.(jpg|png)$/.test(name)).length, 33);
  const comparisonImages = await readdir(new URL("public/comparisons/images/", root));
  assert.equal(comparisonImages.filter((name) => /\.jpg$/.test(name)).length, 28);
  await access(new URL("public/og.png", root));
  await access(new URL("public/northern-michigan-night.png", root));
});

test("public comparisons pair full-field NightSkyAI exports with stable capture IDs", async () => {
  const registry = await readFile(new URL("app/comparisons.ts", root), "utf8");
  const manifest = JSON.parse(await readFile(new URL("public/comparisons/manifest.json", root), "utf8"));

  assert.equal(manifest.captureCount, 28);
  assert.equal(manifest.defaultedToSeestarCount, 17);
  assert.equal(new Set(manifest.captures.map((capture) => capture.captureId)).size, 28);
  assert.equal(manifest.captures.filter((capture) => capture.curatedDefault === "nightskyai").length, 6);
  assert.equal(manifest.captures.filter((capture) => capture.nightSkyAI.nightCount > 1).length, 19);
  assert.match(registry, /comparisonManifest\.captures/);
  assert.match(registry, /nightskyFirstTimestamp/);
  assert.match(registry, /nightskyNightCount/);
  assert.ok(manifest.captures.every((capture) => capture.baseline.frames >= 50 && capture.nightSkyAI.frames >= 50));
  for (const capture of manifest.captures) {
    assert.ok(capture.baseline.width > 0 && capture.baseline.height > 0);
    assert.deepEqual([capture.nightSkyAI.width, capture.nightSkyAI.height], [1080, 1920]);
    const bytes = await readFile(new URL(`public/comparisons/${capture.nightSkyAI.filename}`, root));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), capture.nightSkyAI.sha256);
    const baseline = await readFile(new URL(`public/images/${capture.baseline.filename}`, root));
    assert.equal(createHash("sha256").update(baseline).digest("hex"), capture.baseline.sha256);
  }
});
