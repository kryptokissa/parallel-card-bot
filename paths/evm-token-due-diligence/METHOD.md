# Method

## Design laws

1. **One target, named exactly.** Chain id and address travel together as
   one identity. A same-symbol token on another chain is a different token,
   and the run stops rather than substituting one.
2. **Nothing is current without a pin.** Block number, block hash and UTC
   timestamp, captured from the same header. Each chain gets its own.
3. **Unknown is not a pass.** There is no status value that lets an
   incomplete check aggregate to a clean one.
4. **A failure of the run is not a fact about the token.** Coverage
   limitations are a separate type from findings, and cannot become one.
5. **Deployed behaviour outranks published claims.** Runtime, storage,
   receipts and decoded logs establish; explorers and docs corroborate and
   help you find things.
6. **Derive constants, do not paste them.** Slots, selectors and pool ids
   come from their specification strings through the pack's own Keccak.
7. **Exact integers, always.** Base units are integers end to end, and are
   serialised as strings above 2^53 so nothing is lost on the way to a
   report or a page.
8. **Rate surfaces separately.** Eleven of them, never averaged. One
   critical finding is not offset by ten clean ones.
9. **Read-only by construction.** Not by policy — by there being no code
   path to a signature.

## Enforced in code versus written in prose

Prose can be ignored under time pressure; a type cannot. The split is
deliberate:

| Rule | Where it lives |
|---|---|
| Chain-and-address identity | `evm_dd/addresses.py::TargetRef` |
| Pin validity and header agreement | `evm_dd/pins.py::BlockPin` |
| Unknown never aggregates to clear | `evm_dd/evidence.py::worst_status` |
| Proven requires primary onchain evidence | `evm_dd/evidence.py::Finding.defects` |
| Coverage limitation is not a finding | `evm_dd/evidence.py::CoverageLimitation` |
| No signing, no broadcast, fork only on loopback | `evm_dd/rpc.py` |
| Conservation closes to an explicit residual | `evm_dd/reconcile.py::Reconciliation` |
| No averaging a critical finding away | `evm_dd/report.py::derive_verdict` |
| Report is about the manifest's target | `evm_dd/manifest.py::validate_report` |
| Everything about *how to investigate* | `skill/references/` |

The references carry judgement — what to check on a v4 hook, how to define
a launch cohort, when to escalate to bytecode. Those cannot be typed into
existence, and pretending otherwise would be its own kind of dishonesty.

## Parallel lanes

`TargetPacket.freeze()` returns a canonical payload and its digest. Hand
every lane the same frozen packet and require the digest back with their
findings. Two lanes reporting different digests were not looking at the
same target, and their findings must not be merged.

Each lane returns evidence rows, findings and unresolved questions — not a
separate report. Assembling several small reports into one is how coverage
gaps become invisible.

The whole pack works sequentially. Parallelism is an optimisation, never a
requirement.
