<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";

type ViewName = "chat" | "tasks" | "resources" | "knowledge" | "settings";

interface Workspace {
  workspace_id: string;
  name: string;
  path: string;
  last_opened_at: string;
}

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface ModelProfile {
  profile_id: string;
  display_name: string;
  provider: string;
  model: string;
  has_secret: boolean;
}

interface EmbeddingProfile extends ModelProfile {}

interface ResourceAsset {
  object_path: string;
  package_path: string;
  asset_name: string;
  asset_class: string;
  user_status?: string;
}

interface FolderSummary {
  folder_path: string;
  direct_asset_count: number;
  total_asset_count: number;
  asset_classes: Array<{ asset_class: string; count: number }>;
}

interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  document_count: number;
}

interface KnowledgeDocument {
  id: string;
  kb_id: string;
  filename: string;
  status: string;
  embedding_profile_id: string;
  chunk_count: number;
  created_at: string;
}

interface KnowledgeMatch {
  chunk_id: string;
  filename: string;
  citation: string;
  text: string;
  score: number;
}

const workspaces = ref<Workspace[]>([]);
const activeWorkspaceId = ref("");
const activeView = ref<ViewName>("chat");
const messages = ref<Message[]>([]);
const profiles = ref<ModelProfile[]>([]);
const embeddingProfiles = ref<EmbeddingProfile[]>([]);
const selectedProfileId = ref("");
const selectedEmbeddingProfileId = ref("");
const input = ref("");
const workspacePath = ref("");
const serviceReady = ref(false);
const ueStatus = ref("未检测");
const ueCapabilities = ref<string[]>([]);
const busy = ref(false);
const contextBusy = ref(false);
const errorMessage = ref("");
const noticeMessage = ref("");
const messageListRef = ref<HTMLElement | null>(null);

const resourceBusy = ref(false);
const resourceRoots = ref("/Game");
const resourceMaxResults = ref(10000);
const resourceAssets = ref<ResourceAsset[]>([]);
const resourceFolders = ref<FolderSummary[]>([]);
const resourceFilter = ref("");

const knowledgeBusy = ref(false);
const knowledgeBases = ref<KnowledgeBase[]>([]);
const knowledgeDocuments = ref<KnowledgeDocument[]>([]);
const selectedKnowledgeBaseId = ref("");
const newKnowledgeBaseName = ref("");
const selectedFiles = ref<File[]>([]);
const fileInputRef = ref<HTMLInputElement | null>(null);
const knowledgeQuery = ref("");
const knowledgeMatches = ref<KnowledgeMatch[]>([]);

const activeWorkspace = computed(() =>
  workspaces.value.find((item) => item.workspace_id === activeWorkspaceId.value),
);

const currentDocuments = computed(() =>
  knowledgeDocuments.value.filter((item) => item.kb_id === selectedKnowledgeBaseId.value),
);

const visibleFolders = computed(() =>
  [...resourceFolders.value]
    .sort((left, right) => right.total_asset_count - left.total_asset_count)
    .slice(0, 80),
);

const visibleAssets = computed(() => {
  const query = resourceFilter.value.trim().toLocaleLowerCase();
  const filtered = query
    ? resourceAssets.value.filter((item) =>
        `${item.object_path} ${item.asset_class}`.toLocaleLowerCase().includes(query),
      )
    : resourceAssets.value;
  return filtered.slice(0, 300);
});

async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const envelope = await window.lcagent.request(path, method, body);
  if (!envelope?.ok) {
    throw new Error(envelope?.error?.message || "LCAgent 服务请求失败");
  }
  return envelope.data as T;
}

function beginAction() {
  errorMessage.value = "";
  noticeMessage.value = "";
}

function showError(error: unknown) {
  errorMessage.value = error instanceof Error ? error.message : String(error);
}

