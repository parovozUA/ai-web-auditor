import os
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

from ai_web_auditor.models import (
    Finding,
    InvestigationResult,
    ReviewResult,
)
from ai_web_auditor.prompts import (
    INVESTIGATOR_SYNTHESIS_SYSTEM_PROMPT,
    INVESTIGATOR_TOOL_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    format_findings_payload,
    format_investigation_payload,
)


@tool
def scan_related_pages(reason: str) -> str:
    """Trigger bounded crawl of up to 5 related internal pages to test pattern recurrence."""
    return "ok"


SCAN_RELATED_PAGES_TOOL = scan_related_pages


class AgentUnavailableError(RuntimeError):
    """Raised when the agent backend fails, cannot connect, or is misconfigured."""


class AgentBackend(Protocol):
    @property
    def last_usage(self) -> dict[str, int] | None:
        raise NotImplementedError

    async def request_related_scan(self, seed_findings: list[Finding]) -> bool:
        raise NotImplementedError

    async def investigate(
        self,
        seed_findings: list[Finding],
        related_findings: list[Finding],
    ) -> InvestigationResult:
        raise NotImplementedError

    async def review(
        self,
        investigation: InvestigationResult,
        findings: list[Finding],
    ) -> ReviewResult:
        raise NotImplementedError


def _load_env_if_present() -> None:
    env_file = Path(".env")
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


class GeminiAgentBackend:
    """Bounded Gemini agent implementation using structured outputs and strict tools."""

    def __init__(
        self,
        model_name: str = "gemini-3.5-flash",
        *,
        model: Any = None,
        llm: Any = None,
    ) -> None:
        self._model_name = model_name
        self._llm = model or llm
        self._last_usage: dict[str, int] | None = None

    @property
    def last_usage(self) -> dict[str, int] | None:
        return self._last_usage

    def _get_llm(self) -> Any:
        if self._llm is None:
            _load_env_if_present()
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise AgentUnavailableError(
                    "GEMINI_API_KEY environment variable is not set"
                )
            self._llm = ChatGoogleGenerativeAI(
                model=self._model_name,
                google_api_key=api_key,
                temperature=0.0,
            )
        return self._llm

    def _extract_usage(self, response: Any) -> None:
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            self._last_usage = {
                "input_tokens": response.usage_metadata.get("input_tokens", 0),
                "output_tokens": response.usage_metadata.get("output_tokens", 0),
            }
        elif hasattr(response, "response_metadata") and "token_usage" in response.response_metadata:
            usage = response.response_metadata["token_usage"]
            self._last_usage = {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            }
        else:
            self._last_usage = None

    async def request_related_scan(self, seed_findings: list[Finding]) -> bool:
        llm = self._get_llm()
        tool_bound_llm = llm.bind_tools([SCAN_RELATED_PAGES_TOOL])
        messages = [
            SystemMessage(content=INVESTIGATOR_TOOL_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Observed seed findings:\n\n"
                    f"{format_findings_payload(seed_findings)}\n\n"
                    "Determine whether to call scan_related_pages to investigate "
                    "cross-page recurrence."
                )
            ),
        ]
        try:
            response = await tool_bound_llm.ainvoke(messages)
            self._extract_usage(response)
            tool_calls = getattr(response, "tool_calls", [])
            if not tool_calls:
                return False
            if len(tool_calls) != 1:
                return False
            call = tool_calls[0]
            return bool(call.get("name") == "scan_related_pages")
        except Exception as exc:
            raise AgentUnavailableError(f"Tool request call failed: {exc}") from exc

    async def investigate(
        self,
        seed_findings: list[Finding],
        related_findings: list[Finding],
    ) -> InvestigationResult:
        llm = self._get_llm()
        structured_llm = llm.with_structured_output(InvestigationResult)
        all_findings = seed_findings + related_findings
        messages = [
            SystemMessage(content=INVESTIGATOR_SYNTHESIS_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Observed findings across pages:\n\n"
                    f"{format_findings_payload(all_findings)}\n\n"
                    "Synthesize the findings into coherent incidents."
                )
            ),
        ]
        try:
            result = await structured_llm.ainvoke(messages)
            self._extract_usage(result)
            if isinstance(result, InvestigationResult):
                return result
            if isinstance(result, dict):
                return InvestigationResult.model_validate(result)
            raise AgentUnavailableError("Failed to produce structured investigation")
        except Exception as exc:
            raise AgentUnavailableError(f"Investigation call failed: {exc}") from exc

    async def review(
        self,
        investigation: InvestigationResult,
        findings: list[Finding],
    ) -> ReviewResult:
        llm = self._get_llm()
        structured_llm = llm.with_structured_output(ReviewResult)
        messages = [
            SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"{format_investigation_payload(investigation, findings)}\n\n"
                    "Review the proposed investigation and validate severity, findings citations, "
                    "and remediation."
                )
            ),
        ]
        try:
            result = await structured_llm.ainvoke(messages)
            self._extract_usage(result)
            if isinstance(result, ReviewResult):
                return result
            if isinstance(result, dict):
                return ReviewResult.model_validate(result)
            raise AgentUnavailableError("Failed to produce structured review")
        except Exception as exc:
            raise AgentUnavailableError(f"Reviewer call failed: {exc}") from exc
