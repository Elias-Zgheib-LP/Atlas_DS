"""
CG-IDF v2 — Step 4: Provider B (Verifier LLM)

Responsibilities:
  - ONLY processes items in state.review_queue.
  - Receives text-only data (no images).
  - Reviews Provider A answer + evidence_refs + screen_facts.
  - Outputs one VerificationResult per review_queue item.
  - Does NOT re-score entire layers.

Possible verification statuses:
  confirm | downgrade | contradiction | insufficient_evidence | missing_evidence
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List

from cg_idf_v2.llm import call_llm
from cg_idf_v2.schema import (
    AuditState,
    ReviewQueueItem,
    VerificationResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt for Provider B
# ---------------------------------------------------------------------------

PROVIDER_B_SYSTEM_PROMPT = """
You are Provider B in the CG-IDF v2 Incentive Audit Engine.

Your ONLY job is to verify specific flagged answers produced by Provider A.
You receive text descriptions only — no images.

For each item in the review_queue you must output a VerificationResult with:
  - q_id           : the question ID being verified
  - layer_id       : the layer this question belongs to
  - status         : one of confirm | downgrade | contradiction | insufficient_evidence | missing_evidence
  - rationale      : 1-3 sentence explanation (text only, grounded in evidence provided)
  - revised_confidence : required ONLY when status == "downgrade" (float 0.0-1.0)

Status definitions:
  confirm               → Provider A's answer is well-supported by the evidence provided.
  downgrade             → Answer is partially correct but confidence should be lower.
  contradiction         → Evidence directly contradicts Provider A's answer.
  insufficient_evidence → Evidence exists but is too thin to verify the claim.
  missing_evidence      → The referenced evidence_ids are absent or uninformative.

RULES:
  - Do NOT invent new evidence.
  - Do NOT re-score entire layers.
  - Do NOT provide free-form commentary outside JSON.
  - One VerificationResult per review_queue item.

Output schema:
{
  "run_id": "<uuid>",
  "verifications": [
    {
      "q_id": "...",
      "layer_id": "...",
      "status": "confirm|downgrade|contradiction|insufficient_evidence|missing_evidence",
      "rationale": "...",
      "revised_confidence": <0.0-1.0 or null>
    },
    ...
  ]
}
"""


def _build_verification_prompt(
    queue: List[ReviewQueueItem],
    state: AuditState,
) -> str:
    """Build the Provider B user message from the review_queue."""

    items: List[Dict[str, Any]] = []
    for item in queue:
        # Collect the original answer from state.layers
        original_answer = None
        original_confidence = None
        layer = state.layers.get(item.layer_id)
        if layer:
            for q in layer.questions:
                if q.q_id == item.q_id:
                    original_answer      = q.llm_answer
                    original_confidence  = q.confidence
                    break

        # Gather screen_fact texts for the referenced evidence
        fact_texts: List[str] = []
        for ev_id in item.evidence_refs:
            for fact in state.screen_facts.get(ev_id, []):
                fact_texts.append(f"[{ev_id}] {fact.observation}")

        items.append(
            {
                "q_id":              item.q_id,
                "layer_id":          item.layer_id,
                "flag_reason":       item.reason,
                "flag_code":         item.flag_code,
                "ai1_answer":        original_answer or item.ai1_answer,
                "ai1_confidence":    original_confidence,
                "evidence_refs":     item.evidence_refs,
                "screen_facts_text": fact_texts,
            }
        )

    return (
        "## Review Queue\n"
        "Verify each item below. Return JSON as specified in the system prompt.\n\n"
        + json.dumps(items, indent=2)
    )


def run_provider_b(state: AuditState) -> AuditState:
    """
    LangGraph node — Provider B targeted verification.
    Only invoked when review_queue is non-empty (see conditional edge in graph.py).
    """
    if not state.review_queue:
        logger.info("[ProviderB] review_queue is empty — nothing to verify.")
        return state

    run_id = str(uuid.uuid4())

    logger.info(
        "[ProviderB] Verifying %d items. run_id=%s",
        len(state.review_queue), run_id,
    )

    try:
        raw_text = call_llm(
            system_prompt=PROVIDER_B_SYSTEM_PROMPT,
            user_message=_build_verification_prompt(state.review_queue, state),
            max_tokens=4096,
        ).strip()
    except Exception as exc:
        logger.error("[ProviderB] LLM call failed: %s", exc)
        state.errors.append(f"provider_b_llm_error: {exc}")
        return state

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```", 2)[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.rsplit("```", 1)[0].strip()

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("[ProviderB] JSON parse error: %s", exc)
        state.errors.append(f"provider_b_json_error: {exc}")
        return state

    results: List[VerificationResult] = []
    for v in payload.get("verifications", []):
        try:
            status = VerificationStatus(v.get("status", "insufficient_evidence"))
        except ValueError:
            status = VerificationStatus.insufficient_evidence

        revised_conf = v.get("revised_confidence")
        if revised_conf is not None:
            try:
                revised_conf = float(revised_conf)
            except (TypeError, ValueError):
                revised_conf = None

        results.append(
            VerificationResult(
                q_id=v.get("q_id", "UNKNOWN"),
                layer_id=v.get("layer_id", "UNKNOWN"),
                status=status,
                rationale=v.get("rationale", ""),
                revised_confidence=revised_conf,
            )
        )

    state.verifications = results

    logger.info(
        "[ProviderB] Done. %d verifications returned. run_id=%s",
        len(results), run_id,
    )
    return state
