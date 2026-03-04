"""
CG-IDF v2 — Step 2: Provider A (Primary LLM Analysis)

Responsibilities:
  - Extract screen_facts per evidence item.
  - Populate all layer questions (llm_answer, answer_type, confidence, evidence_refs).
  - Never claim "supported" without evidence_refs.
  - Output structured JSON only.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cg_idf_v2.llm import call_llm
from cg_idf_v2.schema import (
    AnswerType,
    AuditState,
    Layer,
    Question,
    ScreenFact,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layer definitions — what questions Provider A must answer
# ---------------------------------------------------------------------------

LAYER_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "engagement": {
        "label": "Engagement Incentives",
        "questions": [
            ("ENG_01", "What UI patterns drive the user to spend more time in the app?"),
            ("ENG_02", "Are there infinite scroll or auto-play mechanisms present?"),
            ("ENG_03", "Does the app use variable-reward mechanics (e.g., likes, streaks)?"),
            ("ENG_04", "Are notification or badge counts visible on the main surface?"),
        ],
    },
    "monetization": {
        "label": "Monetization Incentives",
        "questions": [
            ("MON_01", "Where and how prominently are purchase CTAs surfaced?"),
            ("MON_02", "Is there urgency or scarcity framing present (e.g., limited time)?"),
            ("MON_03", "Are subscription upsells visible without user initiation?"),
            ("MON_04", "Is pricing obscured or displayed in non-native currency equivalents?"),
        ],
    },
    "retention": {
        "label": "Retention Incentives",
        "questions": [
            ("RET_01", "Are streak or progress mechanics visible?"),
            ("RET_02", "Does the app surface loss-aversion cues (e.g., 'don't lose your streak')?"),
            ("RET_03", "Is there a daily reward or check-in system incentivizing return?"),
            ("RET_04", "Are personalization signals used to create lock-in?"),
        ],
    },
    "social": {
        "label": "Social & Sharing Incentives",
        "questions": [
            ("SOC_01", "Are social proof elements (follower counts, likes) prominently shown?"),
            ("SOC_02", "Does the app incentivize content sharing or invites?"),
            ("SOC_03", "Are leaderboards or comparative rankings visible?"),
            ("SOC_04", "Is there visible social validation feedback (reactions, comments)?"),
        ],
    },
    "dark_patterns": {
        "label": "Dark Pattern Detection",
        "questions": [
            ("DRK_01", "Is the default state biased toward data sharing or purchases?"),
            ("DRK_02", "Are cancel or unsubscribe paths hidden or made deliberately difficult?"),
            ("DRK_03", "Is confirmshaming language present (e.g., 'No thanks, I hate saving money')?"),
            ("DRK_04", "Are there roach-motel flows (easy in, hard out)?"),
        ],
    },
}

# ---------------------------------------------------------------------------
# System prompt for Provider A
# ---------------------------------------------------------------------------

PROVIDER_A_SYSTEM_PROMPT = """
You are Provider A in the CG-IDF v2 Incentive Audit Engine.

Your role:
1. Analyze each evidence item and extract screen_facts (atomic UI observations).
2. Answer every question in every layer.
3. For each answer, set answer_type to one of: supported | inferred | unknown.
   - "supported"  → you have direct evidence_refs (evidence_id values) backing the claim.
   - "inferred"   → reasonable inference from context, but no direct screenshot proof.
   - "unknown"    → cannot be determined from the evidence provided.
4. NEVER set answer_type="supported" without listing at least one evidence_id in evidence_refs.
5. Set confidence between 0.0 and 1.0.
6. Output ONLY valid JSON matching the schema provided. No commentary outside JSON.

Output schema:
{
  "run_id": "<uuid>",
  "screen_facts": {
    "<evidence_id>": [
      {"fact_id": "<id>", "evidence_id": "<id>", "observation": "...", "ui_element": "..."},
      ...
    ]
  },
  "layers": {
    "<layer_id>": {
      "layer_id": "<layer_id>",
      "label": "<label>",
      "sub_scores": {"<dimension>": <0.0-1.0>},
      "questions": [
        {
          "q_id": "<id>",
          "question_text": "...",
          "llm_answer": "...",
          "answer_type": "supported|inferred|unknown",
          "confidence": <0.0-1.0>,
          "evidence_refs": ["<evidence_id>", ...],
          "notes": "optional extra context"
        }
      ]
    }
  }
}
"""


def _load_image(image_path: str) -> Tuple[str, str]:
    """
    Load an image file and return (base64_data, media_type).
    Raises FileNotFoundError if the path does not exist.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        media_type = "image/png"  # safe default
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return data, media_type


