# Surface H — utility, dependencies and development

## Is the advertised utility live, token-linked and enforceable?

Three separate tests, and most claims fail a different one:

- **live** — the thing exists and runs at the pin
- **token-linked** — using it actually requires or consumes *this* token
- **enforceable** — a holder can compel it, or at least obtain it without
  the operator's discretionary cooperation

A working product that does not need the token is a fine product and an
irrelevant token thesis. Say that plainly rather than scoring it.

## External dependencies

Inspect the material ones: oracles, bridges, APIs, keepers, lending
systems, collateral and redemption dependencies. For each, ask what
happens to holders when it stops, and who can point it somewhere else.
A `setOracle` or `setKeeper` selector on a privileged contract is part of
surface A, not a footnote here.

## Development and disclosure

Assess source correspondence (does the published source compile to the
deployed runtime?), reproducible builds, tests, audit *scope* rather than
audit existence, release controls, governance, disclosure accuracy, and
actual operating behaviour.

An audit that covered v1 of a contract that has since been upgraded covers
nothing that is deployed. Read the scope section, not the badge.

## What does not signal anything

Polished marketing does not establish safety. A copied template does not
establish fraud. Awkward or unusual code does not establish incompetence,
and it certainly does not establish AI authorship. None of these belong in
a findings ledger; if they belong anywhere it is in a note about
disclosure quality, clearly marked as impression rather than evidence.

---

## Deep track: external dependency and redemption analysis

**Trigger.** A thesis that rests on an outside system — a bridge, an
oracle, a lending market, an off-chain redemption desk.

**Minimum evidence.** The dependency's address and chain (with its own
pin); its control surface; the failure behaviour of the calling contract
when the dependency reverts, returns stale data, or goes away; and any
admin path that can repoint it.

**Stopping condition.** For each dependency: who controls it, what happens
when it fails, and whether holders have a path that does not require it.

**If it cannot be completed.** The dependency is an unknown, and any right
that flows through it is unproven. That is a statement about the right, not
a neutral gap.
