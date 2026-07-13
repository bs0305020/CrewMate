"""CrewMate 백엔드 공용 모듈 (담당자 A).

이 패키지는 코어 Lambda 5종(worker_api, company_request, office_core,
assignment, notification)이 공유하는 유틸리티를 제공한다.

- state:      근로자/요청/작업조/결원 상태 enum 및 전이 규칙
- responses:  API Gateway 표준 응답 포맷터
- db:         DynamoDB 단일 테이블 접근 헬퍼 및 키 빌더
- auth:       Cognito JWT claim 추출 및 역할 권한 체크
- schemas:    엔터티 검증/직렬화 헬퍼
"""
