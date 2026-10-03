import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import sharp from 'sharp';
import { captureRotation } from '../app/capture-orientation.mjs';
import { purchaseUrlForProduct } from '../app/print-products.mjs';

const root = new URL('../', import.meta.url);
const file = 'Stacked_184_IC 5070_10.0s_LP_20250915-221725_hand_processed.png';
const catalog = JSON.parse(await readFile(new URL('print_products/catalog.json', root), 'utf8'));
const product = catalog.products.find(p => p.captureFile === file);

test('Pelican presentation preserves source provenance and does not rotate alternative captures', async () => {
  const bytes = await readFile(new URL(`public/images/${file}`, root));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '149292474a893a255c51f46d10e986367037048bcec096be203eee351296977c');
  assert.equal(product.sourceSha256, createHash('sha256').update(bytes).digest('hex'));
  assert.equal(captureRotation(file), 180);
  assert.equal(captureRotation(file, 'nightskyai'), 0);
  for (const other of catalog.products.filter(p => p.captureFile !== file)) {
    assert.equal(captureRotation(other.captureFile), 0);
    assert.equal(captureRotation(other.captureFile, 'nightskyai'), 0);
  }
});

test('poster photograph is rotated without mirroring, while information panels stay upright', async () => {
  const source = await readFile(new URL(`public/images/${file}`, root));
  const preview = await readFile(new URL('public/prints/pelican-nebula-ic-5070.jpg', root));
  const metadata = await sharp(preview).metadata();
  assert.deepEqual([metadata.width, metadata.height], [960,1440]);
  const expected = await sharp(source).rotate(180).resize(3600,5400,{fit:'cover',position:'centre'}).resize(960,1440).removeAlpha().raw().toBuffer();
  // Use an exposed photo area well outside both panels. It must match the rotated
  // field, not a mirrored source or an entirely rotated poster with inverted labels.
  const region = {left:650,top:650,width:150,height:150};
  const expectedPatch = await sharp(expected,{raw:{width:960,height:1440,channels:3}}).extract(region).raw().toBuffer();
  const actualPatch = await sharp(preview).extract(region).removeAlpha().raw().toBuffer();
  const averageError = actualPatch.reduce((sum,value,i) => sum + Math.abs(value-expectedPatch[i]),0)/actualPatch.length;
  assert.ok(averageError < 5, `Rotated photo differs by ${averageError}`);
  for (const region of [{left:55,top:120,width:20,height:10},{left:60,top:1370,width:20,height:10}]) {
    const pixels = await sharp(preview).extract(region).removeAlpha().raw().toBuffer();
    assert.ok(pixels.reduce((sum,value)=>sum+value,0)/pixels.length < 3, 'Panel must stay in its upright layout position');
  }
});

test('corrected artwork cannot link to an unverified previous supplier design', () => {
  assert.equal(product.displayRotationDegrees, 180);
  assert.notEqual(product.artworkSha256, 'ca8137b4630add09e757ea2a7f157cf23f12055d3a171fe14eaaf3beca101755');
  assert.equal(product.fourthwallProductId, '73b8d834-9c45-4850-a12b-9b1427a3789f');
  assert.equal(product.fourthwallVariantId, '306651a3-fc01-443d-9da1-9682209af9f9');
  const staleSupplier = {...product, artworkApproved:true, productPublished:true, sampleApproved:false,
    checkoutUrl:`${catalog.storefrontOrigin}/products/${product.fourthwallSlug}`,
    salesApproval:{approved:true,artworkSha256:'ca8137b4630add09e757ea2a7f157cf23f12055d3a171fe14eaaf3beca101755'}};
  assert.equal(purchaseUrlForProduct(staleSupplier, catalog.storefrontOrigin), null);
});
