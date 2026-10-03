"""Load and validate settings from config.toml.

Responsibilities:
- Read the TOML file (stdlib `tomllib`) and turn it into typed, frozen dataclasses.
- Fail fast with a clear message for missing or invalid values.
- Never log the password or tokens.

See config.example.toml for the expected shape.
"""

# TODO(milestone 1): dataclasses for EtiCloudSettings, PollingSettings, etc.,
# and a `load_config(path)` function.
