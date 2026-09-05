# Output standard

## Lead with the answer

A direct, conditional verdict on the question that was actually asked —
first paragraph, no preamble. If the question was "can the team rug this",
the first sentence answers that, not "here is an overview of the token".

## Rate the eleven surfaces separately

| # | Surface |
|---|---|
| 1 | Token controls |
| 2 | Canonical LP-principal custody |
| 3 | Side-pool removal risk |
| 4 | Sellability and exit depth |
| 5 | Current concentration |
| 6 | Historical launch integrity |
| 7 | Admin, treasury and reward custody |
| 8 | Reward accounting and liveness |
| 9 | Utility and redemption rights |
| 10 | External dependencies |
| 11 | Development and disclosure |

Each carries severity, likelihood, confidence, coverage and time basis.

**A critical finding is never averaged away against unrelated positive
checks.** `evm_dd.report.derive_verdict` implements this: one adverse
high-or-critical surface produces NO-GO regardless of how many surfaces are
clear. Ten green rows do not outvote one open mint path.

A surface that was not examined is rated `not_checked`, not omitted. The
absence of a row reads as "fine"; an explicit `not_checked` reads as what
it is.

## Bounded language

- "No current executable removal path found at the pinned block."
- "Sellable at the tested sizes under the quoted state."
- "Unknown because historical state was unavailable."
- "NO-GO under the stated requirement for rug resistance."

Never an unconditional "safe". Never any implication that a favourable
review predicts returns. The validator rejects both.

## The finding-to-evidence ledger

One row per proposition:

finding id · exact proposition · chain · address · pin or transaction ·
artifact and query · decoding basis · evidence type · confidence ·
surviving alternatives · coverage · what would make it stale.

For any discovery claim ("there are no other pools", "these are the top
holders"), the row also records the search universe, block ranges or
pagination, inclusion rules and materiality thresholds. Without those, the
claim is about your sample, not about the chain.

## Close with the parts people skip

- the main reasons, briefly
- the **strongest contrary evidence** — the best case against your own
  conclusion, stated fairly
- unresolved questions
- the specific evidence that would change the verdict
- recommendations that address the deficiencies you actually observed, not
  a generic checklist

## Formal reports

Complete and reconcile the evidence first, then format. If a document needs
restructuring, restructure the document — never re-run the research to make
a section look fuller.
