"""
CG-IDF v2 — Entry Point

Usage:
    python -m cg_idf_v2.main

Demonstrates a full pipeline run with a sample set of 5 evidence items.
Output is written to stdout as formatted JSON and optionally to a file.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from cg_idf_v2.graph import compiled_graph
from cg_idf_v2.schema import AuditState, Evidence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sample evidence (Step 1 — human-supplied)
# In production, this comes from your evidence collection UI / API.
# ---------------------------------------------------------------------------

SAMPLE_EVIDENCE = [
    Evidence(
        evidence_id="ev_001",
        surface="onboarding",
        platform="ios",
        navigation_path="App Launch > Onboarding Carousel > Permissions",
        uri="app://onboarding/step/3",
        raw_description=(
            "Screen shows 'Allow Notifications' prompt immediately after sign-up. "
            "The 'Allow' button is large and green; 'Not Now' is small and grey."
        ),
    ),
    Evidence(
        evidence_id="ev_002",
        surface="home_feed",
        platform="ios",
        navigation_path="Home > Feed",
        uri="app://home/feed",
        raw_description=(
            "Infinite scroll feed with video auto-play. "
            "Red badge showing '3' notifications visible on tab bar. "
            "Streak counter '🔥 7 days' shown at top of screen."
        ),
    ),
    Evidence(
        evidence_id="ev_003",
        surface="checkout",
        platform="ios",
        navigation_path="Home > Feed > Post > Checkout",
        uri="app://checkout/summary",
        raw_description=(
            "Checkout screen shows 'Only 2 left!' label next to product. "
            "Timer counting down '00:14:32'. "
            "Premium subscription upsell banner below the cart."
        ),
    ),
    Evidence(
        evidence_id="ev_004",
        surface="settings",
        platform="ios",
        navigation_path="Profile > Settings > Account",
        uri="app://settings/account",
        raw_description=(
            "Account deletion option buried under: Settings > Account > "
            "Manage Account > Deactivate > Delete. "
            "No 'Delete Account' option visible at top level."
        ),
    ),
    Evidence(
        evidence_id="ev_005",
        surface="social_share",
        platform="ios",
        navigation_path="Home > Feed > Post > Share Sheet",
        uri="app://share/post/12345",
        raw_description=(
            "Share sheet prominently shows follower count (12.4K) and like count (3.2K). "
            "Invite Friends CTA with '+ 500 coins' reward prominently displayed."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_audit(
    evidence: list[Evidence] | None = None,
    output_path: Path | None = None,
) -> dict:
    """
    Run the full CG-IDF v2 pipeline.

    Args:
        evidence:    List of Evidence items. Uses SAMPLE_EVIDENCE if None.
        output_path: Optional file path to write the JSON report.

    Returns:
        The final state dict containing the FinalReport.
    """
    evidence = evidence or SAMPLE_EVIDENCE

    # Initialize pipeline state
    initial_state = AuditState(evidence=evidence)
    logger.info(
        "Starting CG-IDF v2 audit. run_id=%s, evidence_count=%d",
        initial_state.run_id,
        len(evidence),
    )

    # Run the LangGraph pipeline
    final_state_dict: dict = compiled_graph.invoke(initial_state.to_dict())

    # Re-hydrate for structured access
    final_state = AuditState.model_validate(final_state_dict)

    if final_state.errors:
        logger.warning("Pipeline completed with errors: %s", final_state.errors)

    report_dict = (
        final_state.final_report.model_dump(mode="json")
        if final_state.final_report
        else {"error": "FinalReport not generated", "errors": final_state.errors}
    )

    # Pretty-print to stdout
    print(json.dumps(report_dict, indent=2))

    # Optional file output
    if output_path:
        output_path.write_text(json.dumps(report_dict, indent=2))
        logger.info("Report written to %s", output_path)

    return report_dict


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run a CG-IDF v2 Incentive Audit."
    )
    parser.add_argument(
        "--evidence-file",
        type=Path,
        default=None,
        help="Path to a JSON file containing an evidence array. Uses built-in sample if omitted.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the JSON report.",
    )
    args = parser.parse_args()

    custom_evidence = None
    if args.evidence_file:
        raw = json.loads(args.evidence_file.read_text())
        custom_evidence = [Evidence.model_validate(e) for e in raw]

    run_audit(evidence=custom_evidence, output_path=args.output)
