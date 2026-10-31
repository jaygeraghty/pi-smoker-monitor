"""The web page for changing alarm settings from a phone: `smoker web`.

Everything for the page lives in this folder:
- server.py:  the small web server (Python's standard library, no extra install)
- api.py:     what each address does (settings, status, silence, end cook)
- security.py: who may talk to it (your home network only)
- static/:    the page itself (index.html, app.css, app.js, icons)

What it shares with the rest of the program is small: the settings file
format (service/settings.py, which the monitor also reads) and the status file
the monitor writes. See docs/web.md.
"""
