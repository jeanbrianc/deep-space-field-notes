import { readFile, writeFile } from "node:fs/promises";

const source = new URL("../public/comparisons/manifest.json", import.meta.url);
const destination = new URL("../app/comparisons.generated.json", import.meta.url);
const manifest = JSON.parse(await readFile(source, "utf8"));

if (!Array.isArray(manifest.captures) || manifest.captures.length === 0) {
  throw new Error("The public comparison manifest has no captures.");
}

const generated = {
  schemaVersion: manifest.schemaVersion,
  captures: manifest.captures,
};

await writeFile(destination, `${JSON.stringify(generated, null, 2)}\n`, "utf8");
