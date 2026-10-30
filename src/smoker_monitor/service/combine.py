"""Combine every source's Snapshot into the one Snapshot the rest of the app uses.

Each source (brand) produces its own Snapshot. The monitor keeps each one's
last good Snapshot and how it's doing (SourceHealth), and this module merges
them: all the Gateways and probes in one place, plus the health of every
source, so the alarm rules and screens never need to know about brands.

Pure logic, no network: easy to test.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import TypeVar

from smoker_monitor.domain.models import Gateway, Grill, Probe, Snapshot, SourceHealth

log = logging.getLogger(__name__)

Device = TypeVar("Device", Gateway, Grill, Probe)


def combine(now: datetime, parts: Sequence[tuple[SourceHealth, Snapshot | None]]) -> Snapshot:
    """One Snapshot from every source's (health, last good Snapshot).

    A source with no good Snapshot yet adds no devices, only its health. Any
    device a source forgot to label with its `source` is labelled here (with a
    warning), so ids from different brands can never clash.
    """
    gateways: list[Gateway] = []
    grills: list[Grill] = []
    probes: list[Probe] = []
    for health, snapshot in parts:
        if snapshot is None:
            continue
        gateways.extend(labelled(g, health.key) for g in snapshot.gateways)
        grills.extend(labelled(g, health.key) for g in snapshot.grills)
        probes.extend(labelled(p, health.key) for p in snapshot.probes)
    return Snapshot(
        taken_at=now,
        gateways=tuple(gateways),
        probes=tuple(probes),
        sources=tuple(health for health, _ in parts),
        grills=tuple(grills),
    )


def labelled(device: Device, key: str) -> Device:
    """The device with `source` set to `key`, if it wasn't set already."""
    if device.source == key:
        return device
    if device.source:
        log.warning("Source %r sent a device from %r; relabelling it", key, device.source)
    else:
        log.warning("Source %r sent a device without a source; labelling it", key)
    return replace(device, source=key)
