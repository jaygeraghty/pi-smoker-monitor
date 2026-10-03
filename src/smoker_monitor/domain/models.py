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


@dataclass(frozen=True)
class Probe:
    """A single temp probe, and the readings it gives"""

    serial: str
    label: str | None
    battery_pct: int | None
    last_seen: datetime
    sensors: tuple[Reading, ...]

    def core_celsius(self) -> float | None:
        """Lowest sensor temperature (the coldest point in the meat), or None if no readings."""

        temps = []
        for r in self.sensors:
            if r.celsius is not None:
                temps.append(r.celsius)
        if temps:
            return min(temps)
        else:
            return None


@dataclass(frozen=True)
class Fan:
    """The fan is the object controlled by the gateway to manage pit temps"""

    set_temp_celsius: float | None
    state: int
    connected: bool


@dataclass(frozen=True)
class Gateway:
    """The gateway is the object the probes
    connect to, and passes the data to the server via wifi"""

    serial: str
    label: str | None
    battery_pct: int | None
    wifi_dbm: int | None
    last_seen: datetime
    pit: Reading | None
    fan: Fan | None

    def get_pit_temp(self) -> float | None:
        """Get the temp of the pit probes readings"""
        if self.pit is not None:
            return self.pit.celsius
        else:
            return None

    def pit_deviation(self) -> float | None:
        """work out how far from the set temp the pit currently is. Returns Nome if nothing can be done about fixing the temp. A positive number means the pit is too hot, negative too cold"""
        pit_temp = self.get_pit_temp()
        if pit_temp is None:
            # the probe has no temp
            return None
        if self.fan and self.fan.set_temp_celsius is not None and self.fan.connected:
            return pit_temp - self.fan.set_temp_celsius
        # fan is not connected or set up
        return None
