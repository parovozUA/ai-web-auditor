import os
from collections.abc import Mapping
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from website_reliability_agent.models import (
    Finding,
    InvestigationResult,
    ReviewResult,
    StrictModel,
)
from website_reliability_agent.prompts import (
    INVESTIGATOR_SYNTHESIS_SYSTEM_PROMPT,
    INVESTIGATOR_TOOL_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    format_findings_payload,
    format_investigation_payload,
)


class AgentUnavailableError(RuntimeError):
    """Raised when the LLM provider fails, times out, or lacks credentials."""


class AgentBackend(Protocol):
    @property
    def last_usage(self) -> dict[str, int]:
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


class NoToolArguments(StrictModel):
    pass


async def _tool_marker() -> str:
    return "The deterministic graph will execute the injected related-page scan."


SCAN_RELATED_PAGES_TOOL = StructuredTool.from_function(
    coroutine=_tool_marker,
    name="scan_related_pages",
    description=(
        "Scan the graph-selected same-origin pages for finding codes already "
        "present on the seed page. Takes no arguments."
    ),
    args_schema=NoToolArguments,
)


class GeminiAgentBackend:
    """Gemini-backed agent provider with lazy initialization and structured outputs."""

    def __init__(
        self,
        *,
        model_name: str = "gemini-3.5-flash",
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        retries: int = 1,
        model: Any = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._retries = retries
        self._model = model
        self._last_usage: dict[str, int] = {}

    @property
    def last_usage(self) -> dict[str, int]:
        return dict(self._last_usage)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        key = self._api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise AgentUnavailableError("GEMINI_API_KEY is not configured")
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._model = ChatGoogleGenerativeAI(
                model=self._model_name,
                google_api_key=key,
                timeout=self._timeout,
                max_retries=self._retries,
            )
            return self._model
        except Exception as exc:
            raise AgentUnavailableError(f"Failed to initialize Gemini model: {exc}") from exc

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, Mapping):
            self._last_usage = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        else:
            self._last_usage = {}

    async def request_related_scan(self, seed_findings: list[Finding]) -> bool:
        try:
            model = self._get_model().bind_tools(
                [SCAN_RELATED_PAGES_TOOL],
                tool_choice="scan_related_pages",
            )
            messages = [
                SystemMessage(content=INVESTIGATOR_TOOL_SYSTEM_PROMPT),
                HumanMessage(content=format_findings_payload(seed_findings)),
            ]
            response = await model.ainvoke(messages)
            self._record_usage(response)
            calls = getattr(response, "tool_calls", [])
            return (
                len(calls) == 1
                and calls[0].get("name") == "scan_related_pages"
                and calls[0].get("args", {}) == {}
            )
        except AgentUnavailableError:
            raise
        except Exception as exc:
            raise AgentUnavailableError(f"Investigator tool request failed: {exc}") from exc

    async def investigate(
        self,
        seed_findings: list[Finding],
        related_findings: list[Finding],
    ) -> InvestigationResult:
        try:
            structured_model = self._get_model().with_structured_output(InvestigationResult)
            payload = (
                f"Seed Findings:\n{format_findings_payload(seed_findings)}\n\n"
                f"Related Findings:\n{format_findings_payload(related_findings)}"
            )
            messages = [
                SystemMessage(content=INVESTIGATOR_SYNTHESIS_SYSTEM_PROMPT),
                HumanMessage(content=payload),
            ]
            response = await structured_model.ainvoke(messages)
            if not isinstance(response, InvestigationResult):
                raise AgentUnavailableError("Investigator did not return valid structured output")
            return response
        except AgentUnavailableError:
            raise
        except Exception as exc:
            raise AgentUnavailableError(f"Investigator synthesis failed: {exc}") from exc

    async def review(
        self,
        investigation: InvestigationResult,
        findings: list[Finding],
    ) -> ReviewResult:
        try:
            structured_model = self._get_model().with_structured_output(ReviewResult)
            messages = [
                SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
                HumanMessage(content=format_investigation_payload(investigation, findings)),
            ]
            response = await structured_model.ainvoke(messages)
            if not isinstance(response, ReviewResult):
                raise AgentUnavailableError("Reviewer did not return valid structured output")
            return response
        except AgentUnavailableError:
            raise
        except Exception as exc:
            raise AgentUnavailableError(f"Reviewer failed: {exc}") from exc
