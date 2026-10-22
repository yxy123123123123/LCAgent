"""LCAgent 本地控制台。

提供双模型网关、工作区对话、知识库、UE 资源注册表和状态机任务入口。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import uuid
from pathlib import Path

import streamlit as st


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

os.chdir(APP_DIR)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from model_gateway import (  # noqa: E402
    ModelGateway,
    ModelGatewayError,
    ModelProfile,
    ModelProfileStore,
    mask_secret,
)
from context_compactor import ContextCompactor  # noqa: E402
from embedding_gateway import (  # noqa: E402
    EmbeddingGateway,
    EmbeddingProfile,
    EmbeddingProfileStore,
)
from knowledge_base import KnowledgeBaseError, KnowledgeBaseManager  # noqa: E402
from resource_registry import ResourceRegistry, ResourceRegistryError  # noqa: E402
from workspace_store import (  # noqa: E402
    Workspace,
    WorkspaceChatStore,
    WorkspaceError,
    WorkspaceStore,
    build_workspace_context,
)


PROVIDER_LABELS = {
    "deepseek": "DeepSeek（LangChain官方集成）",
    "openai": "OpenAI",
    "openai_compatible": "OpenAI 兼容接口",
}


def apply_compact_layout() -> None:
    """压缩工作台顶部区域，把更多垂直空间留给对话。"""

    st.markdown(
        """
        <style>
        /* 主内容区默认顶部留白较大，工作台界面只保留一小段呼吸空间。 */
        [data-testid="stAppViewBlockContainer"] {
            padding-top: 0.75rem;
            padding-bottom: 0.75rem;
        }
        .lc-main-title {
            font-size: 1.85rem;
            line-height: 1.15;
            font-weight: 650;
            margin: 0 0 0.35rem 0;
        }
        [data-testid="stMetric"] {
            padding: 0.1rem 0 0.25rem 0;
        }
        [data-testid="stMetricLabel"] p {
            font-size: 0.78rem;
            margin-bottom: 0;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.35rem;
            line-height: 1.2;
        }
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            margin-top: 0.1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def run_with_log(func, *args, **kwargs):
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            result = func(*args, **kwargs)
        return result, buffer.getvalue(), None
    except Exception as exc:  # UI 需要将真实错误交给用户，不能误报成功。
        return None, buffer.getvalue(), exc


def test_ue_connection(
    host: str = "127.0.0.1",
    port: int = 13377,
    timeout: float = 3.0,
    project_path: str | None = None,
):
    """按 UEBox 风格先做能力握手，旧版插件自动降级为只读探测。"""

    from ue_protocol import UEProtocolClient

    info = UEProtocolClient(
        host=host,
        port=port,
        project_path=project_path,
        connect_timeout=timeout,
        response_timeout=timeout,
    ).handshake()
    if info.legacy_fallback:
        return (
            "UE 连接正常，但当前插件使用旧协议："
            f"工程/关卡 {info.project_name or '未知'}。请重新编译插件以启用能力握手。"
        )
    return (
        "UE 连接正常："
        f"工程 {info.project_name or '未知'}，"
        f"契约 v{info.contract_version}，"
        f"能力 {len(info.capabilities)} 项"
    )


def choose_directory() -> str:
    """在本机弹出目录选择器；失败时 UI 仍可手工输入路径。"""

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择 LCAgent 工作区")
        root.destroy()
        return selected
    except Exception as exc:
        raise WorkspaceError(f"无法打开系统目录选择器：{exc}") from exc


@st.cache_resource
def get_decision_agent(
    workspace_path: str,
    workspace_id: str,
    profile_id: str,
    embedding_profile_id: str | None,
    dry_run: bool,
):
    # 状态机实例按工作区、双模型档案和执行模式隔离。
    from Agent import DecisionAgent

    return DecisionAgent(
        project_directory=workspace_path,
        workspace_id=workspace_id,
        model_profile_id=profile_id,
        embedding_profile_id=embedding_profile_id,
        dry_run=dry_run,
    )


def _profile_labels(profiles: list[ModelProfile]) -> dict[str, str]:
    return {
        profile.profile_id: f"{profile.display_name}  ·  {profile.model}"
        for profile in profiles
    }


def _embedding_profile_labels(
    profiles: list[EmbeddingProfile],
) -> dict[str, str]:
    return {
        profile.profile_id: f"{profile.display_name}  ·  {profile.model}"
        for profile in profiles
    }


def _workspace_system_messages(workspace: Workspace) -> list[dict[str, str]]:
    workspace_context = build_workspace_context(workspace.path)
    return [{
        "role": "system",
        "content": (
            "你是 LCAgent 的工作区对话助手。\n"
            f"当前工作区：{workspace.path}\n"
            "下面是系统读取的浅层文件清单，它只用于定位工程：\n"
            f"{workspace_context}\n\n"
            "不要声称已经读取未提供的文件内容，不要声称已修改文件或执行 UE 操作。"
        ),
    }]


