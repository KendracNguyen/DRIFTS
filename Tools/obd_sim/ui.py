"""Terminal UI. Falls back to plain printing when `rich` is not installed.

Keyboard, not menus: the point of this tool is flipping between normal and
anomaly while watching the ESP32 serial log in the next window, so a scenario
change has to be one keystroke.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .vehicle import SCENARIOS, Scenario, VehicleState

try:  # pragma: no cover - presentation only
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table

    HAVE_RICH = True
except ImportError:  # pragma: no cover
    HAVE_RICH = False

# Keys 1..7 select scenarios, in the order they are declared.
KEY_ORDER = list(SCENARIOS.keys())
KEYMAP = {str(i + 1): key for i, key in enumerate(KEY_ORDER)}


@dataclass
class Status:
    scenario: Scenario
    state: VehicleState
    transport: str
    endpoint: str
    elapsed_s: float
    requests: int
    no_data: int
    connected: bool


def _rows(status: Status) -> list[tuple[str, str]]:
    s = status.state
    return [
        ("RPM", f"{s.rpm:7.0f}"),
        ("Speed", f"{s.speed_kph:7.1f} km/h"),
        ("Throttle", f"{s.throttle_pct:7.1f} %"),
        ("Engine load", f"{s.engine_load_pct:7.1f} %"),
        ("", ""),
        ("Transport", f"{status.transport} @ {status.endpoint}"),
        ("Client", "connected" if status.connected else "waiting"),
        ("Requests", f"{status.requests}  ({status.no_data} NO DATA)"),
        ("Elapsed", f"{status.elapsed_s:.0f} s"),
    ]


class PlainUI:
    """One line per refresh. Readable in a log, usable over ssh."""

    def __init__(self) -> None:
        self._last = ""

    def banner(self, status: Status) -> None:
        print(f"obd_sim: {status.transport} on {status.endpoint}")
        for key, name in KEYMAP.items():
            print(f"  [{key}] {SCENARIOS[name].label}")
        print("  [q] quit\n")

    def refresh(self, status: Status) -> None:
        s = status.state
        line = (
            f"{status.scenario.key:<10} "
            f"rpm {s.rpm:6.0f}  spd {s.speed_kph:6.1f}  thr {s.throttle_pct:5.1f}  "
            f"req {status.requests:<6} nodata {status.no_data}"
        )
        if line != self._last:
            print(line)
            self._last = line

    def close(self) -> None:
        pass


class RichUI:  # pragma: no cover - presentation only
    """Live-updating panel."""

    def __init__(self) -> None:
        self._console = Console()
        self._live = Live(console=self._console, refresh_per_second=8, screen=False)
        self._live.start()

    def banner(self, status: Status) -> None:
        self.refresh(status)

    def _render(self, status: Status):
        readings = Table.grid(padding=(0, 2))
        readings.add_column(style="bold", justify="right")
        readings.add_column()
        for label, value in _rows(status):
            readings.add_row(label, value)

        picker = Table.grid(padding=(0, 2))
        picker.add_column(justify="right")
        picker.add_column()
        for key, name in KEYMAP.items():
            sc = SCENARIOS[name]
            marker = "[reverse] > [/reverse]" if sc.key == status.scenario.key else f" {key} "
            style = "bold" if sc.key == status.scenario.key else "dim"
            picker.add_row(marker, f"[{style}]{sc.label}[/{style}]")
        picker.add_row(" q ", "[dim]quit[/dim]")

        return Group(
            Panel(readings, title="Virtual ECU", border_style="cyan"),
            Panel(picker, title="Scenario", border_style="magenta"),
            Panel(
                f"[bold]{status.scenario.label}[/bold]\n{status.scenario.description}\n\n"
                f"[dim]Targets: {status.scenario.targets}[/dim]",
                border_style="yellow",
            ),
        )

    def refresh(self, status: Status) -> None:
        self._live.update(self._render(status))

    def close(self) -> None:
        self._live.stop()


def make_ui(force_plain: bool = False):
    if force_plain or not HAVE_RICH or not sys.stdout.isatty():
        return PlainUI()
    return RichUI()
