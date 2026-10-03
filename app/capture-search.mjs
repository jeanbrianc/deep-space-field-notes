const aliasGroups = [['C 27', 'NGC 6888'], ['C 34', 'NGC 6960']];
function normalize(value) {
  return String(value ?? '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
}
function catalogKey(value) {
  const match = normalize(value).match(/^(m|messier|ngc|ic|c|caldwell)\s*(\d+[a-z]?)$/);
  return match ? `${({ messier: 'm', caldwell: 'c' })[match[1]] ?? match[1]}${match[2]}` : null;
}
export function aliasesForObject(object) {
  const base = object.replace(/\s*·\s*Mosaic$/i, '');
  return aliasGroups.find(group => group.includes(base)) ?? [base];
}
export function printPreviewForCapture(file, products) {
  const product = products.find(item => item.captureFile === file);
  return product?.checkoutUrl ? `/prints/${encodeURIComponent(product.id)}` : null;
}
export function searchCaptures(entries, query = '', printsOnly = false) {
  const normalized = normalize(query);
  const key = catalogKey(normalized);
  return entries.filter(entry => {
    if (printsOnly && !entry.printPreviewUrl) return false;
    const aliases = aliasesForObject(entry.object);
    if (key) return aliases.some(alias => catalogKey(alias) === key);
    const haystack = normalize([entry.title, entry.object, ...aliases].join(' '));
    return normalized.split(' ').every(word => haystack.includes(word));
  });
}
