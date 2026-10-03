"""Domain models describing one moment of a cook.

Suggested shape (frozen dataclasses, all temperatures stored in one unit):
- Reading:   a temperature value + when it was taken (timezone-aware UTC).
- Probe:     one RFX MEAT probe — id, label, battery %, its sensor readings,
             and the "core" reading (lowest sensor) used for doneness.
- Pit:       the pit/air probe reading.
- Fan:       Billows state — connected, set temperature, output/state.
- Snapshot:  everything above at one poll, plus `source_last_seen`.

Design notes:
- Pick ONE internal unit (°C or °F) and convert only at the edges.
- Store `last_seen` on each item so staleness can be judged per device.
- Missing data should be explicit (None / Optional), never a fake 0.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Reading:
    """A single temp reading and when it was taken"""

    celsius: float | None
    taken_at: datetime

    def age(self, now: datetime) -> timedelta:
        """How long ago this reading was taken"""
        return now - self.taken_at
