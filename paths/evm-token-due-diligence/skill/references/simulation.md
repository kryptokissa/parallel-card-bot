# Simulation rules

## Never

- No real private keys or seed phrases, ever, for any reason.
- No signing of real transactions.
- No broadcast test trades.

The pack has no signing path at all: `evm_dd/rpc.py` holds a read-method
allowlist and refuses everything else, and `tests/test_read_only.py` proves
there is no key-material identifier or signing import anywhere in it.

## When simulation is permitted

Only on a **verified disposable local fork**, with **synthetic test
accounts**.

```python
import os
from evm_dd.rpc import fork_client

# The operator supplies the endpoint. The pack ships none, and this call is
# refused unless the endpoint the operator gives resolves to loopback.
client = fork_client(os.environ["EVM_FORK_RPC_URL"])
```

`fork_client` raises on any endpoint that does not resolve to loopback.
State-changing methods (`evm_*`, `anvil_*`, `hardhat_*`,
`eth_sendRawTransaction`) unlock only on such a client. Signing and
account-enumeration methods stay blocked even there.

The loopback rule is a **refusal condition**, not a destination: the pack
contains no endpoint of its own, contacts no host it was not handed, and
carries no URL literal anywhere in its published content.

## Labelling

Every result from a fork is **counterfactual**: true of the fork, not of
the chain. Mark the finding `counterfactual=True`; the ledger then refuses
to let it be recorded as proven of the chain.

Declare it in the manifest: `simulation: "fork"`,
`fork_endpoint_loopback: true`, `fork_accounts: "synthetic"`. The validator
rejects a fork declaration that does not meet those conditions.

## What a simulation can settle

A decisive simulated sale or redemption needs a successful receipt **and**
the intended underlying-asset balance delta, with route and costs
explained — and it still only settles what the fork would do from the
forked state.

It is strong evidence against a claim ("this cannot be sold") and weak
evidence for one ("this can be sold at size"), because the live pool's
state at the moment of a real exit is not the forked state.
