"""The `Source` interface every data source implements.

A source is one brand's way of reading the cook: ETI Cloud today, Traeger
later, and fake sources in tests. Each one turns its brand's data into the
shared parts (Gateway, Probe, ...) and returns them as a Snapshot. The monitor
fetches every source and combines their Snapshots into one, so the rest of the
app never depends on a particular brand.

Adding a brand means: a class with the attributes and methods below, its
settings in config.py, and one line in sources/factory.py.

Sources are read-only by design: they fetch readings and never send commands
to a device.

`Source` is a typing.Protocol, Python's equivalent of a C# interface: any class
with matching attributes and methods counts as a Source, without inheriting
from it.
"""

from __future__ import annotations

from typing import Protocol

from smoker_monitor.domain.models import Snapshot


class SourceError(Exception):
    """Raised when a source can't produce a Snapshot (offline, bad login, ...).

    The message is shown to the user, so it must be clear and must never
    contain a password.
    """


class Source(Protocol):
    """Something that can fetch the current state of the cook."""

    key: str  # short and unique, e.g. "eti": prefixes its devices' ids ("eti:G1")
    name: str  # shown on screens and in alarms, e.g. "ETI Cloud"
    poll_seconds: int  # how often to fetch from it
    expects_gateway: bool  # True if "No gateway found" should fire when it reports none

    async def fetch(self) -> Snapshot:
        """Return a Snapshot of everything this source knows right now.

        Every device in it should have `source` set to this source's `key`.
        Raises SourceError if the data can't be fetched.
        """
        ...

    async def close(self) -> None:
        """Let go of any open connection. Called once, when finished."""
        ...
