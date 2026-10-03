import assert from 'node:assert/strict';
import test from 'node:test';
import identities from '../app/capture-identities.json' with { type: 'json' };
import catalog from '../print_products/catalog.json' with { type: 'json' };
import { captureIdForFile, captureFileForId, captureUrl, captureIdFromSearch } from '../app/capture-links.mjs';

test('all 33 permanent identities round-trip and match the exact existing poster capture', () => {
  assert.equal(identities.length, 33);
  assert.equal(new Set(identities.map(x => x.id)).size, 33);
  assert.equal(new Set(identities.map(x => x.file)).size, 33);
  for (const { id, file } of identities) {
    assert.equal(captureIdForFile(file), id);
    assert.equal(captureFileForId(id), file);
    assert.equal(captureIdFromSearch(new URL(captureUrl(id), 'https://example.test').search), id);
    assert.equal(catalog.products.find(p => p.captureFile === file)?.id, id);
  }
});

test('repeated target aliases and Andromeda mosaic have distinct fixed identities', () => {
  for (const pair of [['andromeda-galaxy', 'andromeda-galaxy-mosaic-m-31-mosaic'], ['western-veil-nebula','western-veil-nebula-c-34'], ['crescent-nebula-ngc-6888','crescent-nebula-c-27']]) {
    assert.ok(captureFileForId(pair[0]));
    assert.ok(captureFileForId(pair[1]));
    assert.notEqual(captureFileForId(pair[0]), captureFileForId(pair[1]));
  }
});

test('unknown, malformed, duplicate and redirect-like inputs fail to the root', () => {
  for (const id of [undefined, null, '', 'M 31', '../andromeda-galaxy', 'https://evil.test', '//evil.test', 'andromeda-galaxy/extra', 'ANDROMEDA-GALAXY']) {
    assert.equal(captureFileForId(id), null);
    assert.equal(captureUrl(id), '/');
  }
  for (const search of ['', '?capture=%E0%A4%A', '?capture=https://evil.test', '?capture=andromeda-galaxy&capture=orion-nebula', '?capture=andromeda-galaxy&capture=andromeda-galaxy', '?capture=']) assert.equal(captureIdFromSearch(search), null);
  assert.equal(captureIdFromSearch('?capture=orion-nebula&return=https://evil.test'), 'orion-nebula');
});
