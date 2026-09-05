# Surface B — liquidity custody

## Name every pool exactly

An exact address, or a complete pool key. "The WETH pair" is not an
identifier: forks share shape but not init code, fee tiers multiply pools
per pair, and a v4 pool has no address at all.

```
python path/scripts/main.py pool v3 <token0> <token1> --factory <f> --fee 3000
python path/scripts/main.py pool v2 <token0> <token1> --factory <f>
python path/scripts/main.py pool v4 <c0> <c1> --fee 3000 --tick-spacing 60 --hooks <h>
```

Every derivation is a **candidate** until the address is confirmed to hold
code and to report the expected tokens. Confirm the factory and init-code
hash belong to the deployment you mean.

## v3 and v4 positions

Principal lives in the position, not the pool. For each material position
resolve: position manager, token id, tick range, liquidity, owner,
`getApproved`, `isApprovedForAll` operators, and any locker or hook
authority. `evm_dd.pools.V3Position.removal_paths()` enumerates the ways
principal can leave once those are filled in.

Then inspect the withdrawal surface itself: decrease-liquidity, burn,
collect, rescue, arbitrary-call, approval, upgrade and transfer paths on
whatever holds the position.

## Locks

A locked canonical position establishes exactly one thing: that *this*
position's principal cannot be withdrawn before the unlock, by the parties
checked, given the locker's deployed code.

It does not establish that all liquidity is locked, that price is
supported, or that liquidity will remain in range. Check the locker for a
rescue path, an upgrade path, and whether the lock's owner can extend,
shorten or transfer it.

## Canonical versus side pools

Separate them explicitly. Declare the discovery universe: which factories,
which fee tiers, which quote assets, which block range, which sources.
`evm_dd.pools.PoolDiscovery` carries that and refuses to call a search
exhaustive without it.

A clean canonical pool next to an unlocked side pool is not a clean
liquidity picture — see the worked example
`locked-canonical-removable-side-pool`.

## Uniswap v4 specifics

Record currency ordering, fee, tick spacing, hook address and the derived
PoolId. Ordering is part of the identity: swapping the currencies names a
different pool, and the pack refuses a descending key rather than silently
sorting it.

**Never read the singleton PoolManager's token balance as one pool's
reserves.** It holds every pool's currency at once.

A hook is part of the pool's risk surface. It can run on initialize, swap,
donate and liquidity changes: take fees, block a swap, or make a position
unexitable. Give it its own control review — surface A, applied to the hook.
