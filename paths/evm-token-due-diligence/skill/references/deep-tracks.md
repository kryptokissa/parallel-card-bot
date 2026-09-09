# Triggered deep investigations

Eight tracks. Each states when to open it, the minimum evidence that
closes it, when to stop, and what stays unknown if it cannot be finished.
Six live next to the surface they belong to; two are here.

| Track | Lives in |
|---|---|
| Full holder / Transfer replay | `references/supply-concentration.md` |
| Launch-cohort accounting | `references/launch-integrity.md` |
| Fee-wallet and cross-chain proceeds | `references/fees-treasury-proceeds.md` |
| Reward-epoch accounting and backlog | `references/rewards-backing-redemption.md` |
| External dependency and redemption | `references/utility-dependencies.md` |
| Proxy authority and bytecode reconstruction | `references/bytecode-escalation.md` |
| Complete pool and position history | below |
| Narrow operational attribution | below |

---

## Complete pool and position history

**Trigger.** A liquidity claim that depends on history — "it has always
been locked", "they added liquidity and never touched it", "depth has been
stable" — or a present-state picture that does not explain how it got
there.

**Minimum evidence.** Mint, Burn, Collect and (for v3/v4) position-manager
events for each material pool across a stated block range; position
ownership transfers; the locker's own history if one is involved; the
searched range recorded with its chunk boundaries.

**Stopping condition.** Every material position's current state is
explained by a chain of events from its creation, with no unexplained
appearance or disappearance of principal.

**If it cannot be completed.** Say which pool, which range, and what that
leaves open. Present state stands on its own evidence; the historical claim
does not get made.

---

## Narrow operational attribution

**Trigger.** A specific, decision-relevant question about who controls a
contract or a key — not curiosity, and never a general identity search.

**Minimum evidence.** Authenticated project-control evidence: a signature
from an address the project publicly claims, an onchain action from an
address the project has itself named, a verified deployment from a
published address. Onchain-only correlation is not authentication.

**Stopping condition.** Control of a *contract* is established, or it is
not. Stop there. Identity of a *person* is out of scope by default.

**If it cannot be completed.** Use neutral roles — launch signer, fee
recipient, funder, observed controller — and say what would establish more.
See `references/attribution.md` for what does and does not follow from
shared funding, routers, timing and deterministic deployment.

---

## Escalation discipline

Open a track only when a cheaper check cannot change the conclusion.
Prioritise by what could flip the verdict, not by what is interesting.

Protocol-wide invariant work and substantial exploit campaigns are a
different job: route them to an audit workflow rather than growing this one.
