"""seed_cognito.py (F-A1) 테스트 — moto Cognito로 데모 계정 생성 검증."""

from __future__ import annotations

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

_SEED_DIR = Path(__file__).resolve().parents[1] / "scripts" / "seed"
sys.path.insert(0, str(_SEED_DIR))

pytest.importorskip("boto3")


def _create_pool(client):
    pool = client.create_user_pool(
        PoolName="CrewMate-test",
        Schema=[
            {"Name": "role", "AttributeDataType": "String", "Mutable": True},
            {"Name": "office_id", "AttributeDataType": "String", "Mutable": True},
            {"Name": "company_id", "AttributeDataType": "String", "Mutable": True},
        ],
    )
    return pool["UserPool"]["Id"]


def test_seed_cognito_creates_three_accounts(_aws_env):
    import seed_cognito

    with mock_aws():
        client = boto3.client("cognito-idp", region_name="ap-northeast-2")
        pool_id = _create_pool(client)

        results = seed_cognito.seed_accounts(pool_id, "CrewMate2026!", "ap-northeast-2")
        assert len(results) == 3
        assert all(status == "created" for _, _, status in results)

        users = client.list_users(UserPoolId=pool_id)["Users"]
        assert len(users) == 3

        # 역할별 custom claim 확인
        by_name = {}
        for u in users:
            attrs = {a["Name"]: a["Value"] for a in u["Attributes"]}
            by_name[u["Username"]] = attrs

        assert by_name["office@crewmate.demo"]["custom:role"] == "OFFICE"
        assert by_name["office@crewmate.demo"]["custom:office_id"] == "OFFICE001"
        assert by_name["company@crewmate.demo"]["custom:company_id"] == "COMPANY001"
        assert by_name["worker@crewmate.demo"]["custom:role"] == "WORKER"


def test_seed_cognito_idempotent(_aws_env):
    import seed_cognito

    with mock_aws():
        client = boto3.client("cognito-idp", region_name="ap-northeast-2")
        pool_id = _create_pool(client)

        seed_cognito.seed_accounts(pool_id, "CrewMate2026!", "ap-northeast-2")
        # 재실행 시 이미 존재 → exists, 중복 생성 안 함
        results = seed_cognito.seed_accounts(pool_id, "CrewMate2026!", "ap-northeast-2")
        assert all(status == "exists" for _, _, status in results)
        assert len(client.list_users(UserPoolId=pool_id)["Users"]) == 3
