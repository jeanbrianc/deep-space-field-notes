import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const source = new URL("../public/comparisons/manifest.json", import.meta.url);
const destination = new URL("../app/comparisons.generated.json", import.meta.url);
const manifest = JSON.parse(await readFile(source, "utf8"));

if (!Array.isArray(manifest.captures) || manifest.captures.length === 0) {
  throw new Error("The public comparison manifest has no captures.");
}

const comparisonIds = new Set();
for (const capture of manifest.captures) {
  if (!/^comparison-[a-f0-9]{24}$/.test(capture.comparisonId ?? "")) {
    throw new Error(`Missing hash-versioned comparison ID for ${capture.object ?? "unknown target"}.`);
  }
  if (comparisonIds.has(capture.comparisonId)) {
    throw new Error(`Duplicate comparison ID: ${capture.comparisonId}`);
  }
  const comparisonMaterial = [
    capture.captureId,
    capture.baseline?.sha256,
    capture.nightSkyAI?.sha256,
  ].join("\0");
  const expectedComparisonId = `comparison-${createHash("sha256")
    .update(comparisonMaterial)
    .digest("hex")
    .slice(0, 24)}`;
  if (capture.comparisonId !== expectedComparisonId) {
    throw new Error(`Stale comparison ID for ${capture.object ?? "unknown target"}.`);
  }
  comparisonIds.add(capture.comparisonId);
  if (capture.nightSkyAI?.alignment?.verification?.passed !== true) {
    throw new Error(`NightSkyAI alignment is not verified for ${capture.object ?? "unknown target"}.`);
  }
}

const generated = {
  schemaVersion: manifest.schemaVersion,
  captures: manifest.captures,
};

await writeFile(destination, `${JSON.stringify(generated, null, 2)}\n`, "utf8");
