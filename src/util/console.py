"""Rich console helpers for CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

console = Console()


def print_banner() -> None:
    condor_ascii = r"""
                     _
                    | |
  ___ ___  _ __   __| | ___  _ __
 / __/ _ \| '_ \ / _` |/ _ \| '__|
| (_| (_) | | | | (_| | (_) | |
 \___\___/|_| |_|\__,_|\___/|_|
"""
    console.print(
        Panel(
            f"[cyan]{condor_ascii}[/cyan]\n"
            "[bold cyan]Condor — AZ-900 Reasoning Tutor[/bold cyan]\n"
            "[dim]AZ-900 Certification Prep  •  Powered by Microsoft Foundry[/dim]",
            border_style="bright_blue",
        )
    )


def print_step(step: str, description: str) -> None:
    console.print(f"\n[bold green]▶ {step}[/bold green]  {description}")


def print_question(q_num: int, stem: str, choices: list[str]) -> None:
    console.print(f"\n[bold yellow]Q{q_num}.[/bold yellow] {stem}")
    for i, c in enumerate(choices, 1):
        console.print(f"   {i}) {c}")


def print_diagnosis_summary(diagnosis: dict) -> None:
    table = Table(title="Diagnosis Summary", show_lines=True)
    table.add_column("Q", style="bold")
    table.add_column("Correct?", justify="center")
    table.add_column("Misconception", style="magenta")
    table.add_column("Why")
    for r in diagnosis.get("results", []):
        mark = "✅" if r["correct"] else "❌"
        mid = r.get("misconception_id") or "—"
        table.add_row(r["id"], mark, mid, r["why"])
    console.print(table)
    top = diagnosis.get("top_misconceptions", [])
    if top:
        console.print(
            f"[bold red]Top misconceptions:[/bold red] {', '.join(top)}"
        )


def print_grounded(explanations: list[dict]) -> None:
    for exp in explanations:
        console.print(
            Panel(
                f"[bold]Q{exp['question_id']}[/bold]\n{exp['explanation']}\n\n"
                + "\n".join(
                    f"  📎 [{c['title']}]({c['url']}): {c['snippet']}"
                    for c in exp.get("citations", [])
                ),
                title="Grounded Explanation",
                border_style="green",
            )
        )


def print_coaching(coaching: dict) -> None:
    console.print("\n[bold cyan]📚 Coaching Notes[/bold cyan]")
    for pt in coaching.get("lesson_points", []):
        console.print(f"  • {pt}")
    for drill in coaching.get("micro_drills", []):
        console.print(
            f"\n  [bold magenta]Drill ({drill['misconception_id']}):[/bold magenta]"
        )
        for q in drill.get("questions", []):
            console.print(f"    → {q}")
