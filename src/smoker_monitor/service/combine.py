"""Combine every source's Snapshot into the one Snapshot the rest of the app uses.

Each source (brand) produces its own Snapshot. The monitor keeps each one's
last good Snapshot and how it's doing (SourceHealth), and this module merges
them: all the Gateways and probes in one place, plus the health of every
source, so the alarm rules and screens never need to know about brands.

Pure logic, no network: easy to test.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence, Set
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


def keep_vanished_probes(
    snapshot: Snapshot, remembered: Mapping[str, Probe], live: Set[str]
) -> Snapshot:
    """Put back any probe from this cook that its source has stopped listing.

    Some brands only list probes that are plugged in (a Traeger does). If one
    falls out mid-cook it would simply vanish, and with nothing left to check,
    no alarm could go off. So a live probe (one seen reporting this cook) that
    has vanished is kept, as last seen. Its data then goes stale and the usual
    "gone quiet" alarm rings after [alarms] probe_timeout_minutes.

    `remembered` holds the last copy of each live probe, by id.
    """
    present = {probe.id for probe in snapshot.probes}
    vanished = tuple(
        probe
        for probe_id, probe in remembered.items()
        if probe_id in live and probe_id not in present
    )
    if not vanished:
        return snapshot
    return replace(snapshot, probes=snapshot.probes + vanished)


def labelled(device: Device, key: str) -> Device:
    """The device with `source` set to `key`, if it wasn't set already."""
    if device.source == key:
        return device
    if device.source:
        log.warning("Source %r sent a device from %r; relabelling it", key, device.source)
    else:
        log.warning("Source %r sent a device without a source; labelling it", key)
    return replace(device, source=key)
