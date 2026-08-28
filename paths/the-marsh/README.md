# The Marsh 🦆

A hunting game where the ducks are memecoins, the loot is real, and you
never leave the fire. You are a blind, lazy hunter. Your dog is very
good.

The dog scouts freshly launched tokens on the configured chain, holds
seven gates it will not lower, takes at most one fixed-size shot per
"go now", attaches an exit plan on the spot, and narrates all of it.
Levels, trophies, and seasons score how you hunt — never how lucky you
got.

## Risk disclosure

> The Marsh trades extremely high-risk, newly launched, low-liquidity
> tokens. Most such tokens go to approximately zero. The safety gates
> filter out obvious honeypots, copycats, and whale-concentrated
> launches; they do not make the expected value positive and cannot
> detect every rug. Total loss of any position is a normal outcome, not
> an edge case. The game trades only from its own dedicated wallet:
> whatever you fund it with is your maximum possible loss — fund it
> with entertainment money only. Fixed position sizing, a daily hunt
> limit, and hard stops are enforced in code and cannot be raised
> mid-hunt. Game rewards reflect process discipline only; no game
> element indicates, promises, or improves profitability. Nothing here
> is financial advice.

## Layout

- `engine/` — the money side: feed, gates, shot, exits, signals. Every
  hard limit is enforced here in code.
- `game/marsh_engine.py` — pure functions over the engine's event log:
  XP, levels, rating, trophies, streaks. Imports nothing from the
  engine (the log is the only bridge).
- `strategy.py` — path component: read-only state/decision views for
  the page.
- `skill/instructions.md` — the dog.
- `scripts/marsh_run.py` — hunt / whistle / recap / ghost-hunt driver.
- `tests/` — isolation, fiction lint, deterministic replay,
  worst-degen (zero XP from PnL alone), and the bust ritual.

## Custody & review notes

- **The pack holds no keys.** No module references private keys,
  mnemonics, or wallet credentials; `tests/test_custody.py` enforces
  this with an AST scan over every identifier and import in the pack.
- **Real vs simulated is the executor, never the engine.** The engine
  calls an injected `Executor`. `SimExecutor` (ghost hunts, dry runs)
  produces synthetic accounting fills only. `LiveExecutor` performs
  real satchel-internal swaps and is host-mediated by construction: it
  refuses to instantiate without the signing callback that only the
  SDK strategy runner injects after the hunter authorizes live
  trading. The bundled CLI (`scripts/marsh_run.py`) never has that
  callback and therefore has no trade authority.
- **No transfer primitive exists.** Executor methods have no
  recipient/destination parameter (test-enforced); the only
  fund-moving operations are buy/sell swaps inside the satchel on
  positions the engine itself opened, per the hunter's configured
  rules. `walk_out` is bookkeeping only — it signs and moves nothing.
- **On packaged duplication:** the bundle's `skill/install` and
  `skill/path` trees are generated copies of the canonical source in
  this directory, rendered by `wayfinder path build` for host install.
  They are not independent code: any fix lands in the canonical files
  and re-renders into both copies on the next build.

## Design laws

1. Progression scores decisions; trophies score outcomes.
2. The game is read-only over the engine.
3. The log is the save file.
4. Never penalize de-risking.
5. Post-loss moments get calm, not hooks.
6. Money buys nothing.

## Running

```bash
# practice range (no funds, fixture marsh)
python scripts/marsh_run.py hunt --ghost --fixture tests/fixtures/calm_day.json

# a real hunt (requires configured Wayfinder API key + satchel wallet)
python scripts/marsh_run.py hunt

# the whistle (applies the retrieve plan to open positions)
python scripts/marsh_run.py whistle

# the story so far
python scripts/marsh_run.py recap --expedition 1
```
