# Print fulfillment runbook

Deep Space Field Notes uses Fourthwall as the merchant of record and fulfillment provider. The public gallery never collects payment-card details or shipping addresses. It links to an approved Fourthwall product page only after Brian has inspected a physical sample.

## Initial collection

- Andromeda Galaxy (`M 31`)
- Orion Nebula (`M 42`)
- Eastern Veil Nebula (`NGC 6992`)
- Western Veil Nebula (`NGC 6960`)

Each product is a 12 × 18 inch matte poster. The 3600 × 5400 pixel production file preserves the complete selected photograph without cropping it and places it inside a cinematic black field-note border with the catalog name, Northern Michigan capture credit, and Brian Jean signature.

## Prepare the masters

From the repository root:

```bash
npm run prints:prepare
```

The command reads `print_products/catalog.json` and the public comparison manifest. It uses the same owner-selected Gallery or NightSkyAI source displayed by the public site, writes production files under ignored `print_products/masters/`, and records input/output hashes in a local manifest. It never edits the gallery source images.

## Create and approve a product

1. Create a 12 × 18 matte poster in Fourthwall.
2. Upload the matching file from `print_products/masters/`. Do not enable automatic cropping or expand the photograph past the supplied black border.
3. Confirm the preview shows the entire photograph, all four border edges, the title, and the capture credit.
4. Set the product title, description, retail price, and shipping regions in Fourthwall.
5. Keep the product unavailable to the public and order one sample.
6. Inspect the sample in neutral daylight for crop, crushed shadows, color cast, poster sharpness, readable type, packaging damage, and print alignment.
7. If it passes, copy its exact HTTPS product URL into `print_products/catalog.json` and change only that product's `sampleApproved` value to `true`.
8. Run `npm test` and publish the site. The “Buy this print” link appears only when both the URL and sample approval are present.

If a sample fails, leave `sampleApproved` false, adjust the reproducible print-generation script or Fourthwall product settings, regenerate the master, and order another sample. Do not silently replace the public gallery photograph to fix a print-production issue.

## Suggested listing language

Use this shared disclosure on each listing:

> Captured with a Seestar smart telescope under Northern Michigan skies and processed as part of Deep Space Field Notes. Printed with the complete portrait field preserved inside a cinematic observatory border. Astrophotography by Brian Jean.

Fourthwall owns checkout, tax calculation, fulfillment status, shipping notifications, and its customer-support workflow. Product pricing, return terms, and shipping availability shown at checkout are authoritative.

## Disabling sales safely

Set `sampleApproved` to `false` or remove the checkout URL for the affected product, then republish. The gallery button disappears; the photography and field note remain available.
