"""Build the sources (brands) the config asks for.

This is the one place that knows which brands exist. Adding a brand means a
Source class in sources/, its settings in config.py, and a line here.
"""

from __future__ import annotations

from smoker_monitor.config import Config
from smoker_monitor.sources.base import Source
from smoker_monitor.sources.eti_cloud import EtiCloudSource
from smoker_monitor.sources.traeger import TraegerSource


def build_sources(config: Config) -> list[Source]:
    """A Source for every brand set up in the config, in a fixed order."""
    sources: list[Source] = []
    if config.eti_cloud is not None:
        sources.append(EtiCloudSource(config.eti_cloud))
    if config.traeger_mqtt is not None:
        sources.append(TraegerSource(config.traeger_mqtt))
    return sources
