"""
CG-IDF v2 — Unified LLM Client

Supports OpenAI and Anthropic interchangeably, with optional image (vision) input.

Provider selection (in priority order):
  1. LLM_PROVIDER env var ("openai" | "anthropic")
  2. Auto-detect: if OPENAI_API_KEY is set → OpenAI
                  if ANTHROPIC_API_KEY is set → Anthropic

Model override:
  LLM_MODEL env var (e.g. "gpt-4o", "gpt-4o-mini", "claude-sonnet-4-6")

Image format (images parameter):
  List of dicts: [{"data": "<base64_str>", "media_type": "image/png"}, ...]
  Supported media_types: image/png, image/jpeg, image/webp, image/gif
"""

from __future__ import annotations

import os
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Default models per provider
_DEFAULTS = {
    "openai":     "gpt-4o",
    "anthropic":  "claude-sonnet-4-6",
}


def _detect_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").lower()
    if explicit in ("openai", "anthropic"):
        return explicit
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise EnvironmentError(
        "No LLM provider detected. "
        "Set OPENAI_API_KEY or ANTHROPIC_API_KEY (and optionally LLM_PROVIDER)."
    )


def call_llm(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 8192,
    images: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Send a system + user message to the configured LLM provider.
    Optionally attach screenshot images for vision/multimodal analysis.

    Args:
        system_prompt: The system instruction string.
        user_message:  The user text prompt.
        max_tokens:    Maximum tokens in the response.
        images:        Optional list of image dicts:
                       [{"data": "<base64>", "media_type": "image/png"}, ...]
                       Images are prepended before the text in the user turn.

    Returns:
        Raw text response string.

    Raises:
        RuntimeError on API failure.
    """
    provider = _detect_provider()
    model    = os.getenv("LLM_MODEL", _DEFAULTS[provider])

    logger.info("[LLM] provider=%s model=%s images=%d", provider, model, len(images or []))

    if provider == "openai":
        return _call_openai(system_prompt, user_message, model, max_tokens, images)
    else:
        return _call_anthropic(system_prompt, user_message, model, max_tokens, images)


def _call_openai(
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    images: Optional[List[Dict[str, str]]],
) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError("openai package not installed. Run: pip install openai") from exc

    client = OpenAI()  # reads OPENAI_API_KEY from env

    # Build multimodal content: images first, then text
    if images:
        content = []
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['media_type']};base64,{img['data']}"
                },
            })
        content.append({"type": "text", "text": user})
    else:
        content = user

    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": content},
        ],
    )
    return response.choices[0].message.content or ""


def _call_anthropic(
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    images: Optional[List[Dict[str, str]]],
) -> str:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise ImportError("anthropic package not installed. Run: pip install anthropic") from exc

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env

    # Build multimodal content: images first, then text
    if images:
        content = []
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": img["media_type"],
                    "data":       img["data"],
                },
            })
        content.append({"type": "text", "text": user})
    else:
        content = user

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text
