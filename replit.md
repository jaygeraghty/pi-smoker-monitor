# pi-smoker-monitor on Replit

## Current scope

This is an early-development Python 3.11+ command-line project, not a web app.
The CLI currently prints help and version information. `snapshot` prints
`snapshot!` only; it does not connect to ETI Cloud. Configuration loading,
live readings, alarms, the monitoring service and the touchscreen UI are
roadmap work, not implemented features.

## Running

Press **Run** to launch the existing CLI and see its help in the Console.
The command exits normally after printing help; no server or continuous
monitor is expected at this stage.

```sh
uv run --no-project --with-editable . smoker
uv run --no-project --with-editable . smoker --version
uv run --no-project --with-editable . smoker snapshot
```

The workflows use uv's managed cache rather than a workspace virtual
environment. Keep the existing Python package layout and dependencies.

## Verification

The optional **Run all tests** workflow runs:

```sh
uv run --no-project --with pytest --with pytest-asyncio --with-editable . pytest
```

Additional development checks:

```sh
uv run --no-project --with ruff ruff check src tests
uv run --no-project --with ruff ruff format --check src tests
uv run --no-project --with mypy --with pytest --with-editable . mypy
```

Scope Ruff to `src tests` on Replit so it does not lint platform-provided
helper scripts under `.local/skills/`.

## Credentials and hardware

No credentials are required by the current CLI. Do not invent credentials
or copy example passwords into an active configuration. Once live ETI Cloud
support is implemented, collect credentials through Replit Secrets and never
commit or log them. `config.toml` and raw `captures/` are already git-ignored.
Raspberry Pi GPIO, touchscreen and local speaker behavior must be verified on
the target Pi, not in this hosted environment.