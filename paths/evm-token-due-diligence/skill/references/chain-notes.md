# Chain, venue and platform notes

Conditional. Load the section you actually need.

## Uniswap v3

Pools are `(token0, token1, fee)` under one factory; each fee tier is a
separate pool. Principal sits in position NFTs held by the position
manager, so pool-level balances tell you about inventory, not about who can
withdraw. Resolve the position id, owner, operator and approvals.

Full range is roughly `tickLower ≤ -887000` and `tickUpper ≥ 887000`; a
narrow range that price has left holds one asset and provides no bid.

## Uniswap v4

One singleton PoolManager holds every pool's currency. Its balance is never
one pool's reserves.

A pool is a key — `(currency0, currency1, fee, tickSpacing, hooks)` — and
its PoolId is the keccak of the encoded key. Ordering is part of identity.
Native currency is `address(0)` and sorts first.

The hook is the new risk surface. It runs on initialize, swap, donate and
liquidity changes. It can take fees, block swaps, and make positions
unexitable. Review it as its own contract, using surface A.

## Launch platforms (Pons-style bonding curves)

- The **direct buy recipient** is a parameter. The transaction sender pays;
  the recipient field decides who holds. Never assume they are the same
  address.
- Tax treatment on a curve buy may differ from tax on a pool trade. Read
  the deployed factory's behaviour for the exact version used, not the
  documentation for the current version.
- Distinguish what the platform does automatically for every launch from
  what this deployer supplied by hand. Automatic behaviour is a property of
  the venue; hand-supplied exceptions are a finding.
- Curve-phase sales are proved from curve mechanics and receipts, the same
  standard as pool sales.

## Robinhood Chain and other newer EVM chains

- Verify the chain id from the endpoint before anything else; do not rely
  on a name-to-id mapping that may be stale.
- Confirm which DEX deployments actually exist there rather than assuming
  the canonical mainnet factory addresses. Derived pool addresses are
  candidates until confirmed onchain.
- Archive depth is often shallow on newer chains. Expect historical checks
  to terminate in coverage limitations, and record which block the history
  actually starts at for your endpoint.
- Bridged supply is not native supply. If the token exists on more than one
  chain, each chain gets its own pin, and cross-chain totals need the bridge
  legs matched on both sides.

## Multi-chain targets generally

One pin per chain, never borrowed. `evm_dd.pins.PinSet` refuses to serve a
pin for an unpinned chain and refuses to re-pin a chain mid-run, because
either would mix two states into one report.
