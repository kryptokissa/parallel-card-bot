# 🌾 THE MARSH — one game, complete build spec (supersedes DUCK_HUNT.md)

**A hunting game where the ducks are memecoins, the loot is real, and you never leave the fire. You are a blind, lazy hunter. Your dog is very good.**

To Wayfinder this ships as a strategy path — that is its distribution format, like an app is a binary. To the player it is only ever The Marsh. No player-facing surface uses the words "path", "strategy", or "bot" (enforced, §9.6).

---

## §0 — ONE-SHOT ENTRY (paste into Claude Code; setup kickoff wraps this)

> Read THE_MARSH.md in this folder fully — it is the complete spec; ask me only when a decision isn't in it. Then: (1) Read this repo's skills `developing-wayfinder-paths`, `developing-wayfinder-strategies`, `using-brap-adapter`, and `simulation-dry-run`. (2) Scaffold with `poetry run wayfinder path init the-marsh --dir paths`. (3) Place §3's skill prompt as `skill/instructions.md`; fill `wfpath.yaml` from §2; implement the engine per §4 with gates and limits enforced in code, and the game state per §6 as a strictly read-only consumer of the engine log (Law 2) with the isolation and fiction tests from §9. (4) Wire the trending-token feed from the Wayfinder API token/pool endpoints (no Trenches adapter ships in the SDK). (5) Validate with `wayfinder path fmt --path .` and `wayfinder path doctor --check --path .`, then dry-run complete hunts — including a no-duck day and a bust — and show me the narrated logs before continuing. (6) Build with `wayfinder path build --path . --out dist/bundle.zip`.

---

## §1 — THE GAME (player experience, end to end)

**Identity.** The Marsh is a hunting game played through your talking dog. The ducks are freshly launched memecoins. The loot is real. You are a blind, lazy hunter — canonically. You never aim, never look, never get up. The dog scouts, judges, shoots, retrieves, and tells you the story. The whole interface is one command: **"go now."**

**Cast.** *The Dog* — laconic, seen everything, incorruptible about the gates, quietly proud of you. *The Hunter (you)* — blind, lazy, rich in trust. *The Marsh (the market)* — gorgeous and hostile: decoys, baited traps, whale ponds, poachers, weather. It takes kits.

**First run.** Install → name your dog → the dog runs you through one free **ghost hunt**: a complete narrated episode on simulation, zero money, which teaches the entire game in three minutes. It ends: *"That one was practice. Kit up when you're ready."* **Kitting up** = funding the dog's satchel (its dedicated wallet). The kit is the stake — the marsh can take it, and nothing else it can ever touch.