def render_sidebar(
    profile_store: ModelProfileStore,
    embedding_store: EmbeddingProfileStore,
    workspace_store: WorkspaceStore,
) -> tuple[Workspace, str | None, str | None]:
    st.sidebar.title("LCAgent")
    st.sidebar.caption("工作区与模型控制台")

    try:
        initial_workspace = workspace_store.ensure_default(str(PROJECT_ROOT))
        workspaces = workspace_store.list()
    except WorkspaceError as exc:
        st.sidebar.error(str(exc))
        st.stop()

    workspace_labels = {
        workspace.workspace_id: f"{workspace.name}  ·  {workspace.path}"
        for workspace in workspaces
    }
    workspace_ids = list(workspace_labels)
    current_id = st.session_state.get("active_workspace_id", initial_workspace.workspace_id)
    if current_id not in workspace_ids:
        current_id = initial_workspace.workspace_id
    selected_workspace_id = st.sidebar.selectbox(
        "工作区",
        workspace_ids,
        index=workspace_ids.index(current_id),
        format_func=workspace_labels.get,
    )
    st.session_state["active_workspace_id"] = selected_workspace_id
    try:
        workspace = workspace_store.select(selected_workspace_id)
    except WorkspaceError as exc:
        st.sidebar.error(str(exc))
        st.stop()

    with st.sidebar.expander("添加工作区"):
        if st.button("浏览本机文件夹", width="stretch"):
            selected_path, _, error = run_with_log(choose_directory)
            if error:
                st.error(str(error))
            elif selected_path:
                try:
                    added = workspace_store.add(selected_path)
                    st.session_state["active_workspace_id"] = added.workspace_id
                    st.rerun()
                except WorkspaceError as exc:
                    st.error(str(exc))

        with st.form("manual_workspace_form"):
            workspace_path = st.text_input(
                "工作区路径",
                placeholder=r"E:\UE_project\LandscapeTool",
            )
            workspace_name = st.text_input("显示名称（可选）")
            add_workspace = st.form_submit_button("添加", width="stretch")
        if add_workspace:
            try:
                added = workspace_store.add(workspace_path, workspace_name or None)
                st.session_state["active_workspace_id"] = added.workspace_id
                st.rerun()
            except WorkspaceError as exc:
                st.error(str(exc))

    st.sidebar.divider()
    try:
        profiles = profile_store.list_profiles()
    except ModelGatewayError as exc:
        st.sidebar.error(str(exc))
        profiles = []

    selected_profile_id: str | None = None
    if profiles:
        labels = _profile_labels(profiles)
        profile_ids = list(labels)
        default_id = profile_store.default_profile_id()
        active_id = st.session_state.get("active_profile_id", default_id)
        if active_id not in profile_ids:
            active_id = profile_ids[0]
        selected_profile_id = st.sidebar.selectbox(
            "对话模型",
            profile_ids,
            index=profile_ids.index(active_id),
            format_func=labels.get,
        )
        st.session_state["active_profile_id"] = selected_profile_id
        selected_profile = next(item for item in profiles if item.profile_id == selected_profile_id)
        status = "密钥已配置" if profile_store.has_secret(selected_profile) else "缺少密钥"
        st.sidebar.caption(
            f"{PROVIDER_LABELS[selected_profile.provider]} · {status}"
        )
    else:
        st.sidebar.warning("请先在“模型”页添加一个模型。")

    try:
        embedding_profiles = embedding_store.list_profiles()
    except ModelGatewayError as exc:
        st.sidebar.error(str(exc))
        embedding_profiles = []
    selected_embedding_profile_id: str | None = None
    if embedding_profiles:
        embedding_labels = _embedding_profile_labels(embedding_profiles)
        embedding_ids = list(embedding_labels)
        default_embedding_id = embedding_store.default_profile_id()
        active_embedding_id = st.session_state.get(
            "active_embedding_profile_id", default_embedding_id
        )
        if active_embedding_id not in embedding_ids:
            active_embedding_id = embedding_ids[0]
        selected_embedding_profile_id = st.sidebar.selectbox(
            "Embedding 模型",
            embedding_ids,
            index=embedding_ids.index(active_embedding_id),
            format_func=embedding_labels.get,
        )
        st.session_state["active_embedding_profile_id"] = selected_embedding_profile_id
    else:
        st.sidebar.caption("尚未配置 Embedding 模型，知识库暂不可索引。")

    st.sidebar.divider()
    st.sidebar.caption(f"当前路径\n\n`{workspace.path}`")
    return workspace, selected_profile_id, selected_embedding_profile_id


def render_status_cards(
    workspace: Workspace,
    selected_profile_id: str | None,
    selected_embedding_profile_id: str | None,
    profile_store: ModelProfileStore,
    embedding_store: EmbeddingProfileStore,
) -> None:
    profile = profile_store.get(selected_profile_id) if selected_profile_id else None
    embedding = (
        embedding_store.get(selected_embedding_profile_id)
        if selected_embedding_profile_id
        else None
    )
    columns = st.columns(5)
    columns[0].metric("工作区", workspace.name)
    columns[1].metric("模型", profile.model if profile else "未配置")
    columns[2].metric("Embedding", embedding.model if embedding else "未配置")
    columns[3].metric("提供商", profile.provider if profile else "-")
    columns[4].metric(
        "API Key",
        "已配置" if profile and profile_store.has_secret(profile) else "未配置",
    )


