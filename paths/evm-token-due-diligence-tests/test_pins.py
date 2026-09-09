import pytest

from evm_dd.pins import BlockPin, PinError, PinSet
from helpers import BLOCK_HASH, BLOCK_NUMBER, BLOCK_TIMESTAMP, header


def test_a_pin_is_captured_from_the_header_it_describes():
    pin = BlockPin.from_header(1, header(), source="endpoint")
    assert pin.number == BLOCK_NUMBER
    assert pin.block_hash == BLOCK_HASH
    assert pin.timestamp == BLOCK_TIMESTAMP
    assert pin.block_tag == hex(BLOCK_NUMBER)
    assert pin.utc.endswith("Z")
    assert pin.verify_against_header(header()) == []


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"number": 0, "block_hash": BLOCK_HASH, "timestamp": BLOCK_TIMESTAMP}, "placeholder"),
        ({"number": 10, "block_hash": "0x" + "00" * 32, "timestamp": BLOCK_TIMESTAMP}, "zero hash"),
        ({"number": 10, "block_hash": BLOCK_HASH, "timestamp": 0}, "positive"),
        ({"number": 10, "block_hash": BLOCK_HASH, "timestamp": 1}, "genesis"),
        ({"number": 10, "block_hash": "0xdead", "timestamp": BLOCK_TIMESTAMP}, "32-byte"),
    ],
)
def test_placeholder_pins_are_rejected(payload, fragment):
    with pytest.raises(PinError) as excinfo:
        BlockPin.from_dict({"chain_id": 1, **payload})
    assert fragment in str(excinfo.value)


def test_a_pin_that_disagrees_with_its_header_is_detected():
    pin = BlockPin.from_header(1, header())
    problems = pin.verify_against_header(header(block_hash="0x" + "ff" * 32))
    assert problems and "hash" in problems[0]
    problems = pin.verify_against_header(header(number=BLOCK_NUMBER + 1))
    assert problems and "number" in problems[0]
    problems = pin.verify_against_header(header(timestamp=BLOCK_TIMESTAMP + 12))
    assert problems and "timestamp" in problems[0]


def test_a_future_timestamp_is_refused():
    pin = BlockPin.from_header(1, header())
    with pytest.raises(PinError):
        pin.check_not_in_future(now=BLOCK_TIMESTAMP - 10_000)
    pin.check_not_in_future(now=BLOCK_TIMESTAMP + 10)


def test_one_chain_never_borrows_another_chains_pin():
    pins = PinSet()
    pins.add(BlockPin.from_header(1, header()))
    assert pins.require(1).number == BLOCK_NUMBER
    with pytest.raises(PinError) as excinfo:
        pins.require(8453)
    assert "never reuse another chain's pin" in str(excinfo.value)


def test_repinning_a_chain_mid_run_is_refused():
    pins = PinSet()
    pins.add(BlockPin.from_header(1, header()))
    with pytest.raises(PinError):
        pins.add(BlockPin.from_header(1, header(number=BLOCK_NUMBER + 1, block_hash="0x" + "ee" * 32)))
