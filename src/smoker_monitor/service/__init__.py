"""The long-running monitor loop (milestone 3).

poll Source -> update alarm states -> notify -> publish to UI, every N seconds.
Handles retries with back-off, token refresh, structured logging and clean
shutdown on SIGTERM (so systemd can stop it properly).
"""
