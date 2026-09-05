# Surface A — token code and control

## What to establish

Whether anyone can mint, burn from an account, rebase, rewrite balances,
seize, pause, blacklist, whitelist, tax, exempt, cool down, cap
transactions, gate trading, call out arbitrarily, delegatecall, or upgrade.

Then, separately: **who holds those powers now**, and **who can change who
holds them**.

## Order of work

1. Resolve what actually executes. `packet` walks EIP-1967 (implementation,
   admin, beacon), EIP-1822, the pre-1967 OpenZeppelin slots, and EIP-1167
   minimal-proxy runtime. Scan the *implementation*, not the proxy.
2. Capability scan the executing runtime:
   `python path/scripts/main.py scan --hex 0x…`. Selectors are computed
   from signatures, so the table cannot drift.
3. Read authority live at the pin: `owner()`, `getRoleAdmin`, `hasRole` for
   the roles the scan surfaced, pending-owner state, and whether each
   holder is an EOA, a multisig, or a timelock — check `eth_getCode` on it,
   do not take a label's word.
4. For a timelock, read the delay and who can cancel or bypass it. A
   timelock with a bypass role is a formality.

## What a scan does and does not prove

A present selector shows an exposed entry point. It does not show that the
function is reachable, that it does what its name suggests, or that anyone
currently holds the authority to call it.

An absent selector proves nothing on its own. A contract that DELEGATECALLs
answers for entry points that are not in its own dispatcher, and a fallback
can route anything. `evm_dd.selectors.describe_limits` returns the caveats
that must travel with any conclusion from a scan.

## "Renounced" and "no owner"

Both are claims to investigate.

- Ownership renounced, but the contract has an unchanged `MINTER_ROLE`
  holder → supply is still mutable.
- Ownership renounced on the token, but the token is a proxy → the
  implementation can be replaced and the new one can have any owner it
  likes.
- Owner is the zero address, but a hardcoded address in the runtime still
  passes an access check → read the code, not the getter.

Record what the current code permits *and* what an administrator could
introduce by replacing it. Those are two different risks and they belong in
two different sentences.

## Fee and tax mechanics

Distinguish a fee expressed as a share of gross trade value from a share of
a fee bucket — they differ by an order of magnitude and are routinely
conflated. Distinguish current configuration from historically realised
rates: read the setter's bounds, then check what was actually charged.
