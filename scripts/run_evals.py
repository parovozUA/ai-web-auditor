import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure root package is importable when running script directly
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tests.fakes import RepeatingAgentBackend  # noqa: E402
from tests.fixture_site import FixtureSite  # noqa: E402
from website_reliability_agent.cli import ScanOptions, run_scan  # noqa: E402
from website_reliability_agent.evaluation import (  # noqa: E402
    CaseResult,
    calculate_metrics,
    evaluate_case,
)


async def run_all_evals() -> int:
    cases_file = _repo_root / "tests" / "eval" / "cases.json"
    cases = json.loads(cases_file.read_text("utf-8"))

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    eval_dir = Path("artifacts") / "evals" / timestamp
    eval_dir.mkdir(parents=True, exist_ok=True)

    results: list[CaseResult] = []

    with FixtureSite() as site:
        for case in cases:
            backend = RepeatingAgentBackend()
            exit_code, paths = await run_scan(
                ScanOptions(
                    url=site.url(case["path"]),
                    allow_private=True,
                    artifact_root=eval_dir / case["name"],
                ),
                agents=backend,
            )

            report = json.loads(Path(paths.report_json).read_text("utf-8"))
            trace = json.loads(Path(paths.trace_json).read_text("utf-8"))

            case_res = evaluate_case(case, report, trace)
            results.append(case_res)
            print(
                f"Case '{case['name']}': "
                f"routing={case_res.routing_correct}, "
                f"schema={case_res.schema_valid}"
            )

    metrics = calculate_metrics(results)

    output = {
        "timestamp": timestamp,
        "metrics": metrics.model_dump(mode="json"),
        "cases": [r.model_dump(mode="json") for r in results],
    }

    metrics_file = eval_dir / "metrics.json"
    metrics_file.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print("\n--- Evaluation Summary ---")
    print(f"Metrics saved to: {metrics_file.resolve()}")
    print(f"  Routing Accuracy:          {metrics.routing_accuracy:.2f} (target: 1.00)")
    print(f"  Finding Recall:            {metrics.finding_recall:.2f} (target: 1.00)")
    print(f"  Grounded Claim Precision:  {metrics.grounded_claim_precision:.2f} (target: 1.00)")
    print(f"  Mocked Pattern Recall:     {metrics.pattern_recall:.2f} (target: 1.00)")
    print(f"  Max Related Pages Scanned: {metrics.related_page_limit} (target: <= 5)")
    print(f"  Schema Validity:           {metrics.schema_validity:.2f} (target: 1.00)")

    passed = (
        metrics.routing_accuracy == 1.0
        and metrics.finding_recall == 1.0
        and metrics.grounded_claim_precision == 1.0
        and metrics.pattern_recall == 1.0
        and metrics.related_page_limit <= 5
        and metrics.schema_validity == 1.0
    )

    return 0 if passed else 1


def main() -> None:
    exit_code = asyncio.run(run_all_evals())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
