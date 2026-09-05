# Surface G — rewards, vaults, backing and redemption

## Inventory is not liability; a promise is not a right

Establish separately:

- what the vault or treasury **holds** (assets, at the pin, by asset)
- what holders are **owed** (entitlements, by rule)
- what holders can **enforce** (a redemption path that has actually settled)

A vault balance is not necessarily available backing — it may be
encumbered, be the wrong asset, or be withdrawable by an admin. A synthetic
reward is not necessarily redeemable for an underlying asset.

## The redemption question

For each claimed right, resolve: who can claim, the actual asset received,
conversion units, fees, timing, caps, approvals needed, admin dependencies,
and the exit route afterwards.

Settlement standard: a **successful receipt plus the intended underlying-
asset balance delta**. A success flag or an emitted event is not
settlement. See the worked example `vault-of-synthetic-claims`.

## Distributions

Test conservation, entitlement rules, cumulative caps, duplicate payments,
unpaid amounts, retained inventory and processing liveness.
`evm_dd.reconcile.DistributionAudit` covers all of these.

Do **not** assume distributed amounts must be less than purchases.
Prefunding, donations, carryover and minting are all real mechanisms; the
funded amount is an input to check, not an inference to make.

Liveness matters as much as arithmetic: a distributor that conserves
perfectly but has not processed an epoch in two months has an outstanding
backlog, and that backlog is a liability.

---

## Deep track: reward-epoch accounting and backlog modelling

**Trigger.** Rewards material to the thesis; a backlog visible in epoch
timestamps; a claim that rewards are "fully funded".

**Minimum evidence.** Per-epoch entitlement source; payment receipts by
recipient and epoch; cumulative caps; funding inflows; retained inventory;
the timestamp of the most recent processed epoch.

**Stopping condition.** Each epoch conserves, no duplicate or over-cap
payment survives, and the outstanding backlog is quantified against
available funds.

**If it cannot be completed.** State which epochs are unreconciled and
what that leaves unknown. Never present an unreconciled epoch set as
"funded".
