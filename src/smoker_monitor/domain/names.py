"""What to call each device on screens, reports and logs.

Serials are never shown: they identify your kit, and they would leak into any
screenshot you share. Instead each device gets a display name:

- A probe: your name for it ("white probe"), else its ring colours
  ("Pink probe", "Pink/Blue probe"), else its type ("RFX MEAT"), else "Probe".
- The Gateway: your name for it, else its type ("RFX GATEWAY"), else "Gateway".

If two probes end up with the same name (say, both unnamed with no rings),
they are numbered "RFX MEAT 1", "RFX MEAT 2" in serial order, so the numbers
stay put from one poll to the next.

Names arrive already tidied by the source (see sources/eti_cloud.py).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from smoker_monitor.domain.models import Gateway, Probe, Snapshot


def gateway_name(gateway: Gateway) -> str:
    """What to call the Gateway."""
    return gateway.name or gateway.label or "Gateway"


def probe_base_name(probe: Probe) -> str:
    """What to call one probe, before any numbering to tell twins apart."""
    if probe.name:
        return probe.name
    if probe.rings:
        colours = "/".join(ring.value for ring in probe.rings)
        return f"{colours.capitalize()} probe"
    return probe.label or "Probe"


def probe_names(probes: Iterable[Probe]) -> dict[str, str]:
    """Display names for every probe, keyed by serial. Duplicates are numbered."""
    in_order = sorted(probes, key=lambda probe: probe.serial)
    base = {probe.serial: probe_base_name(probe) for probe in in_order}
    counts = Counter(base.values())

    names: dict[str, str] = {}
    seen: Counter[str] = Counter()
    for probe in in_order:
        name = base[probe.serial]
        if counts[name] > 1:
            seen[name] += 1
            name = f"{name} {seen[name]}"
        names[probe.serial] = name
    return names


def device_names(snapshot: Snapshot) -> dict[str, str]:
    """Display names for every device in the Snapshot, keyed by serial."""
    names = probe_names(snapshot.probes)
    if snapshot.gateway is not None:
        names[snapshot.gateway.serial] = gateway_name(snapshot.gateway)
    return names
