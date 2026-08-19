import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from ai_web_auditor.agents import AgentBackend, GeminiAgentBackend
from ai_web_auditor.link_checker import LinkChecker
from ai_web_auditor.models import ArtifactPaths
from ai_web_auditor.reporting import ReportRenderer
from ai_web_auditor.scanner import PageScanner
from ai_web_auditor.tracing import TraceRecorder
from ai_web_auditor.urls import UrlPolicy
from ai_web_auditor.workflow import (
    AuditState,
    WorkflowServices,
    build_audit_graph,
    exit_code_for,
    initial_state,
)


@dataclass(frozen=True)
class ScanOptions:
    url: str
    allow_private: bool = False
    model: str = "gemini-3.5-flash"
    artifact_root: Path = Path("artifacts")


def create_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"run-{timestamp}-{uuid4().hex[:6]}"


async def run_scan(
    options: ScanOptions,
    *,
    agents: AgentBackend | None = None,
) -> tuple[int, ArtifactPaths]:
    run_id = create_run_id()
    policy = UrlPolicy(allow_private=options.allow_private)
    checker = LinkChecker(policy=policy)
    renderer = ReportRenderer(options.artifact_root)
    backend = agents or GeminiAgentBackend(model_name=options.model)
    trace = TraceRecorder()

    async with PageScanner(policy=policy, link_checker=checker) as scanner:
        graph = build_audit_graph(
            WorkflowServices(
                scanner=scanner,
                agents=backend,
                writer=renderer,
                trace=trace,
            )
        )
        raw_state = await graph.ainvoke(initial_state(run_id, options.url))
        state = cast(AuditState, raw_state)

    paths = state.get("artifact_paths")
    if paths is None:
        raise RuntimeError("Workflow completed without generating artifacts")

    return exit_code_for(state), paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-web-auditor",
        description="AI Web Auditor — Bounded agentic website reliability scanner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan a website seed URL")
    scan_parser.add_argument("url", help="Target seed URL to scan")
    scan_parser.add_argument(
        "--allow-private",
        action="store_true",
        help="Permit loopback, private, and local fixture addresses",
    )
    scan_parser.add_argument(
        "--model",
        default="gemini-3.5-flash",
        help="Gemini model name (default: gemini-3.5-flash)",
    )
    scan_parser.add_argument(
        "--artifacts-dir",
        default=Path("artifacts"),
        type=Path,
        help="Directory root for output artifacts (default: artifacts)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    options = ScanOptions(
        url=args.url,
        allow_private=args.allow_private,
        model=args.model,
        artifact_root=args.artifacts_dir,
    )

    try:
        exit_code, paths = asyncio.run(run_scan(options))
        print(f"Scan complete (exit code {exit_code}).")
        print(f"  Report JSON: {paths.report_json}")
        print(f"  Report HTML: {paths.report_html}")
        print(f"  Trace JSON:  {paths.trace_json}")
        return exit_code
    except Exception as exc:
        print(f"Error executing scan: {exc}")
        return 2
