"""MDT CLI entrypoint — ``python -m src.main``."""

from __future__ import annotations

import sys

from .util.console import console
from .foundry_client import get_foundry_runner
from .orchestration.workflow import run_workflow


def main() -> None:
    # Determine mode
    offline = "--offline" in sys.argv

    foundry_run = None
    if not offline:
        foundry_run = get_foundry_runner()

    if foundry_run is None and not offline:
        console.print("[yellow]No Foundry credentials — switching to offline mode.[/yellow]")
        offline = True

    try:
        run_workflow(offline=offline, foundry_run=foundry_run)
    except KeyboardInterrupt:
        console.print("\n[dim]Session cancelled.[/dim]")
        sys.exit(0)


if __name__ == "__main__":
    main()