def render_workspace_chat(
    workspace: Workspace,
    profile_id: str | None,
    chat_store: WorkspaceChatStore,
    profile_store: ModelProfileStore,
) -> None:
    st.subheader(workspace.name)
    st.caption(
        f"{workspace.path} · 当前对话只提供工作区浅层文件清单，"
        "不会自动修改文件或执行命令；完整原始历史始终保存在本地。"
    )

    messages = chat_store.get_messages(workspace.workspace_id)
    profile = profile_store.get(profile_id) if profile_id else None
    system_messages: list[dict[str, str]] = []
    context_compactor: ContextCompactor | None = None
    context_inspection = None
    try:
        system_messages = _workspace_system_messages(workspace)
        if profile:
            context_compactor = ContextCompactor(ModelGateway(profile_store), chat_store)
            context_inspection = context_compactor.inspect(
                workspace.workspace_id,
                system_messages,
                messages,
                profile,
            )
    except OSError as exc:
        st.warning(f"无法读取工作区上下文：{exc}")

    header_left, header_middle, header_right = st.columns([3, 3, 1])
    with header_left:
        with st.expander("工作区上下文"):
            try:
                st.code(build_workspace_context(workspace.path), language="text")
            except OSError as exc:
                st.error(f"无法读取工作区：{exc}")
    with header_middle:
        label = "上下文管理"
        if context_inspection:
            label = (
                f"上下文 {context_inspection.estimated_tokens:,} / "
                f"{context_inspection.trigger_tokens:,} Token"
            )
        with st.expander(label):
            if not profile or not context_compactor or not context_inspection:
                st.info("选择可用模型后才能估算和压缩上下文。")
            else:
                state = chat_store.get_context_state(workspace.workspace_id)
                st.caption(
                    f"模型窗口：{profile.context_window_tokens:,} Token · "
                    f"自动阈值：{profile.compression_trigger_ratio:.0%} · "
                    f"保留最近 {profile.keep_recent_turns} 轮"
                )
                st.caption(
                    f"原始消息：{len(messages)} 条 · "
                    f"快照已覆盖：{context_inspection.covered_message_count} 条"
                )
                if state.get("summary"):
                    st.json(state["summary"], expanded=False)
                else:
                    st.info("尚未生成压缩快照。达到阈值后会自动生成。")
                if state.get("last_error"):
                    st.warning(f"上次压缩失败：{state['last_error']}")
                if st.button(
                    "立即压缩较早对话",
                    key=f"compact_context_{workspace.workspace_id}",
                    width="stretch",
                    disabled=not messages,
                ):
                    with st.spinner("正在生成结构化上下文快照…"):
                        result = context_compactor.prepare(
                            workspace.workspace_id,
                            system_messages,
                            messages,
                            profile,
                            force=True,
                        )
                    if result.compressed_now:
                        st.success("上下文快照已更新，完整原始历史仍然保留。")
                        st.rerun()
                    elif result.compression_error:
                        st.error(result.compression_error)
                    else:
                        st.info("当前没有足够的较早消息可压缩。")
    with header_right:
        if st.button("清空对话", width="stretch"):
            chat_store.clear(workspace.workspace_id)
            st.rerun()

    pending_key = f"pending_chat_{workspace.workspace_id}"
    pending_request = st.session_state.get(pending_key)
    chat_history = st.container(
        height=500,
        border=True,
        key=f"chat_history_{workspace.workspace_id}",
        autoscroll=True,
    )
    with chat_history:
        # 对话历史只在此容器内部滚动，避免整张网页随着消息增长而变长。
        if not messages:
            with st.chat_message("assistant"):
                st.markdown("工作区已打开。你可以询问工程结构、设计方案或下一步开发计划。")
        for message in messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if pending_request:
            # 先重绘已保存的用户消息，再调用模型，输入框始终留在滚动区下方。
            pending_profile_id = str(pending_request["profile_id"])
            try:
                pending_profile = profile_store.get(pending_profile_id)
                if not system_messages:
                    system_messages = _workspace_system_messages(workspace)
                gateway = ModelGateway(profile_store)
                context_compactor = ContextCompactor(gateway, chat_store)
                with st.chat_message("assistant"):
                    with st.spinner("正在准备上下文并调用模型…"):
                        prepared = context_compactor.prepare(
                            workspace.workspace_id,
                            system_messages,
                            messages,
                            pending_profile,
                        )
                        reply = gateway.chat(
                            prepared.messages,
                            profile_id=pending_profile_id,
                        )
                    st.markdown(reply.content)
                    if reply.usage:
                        st.caption(f"用量：{dict(reply.usage)}")
                    if prepared.compressed_now:
                        st.caption(
                            f"已自动压缩较早对话；当前上下文约 "
                            f"{prepared.estimated_tokens:,} Token"
                        )
                    elif prepared.compression_error:
                        st.warning(
                            "自动压缩失败，已使用安全降级上下文继续回答："
                            f"{prepared.compression_error}"
                        )
                chat_store.append(
                    workspace.workspace_id,
                    "assistant",
                    reply.content,
                    pending_profile_id,
                )
                st.session_state.pop(pending_key, None)
                st.rerun()
            except (ModelGatewayError, OSError) as exc:
                st.session_state.pop(pending_key, None)
                pending_request = None
                with st.chat_message("assistant"):
                    st.error(str(exc))

    prompt = st.chat_input(
        "在当前工作区中对话…",
        disabled=profile_id is None or bool(pending_request),
    )
    if not prompt:
        return
    if not profile_id:
        st.warning("请先添加并选择模型。")
        return

    chat_store.append(workspace.workspace_id, "user", prompt, profile_id)
    st.session_state[pending_key] = {"profile_id": profile_id}
    st.rerun()


def _default_form_values(profile: ModelProfile | None) -> dict[str, object]:
    if profile:
        return {
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "provider": profile.provider,
            "model": profile.model,
            "base_url": profile.base_url,
            "temperature": profile.temperature,
            "timeout_seconds": profile.timeout_seconds,
            "max_retries": profile.max_retries,
            "thinking": profile.thinking,
            "reasoning_effort": profile.reasoning_effort,
            "api_key_env": profile.api_key_env,
            "context_window_tokens": profile.context_window_tokens,
            "compression_trigger_ratio": profile.compression_trigger_ratio,
            "keep_recent_turns": profile.keep_recent_turns,
        }
    return {
        "profile_id": "deepseek-main",
        "display_name": "DeepSeek 主模型",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "temperature": 0.0,
        "timeout_seconds": 120.0,
        "max_retries": 2,
        "thinking": False,
        "reasoning_effort": "high",
        "api_key_env": "MODEL_DEEPSEEK_MAIN_API_KEY",
        "context_window_tokens": 64000,
        "compression_trigger_ratio": 0.65,
        "keep_recent_turns": 6,
    }


