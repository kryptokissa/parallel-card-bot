# Surface E — launch integrity

## Decode the launch, do not narrate it

From the deployment and launch transactions: parameters, allocations, fee
exemptions, direct-buy recipients, funding sources, deterministic
addresses, the deployment sequence, and the earliest transfers and sales.

Distinguish **automatic launch-platform behaviour** from **manually
supplied exceptions**. A platform that always exempts its own router from
fees is not the same finding as a deployer who exempted three addresses by
hand. Verify the exact factory version and its deployed behaviour rather
than the version the docs describe.

## Define the cohort before measuring it

Any "launch wallets" claim needs, written down, before any number:

- the inclusion rule (funded by X, within window W, via route R)
- time bounds, in blocks
- the sources searched
- explicit exclusions
- coverage: what the rule cannot see

Then track, per member: initial allocation, transfers, sales, rebuys,
downstream inventory, proceeds, fees and retained assets.

## Two claims that are almost always overstated

**"The wallet went to zero, so they cashed out."** A zero balance proves
the tokens left. It does not prove a sale, a price, or a beneficiary.

**"They sold."** Prove sales from successful execution and pool or curve
mechanics — a receipt with the pool-side delta. A transfer to a router
address is not a sale; it is a transfer to a router address.

Sell-then-rebuy into different addresses is **market-mediated
redistribution** unless you have independent evidence of common control.
See the worked example `launch-wallets-sold-and-rebought`.

## Proceeds are not profit

Calling proceeds "profit" requires a defensible cost basis, the material
flows, the fees, and the retained inventory. Without all four, report gross
proceeds and say that is what it is.

## Launch platforms

For Pons-style bonding-curve launches, inspect the **direct curve buy
recipient** and the applicable tax treatment rather than assuming the
transaction sender received the benefit. The sender pays; the recipient
field decides who ends up holding. More in `references/chain-notes.md`.

---

## Deep track: launch-cohort accounting

**Trigger.** A concentration or extraction question about the launch, or a
cohort claim that has already been made publicly.

**Minimum evidence.** The cohort rule as above; per-member transfer and
swap receipts; the quote-asset deltas that make a sale a sale; the current
inventory of every member and of every address they funded onward.

**Stopping condition.** Every member's allocation is accounted for as
retained, transferred (to a named address), or sold (with a receipt), and
the proceeds are traced to their first commingling point.

**If it cannot be completed.** Report the fraction of the allocation
accounted for and stop attribution at commingling. An exchange deposit is
where tracing ends: it does not prove a sale, a fiat withdrawal, or a final
beneficiary.
