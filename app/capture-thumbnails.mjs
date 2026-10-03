import manifest from '../public/capture-thumbnails/manifest.json' with { type: 'json' };
export function thumbnailForCapture(file, source, rotation) {
  return manifest.entries.find(entry => entry.file === file && entry.source === source && entry.rotation === rotation) ?? null;
}
