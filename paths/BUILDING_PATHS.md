# Building a Wayfinder path

Everything here was paid for in failed builds and rejected reviews while
shipping **The Marsh 🦆**. It is written for the next path — a grid
trading bot on Hyperliquid — but nothing below is specific to that.

Read it once before `path init`. Most of these traps cost hours, and all
of them are cheap to avoid up front.

---

## 1. The packaging traps

### 1.1 `path init` pins a version that does not exist

`path init` writes `runtime.version` in `wfpath.yaml` from the SDK
**installed in your shell**, which is routinely ahead of what is
published. Installers then fail to resolve the runtime.

Fix it in the first commit:

```bash
pip index versions wayfinder-paths     # what installers can actually get
```

and set `runtime.version` in `wfpath.yaml` to that. The Marsh ships
`0.11.0`.

### 1.2 The published wheel is missing `mcp`

`wayfinder-paths==0.11.0` imports `mcp` at module scope in `mcp/cli.py`
but omits it from its metadata, so every entry point is unimportable on a
clean install:

```bash
pip install 'mcp>=1.10.1,<2'
```

Nothing else in the wheel's imports is genuinely missing — I audited them
all. Expect the same class of gap in later versions; test the runtime in
a clean venv, not in your dev shell.

### 1.3 Everything in the path directory ships

This one cost **five consecutive review rejections**. Anything under the
path directory goes into the bundle — tests, fixtures, scratch files,
`__pycache__`. Reviewers read the bundle, and test files full of fake
endpoints and stub addresses read exactly like the real thing.

Use two directories from the start:

```
paths/
  <slug>/          the path — only what ships
  <slug>-tests/    tests, fixtures, anything else
  tools/           build-time checks
```

Point pytest at the sibling directory. Do not try to fix this with
`.gitignore` or exclusion globs — it is a layout problem, and the layout
fix is the only one that holds.

### 1.4 Check the built bundle, not the source tree

Reviews flag any origin in the bundle that is not `https://wayfinder.ai`.
That includes placeholders. My first fix replaced `example.invalid` with
`127.0.0.1` and got rejected again — a loopback is still a shipped origin.

Run `tools/check_bundle_origins.py` on the **built** artefact before
every publish:

```bash
path build && python tools/check_bundle_origins.py <slug>/dist/<slug>-<ver>.zip
```

It unpacks the zip, scans every text file, and exits non-zero on anything
outside the allowlist. Wire it into whatever you use as a pre-publish
gate.

### 1.5 Verify what you actually published

Twice I "fixed" a finding that the published bundle already contained.
Download your own published bundle and grep it before assuming a review
finding describes current code. Reviews lag; bundles do not.

---

## 2. The runtime model

### 2.1 Paths decide. Agents execute.

The single most expensive misunderstanding of the whole build. I wrote a
`Strategy` subclass, wired it to a `LiveExecutor`, published it — and
then found that `run_strategy` only serves **core SDK strategies**, which
need a `manifest.yaml` inside the SDK itself. An installed path cannot
open that door. The Shell had been telling me "no live-shot mode" all
along and it was right.

The working model:

- The path **decides**: it scouts, gates, sizes, and emits a decision.
- The agent **executes**: it calls the `onchain_swap` MCP tool.

So a path's live output is a structured decision, not a transaction.
`the-marsh/engine/decision.py` is the reference implementation:
`Decision`, `buy_decision`, `sell_decision`, `write_pending` /
`read_pending` / `clear_pending`, and a `DECISION_TTL_SECONDS` so a stale
decision cannot be executed hours later at a price that no longer exists.

`onchain_swap(wallet_label, from_token, to_token, amount, slippage_bps, …)`
— note `amount` must be a **decimal** string. Integer-looking strings are
rejected.

For perps the executing surface is different (§4), but the split is the
same: decide in the path, execute through the agent.

### 2.2 Never assume a wallet label

Labels are generated per install (`thoughtful-lush-narwhal-of-bliss`, and
no, not the same for the next user). A path that reads a label from
config works on exactly one machine.

Two rules:

