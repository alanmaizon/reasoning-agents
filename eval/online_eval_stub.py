"""Online evaluation stub — to be wired to Foundry evaluation tooling.

This is a placeholder for integration with Azure AI Foundry evaluation
pipelines. When configured, it can:
  - Run the full workflow against a live model endpoint
  - Capture agent outputs and compare against golden references
  - Report quality metrics (accuracy, citation coverage, etc.)

Usage:
    python -m eval.online_eval_stub

Requires AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_MODEL_DEPLOYMENT_NAME.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run_online_eval() -> None:
    """Stub for online evaluation pipeline."""
    print("=" * 60)
    print("Condor Online Evaluation Stub")
    print("=" * 60)
    print()
    print("This stub is a placeholder for Foundry evaluation integration.")
    print("To implement:")
    print("  1. Configure AZURE_AI_PROJECT_ENDPOINT in .env")
    print("  2. Define golden test cases in eval/golden_cases.jsonl")
    print("  3. Wire foundry_client.get_foundry_runner() to capture outputs")
    print("  4. Compare outputs against golden references")
    print("  5. Report metrics: accuracy, citation coverage, taxonomy match")
    print()

    # Example golden case structure
    golden_case = {
        "student_answers": {"1": 0, "2": 1, "3": 2},
        "expected_misconceptions": ["SRM", "REGION"],
        "expected_min_citations": 2,
    }
    print("Example golden case:")
    print(json.dumps(golden_case, indent=2))
    print()
    print("Status: NOT IMPLEMENTED — wire to Foundry evaluation SDK.")


if __name__ == "__main__":
    run_online_eval()
