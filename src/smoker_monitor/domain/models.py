"""Domain models: everything we know about a cook at one moment.

These are plain, frozen (read-only) dataclasses with no network or hardware
code, so they are easy to test.

- AlarmLimit:    one high or low alarm limit on a channel.
- AlarmSettings: a channel's high and low alarm limits together.
- Reading:       one temperature and when it was taken.
- RingColour:    the colours a probe's rings can be (the app's own list).
- Probe:         an RFX MEAT probe: sensors, battery, alarms, name and rings.
- FanActivity:   what the fan is doing (blowing, pulsing or unknown).
- Fan:           the Billows fan: connected or not, set temperature, activity.
- Gateway:       the RFX Gateway: pit (air) probe, fan, Wi-Fi and alarm settings.
- GrillMode:     what a grill is doing (off, preheating, cooking, ...).
- Grill:         a cooker that reports its own temperatures (e.g. a Traeger).
- SourceHealth:  whether one source (brand) is working right now.
- Snapshot:      every Gateway, grill and probe from every source, at one poll.

Rules followed throughout:
- Temperatures are always °C. Sources convert from °F before building these.
- Missing data is None, never a fake 0.
- Every device has `last_seen`, so we can tell when its data has gone stale.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class AlarmLimit:
    """The parameter which describes what a high or low alarm must be above or below

    a None celsius meanings nothing received"""

    enabled: bool
    celsius: float | None
    alarming: bool = False


@dataclass(frozen=True)
class AlarmSettings:
    """The parameter which holds probe or gateway alarm settings. None is no alarm set"""

    high: AlarmLimit | None
    low: AlarmLimit | None


NO_ALARMS = AlarmSettings(high=None, low=None)


@dataclass(frozen=True)
class Reading:
    """A single temp reading and when it was taken"""

    celsius: float | None
    taken_at: datetime

    def age(self, now: datetime) -> timedelta:
        """How long ago this reading was taken"""
        return now - self.taken_at


class RingColour(StrEnum):
    """A coloured ring on a probe, used to tell probes apart.

    These are the only colours the app offers. Sources must map whatever they
    receive onto this list (or leave it out), so screens never see raw text.
    """

    BLUE = "blue"
    CHARCOAL = "charcoal"
    GREEN = "green"
    ORANGE = "orange"
    PINK = "pink"
    PURPLE = "purple"
    RED = "red"
    WHITE = "white"
    YELLOW = "yellow"


@dataclass(frozen=True)
class Probe:
    """A single temp probe, and the readings it gives"""

    serial: str
    label: str | None  # the device type, e.g. "RFX MEAT"
    battery_pct: int | None
    last_seen: datetime
    sensors: tuple[Reading, ...]
    alarms: AlarmSettings = NO_ALARMS
    name: str | None = None  # your name for it, e.g. "white probe" (already tidied)
    rings: tuple[RingColour, ...] = ()  # its ring colours, top to bottom (up to 3)
    source: str = ""  # short name of the source that reported it, e.g. "eti"

    @property
    def id(self) -> str:
        """Unique across all sources, e.g. "eti:PROBE-1" (just the serial if no source)."""
        return f"{self.source}:{self.serial}" if self.source else self.serial

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


class FanActivity(StrEnum):
    """What the fan is doing. Sources translate their own codes into these."""

    BLOWING = "blowing"  # running flat out to raise the pit temperature
    PULSING = "pulsing"  # short bursts, near the set temperature
    UNKNOWN = "unknown"  # a code we haven't seen yet; see Fan.state


@dataclass(frozen=True)
class Fan:
    """The fan is the object controlled by the gateway to manage pit temps"""

    set_temp_celsius: float | None
    state: int | None  # the source's raw state code, kept for display (None if missing)
    connected: bool
    activity: FanActivity = FanActivity.UNKNOWN
    output_pct: int | None = None  # how hard it's running, 0-100, if the source says


@dataclass(frozen=True)
class Gateway:
    """The gateway is the object the probes
    connect to, and passes the data to the server via wifi"""

    serial: str
    label: str | None  # the device type, e.g. "RFX GATEWAY"
    battery_pct: int | None
    wifi_dbm: int | None
    last_seen: datetime
    pit: Reading | None
    fan: Fan | None
    pit_alarms: AlarmSettings = NO_ALARMS
    name: str | None = None  # your name for it, if you've given one (already tidied)
    source: str = ""  # short name of the source that reported it, e.g. "eti"

    @property
    def id(self) -> str:
        """Unique across all sources, e.g. "eti:GATEWAY-1" (just the serial if no source)."""
        return f"{self.source}:{self.serial}" if self.source else self.serial

    def get_pit_temp(self) -> float | None:
        """Get the temp of the pit probes readings"""
        if self.pit is not None:
            return self.pit.celsius
        else:
            return None

    def pit_deviation(self) -> float | None:
        """How far the pit is from the Billows set temp, in °C.

        Positive means the pit is too hot, negative too cold. Returns None when
        it can't be known: no pit reading, no fan, fan disconnected, or no set temp.
        """
        pit_temp = self.get_pit_temp()
        if pit_temp is None:
            # the probe has no temp
            return None
        if self.fan is not None and self.fan.set_temp_celsius is not None and self.fan.connected:
            return pit_temp - self.fan.set_temp_celsius
        # fan is not connected or set up
        return None


class GrillMode(StrEnum):
    """What a grill is doing. Sources translate their own codes into these."""

    OFFLINE = "offline"  # not connected to the cloud
    SLEEPING = "sleeping"  # switched on, screen off
    IDLE = "idle"  # switched on, not cooking
    IGNITING = "igniting"
    PREHEATING = "preheating"  # heating up to its set temperature
    COOKING = "cooking"  # holding its set temperature
    COOLING_DOWN = "cooling down"  # burning off after being switched off
    SHUT_DOWN = "shut down"  # finished cooling down
    UNKNOWN = "unknown"  # a code we haven't seen yet

    @property
    def is_on(self) -> bool:
        """Lit or being lit: igniting, preheating, cooking or cooling down."""
        return self in {
            GrillMode.IGNITING,
            GrillMode.PREHEATING,
            GrillMode.COOKING,
            GrillMode.COOLING_DOWN,
        }


@dataclass(frozen=True)
class Grill:
    """A cooker that reports its own temperatures, e.g. a Traeger pellet grill.

    Unlike a Gateway (a hub carrying a pit probe), the grill is the pit. Its
    food probes are separate Probe objects in the Snapshot, like ETI's.
    """

    serial: str
    label: str | None  # the model, e.g. "Pro 575"
    last_seen: datetime
    connected: bool  # online, as far as the grill's cloud knows
    mode: GrillMode
    temp: Reading | None  # the grill (cooking chamber) temperature
    set_temp_celsius: float | None  # what it's set to, if anything
    pellet_pct: int | None = None  # pellets left, 0-100, if it has a sensor
    name: str | None = None  # your name for it, if you've given one
    source: str = ""  # short name of the source that reported it, e.g. "traeger"

    @property
    def id(self) -> str:
        """Unique across all sources, e.g. "traeger:GRILL-1" (just the serial if no source)."""
        return f"{self.source}:{self.serial}" if self.source else self.serial

    def celsius(self) -> float | None:
        """The grill temperature, or None if there's no reading."""
        return self.temp.celsius if self.temp is not None else None


@dataclass(frozen=True)
class SourceHealth:
    """How one source (brand) is doing, as of this poll."""

    key: str  # short name, e.g. "eti": the prefix on its devices' ids
    name: str  # e.g. "ETI Cloud": shown on screens and in alarms
    expects_gateway: bool  # should "No gateway found" fire if it reports none?
    error: str | None = None  # None when its last fetch worked
    failing_since: datetime | None = None  # first failed fetch in a row, if failing


@dataclass(frozen=True)
class Snapshot:
    """Everything known about the cook at one poll, from every source combined.

    A source fills in only what it has: ETI gives its Gateway and probes; a
    Traeger gives its grill and probes; a brand with no hub (e.g. Meater) gives
    just probes.
    """

    taken_at: datetime
    gateways: tuple[Gateway, ...]
    probes: tuple[Probe, ...]
    sources: tuple[SourceHealth, ...] = ()  # how each source is doing
    grills: tuple[Grill, ...] = ()

    def source_name(self, key: str) -> str:
        """A source's display name, e.g. "ETI Cloud" for "eti" (the key if unknown)."""
        for source in self.sources:
            if source.key == key:
                return source.name
        return key
