"""地形创建命令行入口。

该文件可以由决策智能体直接执行；网络协议和错误处理统一放在
terrain_client.py 中，避免编码智能体重复生成不一致的 Socket 代码。
"""

from __future__ import annotations

import argparse
import json
import sys

from terrain_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RESPONSE_TIMEOUT,
    TerrainClientError,
    build_terrain_command,
    create_terrain,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用一个或多个 TIF 文件创建 UE 地形")
    parser.add_argument("--file-paths", required=True, nargs="+", help="TIF/TIFF 高程文件路径")
    parser.add_argument("--material-path", required=True, help="/Game/ 下的 UE 材质对象路径")
    parser.add_argument("--host", default=DEFAULT_HOST, help="UnrealMCP 主机地址")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="UnrealMCP 端口")
    parser.add_argument("--connect-timeout", default=DEFAULT_CONNECT_TIMEOUT, type=float)
    parser.add_argument("--response-timeout", default=DEFAULT_RESPONSE_TIMEOUT, type=float)
    parser.add_argument("--request-id", help="可选的请求标识")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验并打印最终 JSON，不连接 Unreal Engine",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # 离线模式用于自动评测编码智能体生成的参数和最终 JSON。
        if args.dry_run:
            command = build_terrain_command(
                args.file_paths,
                args.material_path,
                request_id=args.request_id,
            )
            print(json.dumps(command, ensure_ascii=False, indent=2))
            return 0

        response = create_terrain(
            args.file_paths,
            args.material_path,
            request_id=args.request_id,
            host=args.host,
            port=args.port,
            connect_timeout=args.connect_timeout,
            response_timeout=args.response_timeout,
        )
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0
    except TerrainClientError as exc:
        # 失败必须返回非零退出码，决策智能体不能再把失败误判为完成。
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
