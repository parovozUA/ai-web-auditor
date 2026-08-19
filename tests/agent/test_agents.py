from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from ai_web_auditor.agents import GeminiAgentBackend
from ai_web_auditor.models import (
    Finding,
    FindingCategory,
    FindingCode,
    IncidentProposal,
    InvestigationResult,
    ReviewDecision,
    ReviewResult,
    Severity,
)


class RecordingChatModel:
    def __init__(
        self,
        *,
        message: AIMessage | None = None,
        structured_result: Any = None,
    ) -> None:
        self.message = message or AIMessage(content="")
        self.structured_result = structured_result
        self.bound_tools: list[BaseTool] = []
        self.tool_choice: str | None = None
        self.invoked_prompts: list[Any] = []
        self.schema: Any = None

    def bind_tools(
        self,
        tools: list[BaseTool],
        *,
        tool_choice: str | None = None,
    ) -> "RecordingChatModel":
        self.bound_tools = list(tools)
        self.tool_choice = tool_choice
        return self

    def with_structured_output(self, schema: Any) -> "RecordingChatModel":
        self.schema = schema
        return self

    async def ainvoke(self, prompt: Any) -> Any:
        self.invoked_prompts.append(prompt)
        if self.schema is not None:
            return self.structured_result
        return self.message


@pytest.fixture
def sample_finding() -> Finding:
    return Finding(
        finding_id="finding-123",
        check_code=FindingCode.TITLE_MISSING,
        category=FindingCategory.SEO,
        source_page="https://example.com/",
        signature="title_missing",
        message="Page title is missing.",
        occurrence_count=1,
    )


async def test_investigator_requests_exactly_the_allowed_tool(
    sample_finding: Finding,
) -> None:
    model = RecordingChatModel(
        message=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "scan_related_pages",
                    "args": {},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )
    )
    backend = GeminiAgentBackend(model=model)

    assert await backend.request_related_scan([sample_finding]) is True
    assert [tool.name for tool in model.bound_tools] == ["scan_related_pages"]


async def test_tool_request_rejects_wrong_or_multiple_calls(
    sample_finding: Finding,
) -> None:
    model = RecordingChatModel(
        message=AIMessage(
            content="",
            tool_calls=[
                {"name": "other_tool", "args": {}, "id": "1", "type": "tool_call"},
                {"name": "scan_related_pages", "args": {}, "id": "2", "type": "tool_call"},
            ],
        )
    )

    assert await GeminiAgentBackend(model=model).request_related_scan(
        [sample_finding]
    ) is False


async def test_investigate_returns_structured_synthesis(
    sample_finding: Finding,
) -> None:
    expected = InvestigationResult(
        summary="Found missing titles on seed and related pages.",
        incidents=[
            IncidentProposal(
                title="Missing titles",
                summary="Title tag is missing across pages.",
                severity=Severity.LOW,
                finding_ids=["finding-123"],
                remediation="Add unique title tags.",
            )
        ],
    )
    model = RecordingChatModel(structured_result=expected)
    backend = GeminiAgentBackend(model=model)

    result = await backend.investigate([sample_finding], [])
    assert result == expected


async def test_review_returns_structured_review(
    sample_finding: Finding,
) -> None:
    investigation = InvestigationResult(
        summary="Missing titles.",
        incidents=[
            IncidentProposal(
                title="Missing titles",
                summary="Summary",
                severity=Severity.LOW,
                finding_ids=["finding-123"],
                remediation="Remediation",
            )
        ],
    )
    expected = ReviewResult(
        decision=ReviewDecision.ACCEPT,
        incidents=investigation.incidents,
        notes="All findings are valid.",
    )
    model = RecordingChatModel(structured_result=expected)
    backend = GeminiAgentBackend(model=model)

    result = await backend.review(investigation, [sample_finding])
    assert result == expected
