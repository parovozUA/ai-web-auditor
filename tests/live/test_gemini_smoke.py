import json
import os
from pathlib import Path

import pytest

from tests.fixture_site import FixtureSite
from website_reliability_agent.agents import GeminiAgentBackend
from website_reliability_agent.cli import ScanOptions, run_scan
from website_reliability_agent.evaluation import evaluate_case

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"),
        reason="GEMINI_API_KEY is required for the live smoke test",
    ),
]


async def test_live_gemini_smoke_seo_case(
    fixture_site: FixtureSite,
    tmp_path: Path,
) -> None:
    backend = GeminiAgentBackend(model_name="gemini-3.5-flash")
    exit_code, paths = await run_scan(
        ScanOptions(
            url=fixture_site.url("/seo"),
            allow_private=True,
            artifact_root=tmp_path / "live_seo",
        ),
        agents=backend,
    )

    report = json.loads(Path(paths.report_json).read_text("utf-8"))
    trace = json.loads(Path(paths.trace_json).read_text("utf-8"))

    case_def = {
        "name": "seo_repeated",
        "path": "/seo",
        "expects_agent_path": True,
        "expected_seed_codes": ["title_missing", "meta_description_missing"],
        "expected_repeated_signatures": ["title_missing", "meta_description_missing"],
    }

    res = evaluate_case(case_def, report, trace)

    assert exit_code == 1
    assert res.schema_valid is True
    assert res.routing_correct is True
    assert res.related_pages_scanned <= 5
    assert res.valid_claim_references == res.all_claim_references
    pattern_recall = (
        res.detected_patterns / res.expected_patterns
        if res.expected_patterns > 0
        else 1.0
    )
    assert pattern_recall >= 0.80
