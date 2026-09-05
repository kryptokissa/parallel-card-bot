"""Assay — evidence-bounded due diligence on one exact EVM token.

Standard library only. Read-only by construction: there is no signing path
in this package, and `tests/test_read_only.py` proves it.

Import map:

    keccak      Keccak-256, and the selectors/topics/slots derived from it
    addresses   EIP-55, and TargetRef — chain and address as one identity
    abi         enough ABI coding to read a chain honestly
    rpc         a JSON-RPC client that cannot write
    pins        block pins: the timestamp on every claim
    proxy       what actually executes, and who can replace it
    selectors   capability scanning of deployed runtime
    pools       v2/v3/v4 pool identity and position custody
    reconcile   conservation arithmetic in exact integers
    evidence    evidence tiers, the ledger, and the status algebra
    target      the frozen target packet every lane shares
    manifest    the target-integrity manifest and its validator
    report      the eleven surfaces, and verdicts that do not average
    collect     building a packet from a live endpoint
    examples    synthetic worked examples, labelled as such
    cli         the command line
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
