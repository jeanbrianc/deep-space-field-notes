import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const catalogPath = path.join(root, "print_products", "catalog.json");
const manifestPath = path.join(root, "public", "comparisons", "manifest.json");
const outputDirectory = path.join(root, "print_products", "masters");

const catalog = JSON.parse(await readFile(catalogPath, "utf8"));
const comparisonManifest = JSON.parse(await readFile(manifestPath, "utf8"));
const { widthPx, heightPx, density, sizeLabel } = catalog.product;

if (catalog.provider !== "fourthwall") throw new Error("Print catalog provider must be fourthwall.");
if (widthPx !== 3600 || heightPx !== 5400 || density !== 300) {
  throw new Error("Print masters must remain exact 12 × 18 inch, 300 DPI files (3600 × 5400 pixels).");
}

await mkdir(outputDirectory, { recursive: true });

function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function sourceFor(capture) {
  if (capture.curatedDefault === "nightskyai") {
    const filename = capture.nightSkyAI.alignment?.sourceFilename ?? capture.nightSkyAI.filename;
    return {
      path: path.join(root, "public", "comparisons", filename),
      treatment: "NIGHTSKYAI SELECTED STACK",
      frames: capture.nightSkyAI.frames,
    };
  }
  if (capture.curatedDefault === "seestar") {
    return {
      path: path.join(root, "public", "images", capture.baseline.filename),
      treatment: "GALLERY SELECTED EDIT",
      frames: capture.baseline.frames,
    };
  }
  throw new Error(`Unsupported curated default for ${capture.object}: ${capture.curatedDefault}`);
}

function validateCheckoutUrl(product) {
  if (!product.checkoutUrl) {
    if (product.sampleApproved) throw new Error(`${product.id} is sample-approved but has no checkout URL.`);
    return;
  }
  const url = new URL(product.checkoutUrl);
  if (url.protocol !== "https:") throw new Error(`${product.id} checkout URL must use HTTPS.`);
}

async function sha256(filename) {
  return createHash("sha256").update(await readFile(filename)).digest("hex");
}

const generated = [];
for (const product of catalog.products) {
  validateCheckoutUrl(product);
  const capture = comparisonManifest.captures.find((item) => item.object === product.object);
  if (!capture) throw new Error(`No public comparison capture found for ${product.object}.`);

  const source = sourceFor(capture);
  const sourceMetadata = await sharp(source.path).metadata();
  if (!sourceMetadata.width || !sourceMetadata.height) throw new Error(`Cannot read ${source.path}.`);

  const imageBox = { width: 3020, height: 4000 };
  const scale = Math.min(imageBox.width / sourceMetadata.width, imageBox.height / sourceMetadata.height);
  const imageWidth = Math.round(sourceMetadata.width * scale);
  const imageHeight = Math.round(sourceMetadata.height * scale);
  const imageLeft = Math.round((widthPx - imageWidth) / 2);
  const imageTop = 700 + Math.round((imageBox.height - imageHeight) / 2);
  const resized = await sharp(source.path)
    .rotate()
    .resize({ width: imageWidth, height: imageHeight, fit: "inside", withoutEnlargement: false, kernel: sharp.kernel.lanczos3 })
    .toColourspace("srgb")
    .jpeg({ quality: 96, chromaSubsampling: "4:4:4" })
    .toBuffer();

  const overlay = Buffer.from(`
    <svg width="${widthPx}" height="${heightPx}" xmlns="http://www.w3.org/2000/svg">
      <rect x="120" y="120" width="3360" height="5160" fill="none" stroke="#2f2b23" stroke-width="3"/>
      <line x1="290" y1="552" x2="3310" y2="552" stroke="#a97b3e" stroke-width="4"/>
      <text x="290" y="330" fill="#d9ad6d" font-family="Courier New, monospace" font-size="54" font-weight="700" letter-spacing="13">NORTHERN MICHIGAN</text>
      <text x="290" y="435" fill="#74766f" font-family="Courier New, monospace" font-size="38" letter-spacing="9">DEEP SPACE FIELD NOTES · OBSERVATORY PRINT</text>
      <rect x="${imageLeft - 18}" y="${imageTop - 18}" width="${imageWidth + 36}" height="${imageHeight + 36}" fill="none" stroke="#675538" stroke-width="4"/>
      <line x1="290" y1="4805" x2="3310" y2="4805" stroke="#413a2d" stroke-width="3"/>
      <text x="290" y="4980" fill="#f0eee4" font-family="Georgia, serif" font-size="112" font-weight="700">${escapeXml(product.title)}</text>
      <text x="290" y="5100" fill="#aa8a5c" font-family="Courier New, monospace" font-size="42" font-weight="700" letter-spacing="9">${escapeXml(product.object)} · ${source.frames} FRAMES · ${source.treatment}</text>
      <text x="290" y="5200" fill="#74766f" font-family="Courier New, monospace" font-size="34" letter-spacing="6">CAPTURED UNDER NORTHERN MICHIGAN SKIES</text>
      <text x="3310" y="5200" text-anchor="end" fill="#a97b3e" font-family="Courier New, monospace" font-size="34" font-weight="700" letter-spacing="5">© BRIAN JEAN</text>
    </svg>
  `);

  const outputPath = path.join(outputDirectory, `${product.id}-12x18.jpg`);
  await sharp({
    create: { width: widthPx, height: heightPx, channels: 3, background: "#030706" },
  })
    .composite([
      { input: resized, left: imageLeft, top: imageTop },
      { input: overlay, left: 0, top: 0 },
    ])
    .withMetadata({ density })
    .jpeg({ quality: 96, chromaSubsampling: "4:4:4", mozjpeg: true })
    .toFile(outputPath);

  const outputMetadata = await sharp(outputPath).metadata();
  if (outputMetadata.width !== widthPx || outputMetadata.height !== heightPx) {
    throw new Error(`${outputPath} has unexpected dimensions.`);
  }

  generated.push({
    id: product.id,
    object: product.object,
    title: product.title,
    sizeLabel,
    source: path.relative(root, source.path),
    sourceTreatment: capture.curatedDefault,
    sourceDimensions: [sourceMetadata.width, sourceMetadata.height],
    sourceSha256: await sha256(source.path),
    output: path.relative(root, outputPath),
    outputDimensions: [outputMetadata.width, outputMetadata.height],
    outputDensity: outputMetadata.density,
    outputSha256: await sha256(outputPath),
  });
  console.log(`Prepared ${product.title}: ${path.relative(root, outputPath)}`);
}

await writeFile(
  path.join(outputDirectory, "manifest.json"),
  `${JSON.stringify({ generatedAt: new Date().toISOString(), products: generated }, null, 2)}\n`,
);
console.log(`Prepared ${generated.length} print masters at ${widthPx} × ${heightPx} pixels (${density} DPI).`);
