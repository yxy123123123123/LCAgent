import os
import shutil
import re  # 新增：用于路径过滤
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
VECTOR_DB_PATH = os.path.join(DOC_DIR, "path_db")

client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)


# ===================== 阿里云 1024 维嵌入（核心）=====================
def get_embedding(text):
    return client.embeddings.create(input=text, model=EMBEDDING_MODEL).data[0].embedding


# ===================== 构建：彻底清空旧库，强制使用阿里云向量 =====================
def build_vector_db():
    try:
        if os.path.exists(VECTOR_DB_PATH):
            shutil.rmtree(VECTOR_DB_PATH)

        # 加载文档
        docs = []
        txt_loader = DirectoryLoader(DOC_DIR, glob="*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"})
        docs.extend(txt_loader.load())
        pdf_loader = DirectoryLoader(DOC_DIR, glob="*.pdf", loader_cls=PyPDFLoader)
        docs.extend(pdf_loader.load())

        # 【关键调整】适配路径的分片器
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=200,  # 覆盖最长路径，避免拆分
            chunk_overlap=0,  # 路径无需重叠
            separators=["\n", "；", "，", " "],  # 优先按换行拆分单行路径
            keep_separator=False,
            strip_whitespace=True
        )
        splits = splitter.split_documents(docs)

        # 【关键调整】过滤仅保留有效路径分片（修改正则适配你的路径格式）
        def is_valid_path_chunk(content):
            # 新增：兼容3类路径
            # 1. Windows路径（正/反斜杠）：E:/UE_project/DEM/CS_01.tif 或 E:\UE_project\DEM\CS_01.tif
            # 2. UE虚拟路径：/Game/M4UE5Master1_Inst.M4UE5Master1_Inst
            path_pattern = r"(?:[A-Za-z]:[\\/].*\.\w+)|(?:/Game/.*\.\w+)"
            return re.search(path_pattern, content) is not None

        valid_splits = [
            s for s in splits
            if s.page_content.strip() and is_valid_path_chunk(s.page_content.strip())
        ]

        # 入库逻辑不变
        db = Chroma(persist_directory=VECTOR_DB_PATH)
        for i, doc in enumerate(valid_splits):
            vec = get_embedding(doc.page_content)
            db._collection.add(
                documents=[doc.page_content],
                embeddings=[vec],
                metadatas=[doc.metadata],
                ids=[str(i)]
            )

        return f"✅ 构建完成！有效路径分片数：{len(valid_splits)}"

    except Exception as e:
        return f"❌ 构建失败：{str(e)}"


# 检索函数不变（仅Agent中调整top_k）
def retrieve(query, top_k=3):
    try:
        query_vec = get_embedding(query)
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


# ===================== 新增：获取向量库所有内容 =====================
def get_all_vector_db_content():
    """获取并格式化输出向量库中的所有内容"""
    try:
        db = Chroma(persist_directory=VECTOR_DB_PATH)
        # 获取向量库中所有数据（包含文档、元数据、ID）
        all_data = db._collection.get()

        # 检查是否为空
        if not all_data["documents"]:
            return "📄 向量库中暂无任何内容"

        # 格式化输出内容
        output = "\n" + "=" * 60 + "\n"
        output += "📋 向量库完整内容列表\n"
        output += "=" * 60 + "\n"

        for idx, (doc_content, metadata, vec_id) in enumerate(
                zip(all_data["documents"], all_data["metadatas"], all_data["ids"])
        ):
            output += f"\n【条目 {idx + 1}】\n"
            output += f"ID：{vec_id}\n"
            output += f"路径内容：{doc_content.strip()}\n"
            output += f"元数据：{metadata}\n"
            output += "-" * 60 + "\n"

        output += f"\n📊 向量库总条目数：{len(all_data['documents'])}\n"
        return output

    except Exception as e:
        return f"❌ 获取向量库内容失败：{str(e)}"


if __name__ == "__main__":
    print("🔍 开始构建向量库...")
    build_result = build_vector_db()
    print(build_result)

    # 仅当构建成功时，输出向量库所有内容
    if "✅ 构建完成" in build_result:
        print(get_all_vector_db_content())
