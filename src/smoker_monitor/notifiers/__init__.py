"""Ways of telling you about an alarm.

All implement the Notifier protocol in base.py, which is told the alarm state
after every poll and whenever silence is pressed:
- console: prints and rings the terminal bell (Replit, development).
- sound:   plays an alarm on the Pi's speaker (coming with the hardware).
- pushover / ntfy: a push to your phone (optional, later).
"""
