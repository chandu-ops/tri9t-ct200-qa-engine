import json
import os
import httpx
from typing import List, Dict, Any
from pydantic import ValidationError
from app.schemas import LLMTestSuiteOutput

SYSTEM_PROMPT = """You are a Senior Medical Device QA Engineer specializing in IEC 62304 regulatory verification for the CardioTrack CT-200 Home Blood Pressure Monitor.
Given a set of version-pinned requirement sections, generate between 3 and 5 concrete, repeatable QA test cases.
STRICT RULES:
1. Return ONLY valid JSON matching the exact schema requested. No markdown fences, no commentary.
2. Every test case MUST cite an exact `source_logical_node_id` from the provided input sections.
3. `expected_result` MUST state specific observable metrics (e.g., exact pressure in mmHg, display error code like E3, deflation time in seconds) and NEVER vague phrases like "works properly".
4. Output JSON schema:
{
  "test_cases": [
    {
      "test_id": "TC-001",
      "title": "...",
      "source_logical_node_id": "<exact logical_node_id from input>",
      "preconditions": ["..."],
      "steps": ["..."],
      "expected_result": "...",
      "priority": "HIGH" | "MEDIUM" | "LOW"
    }
  ]
}"""

def build_user_prompt(nodes_context: List[Dict[str, Any]], validation_error_feedback: str = "") -> str:
    blocks = []
    for n in nodes_context:
        blocks.append(
            f"--- NODE ---\n"
            f"logical_node_id: {n['logical_node_id']}\n"
            f"canonical_path: {n['canonical_path']}\n"
            f"heading: {n['heading']}\n"
            f"body_text:\n{n['body_text']}\n"
            f"tables:\n{n.get('tables_markdown') or 'None'}"
        )
    prompt = "Generate 3 to 5 QA test cases for the following CT-200 specification sections:\n\n" + "\n\n".join(blocks)
    if validation_error_feedback:
        prompt += (
            f"\n\nWARNING: Your previous response failed strict schema validation with error:\n"
            f"{validation_error_feedback}\n"
            f"Fix the JSON structure and ensure all source_logical_node_id values match the input."
        )
    return prompt

def call_llm_provider(system_prompt: str, user_prompt: str) -> str:
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
    model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable is not set.")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(base_url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

def generate_and_validate_test_cases(
    nodes_context: List[Dict[str, Any]], max_retries: int = 3
) -> LLMTestSuiteOutput:
    valid_node_ids = {n["logical_node_id"] for n in nodes_context}
    last_error = ""
    for attempt in range(1, max_retries + 1):
        user_prompt = build_user_prompt(nodes_context, validation_error_feedback=last_error)
        try:
            raw_text = call_llm_provider(SYSTEM_PROMPT, user_prompt)
            cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed_json = json.loads(cleaned)
            suite = LLMTestSuiteOutput.model_validate(parsed_json)
            for tc in suite.test_cases:
                if tc.source_logical_node_id not in valid_node_ids:
                    raise ValueError(
                        f"Hallucinated source_logical_node_id '{tc.source_logical_node_id}'. "
                        f"Allowed IDs: {sorted(valid_node_ids)}"
                    )
            return suite
        except (json.JSONDecodeError, ValidationError, ValueError, httpx.HTTPError) as exc:
            last_error = str(exc)
    raise ValueError(f"LLM failed to produce valid, traceable test cases after {max_retries} attempts. Last error: {last_error}")
