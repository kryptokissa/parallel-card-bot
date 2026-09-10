"""Reading capability off deployed runtime bytecode.

Selectors are computed from signatures at import time with the pack's own
Keccak, so there is no hand-copied hex table to go stale or be mistyped.

What this can and cannot say, stated once so the report can quote it:

*   A selector present in the dispatcher means the deployed runtime exposes
    that entry point. It does **not** prove the function is reachable, that
    it does what its name suggests, or that anyone currently holds the role
    that can call it. Confirm authority separately.
*   A selector absent proves nothing on its own. A contract that
    DELEGATECALLs — every proxy — answers for entry points that are not in
    its own dispatcher, and a fallback can route anything. Resolve the
    implementation first, then scan that.
"""

from __future__ import annotations

from dataclasses import dataclass
from evm_dd.keccak import selector as compute_selector

PUSH1, PUSH32 = 0x60, 0x7F
PUSH4 = 0x63

NOTABLE_OPCODES = {
    0xF1: ("CALL", "calls out to another contract"),
    0xF2: ("CALLCODE", "legacy delegated execution"),
    0xF4: ("DELEGATECALL", "runs foreign code against this contract's storage"),
    0xF5: ("CREATE2", "deploys to a deterministic address"),
    0xFA: ("STATICCALL", "read-only call out"),
    0xFF: ("SELFDESTRUCT", "can remove the code at this address"),
}

# capability -> (signature, note)
_CAPABILITY_SIGNATURES: dict[str, tuple[tuple[str, str], ...]] = {
    "supply_change": (
        ("mint(address,uint256)", "mints to an arbitrary recipient"),
        ("mint(uint256)", "mints to the caller or a fixed sink"),
        ("mintTo(address,uint256)", "mints to an arbitrary recipient"),
        ("burn(address,uint256)", "burns a named account's balance"),
        ("rebase(uint256)", "rewrites every balance by a factor"),
        ("setTotalSupply(uint256)", "rewrites supply directly"),
    ),
    "balance_rewrite": (
        ("setBalance(address,uint256)", "writes a balance directly"),
        ("destroyBlackFunds(address)", "destroys a frozen account's balance"),
        ("seize(address,address,uint256)", "moves another account's balance"),
        ("confiscate(address,uint256)", "takes another account's balance"),
        ("wipeFrozenAddress(address)", "zeroes a frozen account"),
    ),
    "transfer_restriction": (
        ("pause()", "halts transfers"),
        ("unpause()", "resumes transfers"),
        ("setPaused(bool)", "halts or resumes transfers"),
        ("blacklist(address)", "blocks an address"),
        ("addBlackList(address)", "blocks an address"),
        ("setBlacklist(address,bool)", "blocks or unblocks an address"),
        ("setBlackListed(address,bool)", "blocks or unblocks an address"),
        ("freeze(address)", "freezes an account"),
        ("addToWhitelist(address)", "gates transfers on a list"),
        ("setWhitelist(address,bool)", "gates transfers on a list"),
        ("setMaxTxAmount(uint256)", "caps transfer size"),
        ("setMaxWalletAmount(uint256)", "caps holdings"),
        ("setTradingEnabled(bool)", "gates trading"),
        ("enableTrading()", "opens trading, implying it can be shut"),
        ("setCooldown(uint256)", "throttles trading per account"),
        ("setTransferDelay(bool)", "throttles trading per account"),
    ),
    "fee_control": (
        ("setFee(uint256)", "changes a transfer fee"),
        ("setFees(uint256,uint256)", "changes transfer fees"),
        ("setBuyTax(uint256)", "changes the buy tax"),
        ("setSellTax(uint256)", "changes the sell tax"),
        ("setTaxes(uint256,uint256,uint256)", "changes taxes"),
        ("excludeFromFee(address)", "exempts an address from fees"),
        ("setFeeRecipient(address)", "redirects fee flow"),
        ("setTreasury(address)", "redirects treasury flow"),
        ("setMarketingWallet(address)", "redirects fee flow"),
    ),
    "upgrade": (
        ("upgradeTo(address)", "replaces the implementation"),
        ("upgradeToAndCall(address,bytes)", "replaces the implementation and calls it"),
        ("changeAdmin(address)", "moves proxy admin authority"),
        ("setImplementation(address)", "replaces the implementation"),
        ("admin()", "proxy admin getter"),
        ("implementation()", "proxy implementation getter"),
    ),
    "authority": (
        ("owner()", "single-owner authority"),
        ("transferOwnership(address)", "moves ownership"),
        ("renounceOwnership()", "drops ownership"),
        ("acceptOwnership()", "two-step ownership handover"),
        ("setOwner(address)", "moves ownership"),
        ("grantRole(bytes32,address)", "grants a role"),
        ("revokeRole(bytes32,address)", "revokes a role"),
        ("hasRole(bytes32,address)", "role membership getter"),
        ("getRoleAdmin(bytes32)", "role admin getter"),
        ("DEFAULT_ADMIN_ROLE()", "role hierarchy root"),
    ),
    "arbitrary_call": (
        ("execute(address,uint256,bytes)", "performs a caller-supplied call"),
        ("call(address,bytes)", "performs a caller-supplied call"),
        ("multicall(bytes[])", "batches caller-supplied calls"),
        ("functionCall(address,bytes)", "performs a caller-supplied call"),
        ("delegate(address,bytes)", "delegatecalls caller-supplied code"),
    ),
    "asset_extraction": (
        ("rescueTokens(address,uint256)", "moves tokens held by the contract"),
        ("rescueERC20(address,uint256)", "moves tokens held by the contract"),
        ("sweep(address)", "moves tokens held by the contract"),
        ("sweepToken(address,uint256)", "moves tokens held by the contract"),
        ("withdrawToken(address,uint256)", "moves tokens held by the contract"),
        ("emergencyWithdraw()", "moves held assets out"),
        ("withdrawStuckETH()", "moves held native currency out"),
        ("recoverERC20(address,uint256)", "moves tokens held by the contract"),
    ),
    "liquidity_removal": (
        ("decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))",
         "withdraws principal from a v3 position"),
        ("collect((uint256,address,uint128,uint128))", "collects position fees"),
        ("removeLiquidity(address,address,uint256,uint256,uint256,address,uint256)",
         "withdraws principal from a v2 pair"),
        ("unlock()", "releases a lock"),
        ("withdraw()", "withdraws from a locker or vault"),
        ("withdrawNFT(address,uint256)", "moves a position NFT out of a locker"),
        ("extendLock(uint256)", "changes lock duration"),
    ),
    "holder_burn": (
        ("burn(uint256)", "lets a holder burn their own balance"),
        ("burnFrom(address,uint256)", "burns an allowance the holder granted"),
    ),
    "external_dependency": (
        ("setOracle(address)", "changes the price source"),
        ("setPriceFeed(address)", "changes the price source"),
        ("setRouter(address)", "changes the trade route"),
        ("setBridge(address)", "changes the bridge"),
        ("setKeeper(address)", "changes who can run the machinery"),
    ),
}

