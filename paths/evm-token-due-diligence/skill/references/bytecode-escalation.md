# Deep track: proxy authority and bytecode reconstruction

**Trigger.** Published source does not correspond to the deployed runtime;
no source is published and a material question depends on behaviour; a
proxy's delegate target is computed or non-standard; a capability scan
leaves a critical question open.

**Minimum evidence.** See each rung below — do not skip rungs.

**Stopping condition.** The specific question is answered. This track is
not "understand the contract"; it is "establish whether X can happen".

**If it cannot be completed.** The behaviour is unknown. An unreadable
contract is not a safe contract and is not an unsafe one.

## Escalate gradually

1. **Runtime and selectors.** `python path/scripts/main.py scan` — the
   dispatcher, notable opcodes, and whether it delegatecalls. Cheapest, and
   often sufficient.
2. **Verified predecessors and compiler metadata.** Earlier versions of the
   same contract, the metadata hash in the runtime tail, sibling
   deployments with the same code hash. Deduplicate by code hash before
   reading anything twice.
3. **Storage and historical calls.** Read the slots that matter at the pin;
   look at successful calls to the selector in question and decode their
   calldata and effects.
4. **Deeper reconstruction or simulation.** Only now, and only for the
   specific question.

## Source correspondence

Published source is a claim about an address until it is checked. To treat
it as the deployed implementation you need the compiler version, settings
and metadata to reproduce the deployed runtime — or a byte comparison that
accounts for the metadata tail and any immutables.

State which you did. "Verified on an explorer" is corroboration; it is the
explorer's compilation, not yours.

## Decompiler output

Decompiler output is **not** verified source. It is a hypothesis about
behaviour, and a useful one.

Claim executable equivalence only when compilation and byte comparison
close the meaningful differences. Otherwise, quote the decompiled fragment
as inference and say what would confirm it.

## Proxy authority

A proxy with no admin in the standard slot is usually UUPS: the upgrade
authority lives in the *implementation's* own access control. Read it
there. Recording "no admin found" as "no upgrade authority" inverts the
finding.

For beacon proxies, whoever controls the beacon re-points every proxy
pointing at it, in one transaction. The beacon is in scope.
