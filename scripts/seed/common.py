"""시드 스크립트 공용 유틸 (F-A7).

- backend/shared 를 import 경로에 추가
- 사무소/건설사/직종/지역 상수
- 테이블 리셋(스캔 후 삭제) 및 배치 쓰기
- 협업(Collaboration) 아이템 빌더 (양방향 저장)

모든 스크립트는 seed = 42 로 고정하여 재현 가능한 합성 데이터를 만든다.
실데이터는 사용하지 않으며, 주민등록번호는 어떤 형태로도 생성하지 않는다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterable

# backend/ 를 import 루트로 추가 (shared 패키지)
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from shared.db import batch_put, get_table  # noqa: E402
from shared.schemas import now_iso, to_decimal  # noqa: E402
from shared.state import ItemType  # noqa: E402

SEED = 42

OFFICES = ["OFFICE001", "OFFICE002"]
COMPANIES = ["COMPANY001", "COMPANY002", "COMPANY003"]

TRADES = ["FORMWORK", "REBAR", "MASONRY", "MATERIAL_CARRY", "GENERAL"]
REGIONS = [
    "BUSAN_HAEUNDAE",
    "BUSAN_SASANG",
    "BUSAN_BUSANJIN",
    "BUSAN_DONGRAE",
    "BUSAN_GANGSEO",
]
CERTIFICATIONS = ["비계기능사", "거푸집기능사", "철근기능사", "건설안전기사"]


def reset_table() -> int:
    """테이블의 모든 아이템을 삭제한다 (데모 반복 리허설용 원클릭 리셋).

    반환값: 삭제한 아이템 수.
    """
    table = get_table()
    deleted = 0
    scan_kwargs: dict[str, Any] = {"ProjectionExpression": "PK, SK"}
    while True:
        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        with table.batch_writer() as batch:
            for it in items:
                batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
                deleted += 1
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return deleted


def write_items(items: Iterable[dict[str, Any]]) -> int:
    items = list(items)
    batch_put(items)
    return len(items)


def build_collaboration(worker_a: str, worker_b: str, date: str) -> list[dict[str, Any]]:
    """한 협업 건을 양방향 2개 아이템으로 생성한다 (공유 계약 1.4)."""
    def _one(owner: str, other: str) -> dict[str, Any]:
        return to_decimal(
            {
                "PK": f"WORKER#{owner}",
                "SK": f"COLLAB#{other}#{date}",
                "item_type": ItemType.COLLABORATION,
                "worker_id": owner,
                "other_id": other,
                "date": date,
                "created_at": now_iso(),
            }
        )

    return [_one(worker_a, worker_b), _one(worker_b, worker_a)]