def render_model_management(profile_store: ModelProfileStore) -> None:
    st.subheader("模型网关")
    st.caption(
        "可以添加多个模型档案。档案保存非敏感配置，API Key 单独保存在 "
        f"`{profile_store.env_path}`，这两个本地配置都不会提交到 Git。"
    )

    profiles = profile_store.list_profiles()
    choices = ["新建模型"] + [profile.profile_id for profile in profiles]
    labels = {"新建模型": "新建模型"}
    labels.update(_profile_labels(profiles))
    selected = st.selectbox("编辑档案", choices, format_func=labels.get)
    existing = next((item for item in profiles if item.profile_id == selected), None)
    values = _default_form_values(existing)
    editor_id = existing.profile_id if existing else "new"
    key_prefix = f"model_editor_{editor_id}"

    identity_left, identity_right = st.columns(2)
    with identity_left:
        profile_id = st.text_input(
            "Profile ID",
            value=str(values["profile_id"]),
            disabled=existing is not None,
            help="例如 deepseek-main；保存后不可修改。",
            key=f"{key_prefix}_profile_id",
        )
        display_name = st.text_input(
            "显示名称",
            value=str(values["display_name"]),
            key=f"{key_prefix}_display_name",
        )
        provider_keys = list(PROVIDER_LABELS)
        provider = st.selectbox(
            "提供商",
            provider_keys,
            index=provider_keys.index(str(values["provider"])),
            format_func=PROVIDER_LABELS.get,
            key=f"{key_prefix}_provider",
        )
        base_url = st.text_input(
            "Base URL",
            value=str(values["base_url"]),
            help="DeepSeek 例如 https://api.deepseek.com；本机服务允许使用 http。",
            key=f"{key_prefix}_base_url",
        )
    with identity_right:
        api_key = st.text_input(
            "API Key",
            value="",
            type="password",
            placeholder="留空表示保留已有密钥",
            key=f"{key_prefix}_api_key",
        )
        api_key_env = st.text_input(
            "API Key 环境变量",
            value=str(values["api_key_env"]),
            help="模型档案只保存这个变量名，不保存真实密钥。",
            key=f"{key_prefix}_api_key_env",
        )

    st.caption("点击获取后，API Key 只会发送到上面填写的 Base URL。")
    discovery_key = f"{key_prefix}_discovered_models"
    discovery_origin_key = f"{key_prefix}_discovery_origin"
    discovery_origin = (provider, base_url.strip().rstrip("/"))
    discovered_models = st.session_state.get(discovery_key, [])
    if st.session_state.get(discovery_origin_key) != discovery_origin:
        discovered_models = []

    if st.button(
        "自动获取模型列表",
        width="stretch",
        key=f"{key_prefix}_discover",
    ):
        effective_key = api_key.strip() or os.environ.get(api_key_env, "").strip()
        with st.spinner("正在读取服务端模型列表…"):
            try:
                discovered_models = ModelGateway.discover_models(
                    provider,
                    base_url,
                    effective_key,
                    timeout_seconds=30.0,
                )
                st.session_state[discovery_key] = discovered_models
                st.session_state[discovery_origin_key] = discovery_origin
                st.success(f"已获取 {len(discovered_models)} 个模型")
            except ModelGatewayError as exc:
                st.session_state.pop(discovery_key, None)
                st.session_state.pop(discovery_origin_key, None)
                discovered_models = []
                st.warning(f"{exc}。你仍可在下方手工填写模型名称。")

    current_model = str(values["model"])
    if discovered_models:
        model_options = list(discovered_models)
        if current_model and current_model not in model_options:
            model_options.insert(0, current_model)
        manual_model = st.checkbox(
            "模型不在列表中，手工填写",
            value=False,
            key=f"{key_prefix}_manual_model_enabled",
        )
        if manual_model:
            model = st.text_input(
                "模型名称",
                value=current_model,
                key=f"{key_prefix}_manual_model",
            )
        else:
            model = st.selectbox(
                "模型名称",
                model_options,
                index=model_options.index(current_model) if current_model in model_options else 0,
                key=f"{key_prefix}_model_select",
            )
    else:
        model = st.text_input(
            "模型名称",
            value=current_model,
            help="如果服务端不支持 /models，可直接填写厂商文档中的模型 ID。",
            key=f"{key_prefix}_model_text",
        )

    st.markdown("##### 调用参数")
    settings_left, settings_right = st.columns(2)
    with settings_left:
        temperature = st.number_input(
            "Temperature", min_value=0.0, max_value=2.0,
            value=float(values["temperature"]), step=0.1,
            key=f"{key_prefix}_temperature",
        )
        timeout_seconds = st.number_input(
            "超时秒数", min_value=1.0,
            value=float(values["timeout_seconds"]), step=10.0,
            key=f"{key_prefix}_timeout",
        )
        max_retries = st.number_input(
            "重试次数", min_value=0, max_value=10,
            value=int(values["max_retries"]), step=1,
            key=f"{key_prefix}_retries",
        )
    with settings_right:
        thinking = st.checkbox(
            "DeepSeek 思考模式",
            value=bool(values["thinking"]),
            key=f"{key_prefix}_thinking",
        )
        effort_options = ["low", "high", "max"]
        reasoning_effort = st.selectbox(
            "思考强度",
            effort_options,
            index=effort_options.index(str(values["reasoning_effort"])),
            disabled=not thinking,
            key=f"{key_prefix}_reasoning_effort",
        )
        set_default = st.checkbox(
            "设为默认模型",
            value=existing is None or profile_store.default_profile_id() == profile_id,
            key=f"{key_prefix}_set_default",
        )

    st.markdown("##### 上下文管理")
    context_left, context_middle, context_right = st.columns(3)
    with context_left:
        context_window_tokens = st.number_input(
            "上下文窗口（Token）",
            min_value=4096,
            max_value=2_000_000,
            value=int(values["context_window_tokens"]),
            step=4096,
            key=f"{key_prefix}_context_window",
            help="填写模型服务公布的上下文窗口；不确定时可保留 64000。",
        )
    with context_middle:
        compression_trigger_percent = st.number_input(
            "自动压缩阈值（%）",
            min_value=20,
            max_value=90,
            value=int(round(float(values["compression_trigger_ratio"]) * 100)),
            step=5,
            key=f"{key_prefix}_compression_ratio",
        )
    with context_right:
        keep_recent_turns = st.number_input(
            "保留最近对话轮数",
            min_value=1,
            max_value=50,
            value=int(values["keep_recent_turns"]),
            step=1,
            key=f"{key_prefix}_keep_recent_turns",
        )
    save_clicked = st.button(
        "保存模型",
        type="primary",
        width="stretch",
        key=f"{key_prefix}_save",
    )

    if save_clicked:
        try:
            profile = ModelProfile(
                profile_id=profile_id,
                display_name=display_name,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                max_retries=int(max_retries),
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                context_window_tokens=int(context_window_tokens),
                compression_trigger_ratio=float(compression_trigger_percent) / 100.0,
                keep_recent_turns=int(keep_recent_turns),
            )
            if not api_key and not profile_store.has_secret(profile):
                raise ModelGatewayError("新模型必须填写 API Key")
            profile_store.upsert(
                profile,
                api_key=api_key or None,
                set_default=set_default,
            )
            st.session_state["active_profile_id"] = profile.profile_id
            get_decision_agent.clear()
            st.success("模型配置已保存")
            st.rerun()
        except ModelGatewayError as exc:
            st.error(str(exc))

    if existing:
        status_columns = st.columns([2, 2, 1])
        api_key_value = os.environ.get(existing.api_key_env, "")
        status_columns[0].info(f"密钥：{mask_secret(api_key_value)}")
        if status_columns[1].button("测试连接", width="stretch"):
            with st.spinner("正在调用模型…"):
                try:
                    reply = ModelGateway(profile_store).test_connection(existing.profile_id)
                    st.success(
                        f"连接成功：{reply.content}  "
                        f"`{reply.provider}/{reply.model}`"
                    )
                except ModelGatewayError as exc:
                    st.error(str(exc))
        confirm_delete = status_columns[2].checkbox("确认删除", key=f"delete_{existing.profile_id}")
        if st.button(
            "删除当前档案",
            disabled=not confirm_delete,
            width="stretch",
        ):
            profile_store.delete(existing.profile_id, delete_secret=True)
            st.session_state.pop("active_profile_id", None)
            get_decision_agent.clear()
            st.rerun()

    if profiles:
        st.divider()
        st.markdown("#### 已配置模型")
        default_id = profile_store.default_profile_id()
        rows = []
        for profile in profiles:
            rows.append(
                {
                    "默认": "是" if profile.profile_id == default_id else "",
                    "Profile ID": profile.profile_id,
                    "名称": profile.display_name,
                    "提供商": profile.provider,
                    "模型": profile.model,
                    "Base URL": profile.base_url,
                    "上下文窗口": f"{profile.context_window_tokens:,}",
                    "压缩阈值": f"{profile.compression_trigger_ratio:.0%}",
                    "密钥": "已配置" if profile_store.has_secret(profile) else "缺失",
                }
            )
        st.dataframe(rows, width="stretch", hide_index=True)


