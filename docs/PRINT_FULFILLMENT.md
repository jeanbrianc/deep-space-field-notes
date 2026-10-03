# Print fulfillment runbook

Deep Space Field Notes stays at `deepspace.brianjeanbuilds.com` on Sites. Fourthwall is the merchant of record and fulfillment provider. The gallery links to matching Fourthwall product pages and never collects payment-card details or shipping addresses. Do not connect this gallery domain to Fourthwall or replace its DNS records for this integration.

## Collection and customer flow

All 33 gallery captures have independent posters. On September 30, 2026, all 33 matching Fourthwall listings were verified PUBLIC and AVAILABLE, with one White 12 × 18 variant at $25 USD. The shop was verified LIVE, and every exact product page opened publicly without a password. The two Andromeda captures, repeated common names, and catalog aliases each retain their own stable print ID. The gallery control opens `/prints/<id>` with the actual poster composition. Until sales are ready, it says **Print coming soon**. Once activated, **Buy this print** opens the exact Fourthwall product page in a new tab. Fourthwall supplies authoritative pricing, tax, shipping, payment and order support.

Complete Fourthwall mockups were visually reviewed before saving, including the paper edges and inset text panels. The API does not expose the designer's safe-area overlays or resolution warnings, so those checks are not recorded as passed. Physical quality remains unverified until the Orion sample is inspected.

## Prepare the artwork

```bash
npm run prints:prepare
```

This uses the same capture-file-specific owner-selected source shown in the gallery. For NightSkyAI selections it uses the original full-field source, not the registered comparison derivative. The generator applies proportional centered cropping to fill a 2:3 sheet, places black field-note panels inside the edges, and preserves the source pixels' astronomical content. No photo is distorted or generatively retouched.

Web previews go to `public/prints/`; 3600 × 5400 concept JPGs and a provenance manifest go to ignored `print_products/masters/edge-to-edge-v1/`. A collection review sheet is saved alongside the concepts. Original photos, comparison provenance and legacy masters remain unchanged. `npm run prints:prepare-legacy` retains the previous four-print framed layout.

### Correct an existing photograph's orientation

`app/capture-orientation.json` binds presentation rotation to the exact capture filename and selected treatment. The Pelican gallery edit uses 180°; its alternative NightSkyAI source is not rotated. The gallery rotates only its photograph, leaving captions and watermark upright. The current poster generator uses the same setting before adding its information panels. Original files and registered comparison references stay unchanged; the comparison viewer continues to show the original alignment reference.

Regenerate just the affected poster with `npm run prints:prepare -- --only pelican-nebula-ic-5070`. Other catalog entries, approvals, previews, and the full collection review remain unchanged. A targeted provenance manifest and review sheet are written alongside the new master. The preview URL is fingerprinted to avoid stale browser caching. Regeneration preserves Fourthwall product and variant identifiers but clears approvals and the purchase link when the artwork changes.

**Pelican correction verified September 30:** The corrected artwork replaced the design on the existing Fourthwall product `pelican-nebula-northern-michigan`; no duplicate was created. The complete new supplier render was visually inspected before applying its customization to the existing offer with `keepExistingPrices: true`. Readback confirmed all four live mockup hashes match the reviewed previews, the same product and variant IDs remain PUBLIC/AVAILABLE, and the price was $25 at that correction. The later approved poster price is $20, as described below. The merchant feed and public product page verified the original URL. Physical sample quality remains unverified.

For future corrections, reconfirm the shop and inspect the complete new render before restoring its artwork-bound approval and exact purchase URL. `productPublished: false` on regenerated artwork indicates that this artwork has not been verified as published; it does not mean that the existing supplier listing was hidden. Apply the reviewed customization to the existing offer, preserving prices; verify the new live mockups and unchanged listing identity before restoring purchase links. Publish the matching gallery correction only after supplier verification, or explicitly record any partial release.

**Current gallery exports are not production-approved originals.** Their native resolution is about 31–90 pixels per inch at a 12-inch width. A 300 DPI tag on an enlarged file does not add detail. Suitable higher-resolution exports remain preferable. On September 30, Brian explicitly authorized public sales for the complete 33-print collection before the Orion sample arrives for the October 1 demo; this authorization does not establish that physical quality has passed inspection. Brian ordered a physical Orion sample of the current edge-to-edge artwork to judge its real printed quality. Its quality review remains pending. Replacing a source or changing the rendered poster clears that product's approvals and purchase URL on regeneration.