async function loadInitialData() {
  beginAction();
  try {
    const health = await api<{ ok: boolean }>("/api/health");
    serviceReady.value = health.ok;
    const workspaceData = await api<{ workspaces: Workspace[] }>("/api/workspaces");
    workspaces.value = workspaceData.workspaces;
    if (!activeWorkspaceId.value && workspaces.value.length) {
      activeWorkspaceId.value = workspaces.value[0].workspace_id;
    }
    const modelData = await api<{ profiles: ModelProfile[]; default_profile_id: string | null }>("/api/models");
    profiles.value = modelData.profiles;
    selectedProfileId.value = modelData.default_profile_id || profiles.value[0]?.profile_id || "";
    const embeddingData = await api<{ profiles: EmbeddingProfile[]; default_profile_id: string | null }>(
      "/api/embedding-models",
    );
    embeddingProfiles.value = embeddingData.profiles;
    selectedEmbeddingProfileId.value =
      embeddingData.default_profile_id || embeddingProfiles.value[0]?.profile_id || "";
  } catch (error) {
    serviceReady.value = false;
    showError(error);
  }
}

async function loadMessages() {
  if (!activeWorkspaceId.value) {
    messages.value = [];
    return;
  }
  try {
    const data = await api<{ messages: Message[] }>(`/api/workspaces/${activeWorkspaceId.value}/messages`);
    messages.value = data.messages;
    await checkUE();
  } catch (error) {
    showError(error);
  }
}

async function openWorkspace() {
  if (!workspacePath.value.trim()) return;
  beginAction();
  try {
    const data = await api<{ workspace: Workspace }>("/api/workspaces/open", "POST", {
      path: workspacePath.value.trim(),
    });
    const existing = workspaces.value.findIndex((item) => item.workspace_id === data.workspace.workspace_id);
    if (existing >= 0) workspaces.value[existing] = data.workspace;
    else workspaces.value.unshift(data.workspace);
    activeWorkspaceId.value = data.workspace.workspace_id;
    workspacePath.value = "";
    noticeMessage.value = `已打开工作区：${data.workspace.name}`;
  } catch (error) {
    showError(error);
  }
}

async function removeWorkspace(workspace: Workspace) {
  const confirmed = window.confirm(
    `确定从 LCAgent 中移除工作区“${workspace.name}”吗？\n\n不会删除磁盘上的 UE 工程、知识库或历史数据。`,
  );
  if (!confirmed) return;
  beginAction();
  try {
    const data = await api<{ workspaces: Workspace[] }>("/api/workspaces/remove", "POST", {
      workspace_id: workspace.workspace_id,
    });
    workspaces.value = data.workspaces;
    if (activeWorkspaceId.value === workspace.workspace_id) {
      activeWorkspaceId.value = workspaces.value[0]?.workspace_id || "";
    }
    noticeMessage.value = `已移除工作区入口：${workspace.name}`;
  } catch (error) {
    showError(error);
  }
}

async function checkUE() {
  if (!activeWorkspace.value) {
    ueStatus.value = "未检测";
    return;
  }
  ueStatus.value = "连接中…";
  try {
    const data = await api<{
      project_name: string;
      contract_version: number | null;
      capabilities: string[];
      legacy_fallback: boolean;
    }>("/api/ue/handshake", "POST", {
      project_path: activeWorkspace.value.path,
      host: "127.0.0.1",
      port: 13377,
    });
    ueCapabilities.value = data.capabilities;
    ueStatus.value = data.legacy_fallback
      ? `已连接（旧协议）· ${data.project_name || "未知工程"}`
      : `已连接 · ${data.project_name || "未知工程"} · 契约 v${data.contract_version}`;
  } catch {
    ueCapabilities.value = [];
    ueStatus.value = "未连接";
  }
}

async function sendMessage() {
  const content = input.value.trim();
  if (!content || !activeWorkspace.value || busy.value) return;
  input.value = "";
  busy.value = true;
  beginAction();
  messages.value.push({ role: "user", content });
  try {
    const data = await api<{ reply: string; compressed_now: boolean }>("/api/chat", "POST", {
      workspace_id: activeWorkspace.value.workspace_id,
      profile_id: selectedProfileId.value || undefined,
      content,
    });
    messages.value.push({ role: "assistant", content: data.reply });
    if (data.compressed_now) noticeMessage.value = "本轮调用前已自动压缩较早上下文。";
  } catch (error) {
    showError(error);
  } finally {
    busy.value = false;
  }
}

