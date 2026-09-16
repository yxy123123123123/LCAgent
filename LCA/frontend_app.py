import contextlib
import io
import json
import os
import socket
import sys
from pathlib import Path

import streamlit as st


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
ENV_PATH = APP_DIR / ".env"
RESOURCE_PATH = PROJECT_ROOT / "RAG" / "资源库.txt"
HISTORY_PATH = PROJECT_ROOT / "RAG" / "memory" / "history.txt"

os.chdir(APP_DIR)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config
from Agent import DecisionAgent
from RAG_memory import build_vector_db as build_memory_db
from RAG_path import build_vector_db as build_path_db, get_all_vector_db_content


CONFIG_KEYS = [
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
]


def read_env_values():
    values = {}
    if not ENV_PATH.exists():
        return values
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_values(values):
    lines = [
        "# ===================== LCAgent model configuration =====================",
        "# Edit this file when switching model providers, model names, API keys, or base URLs.",
        "# This file is ignored by git.",
        "",
        "# Chat LLM, used by Agent.py and oneAgent.py.",
        f"LLM_API_KEY={values.get('LLM_API_KEY', '')}",
        f"LLM_BASE_URL={values.get('LLM_BASE_URL', '')}",
        f"LLM_MODEL={values.get('LLM_MODEL', '')}",
        "",
        "# Embedding model, used by RAG_path.py and RAG_memory.py.",
        f"EMBEDDING_API_KEY={values.get('EMBEDDING_API_KEY', '')}",
        f"EMBEDDING_BASE_URL={values.get('EMBEDDING_BASE_URL', '')}",
        f"EMBEDDING_MODEL={values.get('EMBEDDING_MODEL', '')}",
        "",
        "# Legacy names are still supported by config.py:",
        "# DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL",
        "# DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_EMBED_MODEL",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_secret(value):
    if not value:
        return "未配置"
    if value.startswith("your-"):
        return "占位值"
    if len(value) <= 8:
        return "已配置"
    return f"{value[:4]}...{value[-4:]}"


def run_with_log(func, *args, **kwargs):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            result = func(*args, **kwargs)
        return result, buffer.getvalue(), None
    except Exception as exc:
        return None, buffer.getvalue(), exc


def test_ue_connection(host="localhost", port=13377, timeout=3):
    command = {
        "type": "Status",
        "params": {
            "command_type": "Ping",
        },
    }
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(json.dumps(command).encode("utf-8"))
        try:
            data = sock.recv(4096)
        except socket.timeout:
            data = b""
    return data.decode("utf-8", errors="replace") if data else "连接成功，但未收到响应"


@st.cache_resource
def get_decision_agent():
    return DecisionAgent(project_directory=str(APP_DIR))


def render_status_cards(env_values):
    llm_key_status = mask_secret(env_values.get("LLM_API_KEY", ""))
    embedding_key_status = mask_secret(env_values.get("EMBEDDING_API_KEY", ""))

    cols = st.columns(4)
    cols[0].metric("聊天模型", env_values.get("LLM_MODEL") or config.LLM_MODEL)
    cols[1].metric("LLM Key", llm_key_status)
    cols[2].metric("向量模型", env_values.get("EMBEDDING_MODEL") or config.EMBEDDING_MODEL)
    cols[3].metric("Embedding Key", embedding_key_status)


def render_config_editor(env_values):
    with st.expander("模型配置", expanded=False):
        st.caption("保存后页面会重新加载，后续任务会使用新的模型配置。")
        with st.form("model_config_form"):
            updated = {}
            updated["LLM_API_KEY"] = st.text_input(
                "LLM_API_KEY",
                value=env_values.get("LLM_API_KEY", ""),
                type="password",
            )
            updated["LLM_BASE_URL"] = st.text_input(
                "LLM_BASE_URL",
                value=env_values.get("LLM_BASE_URL", "https://api.deepseek.com"),
            )
            updated["LLM_MODEL"] = st.text_input(
                "LLM_MODEL",
                value=env_values.get("LLM_MODEL", "deepseek-chat"),
            )
            st.divider()
            updated["EMBEDDING_API_KEY"] = st.text_input(
                "EMBEDDING_API_KEY",
                value=env_values.get("EMBEDDING_API_KEY", ""),
                type="password",
            )
            updated["EMBEDDING_BASE_URL"] = st.text_input(
                "EMBEDDING_BASE_URL",
                value=env_values.get(
                    "EMBEDDING_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            )
            updated["EMBEDDING_MODEL"] = st.text_input(
                "EMBEDDING_MODEL",
                value=env_values.get("EMBEDDING_MODEL", "text-embedding-v4"),
            )
            submitted = st.form_submit_button("保存配置", use_container_width=True)
        if submitted:
            write_env_values(updated)
            get_decision_agent.clear()
            st.success("配置已保存")
            st.rerun()


def render_task_runner():
    st.subheader("任务执行")
    task = st.text_area(
        "输入自然语言任务",
        height=120,
        placeholder="例如：帮我创建一个山地地形，材质使用 M4 材质",
    )
    run_clicked = st.button("执行任务", type="primary", use_container_width=True)

    if run_clicked:
        if not task.strip():
            st.warning("请先输入任务。")
            return
        agent = get_decision_agent()
        with st.spinner("Agent 正在执行任务..."):
            final_answer, logs, error = run_with_log(agent.run, task.strip())
        st.session_state["last_logs"] = logs
        st.session_state["last_answer"] = final_answer
        st.session_state["last_error"] = repr(error) if error else ""

    if st.session_state.get("last_error"):
        st.error(st.session_state["last_error"])
    if st.session_state.get("last_answer"):
        st.success(st.session_state["last_answer"])
    with st.expander("执行日志", expanded=True):
        st.code(st.session_state.get("last_logs", "暂无日志"), language="text")


def render_tools():
    st.subheader("工具")
    if st.button("测试 UE 连接", use_container_width=True):
        result, logs, error = run_with_log(test_ue_connection)
        if error:
            st.error(f"UE 连接失败：{error}")
        else:
            st.success(result)
        if logs:
            st.code(logs, language="text")

    if st.button("重建资源路径库", use_container_width=True):
        with st.spinner("正在重建 path_db..."):
            result, logs, error = run_with_log(build_path_db)
        if error:
            st.error(repr(error))
        else:
            st.success(result)
        if logs:
            st.code(logs, language="text")

    if st.button("重建记忆库", use_container_width=True):
        with st.spinner("正在重建 memory_db..."):
            result, logs, error = run_with_log(build_memory_db)
        if error:
            st.error(repr(error))
        else:
            st.success(result)
        if logs:
            st.code(logs, language="text")

    with st.expander("资源库"):
        if RESOURCE_PATH.exists():
            st.text_area(
                "RAG/资源库.txt",
                RESOURCE_PATH.read_text(encoding="utf-8"),
                height=220,
                disabled=True,
            )
        else:
            st.info("未找到资源库文件。")

    with st.expander("路径向量库内容"):
        if st.button("读取 path_db 内容", use_container_width=True):
            result, logs, error = run_with_log(get_all_vector_db_content)
            if error:
                st.error(repr(error))
            else:
                st.code(result, language="text")
            if logs:
                st.code(logs, language="text")

    with st.expander("最近记忆"):
        if HISTORY_PATH.exists():
            history = HISTORY_PATH.read_text(encoding="utf-8")
            st.text_area("RAG/memory/history.txt", history[-5000:], height=220, disabled=True)
        else:
            st.info("暂无记忆文件。")


def main():
    st.set_page_config(page_title="LCAgent 控制台", layout="wide")
    st.title("LCAgent 控制台")

    env_values = read_env_values()
    render_status_cards(env_values)
    render_config_editor(env_values)

    left, right = st.columns([2, 1], gap="large")
    with left:
        render_task_runner()
    with right:
        render_tools()


if __name__ == "__main__":
    main()
