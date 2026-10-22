"""手工验证 UnrealMCP query_scene_state 的命令行工具。"""

from __future__ import annotations

import argparse
import json
from typing import Any

from terrain_client import DEFAULT_HOST, DEFAULT_PORT
from ue_protocol import UEProtocolClient
from ue_scene_client import query_scene_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取并规范化 UE Actor 场景状态")
    selectors = parser.add_mutually_exclusive_group(required=True)
    selectors.add_argument("--actor-name", help="UE 内部 Actor 名称，不是显示 Label")
    selectors.add_argument("--actor-guid", help="Actor GUID")
    selectors.add_argument("--class-name", help="Actor 类名或完整类路径")
    parser.add_argument(
        "--fields",
        nargs="+",
        default=["identity", "transform", "bounds", "tags"],
        help="要读取的字段投影",
    )
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--project-path")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    actor_filter: dict[str, Any]
    if args.actor_name:
        actor_filter = {"actor_name": args.actor_name}
    elif args.actor_guid:
        actor_filter = {"actor_guid": args.actor_guid}
    else:
        actor_filter = {"class_name": args.class_name}
    query = {
        "query_id": "query_manual_scene_smoke",
        "filter": actor_filter,
        "fields": args.fields,
        "page_size": args.page_size,
    }
    client = UEProtocolClient(
        host=args.host,
        port=args.port,
        project_path=args.project_path,
    )
    capabilities = client.handshake()
    snapshot = query_scene_snapshot(
        client,
        query,
        capabilities=capabilities,
    )
    print(json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
