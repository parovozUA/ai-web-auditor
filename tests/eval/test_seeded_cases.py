import json
from pathlib import Path

from ai_web_auditor.cli import ScanOptions, run_scan
from ai_web_auditor.evaluation import (
    CaseResult,
    calculate_metrics,
    evaluate_case,
)
from tests.fakes import RepeatingAgentBackend
from tests.fixture_site import FixtureSite


async def test_seeded_cases_meet_all_thresholds(
    fixture_site: FixtureSite,
    tmp_path: Path,
) -> None:
    cases_file = Path(__file__).parent / "cases.json"
    cases = json.loads(cases_file.read_text("utf-8"))

    results: list[CaseResult] = []

    for case in cases:
        backend = RepeatingAgentBackend()
        exit_code, paths = await run_scan(
            ScanOptions(
                url=fixture_site.url(case["path"]),
                allow_private=True,
                artifact_root=tmp_path / case["name"],
            ),
            agents=backend,
        )

        report = json.loads(Path(paths.report_json).read_text("utf-8"))
        trace = json.loads(Path(paths.trace_json).read_text("utf-8"))

        case_res = evaluate_case(case, report, trace)
        results.append(case_res)

    metrics = calculate_metrics(results)

    assert metrics.routing_accuracy == 1.0
    assert metrics.finding_recall == 1.0
    assert metrics.grounded_claim_precision == 1.0
    assert metrics.pattern_recall == 1.0
    assert metrics.related_page_limit <= 5
    assert metrics.schema_validity == 1.0
