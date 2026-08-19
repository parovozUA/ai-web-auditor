from website_reliability_agent.evaluation import CaseResult, calculate_metrics


def test_metrics_use_explicit_numerators_and_denominators() -> None:
    results = [
        CaseResult(
            name="clean",
            routing_correct=True,
            expected_fingerprints=0,
            detected_fingerprints=0,
            valid_claim_references=0,
            all_claim_references=0,
            expected_patterns=0,
            detected_patterns=0,
            related_pages_scanned=0,
            schema_valid=True,
        ),
        CaseResult(
            name="failing",
            routing_correct=True,
            expected_fingerprints=2,
            detected_fingerprints=2,
            valid_claim_references=3,
            all_claim_references=3,
            expected_patterns=1,
            detected_patterns=1,
            related_pages_scanned=2,
            schema_valid=True,
        ),
    ]

    metrics = calculate_metrics(results)

    assert metrics.routing_accuracy == 1.0
    assert metrics.finding_recall == 1.0
    assert metrics.grounded_claim_precision == 1.0
    assert metrics.pattern_recall == 1.0
    assert metrics.related_page_limit == 2
    assert metrics.schema_validity == 1.0