- Enumerate with `load_wallets()`, which merges local `config.json` with
  remote Wayfinder-managed wallets. Reading the config file alone misses
  the managed ones — my preflight passed with no satchel because of this.
- Inside a strategy, take the address from the signing callback:
  `callback.wallet_address` and `callback.chain_type`. Check the chain
  type and fail loudly on a mismatch — wallet rings carry both an EVM and
  an SVM leg under one label, and picking the wrong leg is silent.

In any decision payload the agent will read, leave the label as a
placeholder — the agent knows its own wallets:

```python
"wallet_label": "<your satchel wallet label>"
```

### 2.3 The published SDK lags the live API

Call the REST API directly rather than coupling to SDK client classes.
Every time I bound to an SDK client, the shape it expected was a version
behind what the API returned. Direct calls plus your own small response
parsers are less code and age better.

### 2.4 The API throws 500s and timeouts

Not rarely. During this build: a bundle download 500, a metadata read
timeout, and an `httpx.ReadTimeout` mid-hunt that killed a whole run.

Retry every network call — once, on timeout or 5xx, **never** on 4xx. And
decide what a failed call means for the decision: The Marsh treats a
failed safety check as a refusal (`safety_unchecked`) and moves to the
next candidate, rather than either crashing or waving the token through.

---

## 3. Quote and price parsing

Captured live from BRAP, and worth pinning as a fixture in your test
suite (`the-marsh-tests/brap_quote_sample.json`) so a schema change fails
a test instead of a wallet:

- The quote is under a top-level **`best_quote`** key.
- `best_quote.quote.priceImpact` is a **percentage** (`-1.1065` = 1.1%).
- `best_quote.priceImpactPct` is a **fraction** (`"-0.011065"`).
  They are named backwards. Read that twice.
- `best_quote.output_validation` carries `decimals` and a **unit**
  `price_usd` — the price of one token, not the swap total. Using the
  total as the unit price makes every stop level and take-profit
  meaningless while looking entirely plausible.
- The signable transaction is `calldata.serializedTransaction`.

Two defensive habits that caught real bugs:

- A missing impact returns `float("inf")`, never a reassuring `0.0`.
- An entry price of `0.0` must be impossible. In The Marsh a zero entry
  made `check_positions` skip the position entirely — a real funded
  position with no stop-loss, no take-profit and no time stop, silently,
  forever. That is the worst bug I wrote in this project and it was four
  characters wide.

---

## 4. Hyperliquid and perps

`hyperliquid-felix` is a declared dependency of the published `0.11.0`
wheel, and `wayfinder_paths/core/perps/handlers/protocol.py` defines a
full `MarketHandler` protocol. This is what makes a grid bot a genuinely
good fit here, and it is why I reversed my initial "grid trading is a bad
fit" call — that judgement was made against DEX swaps only.

### 4.1 The order surface

```
place_order(symbol, side, size, order_type, limit_price, reduce_only)
cancel(...)
get_open_orders(...)
get_positions(...)
orderbook(...)
quantity_at_price(...)
price_for_quantity(...)
```

`Side = "buy" | "sell"`, `OrderType = "market" | "limit" | "ioc_limit"`.

Handlers ship for `live.py`, `backtest.py`, `reconcile.py` and
`recording.py`.

### 4.2 Why this beats a DEX for grids

A grid needs resting orders at fixed price levels and cheap fills. On a
DEX you get neither: no resting limit orders, so fills depend on how
often you poll, and fee drag is brutal — a real quote from this build
showed `fee_total_usd 0.193` on a **$1.03** swap, including
`rentFeeLamports 1,855,569` for the token account. A grid doing dozens of
round trips at that cost is a fee pump with extra steps.

On Hyperliquid: real resting orders, maker fills at the grid line, no
token-account rent.

### 4.3 Grid mechanics

A range sliced into levels; buy at a level, sell at the next level up;
profits come from oscillation, not direction. **Arithmetic** spacing uses
equal price gaps, **geometric** uses equal percentage gaps — geometric is
usually the right default for volatile assets, since a fixed dollar gap
is a much larger percentage move at the bottom of the range than the top.

