# Surface C — sellability and executable depth

## The three different questions

1. **Has anyone ever sold?** Find a successful historical sell: a receipt,
   with the seller's quote-asset balance actually increasing. Router
   transfers are not sales.
2. **Can a sell be quoted now?** Read-only quotes at the pin, at a small
   probe size and at holder-sized amounts.
3. **Would a sell realise value?** Neither of the above answers this.

Keep them apart in the write-up. A historical sell proves execution *at
that historical state*. A quote proves what a quoter returned, not a
realised exit.

## What to report per quote

Route, input amount, quote asset, expected output, fees, per-unit
degradation against the probe size, and the exact reason for any failure.
Distinguish price impact, slippage tolerance, gas, spot price and
executable depth — four of those are routinely reported as the fifth.

Size the "holder-sized" amount from the actual distribution, not from a
round number: a median material holder, and the largest non-custodial
holder, are both worth quoting.

## Decisive simulated exits

If you simulate a sale or redemption to settle the question, the bar is:

- a successful receipt, **and**
- the intended underlying-asset balance delta on the seller, **and**
- route and costs explained.

A returned success flag is not enough. An emitted event is not enough.
Both are trivially producible by a contract that transfers nothing.

Simulation runs only on a verified disposable local fork with synthetic
accounts, and its results are counterfactual — see
`references/simulation.md`.

## Failure reasons worth naming precisely

- reverts in the token's transfer path only on sells → sell-side gate
- reverts only above a size → transaction cap
- reverts only for this address → blacklist or per-account cooldown
- succeeds but delivers far less than quoted → transfer fee on the
  output leg, or a hook taking a cut
- reverts everywhere including buys → the pool, not the token
