"""
CG-IDF v2 — Step 3: Python Rules Engine (Deterministic)

Responsibilities (non-LLM, fully deterministic):
  1. Coverage check     — required surfaces present in evidence
  2. Unsupported claims — "supported" answers lacking evidence_refs
  3. Missing answers    — questions with no llm_answer
  4. Low confidence     — answers below CONFIDENCE_THRESHOLD
  5. Build review_queue — items Provider B must verify
  6. Set preliminary pipeline_flags
"""

from __future__ import annotations

import logging
from typing import List

from cg_idf_v2.schema import (
    AnswerType,
    AuditFlag,
    AuditState,
    FlagCode,
    ReviewQueueItem,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds & configuration
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD   = 0.6   # answers below this are flagged for review
REQUIRED_SURFACES      = {"home_feed", "checkout", "onboarding"}  # adjust per audit
MISSING_ANSWER_FLAG    = True  # flag questions with no llm_answer
UNSUPPORTED_CLAIM_FLAG = True  # flag "supported" without refs (should be caught by ProviderA)


# ---------------------------------------------------------------------------
# Individual rule functions — each appends to queue/flags and returns nothing
# ---------------------------------------------------------------------------

def _check_surface_coverage(
    state: AuditState,
    queue: List[ReviewQueueItem],
    flags: List[AuditFlag],
) -> None:
    """Rule 1: Verify that all required surfaces are represented in evidence."""
    found_surfaces = {e.surface.lower() for e in state.evidence}
    missing = REQUIRED_SURFACES - found_surfaces

    if missing:
        logger.warning("[RulesEngine] Missing required surfaces: %s", missing)
        flags.append(
            AuditFlag(
                flag_code=FlagCode.MISSING_SURFACE,
                description=f"Required surfaces not covered by evidence: {sorted(missing)}",
            )
        )


def _check_unsupported_claims(
    state: AuditState,
    queue: List[ReviewQueueItem],
    flags: List[AuditFlag],
) -> None:
    """Rule 2: Detect any 'supported' answers that have no evidence_refs."""
    if not UNSUPPORTED_CLAIM_FLAG:
        return

    for layer_id, layer in state.layers.items():
        for q in layer.questions:
            if q.answer_type == AnswerType.supported and not q.evidence_refs:
                logger.warning(
                    "[RulesEngine] Unsupported claim: layer=%s q_id=%s", layer_id, q.q_id
                )
                flags.append(
                    AuditFlag(
                        flag_code=FlagCode.UNSUPPORTED_CLAIM,
                        q_id=q.q_id,
                        layer_id=layer_id,
                        description=(
                            f"{q.q_id} claims answer_type='supported' "
                            "but has no evidence_refs."
                        ),
                    )
                )
                queue.append(
                    ReviewQueueItem(
                        q_id=q.q_id,
                        layer_id=layer_id,
                        reason="answer_type='supported' but evidence_refs is empty",
                        flag_code=FlagCode.UNSUPPORTED_CLAIM,
                        ai1_answer=q.llm_answer,
                        evidence_refs=q.evidence_refs,
                        screen_facts=_collect_facts(state, q.evidence_refs),
                    )
                )


def _check_missing_answers(
    state: AuditState,
    queue: List[ReviewQueueItem],
    flags: List[AuditFlag],
) -> None:
    """Rule 3: Flag questions where Provider A provided no answer at all."""
    if not MISSING_ANSWER_FLAG:
        return

    for layer_id, layer in state.layers.items():
        for q in layer.questions:
            if not q.llm_answer or q.answer_type == AnswerType.unknown:
                logger.warning(
                    "[RulesEngine] Missing/unknown answer: layer=%s q_id=%s", layer_id, q.q_id
                )
                flags.append(
                    AuditFlag(
                        flag_code=FlagCode.MISSING_ANSWER,
                        q_id=q.q_id,
                        layer_id=layer_id,
                        description=(
                            f"{q.q_id} has no answer or answer_type='unknown'."
                        ),
                    )
                )
                queue.append(
                    ReviewQueueItem(
                        q_id=q.q_id,
                        layer_id=layer_id,
                        reason="llm_answer missing or answer_type=unknown",
                        flag_code=FlagCode.MISSING_ANSWER,
                        ai1_answer=q.llm_answer,
                        evidence_refs=q.evidence_refs,
                        screen_facts=_collect_facts(state, q.evidence_refs),
                    )
                )


def _check_low_confidence(
    state: AuditState,
    queue: List[ReviewQueueItem],
    flags: List[AuditFlag],
) -> None:
    """Rule 4: Flag answers with confidence below threshold."""
    for layer_id, layer in state.layers.items():
        for q in layer.questions:
            if q.llm_answer and q.confidence < CONFIDENCE_THRESHOLD:
                logger.info(
                    "[RulesEngine] Low confidence: layer=%s q_id=%s confidence=%.2f",
                    layer_id, q.q_id, q.confidence,
                )
                flags.append(
                    AuditFlag(
                        flag_code=FlagCode.LOW_CONFIDENCE,
                        q_id=q.q_id,
                        layer_id=layer_id,
                        description=(
                            f"{q.q_id} has confidence={q.confidence:.2f} "
                            f"< threshold={CONFIDENCE_THRESHOLD}."
                        ),
                    )
                )
                # Only add to queue once per q_id (may already be there)
                already_queued = any(item.q_id == q.q_id for item in queue)
                if not already_queued:
                    queue.append(
                        ReviewQueueItem(
                            q_id=q.q_id,
                            layer_id=layer_id,
                            reason=f"confidence={q.confidence:.2f} below threshold={CONFIDENCE_THRESHOLD}",
                            flag_code=FlagCode.LOW_CONFIDENCE,
                            ai1_answer=q.llm_answer,
                            evidence_refs=q.evidence_refs,
                            screen_facts=_collect_facts(state, q.evidence_refs),
                        )
                    )


def _check_incomplete_layer_coverage(
    state: AuditState,
    queue: List[ReviewQueueItem],
    flags: List[AuditFlag],
) -> None:
    """Rule 5: Flag if any required layers are missing entirely."""
    from cg_idf_v2.schema import REQUIRED_LAYERS

    for required in REQUIRED_LAYERS:
        if required not in state.layers:
            logger.warning("[RulesEngine] Required layer missing: %s", required)
            flags.append(
                AuditFlag(
                    flag_code=FlagCode.INCOMPLETE_COVERAGE,
                    layer_id=required,
                    description=f"Required layer '{required}' was not populated by Provider A.",
                )
            )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _collect_facts(state: AuditState, evidence_refs: List[str]) -> List[str]:
    """Return observation strings for all screen_facts linked to evidence_refs."""
    observations = []
    for ev_id in evidence_refs:
        for fact in state.screen_facts.get(ev_id, []):
            observations.append(fact.observation)
    return observations


# ---------------------------------------------------------------------------
# Main node entry point
# ---------------------------------------------------------------------------

def run_rules_engine(state: AuditState) -> AuditState:
    """
    LangGraph node — deterministic rules engine.
    Populates state.review_queue and state.pipeline_flags.
    """
    logger.info("[RulesEngine] Starting deterministic checks.")

    queue: List[ReviewQueueItem] = []
    flags: List[AuditFlag]       = []

    _check_surface_coverage(state, queue, flags)
    _check_incomplete_layer_coverage(state, queue, flags)
    _check_unsupported_claims(state, queue, flags)
    _check_missing_answers(state, queue, flags)
    _check_low_confidence(state, queue, flags)

    state.review_queue    = queue
    state.pipeline_flags  = flags

    logger.info(
        "[RulesEngine] Done. review_queue=%d items, flags=%d.",
        len(queue), len(flags),
    )
    return state


# ---------------------------------------------------------------------------
# Conditional routing function (used in graph.py)
# ---------------------------------------------------------------------------

def should_run_provider_b(state: AuditState) -> str:
    """
    LangGraph conditional edge.
    Returns "provider_b" if there are items to verify, else "merge_scoring".
    """
    if state.review_queue:
        logger.info(
            "[Router] review_queue has %d items → routing to Provider B.",
            len(state.review_queue),
        )
        return "provider_b"

    logger.info("[Router] review_queue is empty → skipping Provider B, going to merge.")
    return "merge_scoring"
