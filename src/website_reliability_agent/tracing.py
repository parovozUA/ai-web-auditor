from collections.abc import Mapping
from datetime import UTC, datetime

from website_reliability_agent.models import TraceEvent


class TraceRecorder:
    """Records sanitized in-memory execution trace events."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(
        self,
        *,
        node: str,
        event_type: str,
        status: str,
        elapsed_ms: int,
        tool_name: str | None = None,
        arguments: Mapping[str, str] | None = None,
        counts: Mapping[str, int] | None = None,
        model_usage: Mapping[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.events.append(
            TraceEvent(
                timestamp=datetime.now(UTC).isoformat(),
                node=node,
                event_type=event_type,
                status=status,
                elapsed_ms=max(0, elapsed_ms),
                tool_name=tool_name,
                arguments=dict(arguments or {}),
                counts=dict(counts or {}),
                model_usage=dict(model_usage or {}),
                error_category=type(error).__name__ if error else None,
            )
        )