def _build_user_message(state: AuditState) -> Tuple[str, List[Dict[str, str]]]:
    """
    Serialize evidence and layer questions into the Provider A prompt.

    Returns:
        (text_prompt, images) where images is a list of
        {"data": base64_str, "media_type": "image/png"} dicts,
        one per evidence item that has an image_path set.
        Images are ordered to match the image index references in the text.
    """
    images: List[Dict[str, str]] = []
    image_index_map: Dict[str, int] = {}  # evidence_id -> 1-based image index

    for ev in state.evidence:
        if ev.image_path:
            try:
                img_data, media_type = _load_image(ev.image_path)
                images.append({"data": img_data, "media_type": media_type})
                image_index_map[ev.evidence_id] = len(images)
            except FileNotFoundError as exc:
                logger.warning("[ProviderA] %s — skipping image.", exc)

    # Build evidence metadata block (exclude binary image_path from JSON)
    evidence_list = []
    for ev in state.evidence:
        ev_dict = ev.model_dump(mode="json", exclude={"image_path"})
        if ev.evidence_id in image_index_map:
            ev_dict["screenshot"] = f"Image {image_index_map[ev.evidence_id]} (attached above)"
        evidence_list.append(ev_dict)
    evidence_block = json.dumps(evidence_list, indent=2)

    layers_block = json.dumps(
        {
            layer_id: {
                "label": defn["label"],
                "questions": [
                    {"q_id": q[0], "question_text": q[1]}
                    for q in defn["questions"]
                ],
            }
            for layer_id, defn in LAYER_DEFINITIONS.items()
        },
        indent=2,
    )

    image_note = ""
    if image_index_map:
        refs = "\n".join(
            f"  - Image {idx} → evidence_id={ev_id}"
            for ev_id, idx in image_index_map.items()
        )
        image_note = f"\n## Attached screenshots (analyze each visually)\n{refs}\n"

    text = f"""{image_note}
## Evidence items (human-supplied, do not fabricate)
{evidence_block}

## Layers and questions you must answer
{layers_block}

Analyze the evidence (and the attached screenshots where provided) and return the JSON output described in the system prompt.
run_id must be a new UUID you generate.
"""
    return text, images


def run_provider_a(state: AuditState) -> AuditState:
    """
    LangGraph node — Provider A primary analysis.
    Calls the LLM, parses structured JSON, populates state.screen_facts and state.layers.
    """
    run_id = str(uuid.uuid4())

    logger.info("[ProviderA] Starting analysis. run_id=%s", run_id)

    user_message, images = _build_user_message(state)
    if images:
        logger.info("[ProviderA] Sending %d screenshot(s) to LLM.", len(images))

    try:
        raw_text = call_llm(
            system_prompt=PROVIDER_A_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=8192,
            images=images or None,
        ).strip()
    except Exception as exc:
        logger.error("[ProviderA] LLM call failed: %s", exc)
        state.errors.append(f"provider_a_llm_error: {exc}")
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
        logger.error("[ProviderA] JSON parse error: %s", exc)
        state.errors.append(f"provider_a_json_error: {exc}")
        return state

    # --- Populate screen_facts ---
    raw_facts: Dict[str, list] = payload.get("screen_facts", {})
    for ev_id, facts in raw_facts.items():
        state.screen_facts[ev_id] = [
            ScreenFact(
                fact_id=f.get("fact_id", str(uuid.uuid4())[:8]),
                evidence_id=ev_id,
                observation=f.get("observation", ""),
                ui_element=f.get("ui_element"),
            )
            for f in facts
        ]

    # --- Populate layers ---
    raw_layers: Dict[str, dict] = payload.get("layers", {})
    for layer_id, layer_data in raw_layers.items():
        questions = []
        for q_data in layer_data.get("questions", []):
            answer_type_raw = q_data.get("answer_type", "unknown")
            try:
                answer_type = AnswerType(answer_type_raw)
            except ValueError:
                answer_type = AnswerType.unknown

            # Enforce: supported requires refs
            evidence_refs = q_data.get("evidence_refs", [])
            if answer_type == AnswerType.supported and not evidence_refs:
                logger.warning(
                    "[ProviderA] q_id=%s claims 'supported' with no evidence_refs — downgrading to 'inferred'.",
                    q_data.get("q_id"),
                )
                answer_type = AnswerType.inferred

            questions.append(
                Question(
                    q_id=q_data.get("q_id", "UNKNOWN"),
                    question_text=q_data.get("question_text", ""),
                    llm_answer=q_data.get("llm_answer"),
                    answer_type=answer_type,
                    confidence=float(q_data.get("confidence", 0.0)),
                    evidence_refs=evidence_refs,
                    notes=q_data.get("notes"),
                )
            )

        state.layers[layer_id] = Layer(
            layer_id=layer_id,
            label=layer_data.get("label", layer_id),
            sub_scores=layer_data.get("sub_scores", {}),
            questions=questions,
        )

    logger.info(
        "[ProviderA] Done. Layers populated: %s. run_id=%s",
        list(state.layers.keys()),
        run_id,
    )
    return state