async function compactContext() {
  if (!activeWorkspace.value || contextBusy.value) return;
  if (!selectedProfileId.value) {
    errorMessage.value = "请先选择用于压缩的对话模型。";
    return;
  }
  contextBusy.value = true;
  beginAction();
  try {
    const data = await api<{
      status: string;
      compressed_now: boolean;
      covered_message_count: number;
      estimated_tokens: number;
      compression_error?: string;
    }>("/api/context/compact", "POST", {
      workspace_id: activeWorkspace.value.workspace_id,
      profile_id: selectedProfileId.value,
    });
    if (data.compression_error) {
      errorMessage.value = `上下文压缩失败：${data.compression_error}`;
    } else if (data.compressed_now) {
      noticeMessage.value = `上下文压缩完成，摘要已覆盖 ${data.covered_message_count} 条较早消息。`;
    } else {
      noticeMessage.value = "当前历史较短，无需压缩；最近对话将继续原样保留。";
    }
  } catch (error) {
    showError(error);
  } finally {
    contextBusy.value = false;
  }
}

async function clearConversation() {
  if (!activeWorkspace.value || !messages.value.length) return;
  if (!window.confirm("确定删除当前工作区的全部对话和上下文摘要吗？此操作不可恢复。")) return;
  beginAction();
  try {
    await api("/api/chat/clear", "POST", { workspace_id: activeWorkspace.value.workspace_id });
    messages.value = [];
    noticeMessage.value = "当前工作区对话和压缩摘要已删除。";
  } catch (error) {
    showError(error);
  }
}

async function loadResourceOverview() {
  if (!activeWorkspaceId.value) return;
  try {
    const data = await api<{
      assets: ResourceAsset[];
      folders: FolderSummary[];
    }>(`/api/workspaces/${activeWorkspaceId.value}/resources`);
    resourceAssets.value = data.assets;
    resourceFolders.value = data.folders;
  } catch (error) {
    showError(error);
  }
}

async function scanUEAssets() {
  if (!activeWorkspace.value || resourceBusy.value) return;
  resourceBusy.value = true;
  beginAction();
  try {
    const roots = resourceRoots.value
      .split(/[,，;；\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    const data = await api<{
      asset_count: number;
      folder_count: number;
      assets: ResourceAsset[];
      folders: FolderSummary[];
    }>("/api/ue/assets/scan", "POST", {
      workspace_id: activeWorkspace.value.workspace_id,
      package_roots: roots,
      max_results: resourceMaxResults.value,
      host: "127.0.0.1",
      port: 13377,
    });
    resourceAssets.value = data.assets;
    resourceFolders.value = data.folders;
    noticeMessage.value = `UE 资源读取完成：${data.asset_count} 个资产，${data.folder_count} 个文件夹。`;
  } catch (error) {
    showError(error);
  } finally {
    resourceBusy.value = false;
  }
}

async function loadKnowledgeOverview() {
  if (!activeWorkspaceId.value) return;
  try {
    const data = await api<{
      knowledge_bases: KnowledgeBase[];
      documents: KnowledgeDocument[];
    }>(`/api/workspaces/${activeWorkspaceId.value}/knowledge`);
    knowledgeBases.value = data.knowledge_bases;
    knowledgeDocuments.value = data.documents;
    if (!knowledgeBases.value.some((item) => item.id === selectedKnowledgeBaseId.value)) {
      selectedKnowledgeBaseId.value = knowledgeBases.value[0]?.id || "";
    }
  } catch (error) {
    showError(error);
  }
}

async function createKnowledgeBase() {
  const name = newKnowledgeBaseName.value.trim();
  if (!activeWorkspace.value || !name) return;
  knowledgeBusy.value = true;
  beginAction();
  try {
    const data = await api<{ knowledge_base: KnowledgeBase }>("/api/knowledge-bases/create", "POST", {
      workspace_id: activeWorkspace.value.workspace_id,
      name,
    });
    newKnowledgeBaseName.value = "";
    await loadKnowledgeOverview();
    selectedKnowledgeBaseId.value = data.knowledge_base.id;
    noticeMessage.value = `知识库“${data.knowledge_base.name}”已创建。`;
  } catch (error) {
    showError(error);
  } finally {
    knowledgeBusy.value = false;
  }
}

function onKnowledgeFilesSelected(event: Event) {
  const target = event.target as HTMLInputElement;
  selectedFiles.value = Array.from(target.files || []);
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolvePromise, rejectPromise) => {
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      const separator = value.indexOf(",");
      if (separator < 0) rejectPromise(new Error(`无法读取文件：${file.name}`));
      else resolvePromise(value.slice(separator + 1));
    };
    reader.onerror = () => rejectPromise(reader.error || new Error(`无法读取文件：${file.name}`));
    reader.readAsDataURL(file);
  });
}