Two risks dominate:

1. **Breakout.** Price leaves the range and the grid is left holding a
   fully directional position — long at the bottom of a downtrend, or
   short into a rally. Decide the breakout behaviour *before* writing any
   code: halt and hold, halt and close, or re-centre. There is no neutral
   default; not choosing means "hold, forever, unhedged".
2. **Fee drag.** Grid spacing must clear round-trip fees with margin. If
   the spacing is tighter than fees, the bot loses money on every
   successful cycle. Use `backtest.py` to tune spacing against real data
   rather than guessing.

### 4.4 Perp-specific constraints

- **Leverage and liquidation.** Breakout on a perp is not just an
  unwanted position — it is a liquidation risk. Size the grid so the
  worst case at the range edge leaves a real buffer to liquidation, and
  treat that buffer as a hard gate rather than a preference.
- **Reconcile exchange state against your log.** Orders fill, cancel and
  get rejected without your process observing it, and restarts happen.
  `reconcile.py` exists for exactly this. Treat the exchange as the
  source of truth for positions and open orders, and your log as the
  source of truth for intent.
- **`reduce_only` on every exit.** Otherwise a "close" that races a fill
  opens a position in the opposite direction.

---

## 5. Publishing and bonding

- Bonding **transfers path ownership** to the bonding wallet. Once bonded,
  every subsequent publish must pass
  `--owner-wallet 0x555a0aaeb4820d2de210cb000f247db882a6613f`, or it will
  not attach to the existing path.
- Execution tier bond: **10,300 PROMPT**.
- The lifecycle: publish → automated review → human Blockdash approval
  (`is_actionable`) → `upgrade_bond` (announce) → `upgrade_probation` →
  `promote_upgrade`.
- Reviews can stall. The Marsh 0.2.0 sat in `processing / review running`
  for 12+ hours with `created` and `modified` four seconds apart, while
  0.1.14 had been modified ~16 minutes after creation. If `modified` stops
  advancing, it is stuck — ping with the timestamps or republish under a
  new version rather than waiting.
- Bonding and publishing decisions are the **user's**, always. Money is
  involved. Build up to the gate and stop there.

---

## 6. Testing, and what "tested" has to mean

The Marsh ended at 96 tests. The ones that actually caught bugs were the
ones pinned to **real captured data**:

- `brap_quote_sample.json` is a verbatim live quote. Every price-parsing
  test runs against it. All five silent live-path bugs were found this
  way, none by unit tests over synthetic dicts.
- Test the refusals as hard as the happy path: what happens on a timeout,
  on a missing field, on a zero balance, on a stale decision.
- A fixture feed that can exercise the full decide path (not just scouting)
  is worth building early — `FixtureFeed.quote_swap()` in The Marsh.

And the thing no test suite proves: **The Marsh has never taken a real
fill.** Everything above is verified up to and including a live quote at
0.1893% impact against a funded satchel. Before real money touches a new
path, take one deliberately tiny shot (~0.01 SOL, or the perp equivalent)
and read the result end to end. A path that has never filled is a path
whose execution leg is untested, however green the suite is.

---

## 7. Process notes

- **Investigate the third identical result.** Scout returned 16 cold
  tokens three runs in a row. I explained it away as a cold market twice.
  It was a bug: `dimension=trending` returns established tokens (BONK,
  PENGU, FARTCOIN — ages in years) which fail an age gate every time,
  where `dimension=active` returns actual fresh launches. Two runs is
  noise; three identical runs is a bug.
- **When a fix gets rejected repeatedly, the fix is wrong, not
  insufficient.** Five rejections all named test files. I kept editing
  test *contents*. The problem was that tests were in the bundle at all.
- **Watch your working directory.** Several `git` commands ran in the SDK
  checkout instead of the project repo. Check `pwd` before anything that
  writes.
- **Start a new path in a fresh session, on a new branch off main.** Not a
  copy of the previous path's branch. The branch history is mostly dead
  ends; the reusable value is this document.
