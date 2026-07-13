"""seed_cognito.py (F-A1).

Cognito User Pool에 데모 계정 3종(worker / office / company)을 생성한다.
custom claim(role, office_id, company_id)을 설정하고 비밀번호를 영구 확정한다.

배포(sam deploy) 후 출력된 UserPoolId 로 실행한다.

사용법:
  # 스택 이름으로 UserPoolId 자동 조회 (권장)
  python scripts/seed/seed_cognito.py --stack-name crewmate

  # UserPoolId 직접 지정
  python scripts/seed/seed_cognito.py --user-pool-id ap-northeast-2_XXXXXXXXX

옵션:
  --password  데모 계정 공통 비밀번호 (기본: CrewMate2026!)
  --region    AWS 리전 (기본: 환경변수/기본 프로파일)
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError

DEFAULT_PASSWORD = "CrewMate2026!"

# 데모 계정 3종 (username = email)
DEMO_ACCOUNTS = [
    {"username": "worker@crewmate.demo", "role": "WORKER"},
    {"username": "office@crewmate.demo", "role": "OFFICE", "office_id": "OFFICE001"},
    {"username": "company@crewmate.demo", "role": "COMPANY", "company_id": "COMPANY001"},
]


def resolve_user_pool_id(stack_name: str, region: str | None) -> str:
    """CloudFormation 스택 출력에서 UserPoolId를 조회한다."""
    cfn = boto3.client("cloudformation", region_name=region)
    resp = cfn.describe_stacks(StackName=stack_name)
    outputs = resp["Stacks"][0].get("Outputs", [])
    for out in outputs:
        if out["OutputKey"] == "UserPoolId":
            return out["OutputValue"]
    raise SystemExit(f"스택 '{stack_name}' 출력에서 UserPoolId를 찾을 수 없습니다.")


def _user_attributes(account: dict) -> list[dict[str, str]]:
    attrs = [
        {"Name": "email", "Value": account["username"]},
        {"Name": "email_verified", "Value": "true"},
        {"Name": "custom:role", "Value": account["role"]},
    ]
    if account.get("office_id"):
        attrs.append({"Name": "custom:office_id", "Value": account["office_id"]})
    if account.get("company_id"):
        attrs.append({"Name": "custom:company_id", "Value": account["company_id"]})
    return attrs


def create_account(client, user_pool_id: str, account: dict, password: str) -> str:
    """데모 계정 1종을 생성(또는 이미 있으면 유지)하고 비밀번호를 영구 확정한다."""
    username = account["username"]
    status = "created"
    try:
        client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=username,
            UserAttributes=_user_attributes(account),
            MessageAction="SUPPRESS",  # 초대 이메일 발송 안 함
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "UsernameExistsException":
            status = "exists"
        else:
            raise

    # 비밀번호 영구 확정 (FORCE_CHANGE_PASSWORD 상태 해제)
    client.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    return status


def seed_accounts(user_pool_id: str, password: str, region: str | None = None) -> list[tuple[str, str, str]]:
    client = boto3.client("cognito-idp", region_name=region)
    results = []
    for account in DEMO_ACCOUNTS:
        status = create_account(client, user_pool_id, account, password)
        results.append((account["username"], account["role"], status))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Cognito 데모 계정 3종 시드")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user-pool-id", help="Cognito User Pool ID")
    group.add_argument("--stack-name", help="SAM/CloudFormation 스택 이름 (UserPoolId 자동 조회)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="데모 계정 공통 비밀번호")
    parser.add_argument("--region", default=None, help="AWS 리전")
    args = parser.parse_args()

    user_pool_id = args.user_pool_id or resolve_user_pool_id(args.stack_name, args.region)
    results = seed_accounts(user_pool_id, args.password, args.region)

    print(f"[seed] Cognito 데모 계정 처리 완료 (pool={user_pool_id})")
    for username, role, status in results:
        print(f"  - {role:8} {username}  ({status})")
    print(f"\n비밀번호(공통): {args.password}")
    print("이 3계정으로 프론트에서 worker / office / company 로그인 데모가 가능합니다.")


if __name__ == "__main__":
    sys.exit(main())
