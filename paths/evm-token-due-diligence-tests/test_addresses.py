from evm_dd.addresses import (
    AddressError,
    TargetRef,
    has_valid_checksum,
    is_burn_address,
    parse_target,
    require_address,
    same_address,
    to_checksum,
)

import pytest

EIP55_VECTORS = (
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
)


@pytest.mark.parametrize("address", EIP55_VECTORS)
def test_eip55_round_trip(address):
    assert to_checksum(address.lower()) == address
    assert has_valid_checksum(address)


def test_a_broken_checksum_is_refused_rather_than_normalised():
    tampered = EIP55_VECTORS[0][:-1] + ("D" if EIP55_VECTORS[0][-1] != "D" else "d")
    assert not has_valid_checksum(tampered)
    with pytest.raises(AddressError):
        require_address(tampered)


def test_single_case_addresses_make_no_checksum_claim():
    assert has_valid_checksum(EIP55_VECTORS[0].lower())
    assert has_valid_checksum("0x" + EIP55_VECTORS[0][2:].upper())


def test_target_identity_is_chain_plus_address():
    mainnet = TargetRef(1, EIP55_VECTORS[0])
    base = TargetRef(8453, EIP55_VECTORS[0])
    assert mainnet != base
    assert mainnet.caip10 == f"eip155:1:{EIP55_VECTORS[0]}"
    assert mainnet.matches(1, EIP55_VECTORS[0].lower())
    assert not mainnet.matches(8453, EIP55_VECTORS[0])


@pytest.mark.parametrize(
    "text",
    ["eip155:1:" + EIP55_VECTORS[0], "1:" + EIP55_VECTORS[0], EIP55_VECTORS[0] + "@1"],
)
def test_target_parsing_accepts_the_documented_forms(text):
    assert parse_target(text) == TargetRef(1, EIP55_VECTORS[0])


def test_a_bare_address_is_refused_because_it_names_no_chain():
    with pytest.raises(AddressError):
        parse_target(EIP55_VECTORS[0])


def test_burn_address_recognition_and_comparison():
    assert is_burn_address("0x000000000000000000000000000000000000dEaD")
    assert is_burn_address("0x" + "00" * 20)
    assert same_address(EIP55_VECTORS[0], EIP55_VECTORS[0].lower())
