# Print fulfillment runbook

Deep Space Field Notes stays at `deepspace.brianjeanbuilds.com` on Sites. Fourthwall is the merchant of record and fulfillment provider. The gallery links to matching Fourthwall product pages and never collects payment-card details or shipping addresses. Do not connect this gallery domain to Fourthwall or replace its DNS records for this integration.

## Collection and customer flow

All 33 gallery captures have independent poster concepts. The two Andromeda captures, repeated common names, and catalog aliases each retain their own stable print ID. The gallery control opens `/prints/<id>` with the actual poster composition. Until sales are ready, it says **Print coming soon**. Once activated, **Buy this print** opens the exact Fourthwall product page in a new tab. Fourthwall supplies authoritative pricing, tax, shipping, payment and order support.

## Prepare the artwork

```bash
npm run prints:prepare
```

This uses the same capture-file-specific owner-selected source shown in the gallery. For NightSkyAI selections it uses the original full-field source, not the registered comparison derivative. The generator applies proportional centered cropping to fill a 2:3 sheet, places black field-note panels inside the edges, and preserves the source pixels' astronomical content. No photo is distorted or generatively retouched.

Web previews go to `public/prints/`; 3600 × 5400 concept JPGs and a provenance manifest go to ignored `print_products/masters/edge-to-edge-v1/`. A collection review sheet is saved alongside the concepts. Original photos, comparison provenance and legacy masters remain unchanged. `npm run prints:prepare-legacy` retains the previous four-print framed layout.

**Current gallery exports are not production-approved originals.** Their native resolution is about 31–90 pixels per inch at a 12-inch width. A 300 DPI tag on an enlarged file does not add detail. Obtain suitable higher-resolution exports and review the crop individually before uploading production artwork. Replacing a source or changing the rendered poster clears that product's approvals and purchase URL on regeneration.

## Fourthwall setup and activation

1. Confirm the connected Fourthwall shop identity. The proof-of-concept API returned Brian, `brian-wsn-shop`, with the shop in `COMING_SOON` mode. Reverify before writes; do not infer an account from a historic designer URL.
2. Confirm the intended retail price with Brian. $30 has been recommended but is not automatically authorized.
3. For each approved master, create the Enhanced Matte Paper Poster (Allcolor P001), White, 12 × 18 only, initially hidden. Inspect the complete Fourthwall render, paper edges, printable safe area and resolution warnings before saving.
4. Physical sample review remains required by this runbook. Do not order a sample or spend money without authorization. Record a passed sample honestly; never set `sampleApproved` merely to reveal a button.
5. Obtain authorization to open sales, verify the shop can accept purchases, publish the corresponding product and read back its status and exact customer-facing product URL. The earlier single Andromeda POC remains hidden; its framed artwork is different from these new concepts.
6. Set `storefrontOrigin` in `print_products/catalog.json` to the verified Fourthwall HTTPS storefront origin. Use a separate Fourthwall address, preserving the gallery domain. Set the product's exact `checkoutUrl`, `artworkApproved`, `sampleApproved`, and `productPublished` only when each is verified. All four requirements are checked, along with a product URL on that same origin.
7. Run `npm test` and publish this existing Site in place. Its public preview becomes purchasable for each approved record independently. Do not fabricate links from slugs or use the shop homepage as a substitute for a product page.

No Fourthwall API credential is needed in the public gallery. Store passwords only in the user's approved credential connection, never in site files or browser code.

## Listing language

Captured with a Seestar smart telescope under Northern Michigan skies and processed as part of Deep Space Field Notes. This 12 × 18 inch matte poster fills the sheet with a proportional portrait crop and black field-note panels. Astrophotography by Brian Jean.

Do not claim the full source field is preserved in this edge-to-edge layout.

## Price and shipping

The verified POC production cost is $11.50. Fourthwall's US card fee is 2.9% + $0.30 on the full customer payment, including shipping and tax; payment methods differ. Fourthwall's typical US first-item poster shipping estimate is $4–$7, not an exact variant/destination quote. Customer-paid shipping is passed through. At $30 retail, these assumptions leave about $17.13–$17.21 before tax-related processing fees and other business costs.

Sources: [Transaction fees](https://help.fourthwall.com/frequently-asked-questions/payments-and-pricing/transaction-fees) and [Shipping costs](https://help.fourthwall.com/frequently-asked-questions/shipping-and-orders/shipping-costs), checked September 30, 2026. No checkout is needed for the category estimate.

## Disable sales

Clear the product's `productPublished`, `artworkApproved`, or `sampleApproved`, or remove its checkout URL, then republish the gallery. The local preview remains, but purchasing stops. Also hide the product in Fourthwall if direct product-page sales should stop. A gallery-only takedown does not disable Fourthwall's own URL.