SEVERITY_BY_CAPABILITY = {
    "supply_change": "critical",
    "balance_rewrite": "critical",
    "upgrade": "critical",
    "arbitrary_call": "critical",
    "transfer_restriction": "high",
    "liquidity_removal": "high",
    "asset_extraction": "high",
    "fee_control": "medium",
    "external_dependency": "medium",
    "authority": "informational",
    "holder_burn": "informational",
}

# Capabilities where a monetary materiality threshold must never be applied:
# the existence of the power is the finding.
ALWAYS_MATERIAL = frozenset(
    {
        "supply_change",
        "balance_rewrite",
        "upgrade",
        "arbitrary_call",
        "transfer_restriction",
        "liquidity_removal",
    }
)


@dataclass(frozen=True)
class KnownSelector:
    selector: str
    signature: str
    capability: str
    note: str

    @property
    def severity(self) -> str:
        return SEVERITY_BY_CAPABILITY.get(self.capability, "informational")


def _build_table() -> dict[str, KnownSelector]:
    table: dict[str, KnownSelector] = {}
    for capability, entries in _CAPABILITY_SIGNATURES.items():
        for signature, note in entries:
            sel = compute_selector(signature)
            # A collision would mean two signatures share a selector; keep the
            # first and let the second surface through `signature` in notes.
            table.setdefault(
                sel,
                KnownSelector(
                    selector=sel, signature=signature, capability=capability, note=note
                ),
            )
    return table


KNOWN_SELECTORS: dict[str, KnownSelector] = _build_table()


def strip_hex(code: str | bytes) -> bytes:
    if isinstance(code, (bytes, bytearray)):
        return bytes(code)
    text = str(code).strip()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) % 2:
        raise ValueError("runtime code has an odd number of hex digits")
    return bytes.fromhex(text)


