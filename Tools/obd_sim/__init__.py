"""Virtual ELM327 OBD-II dongle for DRIFTS bench testing.

Replaces the `SIM` serial command that was dropped in the BLE rewrite. The
firmware is not modified and does not know it is talking to a simulation --
the same `obd_ble.cpp` discovery, ELM327 dialogue and PID poller run against
this as against a real Veepeak or Vgate.

Layout mirrors `Raspberry/drifts/`: a package with `__main__.py`, a config-ish
module of constants, and transports kept separate from the protocol so the
protocol can be tested on a laptop with no hardware.
"""

__version__ = "0.1.0"

from .vehicle import SCENARIOS, Vehicle, VehicleState

__all__ = ["SCENARIOS", "Vehicle", "VehicleState", "__version__"]
