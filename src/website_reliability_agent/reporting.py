import json
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from website_reliability_agent.models import (
    AnalysisStatus,
    ArtifactPaths,
    RunReport,
)
from website_reliability_agent.workflow import AuditState, exit_code_for


def _to_run_report(state: AuditState) -> RunReport:
    seed_res = state.get("seed_result")
    related_res = state.get("related_results", [])
    pages_count = (1 if seed_res is not None else 0) + len(related_res)
    pages_scanned = min(pages_count, 6)

    return RunReport(
        run_id=state.get("run_id", "unknown-run"),
        seed_url=state.get("seed_url", "unknown-url"),
        completed_at=datetime.now(UTC).isoformat(),
        exit_code=exit_code_for(state),
        analysis_status=state.get("analysis_status", AnalysisStatus.NOT_NEEDED),
        pages_scanned=pages_scanned,
        findings=state.get("findings", []),
        incidents=state.get("incidents", []),
        operational_errors=state.get("operational_errors", []),
    )


class ReportRenderer:
    """Renders JSON, HTML, and trace artifacts into a dedicated run directory."""

    def __init__(self, artifact_root: Path) -> None:
        self._artifact_root = artifact_root
        self._environment = Environment(
            loader=PackageLoader("website_reliability_agent", "templates"),
            autoescape=select_autoescape(("html", "xml", "j2", "html.j2")),
        )

    def write(self, state: AuditState) -> ArtifactPaths:
        run_id = state.get("run_id", "unknown-run")
        directory = self._artifact_root / run_id
        directory.mkdir(parents=True, exist_ok=False)

        report = _to_run_report(state)
        report_json = directory / "report.json"
        report_html = directory / "report.html"
        trace_json = directory / "trace.json"

        report_json.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )

        trace_events = [
            event.model_dump(mode="json")
            for event in state.get("trace_events", [])
        ]

        template = self._environment.get_template("report.html.j2")
        rendered_html = template.render(
            report=report.model_dump(mode="json"),
            trace=trace_events,
        )
        report_html.write_text(rendered_html, encoding="utf-8")

        trace_json.write_text(
            json.dumps(trace_events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return ArtifactPaths(
            directory=str(directory.resolve()),
            report_json=str(report_json.resolve()),
            report_html=str(report_html.resolve()),
            trace_json=str(trace_json.resolve()),
        )
