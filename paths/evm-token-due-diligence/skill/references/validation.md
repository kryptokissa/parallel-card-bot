# Validating a report

```
python scripts/assay_validate.py manifest.json --report report.json
python scripts/assay_validate.py manifest.json --report report.json --headers headers.json
python scripts/assay_validate.py manifest.json --json
```

`--headers` takes a JSON object mapping chain id to a freshly fetched block
header, and re-checks each pin against it. Use it when a report is being
signed off some time after it was produced.

## What it checks

- requested, queried and reported chain and address agree
- metadata is present as resolved *or* explicitly unresolved, with a reason
- pins are well formed, non-placeholder, and consistent between manifest,
  report and (optionally) a fetched header
- every material scope address has a chain, a role, a provenance and a
  runtime status, on a chain that is pinned
- the report was built from the manifest's target packet (digest match)
- no rating declares a settled status that its own checks do not support —
  this is the "unknown presented as a pass" check
- every favourable rating states its coverage
- material settled findings cite evidence
- the run declared no real signing, no broadcast, no key material, and any
  fork simulation was loopback-verified with synthetic accounts
- the verdict is conditional rather than an unconditional safety claim

## What passing does not mean

Passing validates **internal consistency**. It does not validate that the
RPC endpoint told the truth, that pool or holder discovery was complete,
that decoding was correct, or that the token is safe.

A perfectly consistent report about a target you never should have trusted
still passes. That is the correct behaviour: the validator's job is to stop
a report from being about the wrong thing or claiming more than it checked,
not to have an opinion about the token.

## When it fails

Fix the report, not the validator. Each error names the exact field.

- *different address / different chain* — you have mixed two targets.
  Discard the affected findings; do not "correct" the label.
- *different target packet* — the report was assembled from another run.
  Rebuild it from the packet the manifest names.
- *unknown presented as a settled result* — downgrade the rating. This is
  the check that exists precisely because the temptation is to round up.
- *pin disagrees with the fetched header* — a reorg, a wrong endpoint, or a
  fabricated pin. Re-pin and re-read; do not adjust the recorded pin to
  match.
