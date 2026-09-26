import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("gallery ships the complete capture collection and interactions", async () => {
  const page = await readFile(new URL("app/page.tsx", root), "utf8");
  const css = await readFile(new URL("app/globals.css", root), "utf8");
  const layout = await readFile(new URL("app/layout.tsx", root), "utf8");

  const referencedImages = new Set(page.match(/Stacked_[^"\n]+_(?:cleaned|hand_processed)\.(?:jpg|png)/g) ?? []);
  assert.equal(referencedImages.size, 33);
  assert.equal((page.match(/<h1\b/g) ?? []).length, 1);
  assert.doesNotMatch(page, /M 106|Messier 106/);
  assert.match(page, /ArrowLeft/);
  assert.match(page, /ArrowRight/);
  assert.match(page, /onTouchStart/);
  assert.match(page, /Math\.abs\(distanceX\) > Math\.abs\(distanceY\)/);
  assert.match(page, /galleryInView/);
  assert.match(page, /Observation ·/);
  assert.match(page, /Northern Michigan/);
  assert.match(page, /A small telescope under a very large sky/);
  assert.match(page, /Skip introduction/);
  assert.match(page, /className="skip-link" type="button" onClick=\{\(\) => setEntrancePhase\("entered"\)\}/);
  assert.match(page, /Start exploring/);
  assert.match(page, /startExploring/);
  assert.match(page, /type="button" onClick=\{startExploring\} aria-controls="field-notes"/);
  assert.match(page, /entrancePhase/);
  assert.match(page, /focus\(\{ preventScroll: true \}\)/);
  assert.match(page, /event\.target === event\.currentTarget/);
  assert.match(page, /contenteditable='true'/);
  assert.match(page, /aria-label="Deep Space Field Notes observatory gallery"/);
  assert.match(page, /inert=\{entrancePhase !== "entered"\}/);
  assert.doesNotMatch(page, /entryStep|entryBeatRefs|data-entry-step/);
  assert.match(page, /IntersectionObserver/);
  assert.match(page, /selected observations/);
  assert.match(page, /50\+ frames · every published field/i);
  assert.match(page, /"traveling"/);
  assert.match(page, /raDeg/);
  assert.match(page, /Apparent position · J2000/);
  assert.match(page, /imageRatios/);
  assert.match(page, /photo-watermark/);
  assert.match(page, /© Brian Jean/);
  assert.match(page, /curatedDefault/);
  assert.match(page, /isNightSkyAI/);
  assert.match(page, /Observation span/);
  assert.match(page, /formatObservationSpan/);
  assert.doesNotMatch(page, /Processing view|Which treatment earns the sky|Informal browser poll/);
  assert.doesNotMatch(page, /\/api\/votes|submitVote|variantOverride|voteState/);
  assert.doesNotMatch(css, /\.processing-switch|\.vote-panel|\.vote-options/);
  assert.match(css, /\.entry-cta:focus-visible/);
  assert.match(css, /min-height: 52px/);
  assert.match(css, /touch-action: manipulation/);
  assert.match(css, /overscroll-behavior: none/);
  assert.match(css, /\.observatory-entry/);
  assert.match(css, /\.entry-scene/);
  assert.match(css, /\.entry-welcome/);
  assert.match(css, /\.entry-proof/);
  assert.match(css, /@keyframes entranceIris/);
  assert.match(css, /@keyframes entranceZoom/);
  assert.match(css, /@keyframes observatoryArrival/);
  assert.match(css, /\.site-entering/);
  assert.doesNotMatch(css, /300svh|\.entry-beat|\.entry-ledger/);
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
  const alignedImages = await readdir(new URL("public/comparisons/aligned-v1/", root));
  assert.equal(alignedImages.filter((name) => /\.jpg$/.test(name)).length, 28);
  await access(new URL("public/comparisons/alignment-audit.json", root));
  await access(new URL("public/og.png", root));
  await access(new URL("public/northern-michigan-night.png", root));
});

test("public comparisons preserve full-field sources and ship verified Gallery-aligned derivatives", async () => {
  const registry = await readFile(new URL("app/comparisons.ts", root), "utf8");
  const generated = JSON.parse(await readFile(new URL("app/comparisons.generated.json", root), "utf8"));
  const manifest = JSON.parse(await readFile(new URL("public/comparisons/manifest.json", root), "utf8"));

  assert.equal(manifest.captureCount, 28);
  assert.equal(manifest.defaultedToSeestarCount, 0);
  assert.equal(manifest.curation.source, "owner-review");
  assert.equal(manifest.curation.decisionCount, 28);
  assert.equal(manifest.curation.galleryCount, 15);
  assert.equal(manifest.curation.nightSkyAICount, 13);
  assert.equal(new Set(manifest.captures.map((capture) => capture.captureId)).size, 28);
  assert.equal(new Set(manifest.captures.map((capture) => capture.comparisonId)).size, 28);
  assert.equal(manifest.captures.filter((capture) => capture.curatedDefault === "seestar").length, 15);
  assert.equal(manifest.captures.filter((capture) => capture.curatedDefault === "nightskyai").length, 13);
  assert.equal(manifest.captures.filter((capture) => capture.nightSkyAI.nightCount > 1).length, 19);
  assert.match(registry, /\.\/comparisons\.generated\.json/);
  assert.equal(generated.schemaVersion, manifest.schemaVersion);
  assert.deepEqual(generated.captures, manifest.captures);
  assert.match(registry, /nightskyFirstTimestamp/);
  assert.match(registry, /nightskyNightCount/);
  assert.match(registry, /comparisonId/);
  assert.match(registry, /isGalleryAligned/);
  assert.ok(manifest.captures.every((capture) => capture.baseline.frames >= 50 && capture.nightSkyAI.frames >= 50));
  for (const capture of manifest.captures) {
    const expectedComparisonId = `comparison-${createHash("sha256")
      .update(`${capture.captureId}\0${capture.baseline.sha256}\0${capture.nightSkyAI.sha256}`)
      .digest("hex")
      .slice(0, 24)}`;
    assert.equal(capture.comparisonId, expectedComparisonId);
    assert.ok(capture.baseline.width > 0 && capture.baseline.height > 0);
    assert.deepEqual(
      [capture.nightSkyAI.width, capture.nightSkyAI.height],
      [capture.baseline.width, capture.baseline.height]
    );
    assert.deepEqual(
      [capture.nightSkyAI.alignment.sourceWidth, capture.nightSkyAI.alignment.sourceHeight],
      [1080, 1920]
    );
    assert.equal(capture.nightSkyAI.alignment.referencePolicy, "gallery-edit-is-immutable");
    assert.equal(capture.nightSkyAI.alignment.mode, "registered-to-gallery-edit");
    assert.equal(capture.nightSkyAI.alignment.verification.passed, true);
    assert.equal(capture.nightSkyAI.alignment.verification.reflected, false);
    assert.ok(Math.abs(capture.nightSkyAI.alignment.verification.rotationDegrees) <= 0.5);
    const bytes = await readFile(new URL(`public/comparisons/${capture.nightSkyAI.filename}`, root));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), capture.nightSkyAI.sha256);
    const sourceBytes = await readFile(
      new URL(`public/comparisons/${capture.nightSkyAI.alignment.sourceFilename}`, root)
    );
    assert.equal(
      createHash("sha256").update(sourceBytes).digest("hex"),
      capture.nightSkyAI.alignment.sourceSha256
    );
    const baseline = await readFile(new URL(`public/images/${capture.baseline.filename}`, root));
    assert.equal(createHash("sha256").update(baseline).digest("hex"), capture.baseline.sha256);
  }
});
