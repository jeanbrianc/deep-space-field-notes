import printCatalog from "../print_products/catalog.json";

export type PrintProduct = Readonly<{
  id: string;
  object: string;
  title: string;
  checkoutUrl: string;
  sizeLabel: string;
}>;

type PrintCatalogRecord = Readonly<{
  provider: string;
  product: Readonly<{ sizeLabel: string }>;
  products: readonly Readonly<{
    id: string;
    object: string;
    title: string;
    checkoutUrl: string | null;
    sampleApproved: boolean;
  }>[];
}>;

const catalog = printCatalog as PrintCatalogRecord;

function approvedCheckoutUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

const approvedProducts = new Map<string, PrintProduct>();

if (catalog.provider === "fourthwall") {
  for (const product of catalog.products) {
    const checkoutUrl = product.sampleApproved
      ? approvedCheckoutUrl(product.checkoutUrl)
      : null;
    if (!checkoutUrl) continue;
    approvedProducts.set(product.object, {
      id: product.id,
      object: product.object,
      title: product.title,
      checkoutUrl,
      sizeLabel: catalog.product.sizeLabel,
    });
  }
}

export function printProductForObject(object: string): PrintProduct | null {
  return approvedProducts.get(object) ?? null;
}
