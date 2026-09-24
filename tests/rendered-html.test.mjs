import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

test("gallery ships the complete capture collection and interactions", async () => {
  const page = await readFile(new URL("app/page.tsx", root), "utf8");
  const css = await readFile(new URL("app/globals.css", root), "utf8");
  const layout = await readFile(new URL("app/layout.tsx", root), "utf8");

  assert.equal((page.match(/_(?:cleaned|hand_processed)\.(?:jpg|png)"/g) ?? []).length, 34);
  assert.match(page, /ArrowLeft/);
  assert.match(page, /ArrowRight/);
  assert.match(page, /onTouchStart/);
  assert.match(page, /Observation ·/);
  assert.match(page, /Northern Michigan/);
  assert.match(page, /"traveling"/);
  assert.match(page, /raDeg/);
  assert.match(css, /@keyframes skyTravel/);
  assert.match(css, /\.phase-traveling/);
  assert.match(css, /northern-michigan-night\.png/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(layout, /Deep Space Field Notes/);
  assert.doesNotMatch(layout, /codex-preview|_sites-preview/);
});

test("all gallery image assets and social card are present", async () => {
  const { readdir, access } = await import("node:fs/promises");
  const images = await readdir(new URL("public/images/", root));
  assert.equal(images.filter((name) => /\.(jpg|png)$/.test(name)).length, 34);
  await access(new URL("public/og.png", root));
  await access(new URL("public/northern-michigan-night.png", root));
});
