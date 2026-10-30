"""Tests for building sources from the config."""

from __future__ import annotations

from smoker_monitor.config import Config, EtiCloudSettings, PollingSettings, TraegerSettings
from smoker_monitor.sources.eti_cloud import EtiCloudSource
from smoker_monitor.sources.factory import build_sources
from smoker_monitor.sources.traeger import TraegerSource

ETI = EtiCloudSettings(
    email="me@example.com",
    password="s3cret-pw",
    api_key="key",
    app_id="app",
    referer="https://cloud.etiltd.com/",
    poll_seconds=45,
)


def test_eti_section_builds_an_eti_source() -> None:
    [source] = build_sources(Config(eti_cloud=ETI, polling=PollingSettings(30)))
    assert isinstance(source, EtiCloudSource)
    assert (source.key, source.name, source.poll_seconds) == ("eti", "ETI Cloud", 45)
    assert source.expects_gateway


def test_no_brands_builds_nothing() -> None:
    assert build_sources(Config(eti_cloud=None, polling=PollingSettings(30))) == []


def test_traeger_section_builds_a_traeger_source() -> None:
    traeger = TraegerSettings("grill@example.com", "pw", "client", poll_seconds=20)
    config = Config(eti_cloud=ETI, polling=PollingSettings(30), traeger_mqtt=traeger)
    eti, grill = build_sources(config)
    assert isinstance(eti, EtiCloudSource)
    assert isinstance(grill, TraegerSource)
    assert (grill.key, grill.name, grill.poll_seconds) == ("traeger", "Traeger", 20)
    assert not grill.expects_gateway