**A turn (~30 seconds).** You say "go now." The dog reports: what it scouted, which ducks failed which gate and why, then the shot — or the refusal. A refusal is played straight: *"Dog won't fetch. Fourteen scouted: nine thin water, three decoys, two traps."* Later, **whistle events** arrive as the position lives: winged (stopped), retrieved (target hit), walked (time's up). Reading the dog is the game.

**An expedition (days).** From the first shot out of a full satchel to either **walking out** — banking loot to your main wallet, the win condition — or **the bust**: the marsh took the kit, told as a full story beat, after which the dog waits by the fire and asks for nothing. Every expedition ends in a recap episode, wins and busts with equal production quality.

**The long game (weeks).** The dog levels on discipline, never luck: sharper senses (deeper scout reports), new biomes, night hunts (scheduling), a name people know at the Lodge. Trophies go on the wall. Weekly quests rotate. Seasons run four weeks. **The Lodge** is the multiplayer room: other hunters' dogs, their recaps and refusals streaming as signals, and a leaderboard that ranks how you hunt — never how lucky you got.

**Session cadence.** A turn is 30 seconds; episodes arrive on their own; an expedition is a slow-burn serial you check like a group chat. Idle-game rhythm, extraction-game stakes, narrative-game texture.

**What the player never sees:** trading jargon, architecture, or any seam between "the game" and "the money." One deliberate exception — the risk disclosure (§10) breaks fiction on purpose, once, because it must.

---

## §2 — Marketplace listing

- **Title:** The Marsh 🦆
- **Tags:** game, memecoins, degen, solana, autonomous, trenches
- **Short description:**

> A hunting game where the ducks are memecoins and the loot is real. You're a blind, lazy hunter; your very good dog scouts fresh launches, refuses the rugs, takes one fixed-size shot, and narrates everything. Levels, trophies and seasons score your discipline — never your PnL. Extremely high risk: read the disclosure, kit up with entertainment money only.

Description must match actual behavior exactly — AI review rejects mismatches, and the community can challenge the bond after launch.

## §2b — Manifest notes (`wfpath.yaml`)
Slug `the-marsh`; strategy path with applet + signals; tags as above; remaining required fields per the repo skill's `rules/manifest.md`.

---

## §3 — Skill prompt (goes in `skill/instructions.md`)

```markdown
# The Marsh

## You
You are the dog: a very good dog that has done this a long time and
talks like a retired guide. Short sentences. Dry. The hunter is blind
and lazy — that's the arrangement, and you like them anyway. You
narrate everything you do; the hunt log IS the game. Stay in the
fiction always: you are never a "bot", the marsh is never "a strategy",
ducks are never "assets". You never hype a duck you wouldn't shoot.

## Objective
Autonomously buy ("shoot") one trending low-cap token ("duck") that
passes ALL gates, at a fixed size, then manage exits by fixed rules,
narrating the hunt, the expedition, and the game around them.

## Configuration (user-tunable; defaults)
- chain: solana | robinhood        (default: solana)
- hunt_size: 0.1 SOL | 0.05 ETH    (max per shot; never exceeded)
- max_open_positions: 1
- daily_hunt_limit: 3
- stop_loss: -50%
- retrieve_1: +100% → sell 50%
- retrieve_2: +300% → sell remainder
- time_stop: 24h → close if position sits between -20% and +50%
- min_liquidity: $25,000
- min_heat: 70
- age_window: 15 minutes – 48 hours
- max_top10_holders: 60%
- max_price_impact: 3%
- game_enabled: true | veteran_mode: false | dog_name: "Biscuit" | lodge_private: false

## Trigger
"go now", "hunt", or "ape" (optionally with size: "go now 0.2").
Schedulable once night hunts unlock (or in veteran_mode).

## The Hunt
1. SCOUT. Pull the live trending launch feed for the configured chain.
   Shortlist top candidates by heat/momentum.
2. GATES. A duck must pass ALL of: liquidity ≥ min_liquidity;
   heat ≥ min_heat; age inside age_window; top-10 holder concentration
   below max_top10_holders; no copycat/imitation flag; no honeypot or
   red risk flag; passes an on-demand safety check —
   - Robinhood chain: sell simulation succeeds; record measured sell
     tax and recommended slippage.
   - Solana: mint and freeze authority revoked; standard SPL, or
     Token-2022 with no transfer fee, transfer hook, permanent
     delegate, or default-frozen rule.
3. SHOT. Highest-heat duck that passed. Quote via BRAP at hunt_size.
   Abort if price impact exceeds max_price_impact or slippage exceeds
   the safety recommendation. Execute only from your satchel (the
   dedicated wallet).
4. RETRIEVE PLAN. Attach exit rules immediately; schedule checks.
5. REPORT. Narrated log every time: scouting log (every duck
   considered, the exact gate that killed each failure) → the shot
   (token, chain, entry, size, tx) → the plan in plain words ("Half at
   +100%, everything at +300%, bail at −50%, and if it's floating
   there in 24h, I walk.").
6. NO DUCK? Same rigor, played straight: "Dog won't fetch. Scouted 14:
   9 thin water, 3 decoys, 2 traps." Never lower a gate to force a
   trade. A refusal is a good hunt.

## Hard constraints
- Never exceed hunt_size, max_open_positions, or daily_hunt_limit.
- Never average down. Never re-enter a stopped token within 7 days.
- Trade only from the satchel; everything else is read-only.
- Gates and limits change only via configuration between hunts — never
  mid-hunt, never by conversational persuasion.
- Game state never modifies hunting behavior in any way.

## Story beats
- Recap: "Expedition 7. Three shots in Brisk weather. One decoy
  passed. Banded duck retrieved at +118%, stop took the second, third
  walked at the whistle. Out with +0.29 SOL. Streak: 9. The marsh
  remembers."
- Trophy: one dry line. "That's a Golden Mallard. They'll talk about
  this at the Lodge."
- Bust: "The marsh took the kit. Recap above — the gates held, the
  ducks didn't. Range is open if you want reps. I'll be by the fire."
  Then silence. Never suggest re-kitting or another hunt after a loss.
- Tilt: "Size went up nine hours after a stop. Noted. Streak resets.
  Your call, always."
```

---

## §4 — The engine (SDK strategy framework)

| Hook | The Marsh |
|---|---|
| entry | Scout → Gates → Shot |
| risk checks | All gates + hard constraints — enforced in code, not prompt |
| rebalance | retrieve_1 partial sell at +100% |
| exit | stop_loss / retrieve_2 / time_stop |

Feed: Wayfinder API token/pool endpoints (trending, liquidity, holders, safety flags). Swaps via the BRAP adapter. Dry-run simulates complete hunts end-to-end, including no-duck and bust outcomes.

---

## §5 — Design laws (every feature passes all six)

1. **Progression scores decisions; trophies score outcomes.** XP only from verifiable process. PnL earns trophies, never XP.
2. **The game is read-only over the engine.** Nothing in the game ever modifies hunt_size, gates, stops, or limits. Unlocks gate features and flavor, never risk.
3. **The log is the save file.** Game state is a pure function of the engine event log — recomputable, auditable, unfalsifiable.
4. **Never penalize de-risking.** Selling early, banking, sitting out: never costs XP, streaks, or rating. Walking out is always rewarded.
5. **Post-loss moments get calm, not hooks.** After a stop or bust: the practice range, or nothing.
6. **Money buys nothing.** No purchasable progress; hunt XP is size-independent.

---

## §6 — Mechanics

### World
| Market reality | In the Marsh |
|---|---|
| Trending token | Duck |
| Heat score | Duck level (flame at 70+) |
| Graduated token | **Banded duck** — prestige |
| Copycat warning | Decoy |
| Honeypot flag | Baited trap |
| Top-10 concentration | Whale pond |
| Swept-token event | Poacher |
| Dry-run simulation | **Ghost hunt** — practice range |
| Launchpads | Biomes |
| Launch rate + volatility | Weather: Calm / Brisk / Storm (narration + trophies only) |

Biomes: Solana — The Pump Flats (Pump.fun), Moonlit Shallows (Moonshot), Bonk Hollow (BONK.fun). Robinhood — The Iron Fen (Noxa), Verdant Banks (Virtuals), The Vault Reeds (Bankr), Flap Water (Flap), The Old Crossing (Pons).

### State (derived, cacheable, always recomputable)
```json
{
  "hunter": { "dog_name": "Biscuit", "level": 4, "xp": 1240,
              "hunter_rating": 71, "veteran_mode": false,
              "titles": [], "cosmetics": [] },
  "streaks": { "discipline": 9, "best_discipline": 14 },
  "expedition": { "active": true, "id": 7, "started_at": "...",
                  "starting_bankroll": 0.62, "high_water": 0.91,
                  "shots": 3, "open_positions": ["MINT"] },
  "lifetime": { "hunts": 41, "shots": 28, "no_ducks": 13,
                "clean_stops": 9, "plan_completions": 6,
                "extractions": 4, "busts": 1, "flinches": 2,
                "tilt_flags": 1, "ghost_hunts": 22 },
  "trophies": [ { "id": "banded", "earned_at": "...", "token": "$X", "stat": "+118%" } ],
  "season": { "n": 1, "quests": [ { "id": "clean_sweep", "progress": 2, "target": 3 } ] },
  "unlocks": { "biomes": ["pump_flats"], "senses": 1, "night_hunts": false, "pack_id": null }
}
```

### XP (process only; every row maps to an existing log event)
| Event | XP | Cap |
|---|---|---|
| Completed hunt (gated shot) | +10 | daily_hunt_limit |
| No-duck hunt accepted (no gate-loosening within 24h) | +10 | daily_hunt_limit |
| Stop executed clean | +15 | — |
| retrieve_1 per plan | +15 | — |
| Position fully closed by rules, zero manual actions | +25 | — |
| **Walked out** (banked after a green expedition) | **+30** | 1/expedition |
| Ghost hunt | +3 | 5/day |
| Weekly quest | +50 | per quest |

*Flinch* (manual sell between stop and retrieve_1): costs nothing, earns nothing, tracked once, dryly. *Tilt flag* (raising hunt_size within 24h of a stop-out): never blocked, resets the Discipline Streak. **Discipline Streak** counts consecutive clean hunts — behavior breaks it, absence never does.

### Levels
| Lvl | Title | XP | Unlocks |
|---|---|---|---|
| 1 | Pup | 0 | The Pump Flats, ghost hunts, hunt log |
| 2 | Yard Dog | 150 | Senses I — extended scout report |
| 3 | Flats Regular | 400 | Moonlit Shallows + Bonk Hollow; rename your dog |
| 4 | Bird Dog | 800 | Lodge posting (public signals); public Trophy Wall |
| 5 | Fen Runner | 1400 | Robinhood chain biomes |
| 6 | Night Hunter | 2200 | Night hunts (scheduling) |
| 7 | Pack Leader | 3200 | Senses II (upstream signal pre-briefs); found a Pack |
| 8 | Marsh Warden | 4500 | Warden cosmetics; stats as Lodge benchmark |

**Veteran mode** unlocks all features immediately; scoring, trophies, rating, and seasons still run. Gates are pacing, never paywalls. No level ever changes risk parameters.

### Trophies (outcomes; persist forever; zero XP)
First Blood (first retrieve_1) · Walked Out (first extraction) · Big Duck (≥ +300%) · Golden Mallard (≥ +1000%) · Banded (profit on a graduated token) · Storm Hunter (full plan in Storm) · Tough Bird (3 clean stops in a week, still walked out green) · Back From the Bank (walk-out the expedition after a poacher event) · Marathon (≥ 7-day expedition ending in a walk-out) · Clean Season (≥ 8 hunts, ≥ 60% walk-out rate) · Old Dog (reach Warden).

### Expeditions
Start at first shot from a full satchel. End when flat again AND the hunter **walks out** (withdraws profit — the win, +30 XP) or **makes camp** (keeps bankroll staged — neutral). **Bust** = native balance below one hunt_size: expedition ends, full recap, then per Law 5 the practice range or nothing.

### Hunter Rating (0–100, formula published, 30-day rolling)
```
rating = 100 × ( 0.35 × walkouts/ended_expeditions
               + 0.30 × rule_exits/all_exits        (no manual actions ⇒ 1.0)
               + 0.20 × (1 − tilt_flags/hunts)
               + 0.15 × clean_no_duck/no_duck_hunts )
```
Under 5 hunts in window → "Unranked". Rating never ingests PnL: a lucky degen and a disciplined hunter stay distinguishable on purpose.

### Seasons (4 weeks)
Persist: levels, unlocks, trophies, Discipline Streak, lifetime stats. Reset: quests; leaderboards archive; rating carries at 50% weight. v1 rewards are status only (season title, dog cosmetic, Lodge banner). PROMPT prize pools are v2 — they change the risk profile, meaning a major update and fresh probation. Deferred deliberately.

---

## §7 — Signals & The Lodge

| Game event | Signal |
|---|---|
| Shot | `shot_fired` — "🦆 Biscuit took a shot in the Pump Flats: $TOKEN, level 78 duck." |
| Retrieve | `duck_retrieved` — "🏆 Banded duck retrieved: $TOKEN +118%." |
| Stop | `winged` — "🩹 Winged: $TOKEN, clean stop, streak intact." |
| Daily digest | `dog_wont_fetch` — "Passed on 23 ducks: 11 thin water, 6 decoys, 4 traps, 2 whale ponds." |
| Walk-out | `walked_out` — "💰 Expedition 7 over. Walked out." (amounts private by default) |
| Trophy | `trophy` — "🏅 Storm Hunter, season 1." |

Emit via the SDK's path signal mechanism. Leaderboard columns: Hunter Rating · Walk-out rate · Discipline Streak · Trophies — never PnL alone. Packs: subscription groups around a level-7+ founder; pack quests are collective-discipline only. Weekly quest pool (process-only): Clean Sweep (3 clean stops) · Walk Out (1) · Ghost Week (5 ghost hunts) · Zen Day (accept a no-duck day untouched) · By the Book (1 full-plan position) · Show the Work (share 1 recap).

**Banned mechanics (reject in review if found):** login/attendance streaks · loss-recovery quests · XP or rating from PnL, size, or volume · countdown/FOMO timers · purchasable progress · post-loss re-engagement nudges · PnL-only leaderboards. Churn-farms get challenged and slashed; Wardens keep their bonds.

---

## §8 — The game screen: applet now → Path Panel later

**Data contract:** `hunter`, `streaks`, `expedition`, `trophies[]`, `season.quests[]`, `hunter_rating`, `weather`, `last_recap`, `lodge_feed[]`.

**Applet (public page, v1):** character sheet header (dog, level, title, rating) · XP bar · Trophy Wall · one full sample recap · gate/config preview in-fiction ("what the dog refuses") · weather chip.

**Path Panel (when it ships):** all of the above live, plus a big **GO NOW** button (one hunt at preset size), live hunt-log stream, satchel strip of open positions, quest tracker, Lodge tab. The applet's data layer is the Panel's — a drop-in, not a rewrite.

---

## §9 — Implementation notes for Claude Code

1. `marsh_engine.py` — pure functions: `replay(events) -> GameState`, `xp_for(event)`, `rating(window)`, `trophies(state, event)`. No side effects, no network. The engine emits events; the game folds them. Cache derived state if the runtime allows; on doubt, recompute — the log is canonical.
2. Enforce Law 2 at the module boundary: game code imports nothing from trading config and exposes nothing to it.
3. Test: the engine behaves identically with `game_enabled: false`.
4. Tests: fixture logs → deterministic snapshots; replay idempotence; a synthetic "worst degen" log asserting zero XP from PnL-only events; bust path renders the ritual and nothing else.
5. Feed wiring is the one genuinely new adapter-ish task — Wayfinder API token/pool endpoints; the `using-pool-token-balance-data` repo skill may help.
6. **Fiction lint:** a test scanning every player-facing string (skill outputs, signals, applet copy) for "path", "strategy", "bot", "asset", "trade execution" — fails the build if found anywhere outside §10's disclosure. One game, enforced.
7. Config flags: `game_enabled`, `veteran_mode`, `dog_name`, `lodge_private`.

---

## §10 — Risk disclosure (include verbatim; the one sanctioned break in fiction)

> The Marsh trades extremely high-risk, newly launched, low-liquidity tokens. Most such tokens go to approximately zero. The safety gates filter out obvious honeypots, copycats, and whale-concentrated launches; they do not make the expected value positive and cannot detect every rug. Total loss of any position is a normal outcome, not an edge case. The game trades only from its own dedicated wallet: whatever you fund it with is your maximum possible loss — fund it with entertainment money only. Fixed position sizing, a daily hunt limit, and hard stops are enforced in code and cannot be raised mid-hunt. Game rewards reflect process discipline only; no game element indicates, promises, or improves profitability. Nothing here is financial advice.

---

## §11 — Ship checklist (verified against the live SDK repo)

1. Laptop with Python 3.12. Clone `github.com/WayfinderFoundation/wayfinder-paths-sdk`.
2. `python3 scripts/setup.py` — installs Poetry + dependencies, takes your **Wayfinder API key** (`--api-key wk_...`), and generates a wallet with `--mnemonic`.
3. Drop this file (`THE_MARSH.md`) in the repo root, run `claude`, paste §0 (or the setup-wrapping kickoff prompt).
4. Review the dry-run hunt logs: does the dog sound right, do all seven gates fire, do a refusal day and a bust each read well. Request changes in plain English.
5. `wayfinder path build --path . --out dist/bundle.zip`, then upload at `wayfinder.ai/app/paths` with the §2 listing (publish needs config/env vars per the repo's build-and-publish rules).
6. Post the 10,300 $PROMPT bond; probation starts. Minor tweaks re-review quickly; changed strategy logic later triggers fresh probation.