async function uploadKnowledgeFiles() {
  if (
    !activeWorkspace.value ||
    !selectedKnowledgeBaseId.value ||
    !selectedEmbeddingProfileId.value ||
    !selectedFiles.value.length ||
    knowledgeBusy.value
  ) return;
  knowledgeBusy.value = true;
  beginAction();
  let completed = 0;
  try {
    for (const file of selectedFiles.value) {
      if (file.size > 50 * 1024 * 1024) {
        throw new Error(`文件超过 50 MiB，无法上传：${file.name}`);
      }
      const dataBase64 = await fileToBase64(file);
      await api("/api/knowledge-bases/upload", "POST", {
        workspace_id: activeWorkspace.value.workspace_id,
        kb_id: selectedKnowledgeBaseId.value,
        embedding_profile_id: selectedEmbeddingProfileId.value,
        filename: file.name,
        data_base64: dataBase64,
      });
      completed += 1;
    }
    selectedFiles.value = [];
    if (fileInputRef.value) fileInputRef.value.value = "";
    await loadKnowledgeOverview();
    noticeMessage.value = `已完成 ${completed} 个文档的解析、切片和向量入库。`;
  } catch (error) {
    showError(error);
  } finally {
    knowledgeBusy.value = false;
  }
}

async function searchKnowledge() {
  if (!activeWorkspace.value || !knowledgeQuery.value.trim() || !selectedEmbeddingProfileId.value) return;
  knowledgeBusy.value = true;
  beginAction();
  try {
    const data = await api<{ matches: KnowledgeMatch[] }>("/api/knowledge-bases/search", "POST", {
      workspace_id: activeWorkspace.value.workspace_id,
      kb_ids: selectedKnowledgeBaseId.value ? [selectedKnowledgeBaseId.value] : [],
      embedding_profile_id: selectedEmbeddingProfileId.value,
      query: knowledgeQuery.value.trim(),
      top_k: 5,
    });
    knowledgeMatches.value = data.matches;
    noticeMessage.value = `知识库检索完成，返回 ${data.matches.length} 条结果。`;
  } catch (error) {
    showError(error);
  } finally {
    knowledgeBusy.value = false;
  }
}

// 对话记录在独立容器中滚动；切换工作区或收到新消息后始终展示最新内容。
async function scrollMessagesToBottom() {
  await nextTick();
  const container = messageListRef.value;
  if (container) container.scrollTop = container.scrollHeight;
}