def render_embedding_management(embedding_store: EmbeddingProfileStore) -> None:
    st.divider()
    st.subheader("Embedding 模型")
    st.caption("Embedding 只负责文档和查询向量，不参与任务规划或代码生成。")
    profiles = embedding_store.list_profiles()
    choices = ["新建 Embedding"] + [item.profile_id for item in profiles]
    labels = {"新建 Embedding": "新建 Embedding"}
    labels.update(_embedding_profile_labels(profiles))
    selected = st.selectbox("编辑 Embedding 档案", choices, format_func=labels.get)
    existing = next((item for item in profiles if item.profile_id == selected), None)
    prefix = f"embedding_{existing.profile_id if existing else 'new'}"

    left, right = st.columns(2)
    with left:
        profile_id = st.text_input(
            "Embedding Profile ID",
            value=existing.profile_id if existing else "embedding-main",
            disabled=existing is not None,
            key=f"{prefix}_id",
        )
        display_name = st.text_input(
            "显示名称",
            value=existing.display_name if existing else "主 Embedding 模型",
            key=f"{prefix}_name",
        )
        provider_options = ["openai", "openai_compatible"]
        provider = st.selectbox(
            "提供商",
            provider_options,
            index=provider_options.index(existing.provider) if existing else 0,
            key=f"{prefix}_provider",
        )
        manual_model = st.text_input(
            "手工模型名称",
            value=existing.model if existing else "text-embedding-3-small",
            key=f"{prefix}_manual_model",
        )
    with right:
        base_url = st.text_input(
            "Base URL",
            value=existing.base_url if existing else "https://api.openai.com/v1",
            key=f"{prefix}_url",
        )
        api_key_env = st.text_input(
            "API Key 环境变量",
            value=existing.api_key_env if existing else "EMBEDDING_MAIN_API_KEY",
            key=f"{prefix}_key_env",
        )
        api_key = st.text_input(
            "API Key",
            type="password",
            placeholder="留空表示保留已有密钥",
            key=f"{prefix}_key",
        )
        dimensions_value = st.number_input(
            "指定向量维度（0 表示由模型决定）",
            min_value=0,
            value=existing.dimensions if existing and existing.dimensions else 0,
            step=1,
            key=f"{prefix}_dimensions",
        )
        batch_size = st.number_input(
            "批大小",
            min_value=1,
            max_value=2048,
            value=existing.batch_size if existing else 32,
            step=1,
            key=f"{prefix}_batch",
        )
    discovery_key = f"{prefix}_discovered_models"
    discovery_origin_key = f"{prefix}_discovery_origin"
    discovery_origin = (provider, base_url.strip().rstrip("/"))
    discovered_models = st.session_state.get(discovery_key, [])
    if st.session_state.get(discovery_origin_key) != discovery_origin:
        discovered_models = []
    if st.button("自动获取 Embedding 服务模型列表", width="stretch", key=f"{prefix}_discover"):
        effective_key = api_key.strip() or os.environ.get(api_key_env, "").strip()
        with st.spinner("正在读取服务端 /models…"):
            try:
                discovered_models = ModelGateway.discover_models(
                    provider,
                    base_url,
                    effective_key,
                )
                st.session_state[discovery_key] = discovered_models
                st.session_state[discovery_origin_key] = discovery_origin
                st.success(f"已获取 {len(discovered_models)} 个模型，请选择 Embedding 模型。")
            except ModelGatewayError as exc:
                st.warning(f"{exc}。你仍可使用手工模型名称。")
                discovered_models = []
    if discovered_models:
        options = list(discovered_models)
        if manual_model and manual_model not in options:
            options.insert(0, manual_model)
        use_manual = st.checkbox("列表中没有目标模型，使用手工名称", key=f"{prefix}_use_manual")
        model = manual_model if use_manual else st.selectbox(
            "Embedding 模型名称",
            options,
            index=options.index(manual_model) if manual_model in options else 0,
            key=f"{prefix}_model_select",
        )
    else:
        model = manual_model
    set_default = st.checkbox(
        "设为默认 Embedding",
        value=existing is None or embedding_store.default_profile_id() == profile_id,
        key=f"{prefix}_default",
    )
    if st.button("保存 Embedding", type="primary", width="stretch", key=f"{prefix}_save"):
        try:
            profile = EmbeddingProfile(
                profile_id=profile_id,
                display_name=display_name,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                dimensions=int(dimensions_value) or None,
                batch_size=int(batch_size),
            )
            if not api_key and not embedding_store.has_secret(profile):
                raise ModelGatewayError("新 Embedding 模型必须填写 API Key")
            embedding_store.upsert(
                profile,
                api_key=api_key or None,
                set_default=set_default,
            )
            st.session_state["active_embedding_profile_id"] = profile.profile_id
            get_decision_agent.clear()
            st.success("Embedding 配置已保存")
            st.rerun()
        except ModelGatewayError as exc:
            st.error(str(exc))

    if existing:
        test_col, delete_col = st.columns([3, 1])
        if test_col.button("测试 Embedding", width="stretch", key=f"{prefix}_test"):
            with st.spinner("正在生成测试向量…"):
                try:
                    vector = EmbeddingGateway(embedding_store).embed_query(
                        "LCAgent 连接测试",
                        profile_id=existing.profile_id,
                    )
                    st.success(f"连接成功，返回 {len(vector)} 维向量")
                except ModelGatewayError as exc:
                    st.error(str(exc))
        confirm_delete = delete_col.checkbox("确认删除", key=f"{prefix}_confirm_delete")
        if st.button(
            "删除 Embedding 档案",
            disabled=not confirm_delete,
            width="stretch",
            key=f"{prefix}_delete",
        ):
            embedding_store.delete(existing.profile_id, delete_secret=True)
            st.session_state.pop("active_embedding_profile_id", None)
            get_decision_agent.clear()
            st.rerun()

    if profiles:
        st.dataframe(
            [
                {
                    "默认": "是" if item.profile_id == embedding_store.default_profile_id() else "",
                    "Profile ID": item.profile_id,
                    "名称": item.display_name,
                    "模型": item.model,
                    "维度": item.dimensions or "模型默认",
                    "密钥": "已配置" if embedding_store.has_secret(item) else "缺失",
                }
                for item in profiles
            ],
            hide_index=True,
            width="stretch",
        )


