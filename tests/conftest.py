"""
Shared pytest configuration.

Force plain, uncolored Rich output for the whole test session. Several
commands print via Rich `Console` objects; Rich decides whether to style
output based on the environment at the time each `Console` is constructed
(module import time, for the module-level `console` objects), which can
vary with test collection order and with a development shell's own
terminal settings. `NO_COLOR` suppresses color, but a `FORCE_COLOR` set in
the outer shell (common in interactive dev environments, to keep command
output readable) overrides terminal auto-detection and still lets bold/
other text attributes through even under `NO_COLOR`. Clearing both makes
CLI-output assertions deterministic regardless of collection order or the
shell this suite happens to run in.
"""

import os

os.environ["NO_COLOR"] = "1"
os.environ.pop("FORCE_COLOR", None)