def extract_push4_selectors(code: str | bytes) -> list[str]:
    """Every 4-byte PUSH4 immediate in the runtime, in order of appearance.

    PUSH immediates are skipped rather than decoded as opcodes, so constant
    data cannot masquerade as instructions.
    """
    raw = strip_hex(code)
    found: list[str] = []
    seen: set[str] = set()
    index = 0
    while index < len(raw):
        opcode = raw[index]
        if PUSH1 <= opcode <= PUSH32:
            width = opcode - PUSH1 + 1
            immediate = raw[index + 1 : index + 1 + width]
            if opcode == PUSH4 and len(immediate) == 4:
                value = "0x" + immediate.hex()
                if value not in seen:
                    seen.add(value)
                    found.append(value)
            index += 1 + width
            continue
        index += 1
    return found


def opcodes_present(code: str | bytes) -> dict[str, str]:
    """Notable opcodes actually executed, with PUSH data skipped."""
    raw = strip_hex(code)
    present: dict[str, str] = {}
    index = 0
    while index < len(raw):
        opcode = raw[index]
        if PUSH1 <= opcode <= PUSH32:
            index += 1 + (opcode - PUSH1 + 1)
            continue
        if opcode in NOTABLE_OPCODES:
            name, note = NOTABLE_OPCODES[opcode]
            present.setdefault(name, note)
        index += 1
    return present


@dataclass
class CapabilityScan:
    runtime_size: int
    runtime_hash: str
    selectors: list[str]
    matched: list[KnownSelector]
    unmatched: list[str]
    opcodes: dict[str, str]

    @property
    def capabilities(self) -> dict[str, list[KnownSelector]]:
        grouped: dict[str, list[KnownSelector]] = {}
        for item in self.matched:
            grouped.setdefault(item.capability, []).append(item)
        return grouped

    @property
    def delegates(self) -> bool:
        return "DELEGATECALL" in self.opcodes or "CALLCODE" in self.opcodes

    @property
    def self_destructs(self) -> bool:
        return "SELFDESTRUCT" in self.opcodes

    def to_dict(self) -> dict[str, object]:
        return {
            "runtime_size": self.runtime_size,
            "runtime_hash": self.runtime_hash,
            "selector_count": len(self.selectors),
            "unmatched_selector_count": len(self.unmatched),
            "delegates": self.delegates,
            "self_destructs": self.self_destructs,
            "opcodes": self.opcodes,
            "capabilities": {
                capability: [
                    {
                        "selector": item.selector,
                        "signature": item.signature,
                        "note": item.note,
                        "severity": item.severity,
                        "always_material": capability in ALWAYS_MATERIAL,
                    }
                    for item in items
                ]
                for capability, items in sorted(self.capabilities.items())
            },
        }


def scan_runtime(code: str | bytes) -> CapabilityScan:
    from evm_dd.keccak import keccak_hex

    raw = strip_hex(code)
    selectors = extract_push4_selectors(raw)
    matched = [KNOWN_SELECTORS[item] for item in selectors if item in KNOWN_SELECTORS]
    unmatched = [item for item in selectors if item not in KNOWN_SELECTORS]
    return CapabilityScan(
        runtime_size=len(raw),
        runtime_hash=keccak_hex(raw),
        selectors=selectors,
        matched=matched,
        unmatched=unmatched,
        opcodes=opcodes_present(raw),
    )


def describe_limits(scan: CapabilityScan) -> list[str]:
    """The caveats that must travel with any conclusion drawn from a scan."""
    limits = [
        "A present selector shows an exposed entry point, not that it is "
        "reachable, that it behaves as its name suggests, or that anyone "
        "currently holds the authority to call it.",
        "An absent selector proves nothing by itself.",
    ]
    if scan.delegates:
        limits.append(
            "This runtime DELEGATECALLs: behaviour can live in code that is "
            "not in this bytecode, and can be replaced. Resolve the "
            "implementation and scan that before concluding anything."
        )
    if scan.self_destructs:
        limits.append(
            "This runtime contains SELFDESTRUCT: the code at this address is "
            "not necessarily permanent."
        )
    if "holder_burn" in scan.capabilities:
        limits.append(
            "burn(uint256) and burnFrom(address,uint256) are the standard "
            "ERC20Burnable surface: the first burns the caller's own balance, "
            "the second spends an allowance the holder granted. Neither is "
            "privileged unless this implementation skips the allowance check "
            "— read that path before treating either as seizure."
        )
    if not scan.selectors:
        limits.append(
            "No PUSH4 selectors were found. This is normal for minimal "
            "proxies and hand-written assembly, and means the dispatcher "
            "cannot be read this way."
        )
    return limits
