import catalog from "../print_products/catalog.json";
import { purchaseUrlForProduct } from "./print-products.mjs";

export type PrintProduct = Readonly<{
  id: string; object: string; title: string; captureFile: string;
  previewUrl: string; checkoutUrl: string | null; sizeLabel: string; priceLabel: string;
}>;

export const printProducts: readonly PrintProduct[] = catalog.provider === "fourthwall"
  ? catalog.products.map(product => ({
    id: product.id, object: product.object, title: product.title,
    captureFile: product.captureFile, previewUrl: product.previewUrl,
    checkoutUrl: purchaseUrlForProduct(product, catalog.storefrontOrigin),
    sizeLabel: catalog.product.sizeLabel,
    priceLabel: `${new Intl.NumberFormat('en-US', {style:'currency', currency:catalog.product.retailPrice.currency, maximumFractionDigits:0}).format(catalog.product.retailPrice.value)} ${catalog.product.retailPrice.currency}`,
  })) : [];

export function printProductForCapture(file: string): PrintProduct | null {
  return printProducts.find(product => product.captureFile === file) ?? null;
}

export function printProductForId(id: string): PrintProduct | null {
  return printProducts.find(product => product.id === id) ?? null;
}
