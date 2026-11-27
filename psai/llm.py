"""One function the app uses to get structured JSON from a model.

Providers (``PSAI_LLM``; auto-detected when unset):

* ``vertex``    Claude on Google Cloud Vertex AI (default on Google Cloud; needs
                ``GOOGLE_CLOUD_PROJECT`` and ADC, i.e. the Cloud Run service account).
* ``gemini``    Gemini on Vertex AI via google-genai (``PSAI_MODEL``, default ``gemini-2.5-pro``;
                check the Model Garden for the current id in your region).
* ``anthropic`` Claude via the Anthropic API (needs ``ANTHROPIC_API_KEY``); handy for local runs.
"""

from __future__ import annotations

import json
import os


def provider() -> str:
    p = os.environ.get("PSAI_LLM")
    if p:
        return p
    if os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"):
        return "vertex"
    return "anthropic"


def model_name(role: str = "answer") -> str:
    """Model for a role. Answers default to Sonnet 5.5 (good Arabic, mid price); the small
    search-planning step defaults to Haiku 5.5. Override with PSAI_MODEL / PSAI_PLAN_MODEL,
    e.g. PSAI_MODEL=claude-haiku-5-5 for the cheapest setup, or claude-opus-5-5 for the best."""
    if role == "plan":
        default = {"gemini": "gemini-2.5-flash"}.get(provider(), "claude-haiku-5-5")
        return os.environ.get("PSAI_PLAN_MODEL", default)
    default = {"gemini": "gemini-2.5-pro"}.get(provider(), "claude-sonnet-5-5")
    return os.environ.get("PSAI_MODEL", default)


def _claude(client, system: str, user: str, schema: dict, effort: str, max_tokens: int, role: str) -> dict:
    resp = client.messages.create(
        model=model_name(role),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if resp.stop_reason == "refusal":
        return {}
    return json.loads(next((b.text for b in resp.content if b.type == "text"), "{}"))


def _gemini(system: str, user: str, schema: dict, max_tokens: int, role: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                          location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
    resp = client.models.generate_content(
        model=model_name(role),
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json",
            response_json_schema=schema, max_output_tokens=max_tokens, temperature=0.2),
    )
    return json.loads(resp.text or "{}")


def generate_json(system: str, user: str, schema: dict, effort: str = "medium",
                  max_tokens: int = 6000, role: str = "answer") -> dict:
    """Return the model's JSON answer as a dict ({} if it declined or returned nothing usable)."""
    p = provider()
    try:
        if p == "gemini":
            return _gemini(system, user, schema, max_tokens, role)
        if p == "vertex":
            import anthropic

            client = anthropic.AnthropicVertex(
                project_id=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ["ANTHROPIC_VERTEX_PROJECT_ID"],
                region=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
        else:
            import anthropic

            client = anthropic.Anthropic()
        return _claude(client, system, user, schema, effort, max_tokens, role)
    except json.JSONDecodeError:
        return {}
