"""seed_cognito.py (계약 v2).

Cognito User Pool에 데모 계정을 username 기반으로 생성하고, 대응하는 엔터티 레코드
(Office/Company/Worker)를 연결한다. custom claim(role/office_id/company_id/region) 설정 +
비밀번호 영구 확정. 배포(sam deploy) 후 출력된 UserPoolId 로 실행한다.

데모 계정 (프론트 mock 계정과 동일):
  worker1 / worker2 / worker3 (WORKER, OFFICE001), office1 (OFFICE, OFFICE001),
  company1 (COMPANY, COMPANY001) — 공통 비밀번호 demo1234.

사용법:
  python scripts/seed/seed_cognito.py --stack-name crewmate
  python scripts/seed/seed_cognito.py --user-pool-id ap-northeast-2_XXXX
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError

from common import OFFICES  # noqa: E402 (sys.path 설정)

from shared import db  # noqa: E402
from shared.schemas import build_worker, now_iso  # noqa: E402
from shared.state import WorkerState  # noqa: E402

DEFAULT_PASSWORD = "demo1234"

DEMO_ACCOUNTS = [
    {"username": "worker1", "role": "WORKER", "name": "김건우", "region": "부산 해운대구",
     "office_id": "OFFICE001", "preferred_trades": ["FORMWORK", "MASONRY"], "excluded_trades": ["MATERIAL_CARRY"],
     "career_years": 8, "age": 35, "wage": 180000, "state": "READY"},
    {"username": "worker2", "role": "WORKER", "name": "박철수", "region": "부산 사상구",
     "office_id": "OFFICE001", "preferred_trades": ["REBAR", "GENERAL"], "excluded_trades": [],
     "career_years": 5, "age": 29, "wage": 160000, "state": "READY"},
    {"username": "worker3", "role": "WORKER", "name": "이영희", "region": "부산 동래구",
     "office_id": "OFFICE001", "preferred_trades": ["GENERAL", "FORMWORK"], "excluded_trades": ["REBAR"],
     "career_years": 10, "age": 41, "wage": 150000, "state": "READY"},
    {"username": "office1", "role": "OFFICE", "name": "부산인력사무소", "region": "부산 해운대구",
     "office_id": "OFFICE001"},
    {"username": "company1", "role": "COMPANY", "name": "해운대건설", "region": "부산 해운대구",
     "company_id": "COMPANY001"},
]


def resolve_user_pool_id(stack_name: str, region: str | None) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    resp = cfn.describe_stacks(StackName=stack_name)
    for out in resp["Stacks"][0].get("Outputs", []):
        if out["OutputKey"] == "UserPoolId":
            return out["OutputValue"]
    raise SystemExit(f"스택 '{stack_name}' 출력에서 UserPoolId를 찾을 수 없습니다.")


def _attributes(acc: dict) -> list[dict[str, str]]:
    attrs = [
        {"Name": "name", "Value": acc["name"]},
        {"Name": "custom:role", "Value": acc["role"]},
        {"Name": "custom:region", "Value": acc.get("region", "")},
    ]
    if acc.get("office_id"):
        attrs.append({"Name": "custom:office_id", "Value": acc["office_id"]})
    if acc.get("company_id"):
        attrs.append({"Name": "custom:company_id", "Value": acc["company_id"]})
    return attrs


def _create_user(client, pool_id: str, acc: dict, password: str) -> str:
    """계정 생성(있으면 유지) + 비밀번호 영구 확정 + 그룹 등록. sub 반환."""
    username = acc["username"]
    try:
        resp = client.admin_create_user(
            UserPoolId=pool_id, Username=username,
            UserAttributes=_attributes(acc), MessageAction="SUPPRESS",
        )
        sub = next((a["Value"] for a in resp["User"]["Attributes"] if a["Name"] == "sub"), "")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "UsernameExistsException":
            raise
        got = client.admin_get_user(UserPoolId=pool_id, Username=username)
        sub = next((a["Value"] for a in got["UserAttributes"] if a["Name"] == "sub"), "")
    client.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=True)
    try:
        client.admin_add_user_to_group(UserPoolId=pool_id, Username=username, GroupName=acc["role"])
    except ClientError:
        pass
    return sub


def _link_entity(acc: dict, sub: str) -> None:
    """계정과 엔터티 레코드를 연결한다 (Worker 생성 / Office·Company owner_user_id 갱신)."""
    if acc["role"] == "WORKER":
        if not db.get_worker(sub):
            db.put_worker(build_worker(
                user_id=sub, worker_id=sub, name=acc["name"], phone="010-0000-0000",
                office_id=acc["office_id"], preferred_trades=acc["preferred_trades"],
                excluded_trades=acc["excluded_trades"],
                career_years=acc["career_years"], age=acc["age"], region=acc["region"],
                desired_daily_wage=acc["wage"], certifications=["안전교육이수"],
                state=acc.get("state", "INACTIVE"),
            ))
    elif acc["role"] == "OFFICE":
        office = db.get_office(acc["office_id"])
        if office:
            db.offices_table().update_item(
                Key={"office_id": acc["office_id"]},
                UpdateExpression="SET owner_user_id = :u",
                ExpressionAttributeValues={":u": sub},
            )
    elif acc["role"] == "COMPANY":
        company = db.get_company(acc["company_id"])
        if company:
            db.companies_table().update_item(
                Key={"company_id": acc["company_id"]},
                UpdateExpression="SET owner_user_id = :u",
                ExpressionAttributeValues={":u": sub},
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Cognito 데모 계정 + 엔터티 연결 시드")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-pool-id")
    group.add_argument("--stack-name")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    pool_id = args.user_pool_id or resolve_user_pool_id(args.stack_name, args.region)
    client = boto3.client("cognito-idp", region_name=args.region)

    print(f"[seed] Cognito 데모 계정 처리 (pool={pool_id})")
    for acc in DEMO_ACCOUNTS:
        sub = _create_user(client, pool_id, acc, args.password)
        _link_entity(acc, sub)
        print(f"  - {acc['role']:8} {acc['username']:9} sub={sub}")
    print(f"\n공통 비밀번호: {args.password}")


if __name__ == "__main__":
    sys.exit(main())
