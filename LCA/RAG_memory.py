import os
import shutil
from datetime import datetime
from langchain_community.document_loaders import DirectoryLoader, TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from openai import OpenAI

# 统一配置（API 密钥等）
from config import EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

# ===================== 配置 =====================
# 使用脚本所在目录的上级目录来定位 RAG 资源（不再硬编码绝对路径）
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_DIR = os.path.join(os.path.dirname(_BASE_DIR), "RAG")
DOC_Memory = os.path.join(DOC_DIR, "memory")
# 改为指向 memory_db 文件夹
VECTOR_DB_PATH = os.path.join(DOC_DIR, "memory_db")
# 对话历史存储文件（仍放在RAG根目录，也可放到memory_db里）
CONVERSATION_FILE = os.path.join(DOC_Memory, "history.txt")

client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)


# ===================== 阿里云 1024 维嵌入（核心） =====================
def get_embedding(text):
    return client.embeddings.create(input=text, model=EMBEDDING_MODEL).data[0].embedding


# ===================== 构建：保留旧库，追加新文档到向量库 =====================
def build_vector_db():
    try:
        # 移除删除旧库的逻辑，保留原有向量数据
        # 加载文档（包含对话历史文件）
        docs = []
        txt_loader = DirectoryLoader(DOC_DIR, glob="*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"})
        docs.extend(txt_loader.load())

        # 切分
        splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=100)
        splits = splitter.split_documents(docs)
        valid_splits = [s for s in splits if s.page_content.strip()]

        # 初始化向量库（存在则复用，不存在则新建）
        db = Chroma(persist_directory=VECTOR_DB_PATH)

        # 获取现有向量库的最大ID，避免ID重复
        existing_ids = db._collection.get(include=[])["ids"]
        start_id = len(existing_ids) if existing_ids else 0

        # 逐条追加入库（1024维阿里云向量）
        added_count = 0
        for i, doc in enumerate(valid_splits):
            vec = get_embedding(doc.page_content)
            db._collection.add(
                documents=[doc.page_content],
                embeddings=[vec],
                metadatas=[doc.metadata],
                ids=[str(start_id + i)]
            )
            added_count += 1

        return f"✅ 构建完成！新增文档块：{added_count}，累计文档块：{start_id + added_count}"

    except Exception as e:
        return f"❌ 构建失败：{str(e)}"


# ===================== 检索：强制使用阿里云向量查询 =====================
def retrieve(query, top_k=3):
    try:
        # 用阿里云生成查询向量（1024维）
        query_vec = get_embedding(query)

        # 直接从 Chroma 底层查询，不经过任何默认模型
        db = Chroma(persist_directory=VECTOR_DB_PATH)
        results = db._collection.query(
            query_embeddings=[query_vec],
            n_results=top_k
        )

        output = ""
        for i, doc in enumerate(results['documents'][0]):
            output += f"【结果{i + 1}】\n{doc}\n\n"
        return output

    except Exception as e:
        return f"检索失败：{str(e)}"


# ===================== 新增：存储对话内容到RAG文档 =====================
def save_conversation_to_rag(user_input: str, final_answer: str):
    """
    将单次对话内容存储到RAG文档中，供后续向量库检索使用
    :param user_input: 用户输入的问题
    :param final_answer: Agent返回的最终答案
    :return: 存储结果提示
    """
    try:
        # 格式化对话内容（带时间戳，便于区分）
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conversation_content = f"""
==================== 对话记录 {timestamp} ====================
用户问题：{user_input}
助手回答：{final_answer}
=============================================================

"""
        # 追加写入到对话文件（不存在则创建）
        with open(CONVERSATION_FILE, "a", encoding="utf-8") as f:
            f.write(conversation_content)

        # 可选：写入后自动更新memory_db向量库（也可以手动调用build_vector_db）
        build_vector_db()

        return f"✅ 对话内容已存储到 {CONVERSATION_FILE}"

    except Exception as e:
        return f"❌ 对话存储失败：{str(e)}"


# ===================== 执行 =====================
if __name__ == "__main__":
    print("🔍 开始构建memory_db向量库（保留旧数据）...")
    print(build_vector_db())
