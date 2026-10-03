"""The `Source` interface every data source implements.

Think of it as a C# interface: a `typing.Protocol` with something like
`async def fetch(self) -> Snapshot`. Implementations:
- `eti_cloud.EtiCloudSource` — real data via ETI Cloud (milestone 1).
- A fake/replay source for tests and for running on a PC without a cook.
- Later: a source for the DIY pit-controller Pi.
"""

# TODO(milestone 1): define the Source protocol once the Snapshot model exists.
