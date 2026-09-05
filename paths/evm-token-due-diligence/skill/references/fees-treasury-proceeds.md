# Surface F — fees, treasury and proceeds

## Map before you count

Fee basis, denomination, splits, escrow, claim authority, recipients,
configurable routes, and what happens to the assets afterwards.

Two distinctions that break most fee analyses:

- **a percentage of gross trade value** ≠ **a percentage of a fee bucket**
- **current configuration** ≠ **historically realised rates**

Read the setter bounds for the first. Read what was actually charged, over
a stated range, for the second.

## Close every asset

```
opening + inflows + explained adjustments
  = outflows + closing + explicitly bounded unexplained delta
```

`python path/scripts/main.py reconcile flows.json` computes this in exact
base-unit integers and reports the residual rather than absorbing it. It
also refuses to let a transformation count as flow.

Handle correctly:

- **wraps and unwraps** — one value in two forms; both legs, netting to
  zero, marked as transformations
- **burns** — an outflow that reduces supply, not a transfer
- **bridge legs** — the same value seen twice; do not count both
- **gas** — an outflow of the native asset, easy to omit and material at
  scale
- **reverted transactions** — moved nothing; exclude them, and note that
  they were attempted

"Did this balance come from LP fees?" is answered by
`attributable_fraction_bps(FlowKind.FEE_COLLECTION)` over a **closed**
reconciliation, with fee collections proved from receipt-level events —
not by pattern-matching amounts.

---

## Deep track: fee-wallet and cross-chain proceeds reconciliation

**Trigger.** A treasury or fee-wallet balance that is material to the
decision, or a claim about where proceeds went.

**Minimum evidence.** Per-asset reconciliation as above for each material
asset; receipt-level fee collection events; for each bridge hop, matched
source execution, transfer identifier, destination chain, recipient,
delivered amount, and destination-side evidence — with the destination
chain carrying **its own pin**.

**Stopping condition.** Every material asset closes within a stated
tolerance, or the residual is bounded and reported; every bridge hop is
matched on both sides.

**If it cannot be completed.** Name the unmatched hop and stop. Exact
attribution stops at commingling in all cases — a deposit to an exchange or
a mixer ends the trace, and any further claim is inference.