def render_knowledge_base(
    workspace: Workspace,
    embedding_profile_id: str | None,
    embedding_store: EmbeddingProfileStore,
) -> None:
    st.subheader("文档知识库")
    st.caption("按工作区保存 PDF、DOCX、TXT、MD 的原文、切片、向量和引用信息。")
    manager = KnowledgeBaseManager(
        workspace.workspace_id,
        embedding_gateway=EmbeddingGateway(embedding_store),
    )
    bases = manager.list_bases()
    with st.form(f"create_kb_{workspace.workspace_id}"):
        kb_name = st.text_input("新知识库名称")
        kb_description = st.text_input("说明（可选）")
        create_clicked = st.form_submit_button("创建知识库")
    if create_clicked:
        try:
            manager.create_base(kb_name, kb_description)
            st.success("知识库已创建")
            st.rerun()
        except KnowledgeBaseError as exc:
            st.error(str(exc))
    if not bases:
        st.info("先创建一个知识库，再上传文档。")
        return

    base_labels = {item["id"]: f"{item['name']} · {item['document_count']} 个文档" for item in bases}
    kb_id = st.selectbox("当前知识库", list(base_labels), format_func=base_labels.get)
    uploaded_files = st.file_uploader(
        "上传文档",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
        disabled=embedding_profile_id is None,
    )
    if not embedding_profile_id:
        st.warning("请先配置并选择 Embedding 模型。")
    if st.button(
        "解析、切片并建立索引",
        type="primary",
        width="stretch",
        disabled=not uploaded_files or embedding_profile_id is None,
    ):
        results = []
        for uploaded in uploaded_files or []:
            try:
                results.append(
                    manager.ingest_bytes(
                        kb_id,
                        uploaded.name,
                        uploaded.getvalue(),
                        embedding_profile_id=embedding_profile_id,
                    )
                )
            except (KnowledgeBaseError, ModelGatewayError) as exc:
                st.error(f"{uploaded.name}：{exc}")
        if results:
            st.success(f"已完成 {len(results)} 个文档，共 {sum(item['chunk_count'] for item in results)} 个切片")
            st.rerun()

    documents = manager.list_documents(kb_id)
    if documents:
        st.dataframe(
            [
                {
                    "文件": item["filename"],
                    "状态": item["status"],
                    "切片数": item["chunk_count"],
                    "Embedding": item["embedding_profile_id"],
                    "入库时间": item["created_at"],
                }
                for item in documents
            ],
            hide_index=True,
            width="stretch",
        )

    query = st.text_input("测试检索", placeholder="例如：道路坡度应满足什么要求？")
    if st.button(
        "检索知识库",
        disabled=not query.strip() or embedding_profile_id is None,
        width="stretch",
    ):
        try:
            results = manager.search(
                query,
                kb_ids=[kb_id],
                embedding_profile_id=embedding_profile_id,
            )
            if not results:
                st.info("没有检索到使用当前 Embedding 档案建立的切片。")
            for item in results:
                with st.expander(f"{item['citation']} · {item['score']:.3f}"):
                    st.write(item["text"])
        except (KnowledgeBaseError, ModelGatewayError) as exc:
            st.error(str(exc))


