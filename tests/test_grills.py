"""Tests for grills (e.g. a Traeger): alarms, names, report, status and log line."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from smoker_monitor.domain.alarm_state import AlarmState
from smoker_monitor.domain.alarms import Alarm, AlarmKind, AlarmRules, evaluate
from smoker_monitor.domain.models import Grill, GrillMode, Probe, Reading, Snapshot, SourceHealth
from smoker_monitor.domain.names import device_names, grill_names
from smoker_monitor.report import describe_alarm, format_snapshot
from smoker_monitor.service.combine import combine, keep_vanished_probes
from smoker_monitor.service.monitor import summary
from smoker_monitor.service.status import build_status

NOW = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
TRAEGER = SourceHealth(key="traeger", name="Traeger", expects_gateway=False)
RULES = AlarmRules()


def grill(
    temp: float | None = 110.0,
    set_to: float | None = 110.0,
    mode: GrillMode = GrillMode.COOKING,
    pellets: int | None = 80,
    last_seen: datetime = NOW,
) -> Grill:
    return Grill(
        serial="GRILL-1",
        label="Pro 575",
        last_seen=last_seen,
        connected=True,
        mode=mode,
        temp=Reading(temp, last_seen),
        set_temp_celsius=set_to,
        pellet_pct=pellets,
        source="traeger",
    )


def snapshot(*grills: Grill, sources: tuple[SourceHealth, ...] = (TRAEGER,)) -> Snapshot:
    return Snapshot(NOW, (), (), sources, grills)


def alarms(*grills: Grill, rules: AlarmRules = RULES) -> tuple[Alarm, ...]:
    return evaluate(snapshot(*grills), NOW, rules)


def kinds(found: tuple[Alarm, ...]) -> list[AlarmKind]:
    return [a.kind for a in found]


# --------------------------------------------------------------- the model ---


@pytest.mark.parametrize(
    ("mode", "on"),
    [
        (GrillMode.OFFLINE, False),
        (GrillMode.SLEEPING, False),
        (GrillMode.IDLE, False),
        (GrillMode.IGNITING, True),
        (GrillMode.PREHEATING, True),
        (GrillMode.COOKING, True),
        (GrillMode.COOLING_DOWN, True),
        (GrillMode.SHUT_DOWN, False),
        (GrillMode.UNKNOWN, False),
    ],
)
def test_which_modes_count_as_on(mode: GrillMode, on: bool) -> None:
    assert mode.is_on is on


def test_grill_id_includes_its_source() -> None:
    assert grill().id == "traeger:GRILL-1"


# ------------------------------------------------------------------ alarms ---


def test_cooking_on_target_is_fine() -> None:
    assert alarms(grill(temp=118.0, set_to=110.0)) == ()


def test_too_cold_while_cooking_alarms() -> None:
    assert alarms(grill(temp=90.0, set_to=110.0)) == (
        Alarm(AlarmKind.GRILL_OFF_TARGET, "traeger:GRILL-1", 90.0, 110.0),
    )


def test_too_hot_while_cooking_alarms() -> None:
    assert kinds(alarms(grill(temp=130.0, set_to=110.0))) == [AlarmKind.GRILL_OFF_TARGET]


def test_exactly_on_the_allowed_difference_is_fine() -> None:
    assert alarms(grill(temp=124.0, set_to=110.0)) == ()


@pytest.mark.parametrize("mode", [GrillMode.IGNITING, GrillMode.PREHEATING])
def test_heating_up_is_never_off_target(mode: GrillMode) -> None:
    assert alarms(grill(temp=30.0, set_to=110.0, mode=mode)) == ()


def test_off_target_uses_configured_degrees() -> None:
    relaxed = AlarmRules(grill_off_target_c=25)
    assert alarms(grill(temp=90.0, set_to=110.0), rules=relaxed) == ()


def test_off_target_can_be_switched_off() -> None:
    assert alarms(grill(temp=60.0), rules=AlarmRules(grill_off_target=False)) == ()


def test_pellets_low_alarms() -> None:
    assert alarms(grill(pellets=15)) == (Alarm(AlarmKind.PELLETS_LOW, "traeger:GRILL-1", 15, 20),)


def test_pellets_low_can_be_switched_off() -> None:
    assert alarms(grill(pellets=5), rules=AlarmRules(pellets_low_pct=0)) == ()


def test_no_pellet_sensor_means_no_pellet_alarm() -> None:
    assert alarms(grill(pellets=None)) == ()


def test_cooling_down_ignores_temperature_and_pellets() -> None:
    assert alarms(grill(temp=60.0, pellets=5, mode=GrillMode.COOLING_DOWN)) == ()


@pytest.mark.parametrize(
    "mode", [GrillMode.OFFLINE, GrillMode.IDLE, GrillMode.SHUT_DOWN, GrillMode.SLEEPING]
)
def test_switched_off_grill_stays_quiet(mode: GrillMode) -> None:
    """Off in the shed for a week: no "gone quiet", no pellets, nothing."""
    week_ago = NOW - timedelta(days=7)
    assert alarms(grill(temp=10.0, pellets=0, mode=mode, last_seen=week_ago)) == ()


def test_grill_gone_quiet_while_cooking_alarms() -> None:
    quiet = grill(last_seen=NOW - timedelta(minutes=6))
    assert alarms(quiet) == (Alarm(AlarmKind.GRILL_TIMEOUT, "traeger:GRILL-1", 6.0, 5.0),)


def test_grill_timeout_can_be_switched_off() -> None:
    quiet = grill(last_seen=NOW - timedelta(minutes=6))
    assert alarms(quiet, rules=AlarmRules(grill_timeout=False)) == ()


def test_traeger_unreachable_replaces_grill_timeout() -> None:
    failing_since = NOW - timedelta(minutes=6)
    down = replace(TRAEGER, error="down", failing_since=failing_since)
    found = evaluate(snapshot(grill(last_seen=failing_since), sources=(down,)), NOW, RULES)
    assert kinds(found) == [AlarmKind.SOURCE_UNREACHABLE]


def test_grill_source_never_says_no_gateway_found() -> None:
    assert evaluate(snapshot(), NOW, RULES) == ()


def test_grill_alarm_kinds_have_config_settings() -> None:
    assert RULES.grill_timeout and RULES.grill_off_target
    assert (RULES.grill_off_target_c, RULES.pellets_low_pct) == (14, 20)


# ----------------------------------------------------------------- wording ---


@pytest.mark.parametrize(
    ("alarm", "words"),
    [
        (
            Alarm(AlarmKind.GRILL_OFF_TARGET, "traeger:GRILL-1", 90.0, 110.0),
            "Pro 575 too cold: 90.0°C (set to 110.0°C)",
        ),
        (
            Alarm(AlarmKind.GRILL_OFF_TARGET, "traeger:GRILL-1", 130.0, 110.0),
            "Pro 575 too hot: 130.0°C (set to 110.0°C)",
        ),
        (
            Alarm(AlarmKind.PELLETS_LOW, "traeger:GRILL-1", 15, 20),
            "Pro 575 pellets low: 15% (below 20%)",
        ),
        (
            Alarm(AlarmKind.GRILL_TIMEOUT, "traeger:GRILL-1", 6.0, 5.0),
            "Pro 575 silent for 6 min (limit 5 min)",
        ),
    ],
)
def test_grill_alarm_wording(alarm: Alarm, words: str) -> None:
    assert describe_alarm(alarm, snapshot(grill())) == words


# ------------------------------------------------------------------- names ---


def test_your_name_beats_the_model() -> None:
    named = replace(grill(), name="The Beast")
    assert device_names(snapshot(named)) == {"traeger:GRILL-1": "The Beast"}


def test_twin_grills_are_numbered() -> None:
    twin = replace(grill(), serial="GRILL-2")
    assert grill_names([grill(), twin]) == {
        "traeger:GRILL-1": "Pro 575 1",
        "traeger:GRILL-2": "Pro 575 2",
    }


# ------------------------------------------------------------------ report ---


def test_report_shows_the_grill() -> None:
    report = format_snapshot(snapshot(grill(temp=109.5)), NOW)
    assert "No gateway found" not in report
    assert "Pro 575\n  Cooking, last seen 0 s ago\n" in report
    assert "  Grill:   109.5°C, set to 110.0°C" in report
    assert "  Pellets: 80%" in report


# ------------------------------------------------------------------ status ---


def test_cooking_grill_as_a_pit() -> None:
    [pit] = build_status(snapshot(grill()), AlarmState(), NOW, RULES)["pits"]
    assert pit == {
        "id": "traeger:GRILL-1",
        "source": "Traeger",
        "kind": "grill",
        "mode": "cooking",
        "is_on": True,
        "name": "Pro 575",
        "fresh": True,
        "last_seen": "2026-10-30T18:00:00+00:00",
        "temp_c": 110.0,
        "set_c": 110.0,
        "high_c": 124.0,
        "low_c": 96.0,
        "high_origin": None,
        "low_origin": None,
        "fan": None,
        "fan_output_pct": None,
        "pellet_pct": 80,
        "details": [
            {"label": "Status", "value": "cooking"},
            {"label": "Pellets", "value": "80%"},
        ],
    }


def test_grill_extras_in_status_and_report() -> None:
    extra = replace(grill(), ambient_celsius=12.4, wifi_dbm=-78, keep_warm=True)
    [pit] = build_status(snapshot(extra), AlarmState(), NOW, RULES)["pits"]
    assert pit["details"] == [
        {"label": "Status", "value": "cooking"},
        {"label": "Pellets", "value": "80%"},
        {"label": "Outside", "value": "12°C"},
        {"label": "Wi-Fi", "value": "-78 dBm"},
        {"label": "Keep Warm", "value": "on"},
    ]
    report = format_snapshot(snapshot(extra), NOW)
    assert "  Also:    outside 12.4°C, Wi-Fi -78 dBm, Keep Warm" in report


def test_switched_off_grill_has_no_target_or_limits() -> None:
    off = grill(mode=GrillMode.SHUT_DOWN, set_to=110.0)
    [pit] = build_status(snapshot(off), AlarmState(), NOW, RULES)["pits"]
    assert (pit["set_c"], pit["high_c"], pit["low_c"]) == (None, None, None)


def test_preheating_grill_shows_its_target_but_no_limits() -> None:
    warming = grill(temp=60.0, mode=GrillMode.PREHEATING)
    [pit] = build_status(snapshot(warming), AlarmState(), NOW, RULES)["pits"]
    assert (pit["set_c"], pit["high_c"], pit["low_c"]) == (110.0, None, None)


# --------------------------------------------------------- combine and log ---


def test_combine_keeps_grills() -> None:
    combined = combine(NOW, [(TRAEGER, snapshot(replace(grill(), source="")))])
    assert [g.id for g in combined.grills] == ["traeger:GRILL-1"]


def test_summary_line_shows_the_grill() -> None:
    assert summary(snapshot(grill()), AlarmState(), NOW, RULES) == (
        "Pro 575 110.0°C | 0 ringing, 0 silenced"
    )


def test_summary_line_says_when_the_grill_is_off() -> None:
    off = grill(mode=GrillMode.SHUT_DOWN, last_seen=NOW - timedelta(days=2))
    assert summary(snapshot(off), AlarmState(), NOW, RULES).startswith("Pro 575 shut down |")


def test_keep_vanished_probes_only_keeps_live_ones() -> None:
    pulled = Probe("GRILL-1-p0", "Traeger probe", None, NOW, (), source="traeger")
    other = replace(pulled, serial="GRILL-1-p1")
    kept = keep_vanished_probes(
        snapshot(grill()), {pulled.id: pulled, other.id: other}, {pulled.id}
    )
    assert [p.id for p in kept.probes] == ["traeger:GRILL-1-p0"]


def test_keep_vanished_probes_leaves_present_ones_alone() -> None:
    present = Probe("GRILL-1-p0", "Traeger probe", None, NOW, (), source="traeger")
    with_probe = replace(snapshot(grill()), probes=(present,))
    assert keep_vanished_probes(with_probe, {present.id: present}, {present.id}) is with_probe
