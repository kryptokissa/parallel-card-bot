# Surface D — supply and concentration

## Keep the units apart

Raw assets, rebasing units, synthetic claims, bond or NFT shares, LP
shares, custody balances, total supply and circulating float are all
different quantities. A report that adds two of them together is wrong
even when every number in it is right.

State the denominator and the exclusions every time you give a percentage.
"Top 10 hold 61%" of what — total supply, supply less burns, supply less
pool and locker custody, or circulating float? Each gives a different
number and each is defensible; leaving it unstated is not.

## Classify custody before counting it

Separate: pool custody, protocol custody, lockers, treasuries, burn
addresses, creator allocations, and investor-like balances. A pool holding
30% is not a whale; a treasury holding 30% is a governance question; one
externally owned account holding 30% is a different risk entirely.

Burn addresses are a **convention**. Balance at `0x…dEaD` is unrecoverable
only if nothing can move it — check for code and for any known control.

## Accounting appropriate to the token

Reconcile with the method the token's own design requires. A rebasing token
does not conserve balances under Transfer replay. A token with a permanent
delegate, a fee-on-transfer, or balance rewrites can change holdings without
an ordinary Transfer event. Establish which of those apply before choosing
a method, and say which method you used.

---

## Deep track: full holder or Transfer replay

**Trigger.** A discrepancy between `totalSupply` and the sum of known
balances; a historical question about who held what and when; a
concentration claim that a top-holder API cannot support.

**Minimum evidence.** `eth_getLogs` over the Transfer topic across a stated
block range, chunked, with the chunk boundaries recorded; a closing balance
check against `balanceOf` for the largest reconstructed holders; explicit
handling for mint and burn legs.

**Stopping condition.** Reconstructed balances match `balanceOf` at the pin
for every holder above the materiality threshold, or the residual is
bounded and stated.

**If it cannot be completed.** Concentration before the earliest reachable
block is unknown. Say which block, say why (pruning, rate limit, range
cap), and mark the surface unknown. Do not substitute an aggregator's top-
holder list and present it as a replay — cite it as corroboration at best.
