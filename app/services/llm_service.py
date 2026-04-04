"""Gemini-based per-file and repository-level analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import google.generativeai as genai

from app.config import Settings, get_settings
from app.exceptions import LLMConfigurationError

logger = logging.getLogger(__name__)

_FILE_RESPONSE_SCHEMA = """Respond with ONLY valid JSON (no markdown fences):
{
  "summary": "one or two sentences",
  "issues": ["short bullet", "..."],
  "suggestions": ["short bullet", "..."]
}"""


class GeminiAnalyzer:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._model = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        if self._settings.gemini_api_key:
            genai.configure(api_key=self._settings.gemini_api_key)
            self._model = genai.GenerativeModel(self._settings.gemini_model)

    @property
    def enabled(self) -> bool:
        return self._model is not None

    async def analyze_file(
        self,
        path: str,
        language: str,
        truncated_content: str,
    ) -> dict[str, Any]:
        if not self._model:
            raise LLMConfigurationError("GEMINI_API_KEY is not set")

        prompt = (
            f"You are a senior code reviewer. Analyze this {language} file `{path}`.\n"
            f"{_FILE_RESPONSE_SCHEMA}\n\n---\n{truncated_content}\n---"
        )

        def _call() -> str:
            in_tokens = len(prompt) // 4
            self.total_input_tokens += in_tokens
            resp = self._model.generate_content(prompt)
            txt = _gemini_response_text(resp)
            self.total_output_tokens += len(txt) // 4
            return txt

        raw = await asyncio.to_thread(_call)
        return _parse_json_block(raw)

    async def analyze_files_batch(
        self,
        items: list[tuple[str, str, str]],
    ) -> list[dict[str, Any]]:
        """Batch several files into one LLM call. Each item is (path, language, snippet)."""
        if not items:
            return []
        if not self._model:
            raise LLMConfigurationError("GEMINI_API_KEY is not set")

        blocks: list[str] = []
        for path, lang, snippet in items:
            blocks.append(f'### FILE `{path}` ({lang})\n```\n{snippet}\n```\n')

        prompt = (
            "You are a senior code reviewer. Analyze EACH file below.\n"
            "Return ONLY a JSON array (no markdown fences). Each object must include "
            '"path" (exact path string), "summary", "issues" (array of strings), '
            '"suggestions" (array of strings). Order must match the file list.\n\n'
            + "\n".join(blocks)
        )

        def _call() -> str:
            in_tokens = len(prompt) // 4
            self.total_input_tokens += in_tokens
            resp = self._model.generate_content(prompt)
            txt = _gemini_response_text(resp)
            self.total_output_tokens += len(txt) // 4
            return txt

        raw = await asyncio.to_thread(_call)
        return _parse_json_array(raw)

    async def analyze_repository_design(
        self,
        owner: str,
        repo: str,
        file_summaries: list[dict[str, Any]],
        static_issue_sample: list[str],
    ) -> dict[str, Any]:
        """Single short design description for the repository (no separate arch/doc blocks)."""
        if not self._model:
            raise LLMConfigurationError("GEMINI_API_KEY is not set")

        payload = json.dumps(
            {
                "repository": f"{owner}/{repo}",
                "file_summaries": file_summaries[:35],
                "sample_static_issues": static_issue_sample[:40],
            },
            ensure_ascii=False,
        )[:24000]

        prompt = (
            "You are a software architect. Given the file summaries and static issues, "
            "write a brief design assessment (3–5 short sentences only).\n"
            "Describe structure, layering, cohesion, and obvious design risks. No bullet lists.\n"
            'Respond with ONLY valid JSON: {"design_summary": "plain text paragraph"}'
            f"\n\nDATA:\n{payload}"
        )

        def _call() -> str:
            in_tokens = len(prompt) // 4
            self.total_input_tokens += in_tokens
            resp = self._model.generate_content(prompt)
            txt = _gemini_response_text(resp)
            self.total_output_tokens += len(txt) // 4
            return txt

        raw = await asyncio.to_thread(_call)
        return _parse_json_block(raw)


def _gemini_response_text(resp: Any) -> str:
    try:
        return (resp.text or "").strip()
    except (ValueError, AttributeError):
        return ""


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text, flags=re.I)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except json.JSONDecodeError:
        logger.warning("LLM batch output was not valid JSON array")
    return []


def _parse_json_block(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text, flags=re.I)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON; wrapping as text")
        return {
            "summary": text[:2000],
            "issues": ["LLM output was not valid JSON"],
            "suggestions": [],
            "design_summary": text[:1200],
        }
