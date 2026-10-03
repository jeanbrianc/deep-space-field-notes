import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import sharp from 'sharp';
import identities from '../app/capture-identities.json' with { type: 'json' };
import comparisons from '../app/comparisons.generated.json' with { type: 'json' };
import { captureRotation } from '../app/capture-orientation.mjs';
const root = new URL('../', import.meta.url);
const directory = new URL('public/capture-thumbnails/', root);
await mkdir(directory, { recursive: true });
const entries = [];
for (const { id, file } of identities) {
  const comparison = comparisons.captures.find(item => item.baseline.filename === file);
  const nightsky = comparison?.curatedDefault === 'nightskyai';
  const source = nightsky ? `/comparisons/${comparison.nightSkyAI.alignment?.sourceFilename ?? comparison.nightSkyAI.filename}` : file;
  const rotation = captureRotation(file, nightsky ? 'nightskyai' : 'seestar');
  const bytes = await readFile(new URL(`public/${source.startsWith('/') ? source.slice(1) : `images/${source}`}`, root));
  const sourceSha256 = createHash('sha256').update(bytes).digest('hex');
  const fingerprint = createHash('sha256').update(`${sourceSha256}:${rotation}:240x360:inside:q72:v1`).digest('hex').slice(0, 16);
  const name = `${id}.${fingerprint}.jpg`;
  const thumbnail = await sharp(bytes).rotate(rotation || undefined).resize({ width: 240, height: 360, fit: 'inside', withoutEnlargement: true }).jpeg({ quality: 72 }).toBuffer();
  await writeFile(new URL(name, directory), thumbnail);
  const { width, height } = await sharp(thumbnail).metadata();
  entries.push({ id, file, source, rotation, sourceSha256, url: `/capture-thumbnails/${name}`, width, height, bytes: thumbnail.length });
}
await writeFile(new URL('manifest.json', directory), `${JSON.stringify({ schemaVersion: 1, entries }, null, 2)}\n`);
console.log(`Prepared ${entries.length} proportional thumbnails, ${entries.reduce((sum, item) => sum + item.bytes, 0)} bytes; originals unchanged.`);
