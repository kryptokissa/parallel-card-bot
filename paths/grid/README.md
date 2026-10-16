# Grid

A grid trading bot for Hyperliquid perps. It buys at fixed levels below price,
sells at fixed levels above, and earns from oscillation rather than direction.

What makes this one different from a script with constants at the top: **it
interviews you before it has any orders**, and it refuses to start on an answer
it cannot defend.

## The interview

Nothing about a grid has a safe default, so the path asks rather than assumes:

| Question | Notes |
| --- | --- |
| market | which perp |
| lower / upper | the range it works inside |
| levels | how many rungs, at most 50 |
| spacing | geometric (equal %) or arithmetic (equal $) |
| capital_usd | margin behind the whole grid |
| leverage | clamped to 5.0x, loudly |
| **breakout** | **required — the grid will not start without it** |

Breakout behaviour is the one decision with no neutral option. Price leaving
the range leaves the grid holding a fully directional position, so the path
takes one of three answers — keep the position and stop, flatten and stop, or
rebuild around the new price — and refuses to pick for you.

Any question can be handed over with "you decide". The path then proposes a
concrete grid, with a reason attached to every value, and will not start until
you have seen it and confirmed once. Delegation changes who proposes a number.
It never changes which numbers are allowed.

## The gates

`start` places nothing unless all of these pass:

- the mark sits inside the range, and not on a bound
- rungs are distinct after snapping to the venue's tick grid, and every order
  clears HL's $10 minimum
- the tightest gap clears a round trip's full cost — fees **and** funding
- fully loaded at the worst edge, equity still covers maintenance margin
- fully loaded at the worst edge, the drawdown is within your limit

The last one usually binds first. Hyperliquid holds roughly 2% maintenance
margin, so a grid can sit far from liquidation while having given back most of
the capital behind it; a liquidation check alone would wave that through.

Funding is in the cost gate because a grid holds inventory and a perp charges
for every hour of it. At default rates a round trip costs about 14 bps — 13 of
fees, including a 5 bps builder fee per side, and 1 of funding over an eight-hour
hold. Spacing tighter than that loses money on every cycle that works.

## Tuning

The gates prove a grid is not guaranteed to lose. They do not say which shape is
best, so:

```
sweep     rank level counts and both spacings over a recent price series
simulate  walk one configuration through that series
```

Ranked by **cycling edge** — profit from completed round trips, after costs —
rather than net PnL. Net PnL includes the leftover inventory's mark-to-market,
so over a trending window it rewards a grid for having been accidentally long.
The output separates cycling, inventory, and any profit from flattening at a
breakout, and it flags a winner that rests on only one or two cycles.

It is a comparison between configurations over one series. It is not a forecast.

## The daily loss limit

Optional, and enforced rather than advertised: set it and every `step` requires
the account equity the exchange reports. The day's loss is measured from the
equity that UTC day opened at, so fills, fees and funding are all already in the
number. Hit it and the grid cancels its rungs and stops; recovering equity does
not restart it.

## Running it

```
questions                   what to ask
propose                     a concrete grid, when you delegate
gates                       dry run, touches nothing
simulate / sweep            tune the shape against real prices
start                       validate, clamp, gate, place the rungs
step                        refill fills, cancel strays, act on a breakout
state                       what it thinks, and which save file it read
```

The path decides; the agent executes. Commands return the `hyperliquid_*` calls
to make, and the path itself never signs or sends anything.

## The first live grid

This path has never taken a real fill, so the execution leg is untested however
green the suite is. The first run exists to prove orders place, fill and
reconcile — not to make money. Make it small enough that being wrong is boring.

**Before starting**

- USDC in the Hyperliquid clearinghouse for the wallet you will use.
  `hyperliquid_deposit_usdc` bridges it from Arbitrum; the minimum is $5 and the
  bridge takes a few minutes.
- The current mark, the market's `szDecimals`, and its funding rate. All three
  come from the venue: `metaAndAssetCtxs` carries mark, `szDecimals` and funding
  in one response.
- `hyperliquid_update_leverage` set for the market, before any order.

**A deliberately boring first grid**

$100–200, 2–4 rungs, leverage 1–2x, `breakout: halt_close`, and a daily loss
limit of roughly 10% of the capital. Four rungs on $150 puts about $37 an
order, comfortably over the $10 minimum, and `halt_close` means a breakout ends
with no position rather than a held bag you then have to decide about.

Pick a range price is currently *inside* and has crossed repeatedly in the last
day or two. A range price has already left fails `mark_in_range`, and one price
never revisits completes no cycles and teaches nothing.

**Running it**

```
gates      confirm all five pass at the live mark before anything else
start      place the rungs; check every order appears on the exchange
step       after any fill, with the venue's open orders, position and equity
state      read the event log; confirm it matches what the exchange shows
```

**What to actually verify, in order**

1. Every order from `start` exists on the exchange at the exact price and size
   the path returned. A rejection here means a venue contract was missed, and
   the error text names which.
2. After the first fill, `step` replaces that rung and nothing else. Run it
   twice: the second run must place nothing.
3. `reduce_only` behaves. While flat, no rung carries it. Once long, the sell
   rungs do and the buy rungs do not.
4. One completed cycle — a buy rung fills, price rises, its sell fills — and the
   realised amount matches the rung gap minus fees.
5. One breakout, or force it by setting a range price is about to leave. Rungs
   cancel, and the position is handled the way `breakout` says.

Stop and read carefully if the exchange and `state` ever disagree. The exchange
is right about positions and orders; the log is right about intent. A
disagreement is either a missed fill or a bug, and both are worth understanding
before adding money.

Only after one full cycle and one breakout is a larger grid worth considering.

## Risk

A grid is short volatility. It makes small amounts often and loses a large
amount rarely, when price leaves the range and keeps going. Leverage turns that
loss into a liquidation. The gates above are there to make the rare case
survivable, not to make it unlikely.

This path has never taken a real fill. Before real money, run one deliberately
small grid and read the result end to end.
