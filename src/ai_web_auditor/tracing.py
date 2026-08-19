from datetime import UTC, datetime

from ai_web_auditor.models import TraceEvent


class TraceRecorder:
    """Collects sanitized execution events without secrets or raw LLM bodies."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def record(
        self,
        node: str,
        event_type: str,
        status: str,
        elapsed_ms: int,
        counts: dict[str, int] | None = None,
        tool_name: str | None = None,
        model_usage: dict[str, int] | None = None,
        error: BaseException | None = None,
    ) -> None:
        now_str = datetime.now(UTC).isoformat()
        err_msg = type(error).__name__ if error is not None else None

        event = TraceEvent(
            timestamp=now_str,
            node=node,
            event_type=event_type,
            status=status,
            elapsed_ms=elapsed_ms,
            counts=counts or {},
            tool_name=tool_name,
            model_usage=model_usage or {},
            error_category=err_msg,
        )
        self._events.append(event)

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)
