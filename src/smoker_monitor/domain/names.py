"""What to call each device on screens, reports and logs.

Serials are never shown: they identify your kit, and they would leak into any
screenshot you share. Instead each device gets a display name:

- A probe: your name for it ("white probe"), else its ring colours
  ("Pink probe", "Pink/Blue probe"), else its type ("RFX MEAT"), else "Probe".
- The Gateway: your name for it, else its type ("RFX GATEWAY"), else "Gateway".
- A grill: your name for it, else its model ("Pro 575"), else "Grill".

If two devices of the same type end up with the same name (say, two unnamed
probes with no rings, or two RFX Gateways), they are numbered "RFX MEAT 1",
"RFX MEAT 2" in id order, so the numbers stay put from one poll to the next.

Names arrive already tidied by the source (see sources/eti_cloud.py).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from smoker_monitor.domain.models import Gateway, Grill, Probe, Snapshot


def gateway_base_name(gateway: Gateway) -> str:
    """What to call one Gateway, before any numbering to tell twins apart."""
    return gateway.name or gateway.label or "Gateway"


def gateway_names(gateways: Iterable[Gateway]) -> dict[str, str]:
    """Display names for every Gateway, keyed by id. Duplicates are numbered."""
    return numbered({gateway.id: gateway_base_name(gateway) for gateway in gateways})


def grill_base_name(grill: Grill) -> str:
    """What to call one grill, before any numbering to tell twins apart."""
    return grill.name or grill.label or "Grill"


def grill_names(grills: Iterable[Grill]) -> dict[str, str]:
    """Display names for every grill, keyed by id. Duplicates are numbered."""
    return numbered({grill.id: grill_base_name(grill) for grill in grills})


def probe_base_name(probe: Probe) -> str:
    """What to call one probe, before any numbering to tell twins apart."""
    if probe.name:
        return probe.name
    if probe.rings:
        colours = "/".join(ring.value for ring in probe.rings)
        return f"{colours.capitalize()} probe"
    return probe.label or "Probe"


def probe_names(probes: Iterable[Probe]) -> dict[str, str]:
    """Display names for every probe, keyed by id. Duplicates are numbered."""
    return numbered({probe.id: probe_base_name(probe) for probe in probes})


def numbered(base: Mapping[str, str]) -> dict[str, str]:
    """Number any names shared by more than one device ("RFX MEAT 1", ...), in id order.

    `base` maps each device id to its name before numbering. The result keeps
    the original order of `base`.
    """
    counts = Counter(base.values())
    seen: Counter[str] = Counter()
    final: dict[str, str] = {}
    for device_id in sorted(base):
        name = base[device_id]
        if counts[name] > 1:
            seen[name] += 1
            name = f"{name} {seen[name]}"
        final[device_id] = name
    return {device_id: final[device_id] for device_id in base}


def device_names(snapshot: Snapshot) -> dict[str, str]:
    """Display names for every device in the Snapshot, keyed by id."""
    return (
        probe_names(snapshot.probes)
        | gateway_names(snapshot.gateways)
        | grill_names(snapshot.grills)
    )
