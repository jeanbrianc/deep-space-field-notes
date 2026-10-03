import identities from './capture-identities.json' with { type: 'json' };

// These explicit identifiers are a permanent URL contract, never regenerated
// from display titles, catalog aliases, collection ordering or product names.
const byId = new Map(identities.map(({ id, file }) => [id, file]));
const byFile = new Map(identities.map(({ id, file }) => [file, id]));
if (byId.size !== identities.length || byFile.size !== identities.length) {
  throw new Error('Capture identities must be unique');
}
export function captureIdForFile(file) { return byFile.get(file) ?? null; }
export function captureFileForId(id) { return typeof id === 'string' ? byId.get(id) ?? null : null; }
export function captureUrl(id) { return captureFileForId(id) ? `/?capture=${encodeURIComponent(id)}` : '/'; }
export function captureIdFromSearch(search) {
  const params = new URLSearchParams(search);
  const values = params.getAll('capture');
  return values.length === 1 && captureFileForId(values[0]) ? values[0] : null;
}
