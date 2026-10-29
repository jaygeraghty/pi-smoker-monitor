"""Quick offline check: build a status file from made-up readings and print it.

Uses only plain Python and our own code (no internet, no extra libraries), so
it runs in phone editors like Deskless. It isn't a test: read the output and
check it looks right.

Run from the project folder:  python scripts/check_status.py
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Let Python find our code in src/ without installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> None:
    from smoker_monitor.domain.alarm_state import AlarmState
    from smoker_monitor.domain.alarms import AlarmRules
    from smoker_monitor.domain.models import (
        AlarmLimit,
        AlarmSettings,
        Fan,
        FanActivity,
        Gateway,
        Probe,
        Reading,
        RingColour,
        Snapshot,
    )
    from smoker_monitor.service.status import build_status

    now = datetime.now(UTC)
    gateway = Gateway(
        serial="GATEWAY-1",
        label="RFX GATEWAY",
        battery_pct=75,
        wifi_dbm=-74,
        last_seen=now,
        pit=Reading(110.4, now),
        fan=Fan(set_temp_celsius=110.0, state=2, connected=True, activity=FanActivity.PULSING),
        pit_alarms=AlarmSettings(high=AlarmLimit(True, 123.9), low=AlarmLimit(True, 96.1)),
    )
    cooking = Probe(
        serial="PROBE-1",
        label="RFX MEAT",
        battery_pct=100,
        last_seen=now - timedelta(minutes=1),
        sensors=(Reading(68.2, now), Reading(70.1, now)),
        alarms=AlarmSettings(high=AlarmLimit(True, 95.0), low=None),
        name="white probe",
        rings=(RingColour.WHITE,),
    )
    in_the_drawer = Probe(
        serial="PROBE-2",
        label="RFX MEAT",
        battery_pct=100,
        last_seen=now - timedelta(hours=9),
        sensors=(Reading(22.5, now),),
        name="pink probe",
        rings=(RingColour.PINK,),
    )
    snapshot = Snapshot(taken_at=now, gateways=(gateway,), probes=(cooking, in_the_drawer))

    status = build_status(snapshot, AlarmState(), now, AlarmRules(), None)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