def render_resource_registry(
    workspace: Workspace,
    profile_id: str | None,
    profile_store: ModelProfileStore,
    chat_store: WorkspaceChatStore,
) -> None:
    st.subheader("UE 资源注册表")
    st.caption(
        "UE 提供资产级权威路径；决策模型按文件夹层级分析用途；"
        "用户只审核整份报告，不再逐个确认资产。"
    )
    from asset_registry_client import scan_project_assets
    from resource_folder_report import (
        ResourceFolderReportStore,
        build_resource_folder_report,
        render_report_markdown,
    )

    registry = ResourceRegistry(workspace.workspace_id)
    report_store = ResourceFolderReportStore(workspace.workspace_id, workspace.path)
    stats = registry.stats()
    draft = report_store.load_draft()
    approved = report_store.load_approved()
    stat_columns = st.columns(4)
    for column, key, label in zip(
        stat_columns,
        ["total", "confirmed", "pending", "missing"],
        ["资产总数", "报告已覆盖", "待报告覆盖", "已失效"],
    ):
        column.metric(label, stats[key])

    st.markdown("#### 扫描与更新")
    st.info(
        "首次点击“读取 UE 并生成报告”；以后在 UE 中添加、删除或移动资产后，"
        "点击“更新注册表”即可重新扫描并生成变化报告。"
    )
    with st.expander("扫描设置", expanded=approved is None):
        roots_text = st.text_input("包路径", value="/Game", help="多个路径用逗号分隔")
        max_results = st.number_input("最多返回", min_value=1, max_value=50000, value=50000)
        batch_size = st.number_input(
            "每次交给模型的文件夹数量",
            min_value=5,
            max_value=100,
            value=40,
            help="文件夹较多时会分批调用同一个决策模型。",
        )

    scan_label = "读取 UE 并生成报告" if approved is None else "更新注册表"
    if st.button(
        scan_label,
        type="primary",
        width="stretch",
        disabled=profile_id is None,
    ):
        roots = [
            item.strip()
            for item in roots_text.replace("，", ",").split(",")
            if item.strip()
        ]
        with st.spinner("正在读取 UE、构建文件夹树并分层调用决策模型…"):
            try:
                scan_result = scan_project_assets(roots, max_results=int(max_results))
                if scan_result.get("truncated"):
                    raise ResourceRegistryError(
                        "扫描结果达到上限，报告会不完整；请提高上限或缩小包路径后重试"
                    )
                ingest_result = registry.ingest_scan(scan_result["assets"])
                generated = build_resource_folder_report(
                    scan_result["assets"],
                    workspace_id=workspace.workspace_id,
                    workspace_path=workspace.path,
                    scan_revision=ingest_result["scan_revision"],
                    model_gateway=ModelGateway(profile_store),
                    profile_id=profile_id,
                    previous_report=approved,
                    batch_size=int(batch_size),
                )
                generated["requested_batch_size"] = int(batch_size)
                report_store.save_draft(generated)
                st.success(
                    f"已扫描 {generated['asset_count']} 个资产、分析 "
                    f"{generated['folder_count']} 个文件夹，请在下方审核报告。"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"注册表报告生成失败：{exc}")

    with st.expander("导入扫描 JSON"):
        st.caption("用于离线联调；导入后同样按文件夹生成一份完整审核报告。")
        scan_file = st.file_uploader("扫描 JSON", type=["json"], key="asset_scan_json")
        if st.button(
            "导入 JSON 并生成报告",
            disabled=scan_file is None or profile_id is None,
            width="stretch",
        ):
            try:
                document = json.loads(scan_file.getvalue().decode("utf-8-sig"))
                records = document.get("assets") if isinstance(document, dict) else document
                if not isinstance(records, list):
                    raise ResourceRegistryError("JSON 必须是数组或包含 assets 数组")
                ingest_result = registry.ingest_scan(records)
                generated = build_resource_folder_report(
                    records,
                    workspace_id=workspace.workspace_id,
                    workspace_path=workspace.path,
                    scan_revision=ingest_result["scan_revision"],
                    model_gateway=ModelGateway(profile_store),
                    profile_id=profile_id,
                    previous_report=approved,
                    batch_size=int(batch_size),
                )
                report_store.save_draft(generated)
                st.success(f"已导入 {len(records)} 条资源并生成文件夹报告")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    draft = report_store.load_draft()
    if draft:
        report_markdown = render_report_markdown(draft)
        st.markdown("#### 文件夹注册报告")
        status_label = "已批准" if draft.get("status") == "approved" else "等待审核"
        st.caption(
            f"状态：{status_label} · 资产 {draft.get('asset_count', 0)} · "
            f"文件夹 {draft.get('folder_count', 0)}"
        )
        with st.container(height=620, border=True):
            st.markdown(report_markdown)
        st.download_button(
            "下载报告 Markdown",
            data=report_markdown,
            file_name="resource_registry_report.md",
            mime="text/markdown",
            width="stretch",
        )

    if draft and draft.get("status") != "approved":
        review_feedback = st.text_area(
            "审核意见（可选）",
            placeholder="例如：/Game/EnvModels 主要用于环境静态模型，请据此修订。",
        )
        review_left, review_right = st.columns(2)
        if review_left.button(
            "根据审核意见重新生成报告",
            disabled=not review_feedback.strip() or profile_id is None,
            width="stretch",
        ):
            current_assets = registry.list_resources(include_missing=False)
            with st.spinner("正在根据审核意见重新分类文件夹…"):
                try:
                    regenerated = build_resource_folder_report(
                        current_assets,
                        workspace_id=workspace.workspace_id,
                        workspace_path=workspace.path,
                        scan_revision=str(draft.get("scan_revision") or "review-revision"),
                        model_gateway=ModelGateway(profile_store),
                        profile_id=profile_id,
                        previous_report=approved,
                        review_feedback=review_feedback,
                        batch_size=int(batch_size),
                    )
                    report_store.save_draft(regenerated)
                    st.success("报告已根据审核意见重新生成")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        approve_confirmed = review_right.checkbox("我已审核整份报告，确认分类可用")
        if st.button(
            "批准报告并建立注册表",
            type="primary",
            disabled=not approve_confirmed,
            width="stretch",
        ):
            with st.spinner("正在保存报告、应用文件夹分类并写入对话上下文…"):
                try:
                    apply_result = registry.apply_folder_report(draft)
                    paths = report_store.approve(draft)
                    chat_store.append(
                        workspace.workspace_id,
                        "assistant",
                        (
                            "已审核并批准当前 UE 工程资源文件夹注册报告。"
                            "后续任务可使用报告中已确认的目录用途和资产路径。\n\n"
                            + report_markdown
                        ),
                        profile_id or "resource-registry",
                    )
                    get_decision_agent.clear()
                    st.success(
                        f"报告已批准：覆盖 {apply_result['confirmed']} 个资产；"
                        f"工程报告保存到 {paths['project_markdown']}"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    resources = registry.list_resources()
    if not resources:
        st.info("尚无资源，请打开 UE 后生成第一份文件夹注册报告。")
        return
    with st.expander("查看资产级权威路径（无需逐个审核）"):
        st.caption(f"共 {len(resources)} 条；为保证页面性能，表格最多显示前 500 条。")
        st.dataframe(
            [
                {
                    "名称": item["asset_name"],
                    "类型": item["asset_class"],
                    "对象路径": item["object_path"],
                    "继承用途": "、".join(item["purposes"]),
                    "状态": item["user_status"],
                    "仍存在": item["exists_in_project"],
                }
                for item in resources[:500]
            ],
            hide_index=True,
            width="stretch",
        )


def render_advanced_task(
    workspace: Workspace,
    profile_id: str | None,
    embedding_profile_id: str | None,
) -> None:
    st.subheader("智能体任务")
    st.caption(
        "单一决策模型负责编排和编码；知识检索、资源检索、校验和执行均为确定性工具。"
    )
    task = st.text_area(
        "输入自然语言任务",
        height=120,
        placeholder="例如：帮我创建一个山地地形",
    )
    dry_run = st.checkbox(
        "离线 dry-run（推荐）",
        value=True,
        help="会执行受控 Python 脚本并生成 JSON，但不会向 UE 发送创建命令。",
    )
    live_confirmed = True
    if not dry_run:
        live_confirmed = st.checkbox("我确认 UE 已打开，并允许本次任务真实发送命令")
    run_clicked = st.button(
        "运行状态机任务",
        type="primary",
        width="stretch",
        disabled=profile_id is None or not live_confirmed,
    )
    if run_clicked:
        if not task.strip():
            st.warning("请先输入任务。")
            return
        agent = get_decision_agent(
            workspace.path,
            workspace.workspace_id,
            profile_id,
            embedding_profile_id,
            dry_run,
        )
        with st.spinner("状态机正在分析、调用工具并核验观察结果…"):
            result, logs, error = run_with_log(agent.run_with_trace, task.strip())
        st.session_state["last_logs"] = logs
        st.session_state["last_answer"] = result.final_answer if result else ""
        st.session_state["last_trace"] = result.as_dict() if result else None
        st.session_state["last_error"] = repr(error) if error else ""

    if st.session_state.get("last_error"):
        st.error(st.session_state["last_error"])
    if st.session_state.get("last_answer"):
        st.success(st.session_state["last_answer"])
    if st.session_state.get("last_trace"):
        with st.expander("状态机轨迹", expanded=True):
            st.json(st.session_state["last_trace"])
    with st.expander("执行日志", expanded=bool(st.session_state.get("last_logs"))):
        st.code(st.session_state.get("last_logs", "暂无日志"), language="text")


def render_tools(workspace: Workspace) -> None:
    st.subheader("连接与诊断")
    connection_columns = st.columns([2, 1])
    ue_host = connection_columns[0].text_input(
        "UnrealMCP 地址",
        value="127.0.0.1",
        help="第一版只建议连接本机 UE；不要把 UnrealMCP 暴露到局域网。",
    )
    ue_port = connection_columns[1].number_input(
        "端口",
        min_value=1,
        max_value=65535,
        value=13377,
        step=1,
    )
    if st.button("测试 UE 连接", width="stretch"):
        result, logs, error = run_with_log(
            test_ue_connection,
            host=ue_host.strip() or "127.0.0.1",
            port=int(ue_port),
            project_path=workspace.path,
        )
        if error:
            st.error(f"UE 连接失败：{error}")
        else:
            st.success(result)
        if logs:
            st.code(logs, language="text")

    st.info(
        "旧 path_db / memory_db 已退出主链路：资源由 UE 注册表管理，"
        "对话由上下文快照管理，上传文档由工作区知识库管理。"
    )


def main() -> None:
    st.set_page_config(page_title="LCAgent 工作区", page_icon="🌍", layout="wide")
    apply_compact_layout()

    profile_store = ModelProfileStore()
    embedding_store = EmbeddingProfileStore()
    workspace_store = WorkspaceStore()
    chat_store = WorkspaceChatStore()
    workspace, selected_profile_id, selected_embedding_profile_id = render_sidebar(
        profile_store,
        embedding_store,
        workspace_store,
    )

    st.markdown('<h1 class="lc-main-title">LCAgent 工作区</h1>', unsafe_allow_html=True)
    render_status_cards(
        workspace,
        selected_profile_id,
        selected_embedding_profile_id,
        profile_store,
        embedding_store,
    )

    chat_tab, model_tab, knowledge_tab, resource_tab, agent_tab, tools_tab = st.tabs(
        ["对话", "模型", "知识库", "资源注册表", "智能体任务", "工具"]
    )
    with chat_tab:
        render_workspace_chat(
            workspace,
            selected_profile_id,
            chat_store,
            profile_store,
        )
    with model_tab:
        render_model_management(profile_store)
        render_embedding_management(embedding_store)
    with knowledge_tab:
        render_knowledge_base(
            workspace,
            selected_embedding_profile_id,
            embedding_store,
        )
    with resource_tab:
        render_resource_registry(
            workspace,
            selected_profile_id,
            profile_store,
            chat_store,
        )
    with agent_tab:
        render_advanced_task(
            workspace,
            selected_profile_id,
            selected_embedding_profile_id,
        )
    with tools_tab:
        render_tools(workspace)


if __name__ == "__main__":
    main()
