"""Tests for display names: what screens, reports and logs call each device."""

from __future__ import annotations

from datetime import UTC, datetime

from smoker_monitor.domain.models import Gateway, Probe, RingColour, Snapshot
from smoker_monitor.domain.names import (
    device_names,
    gateway_name,
    probe_base_name,
    probe_names,
)

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)


def probe(
    serial: str,
    name: str | None = None,
    rings: tuple[RingColour, ...] = (),
    label: str | None = "RFX MEAT",
) -> Probe:
    return Probe(serial, label, 90, NOW, (), name=name, rings=rings)


def gateway(name: str | None = None, label: str | None = "RFX GATEWAY") -> Gateway:
    return Gateway("G1", label, 80, -50, NOW, None, None, name=name)


# ------------------------------------------------------------ one probe ---


def test_your_name_comes_first() -> None:
    assert probe_base_name(probe("P1", "brisket", (RingColour.PINK,))) == "brisket"


def test_ring_colour_when_unnamed() -> None:
    assert probe_base_name(probe("P1", rings=(RingColour.PINK,))) == "Pink probe"


def test_several_rings() -> None:
    rings = (RingColour.PINK, RingColour.BLUE, RingColour.WHITE)
    assert probe_base_name(probe("P1", rings=rings)) == "Pink/blue/white probe"


def test_type_when_no_name_or_rings() -> None:
    assert probe_base_name(probe("P1")) == "RFX MEAT"


def test_last_resort() -> None:
    assert probe_base_name(probe("P1", label=None)) == "Probe"


# ---------------------------------------------------------- many probes ---


def test_different_names_are_left_alone() -> None:
    names = probe_names([probe("P1", "brisket"), probe("P2", "ribs")])
    assert names == {"P1": "brisket", "P2": "ribs"}


def test_twins_are_numbered_in_serial_order() -> None:
    """Numbering follows the serials, not the order ETI happens to list them."""
    names = probe_names([probe("P2"), probe("P1"), probe("P3", "ribs")])
    assert names == {"P1": "RFX MEAT 1", "P2": "RFX MEAT 2", "P3": "ribs"}


def test_same_custom_name_is_numbered_too() -> None:
    names = probe_names([probe("P1", "brisket"), probe("P2", "brisket")])
    assert names == {"P1": "brisket 1", "P2": "brisket 2"}


# -------------------------------------------------------------- gateway ---


def test_gateway_names() -> None:
    assert gateway_name(gateway()) == "RFX GATEWAY"
    assert gateway_name(gateway(name="Big Green Egg")) == "Big Green Egg"
    assert gateway_name(gateway(label=None)) == "Gateway"


def test_device_names_covers_everything() -> None:
    snapshot = Snapshot(NOW, gateway(), (probe("P1", "brisket"),))
    assert device_names(snapshot) == {"G1": "RFX GATEWAY", "P1": "brisket"}
