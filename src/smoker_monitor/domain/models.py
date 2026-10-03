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

# TODO(milestone 1): implement the models above.
