"""pytest 공용 픽스처: moto 기반 DynamoDB 단일 테이블 + API 이벤트 빌더."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# backend/ 를 import 루트로 추가 (shared, functions 패키지)
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TABLE_NAME = "CrewMate-test"


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("TABLE_NAME", TABLE_NAME)


@pytest.fixture
def table(_aws_env):
    """moto 목킹된 CrewMate 단일 테이블 (GSI1/GSI2 포함)."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ap-northeast-2")
        client.create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )

        # shared.db 모듈 캐시 리셋 (목킹된 리소스를 쓰도록)
        import shared.db as db

        db._resource = None
        db._table = None

        yield boto3.resource("dynamodb", region_name="ap-northeast-2").Table(TABLE_NAME)


def make_event(
    method: str,
    path: str,
    *,
    role: str,
    sub: str = "user-1",
    body: dict | None = None,
    office_id: str | None = None,
    company_id: str | None = None,
    path_params: dict | None = None,
) -> dict:
    """API Gateway REST 프록시 이벤트를 생성한다."""
    claims: dict = {"sub": sub, "custom:role": role}
    if office_id:
        claims["custom:office_id"] = office_id
    if company_id:
        claims["custom:company_id"] = company_id
    return {
        "httpMethod": method,
        "path": path,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_params,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def body_of(response: dict) -> dict:
    return json.loads(response["body"])
