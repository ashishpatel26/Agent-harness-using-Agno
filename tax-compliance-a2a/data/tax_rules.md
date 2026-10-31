# Compliance Playbook (Demo)

Internal guidance for the compliance agent network. Simplified for demonstration.
Not legal or tax advice.

## Sourcing

- Sales tax is sourced to the **ship-to state** (destination-based sourcing).
- The engine applies the **state base rate only**. Local and district rates are
  out of scope for this demo.
- Supported jurisdictions: CA, NY, TX, WA, FL, OR. Oregon has no sales tax.

## Tax codes

Every product must carry exactly one tax code from the catalog:

| Code | Use for |
|------|---------|
| TPP-GEN | Physical goods with no more specific code (furniture, hardware, electronics) |
| SW-SAAS | Software accessed remotely, billed as a subscription |
| DIG-DL | Software, e-books or media delivered by download or license key |
| FOOD-GROC | Unprepared food for home consumption |
| CLOTH-GEN | Clothing and footwear |
| SVC-PRO | Professional services: consulting, legal, accounting, audits |

When a description fits two codes, prefer the more specific one. A downloaded
license key is DIG-DL, not TPP-GEN. A hosted subscription is SW-SAAS, not DIG-DL.
Low-confidence classifications (below 0.8) must be sent for human review.

## Exemption certificates

- A certificate is valid only if it is on file, covers the ship-to state, and is
  not expired on the invoice date.
- An expired certificate means the sale is **taxable**. The seller must collect
  tax or obtain a renewed certificate.
- Common exemption reasons: resale, government, nonprofit, manufacturing.
- Keep certificates for at least four years after the last sale they cover.

## Reconciliation

- Under-collected tax is still owed by the seller. Recommend either invoicing the
  customer for the difference or accruing it as a seller expense.
- Over-collected tax must be refunded to the customer or remitted to the state.
  It can never be kept.
- An invoice missing from the tax log was never reported. Add it to the log before
  filing.
- An unknown tax code blocks calculation. Reclassify the item before filing.

## Returns and filing

- A return is **ready to file** only when the period has zero open discrepancies
  for that state.
- Filing frequency (demo): CA and TX monthly, NY quarterly, WA monthly, FL monthly.
- Returns are due on the 20th of the month after the period ends.
- Submitting a return is irreversible and always requires human approval.
