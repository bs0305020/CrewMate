"""template.yaml 로컬 검증 (SAM CLI 없이).

CloudFormation 단축 태그(!Ref, !Sub, !GetAtt 등)를 무시하는 관대한 로더로
YAML 문법과 리소스/라우트 구조만 확인한다. 실제 배포 검증은 `sam validate` 사용.
"""

from __future__ import annotations

import sys

import yaml


class CfnLoader(yaml.SafeLoader):
    pass


def _any_tag(loader, tag_suffix, node):  # noqa: ANN001
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor("!", _any_tag)


def main() -> int:
    with open("template.yaml", encoding="utf-8") as f:
        doc = yaml.load(f, Loader=CfnLoader)

    resources = doc["Resources"]
    print("YAML OK. 리소스 수:", len(resources))
    routes: list[str] = []
    for name, res in resources.items():
        rtype = res["Type"]
        print(f"  - {name}: {rtype}")
        if rtype == "AWS::Serverless::Function":
            for ev in res["Properties"].get("Events", {}).values():
                if ev.get("Type") == "Api":
                    p = ev["Properties"]
                    routes.append(f"{p['Method'].upper():5} {p['Path']}")
    print("\nAPI 라우트:")
    for r in sorted(routes):
        print("  ", r)
    print("\n총 라우트:", len(routes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
