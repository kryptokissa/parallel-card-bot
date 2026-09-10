# Assay 🔬

Evidence-bounded due diligence on **one exact EVM token**, on **one exact
chain**.

Point it at a contract and it answers the questions that actually decide
things: what privileged actors can change or take, who can remove liquidity
principal, whether ordinary holders can sell and at what size, how
concentrated ownership really is, where fees and treasury assets go, and
which dependencies could flip the verdict.

Every claim is tied to a block number, a block hash and a raw artifact you
can re-run. Unknowns stay unknown. Nothing is ever quietly marked safe.

## What it is not

Not a price predictor, not a scanner score, not a full protocol exploit
audit. It will tell you what a privileged key can do to you. It will not
tell you what the token is worth.

## Read-only by construction

The pack has no signing path. `evm_dd/rpc.py` holds a read-method
allowlist and refuses everything else; state-changing methods unlock only
on a client explicitly bound to a **loopback** endpoint (a disposable local
fork), and signing or account-enumeration methods stay blocked even there.
Results from a fork are labelled counterfactual all the way into the
report, and the ledger refuses to record one as proven of the chain.

## Reviewer checklist → where each safeguard lives

| Concern | Enforced at | Proven by |
|---|---|---|
| Can this pack sign or broadcast? | `evm_dd/rpc.py` — read allowlist, `FORBIDDEN_METHODS` blocked in every mode, fork methods gated on a loopback client | `tests/test_read_only.py::test_signing_and_account_methods_are_never_reachable`, `::test_fork_mode_refuses_a_remote_endpoint` |
| Does it touch key material? | No key-material identifier or signing import exists anywhere in the pack | `tests/test_read_only.py::test_no_key_material_identifier_exists_anywhere_in_the_pack`, `::test_the_pack_imports_no_signing_or_key_library` |
| Could it analyse the wrong token? | Chain id is read from the endpoint and compared to the request before any other read; `TargetRef` binds chain **and** address as one identity | `tests/test_collect.py::test_an_endpoint_answering_for_another_chain_stops_the_run`, `tests/test_validator_rejections.py::test_rejects_a_same_symbol_token_substituted_from_another_chain` |
| Can an unknown become a pass? | `Status` has no such value; `worst_status` cannot aggregate an unknown to clear; the validator recomputes every rating from its own checks | `tests/test_evidence.py::test_an_unknown_can_never_aggregate_to_a_pass`, `tests/test_validator_rejections.py::test_rejects_an_unknown_check_presented_as_a_pass` |
| Is an RPC failure reported as a token finding? | `RpcUnavailable` carries a `CoverageLimitation`, a separate type from `Finding` | `tests/test_collect.py::test_an_endpoint_failure_becomes_a_coverage_limitation`, `tests/test_behavioural_examples.py::test_an_rpc_failure_is_a_coverage_limitation_and_never_a_pass` |
| Is metadata ever defaulted? | `metadata_from_calls` returns `Resolved` or `Unresolved`; there is no default branch | `tests/test_collect.py::test_metadata_that_does_not_answer_stays_unresolved_and_is_never_defaulted` |
| Can a pin be fabricated? | `BlockPin` rejects zero numbers, zero hashes, pre-genesis and future timestamps, and verifies against the header it was captured from | `tests/test_pins.py`, `tests/test_validator_rejections.py::test_rejects_placeholder_pins` |
| Are derived pool addresses trustworthy? | CREATE2 derivations are checked against published Uniswap deployments | `tests/test_pools.py::test_v3_pool_derivation_matches_the_deployed_pools` |
| Does a critical finding get averaged away? | `derive_verdict` is worst-first; one adverse high/critical surface produces NO-GO regardless of clean ones | `tests/test_behavioural_examples.py::test_locked_canonical_liquidity_does_not_clear_the_side_pool` |
| Are credentials written to disk? | `redact` strips key-shaped material — including keys in URL path segments — from every persisted string | `tests/test_evidence.py::test_credentials_are_redacted_before_anything_is_persisted` |
| Do examples masquerade as real findings? | Every worked example is generated from `evm_dd/examples.py`, flagged `synthetic`, and banner-labelled in the applet | `tests/test_behavioural_examples.py::test_every_example_is_labelled_synthetic_and_is_internally_sound` |

## Layout

- `evm_dd/` — the deterministic core. Keccak, EIP-55, ABI coding, the
  read-only RPC client, block pins, proxy resolution, capability scanning,
  pool identity, conservation arithmetic, the evidence ledger, the
  target-integrity manifest and its validator, and the report model.
- `scripts/main.py` — the path component: read-only views plus the CLI.
- `skill/instructions.md` — the operating procedure.
- `skill/references/` — conditional deep procedures, loaded on demand.
- `skill/scripts/assay_validate.py` — the standalone report validator.
- `applet/` — the report page: target card, surface matrix, evidence
  ledger, coverage limitations.