## Fourthwall setup and activation

1. Confirm the connected Fourthwall shop identity. The API returned Brian, `brian-wsn-shop`, verified LIVE on September 30. Reverify before writes; do not infer an account from a historic designer URL.
2. Brian approved $20 USD per 12 × 18 print on September 30, 2026, superseding the initial $25 launch price. Apply $20 to matching public posters, preserve exact artwork/product/variant identities, and verify each price by supplier readback plus the public merchant feed. Keep the shared gallery retail price consistent. Customer-paid shipping remains calculated at checkout; this approval creates no sample order or price-reset schedule.
3. For each approved master, create the Enhanced Matte Paper Poster (Allcolor P001), White, 12 × 18 only. Inspect the complete Fourthwall render and paper edges before saving; check printable safe areas and resolution warnings when the designer exposes them. The API creation step temporarily stages a hidden listing, then immediately publishes and verifies it when public sales are authorized. Final collection listings must be public.
4. Physical sample review is the default launch gate. Brian may explicitly authorize earlier sales for reviewed artwork; he authorized the complete collection on September 30. Record that decision separately in `salesApproval`, binding it to the exact artwork SHA, and keep `sampleApproved` false until physical inspection passes. Do not order a sample or spend money without authorization.
5. Obtain authorization to open sales, verify the shop can accept purchases, publish the corresponding product and read back its status and exact customer-facing product URL. The earlier single Andromeda POC remains hidden; its framed artwork is different from these new concepts.
6. Set `storefrontOrigin` in `print_products/catalog.json` to the verified Fourthwall HTTPS storefront origin. Use a separate Fourthwall address, preserving the gallery domain. Set the product's exact `checkoutUrl`, `artworkApproved`, and `productPublished` only when each is verified. Purchasing also requires either an approved physical sample or explicit owner `salesApproval` for that same artwork hash. The complete 33-print collection is authorized for early public sales; the Orion sample is ordered and awaiting review. Each approved layout retains its own artwork-bound launch approval. Changed artwork invalidates the early-sales approval. Verify the storefront is Live and the exact page is publicly accessible without a password before linking it.
7. Run `npm test` and publish this existing Site in place. Its public preview becomes purchasable for each approved record independently. Do not fabricate links from slugs or use the shop homepage as a substitute for a product page.

No Fourthwall API credential is needed in the public gallery. Store passwords only in the user's approved credential connection, never in site files or browser code. Brian provided an AWS Secrets Manager reference: `fourthwall/api/creds` in `us-east-1`, with `API_USERNAME` and `API_PASSWORD` fields. Use the AWS Secrets Manager skill's runtime dynamic-reference wrapper for future connections, with sanitized identity verification. Never print or persist resolved secret values.

## Listing language

Captured with a Seestar smart telescope under Northern Michigan skies and processed as part of Deep Space Field Notes. This 12 × 18 inch matte poster fills the sheet with a proportional portrait crop and black field-note panels. Astrophotography by Brian Jean.

Do not claim the full source field is preserved in this edge-to-edge layout.

## Price and shipping

The current verified production cost is $11.50 for each of the 33 public 12 × 18 poster variants. Fourthwall's US card fee is 2.9% + $0.30 on the full customer payment, including shipping and tax; payment methods differ. Fourthwall's typical US first-item poster shipping estimate is $4–$7, not an exact variant/destination quote. Customer-paid shipping is passed through. At the approved $20 retail price, production leaves $8.50 (42.5% gross margin). Including those illustrative shipping-related processing fees, these assumptions leave about $7.42–$7.50 before tax-related processing fees and other business costs.

Sources: [Transaction fees](https://help.fourthwall.com/frequently-asked-questions/payments-and-pricing/transaction-fees) and [Shipping costs](https://help.fourthwall.com/frequently-asked-questions/shipping-and-orders/shipping-costs), checked September 30, 2026. No checkout is needed for the category estimate.

## Disable sales

Clear the product's `productPublished` or `artworkApproved`, or remove its checkout URL, then republish the gallery. The local preview remains, but purchasing stops. Also hide the product in Fourthwall if direct product-page sales should stop. A gallery-only takedown does not disable Fourthwall's own URL.
