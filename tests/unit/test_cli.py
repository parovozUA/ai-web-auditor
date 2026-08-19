from typing import Any

import pytest

from ai_web_auditor.cli import build_parser, main
from ai_web_auditor.models import ArtifactPaths


@pytest.fixture
def artifact_paths() -> ArtifactPaths:
    return ArtifactPaths(
        directory="/tmp/artifacts/run-1",
        report_json="/tmp/artifacts/run-1/report.json",
        report_html="/tmp/artifacts/run-1/report.html",
        trace_json="/tmp/artifacts/run-1/trace.json",
    )


def test_scan_command_accepts_one_url() -> None:
    args = build_parser().parse_args(["scan", "https://example.com"])

    assert args.command == "scan"
    assert args.url == "https://example.com"
    assert args.allow_private is False
    assert args.model == "gemini-3.5-flash"


def test_main_returns_scan_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    artifact_paths: ArtifactPaths,
) -> None:
    async def fake_run_scan(options: Any, agents: Any = None) -> tuple[int, ArtifactPaths]:
        return 1, artifact_paths

    monkeypatch.setattr("ai_web_auditor.cli.run_scan", fake_run_scan)

    assert main(["scan", "https://example.com"]) == 1
