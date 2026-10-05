"""The `Source` interface every data source implements.

A source is anything that can produce a Snapshot of the cook: ETI Cloud today,
a fake source in tests, and later the DIY pit-controller Pi. The rest of the
app only depends on this interface, never on a particular source.

`Source` is a typing.Protocol, Python's equivalent of a C# interface: any class
with a matching `fetch` method counts as a Source, without inheriting from it.
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

    async def fetch(self) -> Snapshot:
        """Return a Snapshot of everything known right now.

        Raises SourceError if the data can't be fetched.
        """
        ...

    async def close(self) -> None:
        """Let go of any open connection. Called once, when finished."""
        ...
