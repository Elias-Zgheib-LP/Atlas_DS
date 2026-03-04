"""
CG-IDF v2 — Step 5: Merge + Scoring Layer

Responsibilities (deterministic):
  1. Apply Provider B verification results to layer questions.
  2. Adjust confidence when status == "downgrade".
  3. Flag contradictions.
  4. Compute per-layer rollup scores (mean of question confidences).
  5. Compute overall audit score (mean of layer rollups).
  6. Assemble FinalReport with completed_at timestamp.
"""

from __future__ import annotations

import logging
from datetime import datetime
from statistics import mean
from typing import Dict, List

from cg_idf_v2.schema import (
    AuditFlag,
    AuditState,
    FinalReport,
    FlagCode,
    Layer,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Merge policy constants
# ---------------------------------------------------------------------------

# How much to reduce confidence when Provider B downgrades (multiplicative)
DOWNGRADE_MULTIPLIER = 0.6

# Minimum confidence floor after downgrade
CONFIDENCE_FLOOR = 0.1


# ---------------------------------------------------------------------------
# Merge policy application
# ---------------------------------------------------------------------------

def _apply_verifications(state: AuditState) -> List[AuditFlag]:
    """
    Walk through verifications and mutate the corresponding Question objects.
    Returns a list of contradiction flags.
    """
    contradiction_flags: List[AuditFlag] = []

    for result in state.verifications:
        layer = state.layers.get(result.layer_id)
        if not layer:
            logger.warning(
                "[Merge] Verification references unknown layer_id=%s", result.layer_id
            )
            continue

        question = next((q for q in layer.questions if q.q_id == result.q_id), None)
        if not question:
            logger.warning(
                "[Merge] Verification references unknown q_id=%s in layer=%s",
                result.q_id, result.layer_id,
            )
            continue

        if result.status == VerificationStatus.confirm:
            # No change needed; confidence stays as-is
            logger.debug("[Merge] %s confirmed.", result.q_id)

        elif result.status == VerificationStatus.downgrade:
            old_conf = question.confidence
            if result.revised_confidence is not None:
                question.confidence = max(result.revised_confidence, CONFIDENCE_FLOOR)
            else:
                question.confidence = max(
                    question.confidence * DOWNGRADE_MULTIPLIER, CONFIDENCE_FLOOR
                )
            logger.info(
                "[Merge] %s downgraded: %.2f → %.2f",
                result.q_id, old_conf, question.confidence,
            )
            question.notes = (
                (question.notes or "") + f" [B-downgraded: {result.rationale}]"
            ).strip()

        elif result.status == VerificationStatus.contradiction:
            logger.warning("[Merge] Contradiction on %s: %s", result.q_id, result.rationale)
            question.confidence = CONFIDENCE_FLOOR
            question.notes = (
                (question.notes or "") + f" [B-contradiction: {result.rationale}]"
            ).strip()
            contradiction_flags.append(
                AuditFlag(
                    flag_code=FlagCode.CONTRADICTION_DETECTED,
                    q_id=result.q_id,
                    layer_id=result.layer_id,
                    description=f"Provider B contradiction: {result.rationale}",
                )
            )

        elif result.status in (
            VerificationStatus.insufficient_evidence,
            VerificationStatus.missing_evidence,
        ):
            # Reduce confidence but do not null the answer
            question.confidence = max(
                question.confidence * DOWNGRADE_MULTIPLIER, CONFIDENCE_FLOOR
            )
            question.notes = (
                (question.notes or "")
                + f" [B-{result.status.value}: {result.rationale}]"
            ).strip()

    return contradiction_flags


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_layer_rollup(layer: Layer) -> float:
    """Mean confidence across all answered questions in a layer."""
    answered = [q.confidence for q in layer.questions if q.llm_answer]
    if not answered:
        return 0.0
    return round(mean(answered), 4)


def _compute_overall_score(layers: Dict[str, Layer]) -> float:
    """Mean of all layer rollup scores."""
    rollups = [l.rollup_score for l in layers.values() if l.rollup_score is not None]
    if not rollups:
        return 0.0
    return round(mean(rollups), 4)


# ---------------------------------------------------------------------------
# Main node entry point
# ---------------------------------------------------------------------------

def run_merge_scoring(state: AuditState) -> AuditState:
    """
    LangGraph node — merge verifications + compute scores + build FinalReport.
    """
    logger.info("[Merge] Starting merge and scoring.")

    # Step 1: Apply Provider B results
    contradiction_flags = _apply_verifications(state)

    # Step 2: Compute per-layer rollup scores
    for layer in state.layers.values():
        layer.rollup_score = _compute_layer_rollup(layer)
        logger.info(
            "[Merge] Layer '%s' rollup_score=%.4f", layer.layer_id, layer.rollup_score
        )

    # Step 3: Compute overall score
    overall_score = _compute_overall_score(state.layers)
    logger.info("[Merge] Overall score=%.4f", overall_score)

    # Step 4: Collect all flags
    all_flags: List[AuditFlag] = list(state.pipeline_flags) + contradiction_flags

    # Step 5: Build FinalReport
    state.final_report = FinalReport(
        run_id=state.run_id,
        completed_at=datetime.utcnow().isoformat() + "Z",
        layers=state.layers,
        flags=all_flags,
        contradictions=[f.description for f in contradiction_flags],
        overall_score=overall_score,
        summary=_build_summary(state, overall_score, all_flags),
    )

    logger.info("[Merge] FinalReport assembled. audit_id=%s", state.final_report.audit_id)
    return state


def _build_summary(state: AuditState, overall_score: float, flags: List[AuditFlag]) -> str:
    """
    Short deterministic text summary for the final report.
    Not a free-form LLM output — computed from scores only.
    """
    flag_counts: Dict[str, int] = {}
    for f in flags:
        flag_counts[f.flag_code.value] = flag_counts.get(f.flag_code.value, 0) + 1

    top_layers = sorted(
        state.layers.values(),
        key=lambda l: l.rollup_score or 0.0,
        reverse=True,
    )

    top_label = top_layers[0].label if top_layers else "N/A"

    return (
        f"Audit complete. Overall score: {overall_score:.2f}. "
        f"Highest incentive layer: '{top_label}'. "
        f"Total flags: {len(flags)} "
        f"({', '.join(f'{v}x {k}' for k, v in flag_counts.items()) or 'none'})."
    )