- `tests/evals/` — eval specs for `wayfinder path eval`.

The Python suite — 184 checks, including the four synthetic rejection
cases and the six behavioural examples — lives in
`evm-token-due-diligence-tests`, one level up. Everything inside the path
directory is packaged into the published bundle, and test code is neither
something an installer should download nor something a bundle scanner
should be reading as runtime.

```bash
python -m pytest evm-token-due-diligence-tests -q   # run from the paths/ directory
```

## Dependencies

CPython only. No third-party packages, enforced by
`tests/test_read_only.py::test_the_pack_depends_only_on_the_standard_library`.

Keccak-256 is implemented in the pack because `hashlib.sha3_256` is
FIPS-202 SHA3 and produces different digests than the Keccak Ethereum uses.
It is pinned to published vectors and to every sponge boundary. Constants
that are usually pasted in — the EIP-1967 and EIP-1822 slots, function
selectors, the v4 PoolId — are **derived** from their specification
strings instead, so they cannot drift or be mistyped.

## What you supply

An RPC endpoint, in `EVM_RPC_URL` or `--rpc`. The pack ships none and
redacts credentials out of everything it writes down. Historical replay
needs archive depth; without it, the affected checks come back as coverage
limitations rather than as results.

## Running

```bash
# the whole pass in one command: collect, rate, self-validate, emit
python scripts/main.py report eip155:1:0xYourToken --rpc "$EVM_RPC_URL" --out report.json

# just the pinned target packet plus its integrity manifest
python scripts/main.py packet eip155:1:0xYourToken --rpc "$EVM_RPC_URL" --out packet.json

# capability scan of runtime bytecode, offline
python scripts/main.py scan --hex 0x60806040...

# pool identity: a v3 candidate address, or a v4 PoolId from a complete key
python scripts/main.py pool v3 0xToken0 0xToken1 --factory 0xFactory --fee 3000
python scripts/main.py pool v4 0xCurrency0 0xCurrency1 --fee 3000 --tick-spacing 60

# close a balance to an explicit residual
python scripts/main.py reconcile flows.json

# validate a finished report against its manifest (identical checks, two entry points)
python scripts/main.py validate manifest.json --report report.json
python skill/scripts/assay_validate.py manifest.json --report report.json --json

# the six synthetic worked examples
python scripts/main.py example
python scripts/main.py example locked-canonical-removable-side-pool

# tests (from the directory above this one)
python -m pytest evm-token-due-diligence-tests -q
```

### The shape of a flows file

```json
{
  "asset": "WETH",
  "holder": "0xaaaa…",
  "decimals": 18,
  "opening": "0",
  "closing": "41000000000000000000",
  "from_block": 21000000,
  "to_block": 21633856,
  "tolerance": "0",
  "flows": [
    { "label": "LP fee collection", "amount": "24000000000000000000",
      "direction": "in", "kind": "fee_collection", "tx_hash": "0x…" },
    { "label": "wrap: ETH out", "amount": "1000000000000000000",
      "direction": "out", "kind": "transformation" },
    { "label": "wrap: WETH in", "amount": "1000000000000000000",
      "direction": "in", "kind": "transformation" },
    { "label": "reverted claim", "amount": "5000000000000000000",
      "direction": "in", "kind": "fee_collection", "reverted": true }
  ]
}
```

Amounts are exact base units as strings. A `transformation` is the same
value in another form and must appear as both legs, netting to zero — it
is never counted as flow. A reverted transaction moved nothing and is
recorded so the attempt stays visible. The command prints inflows,
outflows, the residual, and the residual as basis points of turnover.

A complete worked file lives in
`evm-token-due-diligence-tests/fixtures/example-flows.json`.

## What `report` gives you

One command does the whole pass: verifies the chain id, pins the block,
resolves the executing implementation behind any proxy, scans that runtime
for privileged capability, rates the surfaces the evidence supports, and
runs the report through the pack's own validator before handing it over.

It rates what it read and **names every surface it did not reach as
`not_checked`**. Because ten of the eleven surfaces need reads this pass
does not make, a clean result on token controls produces
`INSUFFICIENT EVIDENCE` at the document level, not `GO` — while still
leading with what was established. A critical finding still outranks the
gaps and returns `NO-GO`.

That asymmetry is the point: gaps cannot be argued into an approval, and a
blocker cannot be diluted by them.

## The one thing to remember about the validator

Passing means the report is **internally consistent**: about the target it
claims to be about, pinned to real blocks, with attributed scope addresses
and no unknown dressed up as a pass.

It does not mean the endpoint told the truth, that discovery was complete,
or that the token is safe. A validator that claimed otherwise would be
committing the exact error it exists to catch.
