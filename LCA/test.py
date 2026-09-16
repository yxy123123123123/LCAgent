import os
import click

# 导入两个知识库的检索函数和路径
from RAG_path import retrieve as retrieve_path, VECTOR_DB_PATH as PATH_DB_PATH
from RAG_memory import retrieve as retrieve_memory, VECTOR_DB_PATH as MEMORY_DB_PATH


def query_knowledge_base(query_text: str, db_type: str = "all") -> str:
    """
    核心查询函数：检索指定类型的知识库
    :param query_text: 查询文本（用户输入的问题）
    :param db_type: 知识库类型，可选：path(路径库)、memory(记忆库)、all(全部)
    :return: 格式化的检索结果
    """
    result = []
    result.append(f"📝 查询关键词：{query_text}")
    result.append("=" * 60)

    # 1. 检索路径库
    if db_type in ["path", "all"]:
        result.append("\n【路径库检索结果】")
        if os.path.exists(PATH_DB_PATH):
            path_result = retrieve_path(query_text, top_k=3)
            result.append(path_result if path_result.strip() else "⚠️ 路径库中未检索到相关内容")
        else:
            result.append(f"❌ 路径库文件不存在：{PATH_DB_PATH}")

    # 2. 检索记忆库
    if db_type in ["memory", "all"]:
        result.append("\n【记忆库检索结果】")
        if os.path.exists(MEMORY_DB_PATH):
            memory_result = retrieve_memory(query_text, top_k=3)
            result.append(memory_result if memory_result.strip() else "⚠️ 记忆库中未检索到相关内容")
        else:
            result.append(f"❌ 记忆库文件不存在：{MEMORY_DB_PATH}")

    return "\n".join(result)


@click.command()
def main():
    """知识库独立查询工具（无大模型）"""
    print("=== 知识库查询工具 ===")
    print("支持的指令：")
    print("  1. 直接输入查询内容 → 同时查询路径库+记忆库")
    print("  2. 输入格式：路径库|查询内容 → 仅查询路径库")
    print("  3. 输入格式：记忆库|查询内容 → 仅查询记忆库")
    print("  4. 输入 退出/quit/exit → 退出工具")
    print("-" * 60)

    while True:
        user_input = input("\n请输入查询内容：").strip()
        if user_input in ["退出", "quit", "exit"]:
            print("👋 退出查询工具，再见！")
            break
        if not user_input:
            print("⚠️ 请输入有效的查询内容！")
            continue

        # 解析查询类型和查询文本
        if "|" in user_input:
            parts = user_input.split("|", 1)
            db_type = parts[0].strip()
            query_text = parts[1].strip()
            if db_type not in ["路径库", "记忆库"]:
                print("⚠️ 库类型只能是「路径库」或「记忆库」，默认查询全部")
                db_type = "all"
                query_text = user_input
            else:
                db_type = "path" if db_type == "路径库" else "memory"
        else:
            db_type = "all"
            query_text = user_input

        # 执行查询并输出结果
        try:
            result = query_knowledge_base(query_text, db_type)
            print("\n" + result)
        except Exception as e:
            print(f"❌ 查询出错：{str(e)}")
        print("-" * 60)


if __name__ == "__main__":
    main()