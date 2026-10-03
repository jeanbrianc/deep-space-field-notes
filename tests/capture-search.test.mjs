import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import sharp from 'sharp';
import { searchCaptures, printPreviewForCapture } from '../app/capture-search.mjs';
import { thumbnailForCapture } from '../app/capture-thumbnails.mjs';
import { purchaseUrlForProduct } from '../app/print-products.mjs';
import thumbnails from '../public/capture-thumbnails/manifest.json' with { type: 'json' };
import identities from '../app/capture-identities.json' with { type: 'json' };
import comparisons from '../app/comparisons.generated.json' with { type: 'json' };
import { captureRotation } from '../app/capture-orientation.mjs';
const captures = [
  { id:'one', title:'Andromeda Galaxy', object:'M 31', printPreviewUrl:'/prints/one' },
  { id:'mosaic', title:'Andromeda Galaxy', object:'M 31 · Mosaic', printPreviewUrl:null },
  { id:'m3', title:'Messier 3', object:'M 3', printPreviewUrl:null },
  { id:'west', title:'Western Veil Nebula', object:'C 34', printPreviewUrl:'/prints/west' },
  { id:'west-other', title:'Western Veil Nebula', object:'NGC 6960', printPreviewUrl:null },
  { id:'crescent', title:'Crescent Nebula', object:'C 27', printPreviewUrl:'/prints/crescent' },
  { id:'bode', title:'Bode’s Galaxy', object:'M 81', printPreviewUrl:null },
];
test('search normalizes case/whitespace and distinguishes exact catalog numbers and repeated captures', () => {
  const ids = query => searchCaptures(captures, query).map(x => x.id);
  assert.deepEqual(ids('  AnDRomeda   Galaxy '), ['one','mosaic']);
  assert.deepEqual(ids('M31'), ['one','mosaic']);
  assert.deepEqual(ids('Messier 31'), ['one','mosaic']);
  assert.deepEqual(ids('m 3'), ['m3']);
  assert.deepEqual(ids('mosaic'), ['mosaic']);
  assert.deepEqual(ids('  WESTERN   veil '), ['west','west-other']);
  assert.deepEqual(ids('NGC6960'), ['west','west-other']);
  assert.deepEqual(ids('Caldwell34'), ['west','west-other']);
  assert.deepEqual(ids('ngc 6888'), ['crescent']);
  assert.deepEqual(ids("bode's"), ['bode']);
  assert.equal(searchCaptures(captures, '     ').length, captures.length);
  assert.deepEqual(ids('missing-object'), []);
  assert.deepEqual(searchCaptures(captures, 'M31', true).map(x => x.id), ['one']);
  assert.deepEqual(searchCaptures(captures, 'M 81', true), []);
});
test('print browsing uses exact source mapping and the existing guarded availability, never guesses URLs', () => {
  const origin='https://example.fourthwall.com';
  const ready={ id:'named-poster', captureFile:'exact-original.jpg', sampleApproved:true, artworkApproved:true, productPublished:true, checkoutUrl:`${origin}/products/exact` };
  const guarded = products => products.map(p => ({...p, checkoutUrl: purchaseUrlForProduct(p, origin)}));
  assert.equal(printPreviewForCapture('exact-original.jpg', guarded([ready])), '/prints/named-poster');
  assert.equal(printPreviewForCapture('wrong-alias.jpg', guarded([ready])), null);
  for (const changes of [{sampleApproved:false},{artworkApproved:false},{productPublished:false},{checkoutUrl:'https://evil.test/products/exact'}]) assert.equal(printPreviewForCapture('exact-original.jpg', guarded([{...ready,...changes}])), null);
  assert.equal(printPreviewForCapture('exact-original.jpg', []), null);
});
test('all 33 bounded thumbnails retain the selected source hash, proportional framing and presentation rotation', async () => {
  assert.equal(thumbnails.entries.length, 33);
  assert.equal(new Set(thumbnails.entries.map(x => x.file)).size, 33);
  let total=0;
  for (const {id,file} of identities) {
    const comparison=comparisons.captures.find(x=>x.baseline.filename===file);
    const nightsky=comparison?.curatedDefault==='nightskyai';
    const source=nightsky?`/comparisons/${comparison.nightSkyAI.alignment?.sourceFilename??comparison.nightSkyAI.filename}`:file;
    const rotation=captureRotation(file,nightsky?'nightskyai':'seestar');
    const thumb=thumbnailForCapture(file,source,rotation);
    assert.equal(thumb?.id,id);
    const original=await readFile(new URL(`../public/${source.startsWith('/')?source.slice(1):`images/${source}`}`,import.meta.url));
    assert.equal(createHash('sha256').update(original).digest('hex'),thumb.sourceSha256);
    const output=await readFile(new URL(`../public${thumb.url}`,import.meta.url));
    const metadata=await sharp(output).metadata();const originalMetadata=await sharp(original).metadata();
    assert.equal(metadata.width,thumb.width);assert.equal(metadata.height,thumb.height);
    assert.ok(thumb.width<=240&&thumb.height<=360);assert.ok(Math.abs(thumb.width/thumb.height-originalMetadata.width/originalMetadata.height)<.01);
    assert.equal(output.length,thumb.bytes);total+=output.length;
    assert.equal(thumbnailForCapture(file,'changed-source',rotation),null);
    assert.equal(thumbnailForCapture(file,source,rotation+180),null);
  }
  assert.ok(total<500000);
});
