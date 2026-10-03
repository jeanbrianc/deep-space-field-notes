import assert from 'node:assert/strict';
import test from 'node:test';
import { purchaseUrlForProduct } from '../app/print-products.mjs';

const origin = 'https://test-print-shop.fourthwall.com';
const ready = { sampleApproved:true, artworkApproved:true, productPublished:true, checkoutUrl:`${origin}/products/orion` };
test('only reviewed and published products can accept purchases', () => {
  assert.equal(purchaseUrlForProduct(ready, origin), ready.checkoutUrl);
  for (const key of ['sampleApproved', 'artworkApproved', 'productPublished']) {
    assert.equal(purchaseUrlForProduct({...ready,[key]:false}, origin), null);
  }
  assert.equal(purchaseUrlForProduct(ready, null), null);
  assert.equal(purchaseUrlForProduct({...ready,checkoutUrl:null}, origin), null);
});
test('purchase redirects stay on the verified HTTPS shop product pages', () => {
  for (const checkoutUrl of ['http://test-print-shop.fourthwall.com/products/orion', 'https://another-shop.fourthwall.com/products/orion', 'javascript:alert(1)', `${origin}/checkout`, `${origin}/products/`, 'https://user:pass@test-print-shop.fourthwall.com/products/orion']) {
    assert.equal(purchaseUrlForProduct({...ready,checkoutUrl}, origin), null);
  }
});
test('explicit owner launch approval can precede sample review only for the same artwork', () => {
  const product = {...ready, sampleApproved:false, artworkSha256:'reviewed-artwork', salesApproval:{approved:true, artworkSha256:'reviewed-artwork'}};
  assert.equal(purchaseUrlForProduct(product, origin), ready.checkoutUrl);
  for (const changed of [
    {salesApproval:{approved:false,artworkSha256:'reviewed-artwork'}},
    {artworkSha256:'replacement-artwork'},
    {artworkSha256:null},
    {artworkApproved:false},
    {productPublished:false},
  ]) assert.equal(purchaseUrlForProduct({...product,...changed}, origin), null);
});
