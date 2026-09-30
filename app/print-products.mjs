export function purchaseUrlForProduct(product, storefrontOrigin) {
  const ownerAuthorizedBeforeSample = product?.salesApproval?.approved === true
    && !!product.artworkSha256
    && product.salesApproval.artworkSha256 === product.artworkSha256;
  if (!(product?.sampleApproved || ownerAuthorizedBeforeSample) || !product.artworkApproved || !product.productPublished || !product.checkoutUrl || !storefrontOrigin) return null;
  try {
    const origin = new URL(storefrontOrigin);
    const url = new URL(product.checkoutUrl);
    if (origin.protocol !== 'https:' || url.protocol !== 'https:' || url.origin !== origin.origin || url.username || url.password) return null;
    if (!url.pathname.startsWith('/products/') || url.pathname === '/products/') return null;
    return url.toString();
  } catch {
    return null;
  }
}
