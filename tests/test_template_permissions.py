from pathlib import Path


def _resource_block(template: str, logical_id: str, next_logical_id: str) -> str:
    start = template.index(f"  {logical_id}:")
    end = template.index(f"  {next_logical_id}:", start)
    return template[start:end]


def test_office_core_can_read_related_company_and_office_records() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    block = _resource_block(template, "OfficeCoreFunction", "AssignmentFunction")

    assert "DynamoDBReadPolicy: { TableName: !Ref CompaniesTable }" in block
    assert "DynamoDBReadPolicy: { TableName: !Ref OfficesTable }" in block


def test_report_storage_moves_to_standard_ia_then_expires() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    bucket = _resource_block(template, "ReportOutputBucket", "ReportOutputBucketPolicy")

    assert "StorageClass: STANDARD_IA" in bucket
    assert "TransitionInDays: !Ref ReportStandardIaTransitionDays" in bucket
    assert "ExpirationInDays: !Ref ReportRetentionDays" in bucket
    assert "ObjectSizeGreaterThan: 0" in bucket
    assert "Default: 61" in template
    assert "StorageClass: GLACIER" not in bucket


def test_agent_lambdas_have_async_self_invoke_permissions() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    crew = _resource_block(template, "AgentInvokeFunction", "SpecReportAgentFunction")
    report = template[
        template.index("  SpecReportAgentFunction:"):template.index("\nOutputs:")
    ]

    assert "CREW_AGENT_ASYNC_ENABLED: \"true\"" in crew
    assert "lambda:InvokeFunction" in crew
    assert "lambda:InvokeFunction" in report
    assert "s3:GetObject" in report and "s3:PutObject" in report


def test_report_agent_has_bounded_background_timeout() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    assert 'ReportAgentTimeoutSeconds:\n    Type: String\n    Default: "60"' in template


def test_report_jobs_have_owner_history_index_and_query_permission() -> None:
    template = (Path(__file__).parents[1] / "template.yaml").read_text(encoding="utf-8")
    table = _resource_block(template, "SpecReportJobs", "AgentDepsLayer")
    report = template[
        template.index("  SpecReportAgentFunction:"):template.index("\nOutputs:")
    ]

    assert "OwnerCreatedAtIndex" in table
    assert "owner_user_id" in table and "created_at" in table
    assert "dynamodb:Query" in report
    assert "/reports/spec-gap/jobs, Method: get" in report