watch(activeWorkspaceId, () => {
  void loadMessages();
  if (activeView.value === "resources") void loadResourceOverview();
  if (activeView.value === "knowledge") void loadKnowledgeOverview();
});
watch(activeView, (view) => {
  beginAction();
  if (view === "resources") void loadResourceOverview();
  if (view === "knowledge") void loadKnowledgeOverview();
});
watch(
  [activeWorkspaceId, () => messages.value.length, busy],
  () => void scrollMessagesToBottom(),
  { flush: "post" },
);
onMounted(() => void loadInitialData());
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand"><span class="brand-mark">LC</span><span>LCAgent</span></div>
      <div class="topbar-center">{{ activeWorkspace?.name || "未打开工作区" }}</div>
      <div class="topbar-actions">
        <span class="service-badge" :class="{ online: serviceReady }">
          {{ serviceReady ? "本地服务正常" : "服务未连接" }}
        </span>
        <select v-model="selectedProfileId" class="model-select">
          <option value="" disabled>选择模型</option>
          <option v-for="profile in profiles" :key="profile.profile_id" :value="profile.profile_id">
            {{ profile.display_name }} · {{ profile.model }}
          </option>
        </select>
      </div>
    </header>

    <div class="workspace-layout">
      <aside class="sidebar">
        <div class="sidebar-title">工作区</div>
        <div v-for="workspace in workspaces" :key="workspace.workspace_id" class="workspace-row">
          <button
            class="workspace-item"
            :class="{ active: activeWorkspaceId === workspace.workspace_id }"
            @click="activeWorkspaceId = workspace.workspace_id"
          >
            <span class="workspace-dot"></span>
            <span class="workspace-name">{{ workspace.name }}</span>
          </button>
          <button class="workspace-remove" title="移除工作区" @click="removeWorkspace(workspace)">×</button>
        </div>
        <div class="open-workspace">
          <input v-model="workspacePath" placeholder="输入 UE 工程路径" @keyup.enter="openWorkspace" />
          <button class="secondary-button" @click="openWorkspace">打开工作区</button>
        </div>
        <nav class="nav-list">
          <button class="nav-item" :class="{ active: activeView === 'chat' }" @click="activeView = 'chat'">对话</button>
          <button class="nav-item" :class="{ active: activeView === 'tasks' }" @click="activeView = 'tasks'">任务</button>
          <button class="nav-item" :class="{ active: activeView === 'resources' }" @click="activeView = 'resources'">UE 资源</button>
          <button class="nav-item" :class="{ active: activeView === 'knowledge' }" @click="activeView = 'knowledge'">知识库</button>
          <button class="nav-item" :class="{ active: activeView === 'settings' }" @click="activeView = 'settings'">设置</button>
        </nav>
        <div class="sidebar-footer">Electron + Vue3 客户端</div>
      </aside>

      <main class="chat-panel">
        <template v-if="activeView === 'chat'">
          <div class="chat-header">
            <div>
              <div class="eyebrow">WORKSPACE CHAT</div>
              <h1>{{ activeWorkspace?.name || "LCAgent 工作区" }}</h1>
              <p>{{ activeWorkspace?.path || "请选择一个工作区" }}</p>
            </div>
            <div class="chat-actions">
              <button class="toolbar-button" :disabled="contextBusy || !messages.length" @click="compactContext">
                {{ contextBusy ? "压缩中…" : "压缩上下文" }}
              </button>
              <button class="toolbar-button danger" :disabled="!messages.length" @click="clearConversation">删除对话</button>
              <button class="icon-button" title="重新检查 UE" @click="checkUE">↻</button>
            </div>
          </div>

          <div ref="messageListRef" class="message-list">
            <div v-if="!messages.length" class="empty-state">
              <div class="empty-icon">✦</div>
              <h2>开始一个 UE 工作任务</h2>
              <p>可以询问工程结构、资源用途，或让 LCAgent 生成地形任务。</p>
            </div>
            <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
              <div class="message-avatar">{{ message.role === "user" ? "你" : "LC" }}</div>
              <div class="message-body">
                <div class="message-role">{{ message.role === "user" ? "你" : "LCAgent" }}</div>
                <div class="message-content">{{ message.content }}</div>
              </div>
            </article>
            <article v-if="busy" class="message assistant">
              <div class="message-avatar">LC</div>
              <div class="message-body">
                <div class="message-role">LCAgent</div>
                <div class="typing"><i></i><i></i><i></i></div>
              </div>
            </article>
          </div>

          <div class="composer">
            <textarea
              v-model="input"
              :disabled="busy || !activeWorkspace"
              placeholder="在当前工作区中输入任务…"
              @keydown.enter.exact.prevent="sendMessage"
            ></textarea>
            <div class="composer-footer">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <button class="send-button" :disabled="busy || !input.trim() || !activeWorkspace" @click="sendMessage">发送</button>
            </div>
          </div>
        </template>

        <div v-else-if="activeView === 'resources'" class="feature-page">
          <div class="feature-header">
            <div><div class="eyebrow">UE ASSET REGISTRY</div><h1>UE 工程资源</h1><p>从已连接 UE 的 Asset Registry 读取权威对象路径并保存到当前工作区。</p></div>
            <button class="primary-button" :disabled="resourceBusy || !activeWorkspace" @click="scanUEAssets">
              {{ resourceBusy ? "正在读取…" : "读取 / 更新 UE 资源" }}
            </button>
          </div>
          <div class="feature-scroll">
            <section class="control-card control-grid">
              <label><span>扫描包路径</span><input v-model="resourceRoots" placeholder="/Game，可用逗号分隔多个路径" /></label>
              <label><span>最大资产数</span><input v-model.number="resourceMaxResults" type="number" min="1" max="50000" /></label>
              <label><span>筛选已读取资产</span><input v-model="resourceFilter" placeholder="对象路径或资产类型" /></label>
            </section>
            <div class="stats-grid">
              <div class="stat-card"><strong>{{ resourceAssets.length }}</strong><span>有效资产</span></div>
              <div class="stat-card"><strong>{{ resourceFolders.length }}</strong><span>资源文件夹</span></div>
              <div class="stat-card"><strong>{{ visibleAssets.length }}</strong><span>当前展示</span></div>
            </div>
            <section class="data-card">
              <div class="section-heading"><h2>文件夹概览</h2><span>按资产数量排序展示前 80 项</span></div>
              <div v-if="!resourceFolders.length" class="inline-empty">尚未读取资源。请打开 UE 并确认 UnrealMCP 已连接。</div>
              <div v-else class="folder-grid">
                <div v-for="folder in visibleFolders" :key="folder.folder_path" class="folder-item">
                  <div class="folder-path">{{ folder.folder_path }}</div>
                  <div>{{ folder.total_asset_count }} 个资产 · 直属 {{ folder.direct_asset_count }}</div>
                </div>
              </div>
            </section>
            <section class="data-card">
              <div class="section-heading"><h2>资产路径</h2><span>为保证界面流畅，最多展示 300 项</span></div>
              <div class="table-wrap">
                <table><thead><tr><th>资产</th><th>类型</th><th>对象路径</th></tr></thead><tbody>
                  <tr v-for="asset in visibleAssets" :key="asset.object_path"><td>{{ asset.asset_name }}</td><td>{{ asset.asset_class }}</td><td class="mono-cell">{{ asset.object_path }}</td></tr>
                </tbody></table>
              </div>
            </section>
          </div>
        </div>

        <div v-else-if="activeView === 'knowledge'" class="feature-page">
          <div class="feature-header">
            <div><div class="eyebrow">WORKSPACE KNOWLEDGE</div><h1>知识库</h1><p>上传 PDF、DOCX、TXT 或 Markdown，使用独立 Embedding 模型建立向量索引。</p></div>
          </div>
          <div class="feature-scroll">
            <section class="control-card create-row">
              <input v-model="newKnowledgeBaseName" placeholder="新知识库名称" @keyup.enter="createKnowledgeBase" />
              <button class="primary-button" :disabled="knowledgeBusy || !newKnowledgeBaseName.trim()" @click="createKnowledgeBase">创建知识库</button>
            </section>
            <section class="control-card knowledge-controls">
              <label><span>当前知识库</span><select v-model="selectedKnowledgeBaseId"><option value="" disabled>请先创建知识库</option><option v-for="base in knowledgeBases" :key="base.id" :value="base.id">{{ base.name }} · {{ base.document_count }} 个文档</option></select></label>
              <label><span>Embedding 模型</span><select v-model="selectedEmbeddingProfileId"><option value="" disabled>请先在管理控制台配置</option><option v-for="profile in embeddingProfiles" :key="profile.profile_id" :value="profile.profile_id">{{ profile.display_name }} · {{ profile.model }}</option></select></label>
              <label class="file-picker"><span>选择文档</span><input ref="fileInputRef" type="file" multiple accept=".pdf,.docx,.txt,.md" @change="onKnowledgeFilesSelected" /></label>
              <button class="primary-button upload-button" :disabled="knowledgeBusy || !selectedFiles.length || !selectedKnowledgeBaseId || !selectedEmbeddingProfileId" @click="uploadKnowledgeFiles">{{ knowledgeBusy ? "处理中…" : `上传并建立索引${selectedFiles.length ? `（${selectedFiles.length}）` : ''}` }}</button>
            </section>
            <div v-if="selectedFiles.length" class="selected-files">待上传：{{ selectedFiles.map((file) => file.name).join("、") }}</div>
            <section class="data-card">
              <div class="section-heading"><h2>已入库文档</h2><span>{{ currentDocuments.length }} 项</span></div>
              <div v-if="!currentDocuments.length" class="inline-empty">当前知识库还没有文档。</div>
              <div v-else class="document-list"><div v-for="document in currentDocuments" :key="document.id" class="document-item"><div><strong>{{ document.filename }}</strong><span>{{ document.embedding_profile_id }}</span></div><div>{{ document.chunk_count }} 个切片 · {{ document.status }}</div></div></div>
            </section>
            <section class="data-card">
              <div class="section-heading"><h2>检索测试</h2><span>验证文档是否正确切片和索引</span></div>
              <div class="search-row"><input v-model="knowledgeQuery" placeholder="输入要检索的问题" @keyup.enter="searchKnowledge" /><button class="primary-button" :disabled="knowledgeBusy || !knowledgeQuery.trim() || !selectedEmbeddingProfileId" @click="searchKnowledge">检索</button></div>
              <div class="match-list"><article v-for="match in knowledgeMatches" :key="match.chunk_id" class="match-item"><div><strong>{{ match.citation }}</strong><span>相似度 {{ match.score.toFixed(3) }}</span></div><p>{{ match.text }}</p></article></div>
            </section>
          </div>
        </div>

        <div v-else class="feature-page placeholder-page">
          <div class="empty-state"><div class="empty-icon">◇</div><h2>{{ activeView === 'tasks' ? '任务面板正在迁移' : '设置面板正在迁移' }}</h2><p>当前功能仍可在 Streamlit 管理控制台中使用。</p></div>
        </div>
      </main>

      <aside class="context-panel">
        <section class="context-card">
          <div class="card-heading"><span>UE 连接</span><button class="text-button" @click="checkUE">检查</button></div>
          <div class="connection-state" :class="{ connected: ueStatus.startsWith('已连接') }"><span class="state-dot"></span>{{ ueStatus }}</div>
          <div v-if="ueCapabilities.length" class="capability-count">已发现 {{ ueCapabilities.length }} 项插件能力</div>
        </section>
        <section class="context-card">
          <div class="card-heading"><span>当前工作区</span></div>
          <div class="context-label">项目路径</div>
          <div class="context-value path-value">{{ activeWorkspace?.path || "未选择" }}</div>
          <div class="context-label">默认执行模式</div>
          <div class="mode-chip">DRY RUN · 需确认后执行</div>
        </section>
        <section class="context-card task-card">
          <div class="card-heading"><span>任务时间线</span></div>
          <div class="timeline-item"><span class="timeline-dot done"></span><span>工作区已加载</span></div>
          <div class="timeline-item"><span class="timeline-dot" :class="{ done: ueStatus.startsWith('已连接') }"></span><span>UE 握手</span></div>
          <div class="timeline-item"><span class="timeline-dot"></span><span>等待任务</span></div>
        </section>
        <div v-if="noticeMessage" class="notice-card">{{ noticeMessage }}</div>
        <div v-if="errorMessage" class="error-card">{{ errorMessage }}</div>
      </aside>
    </div>
  </div>
</template>
