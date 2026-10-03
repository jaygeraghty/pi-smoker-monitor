"""Alarm rules and their state machine (milestone 2).

- Each rule looks at a Snapshot (plus a little history) and says whether its
  condition is currently true: pit drift, meat at target, stale data,
  Billows disconnected, probe battery low.
- Each alarm moves OK -> ALARMING -> ACKNOWLEDGED -> OK, with hysteresis and
  minimum durations so it fires once per problem rather than every poll.
- Pure logic: no clocks read directly (pass `now` in), no I/O. Easy to unit test.
"""
