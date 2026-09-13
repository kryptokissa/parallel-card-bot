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

- the mark sits inside the range
- rungs are distinct after snapping to the venue's tick grid, and every order
  clears HL's $10 minimum
- the tightest gap clears round-trip fees with margin
- fully loaded at the worst edge, equity still covers maintenance margin
- fully loaded at the worst edge, the drawdown is within your limit

The last one usually binds first. Hyperliquid holds roughly 2% maintenance
margin, so a grid can sit far from liquidation while having given back most of
the capital behind it; a liquidation check alone would wave that through.

## Running it

```
questions                   what to ask
propose                     a concrete grid, when you delegate
gates                       dry run, touches nothing
start                       validate, clamp, gate, place the rungs
step                        refill fills, cancel strays, act on a breakout
state                       what it thinks, and which save file it read
```

The path decides; the agent executes. Commands return the `hyperliquid_*` calls
to make, and the path itself never signs or sends anything.

## Risk

A grid is short volatility. It makes small amounts often and loses a large
amount rarely, when price leaves the range and keeps going. Leverage turns that
loss into a liquidation. The gates above are there to make the rare case
survivable, not to make it unlikely.

This path has never taken a real fill. Before real money, run one deliberately
small grid and read the result end to end.
