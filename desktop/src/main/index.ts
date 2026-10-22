import { app, BrowserWindow, ipcMain } from "electron";
import { ChildProcess, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const PROJECT_ROOT = process.env.LCAGENT_ROOT
  ? resolve(process.env.LCAGENT_ROOT)
  : resolve(__dirname, "../../..");
const SERVICE_HOST = "127.0.0.1";
const SERVICE_PORT = Number(process.env.LCAGENT_SERVICE_PORT || 8765);
const SERVICE_URL = `http://${SERVICE_HOST}:${SERVICE_PORT}`;

class PythonServiceManager {
  private child?: ChildProcess;

  private findPython(): string {
    const configured = process.env.LCAGENT_PYTHON?.trim();
    const candidates = [
      configured,
      join(PROJECT_ROOT, "langchain_env", "Scripts", "python.exe"),
      join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),
      "python",
    ].filter((item): item is string => Boolean(item));
    return candidates.find((item) => item === "python" || existsSync(item)) || "python";
  }

  async start(): Promise<void> {
    if (this.child && this.child.exitCode === null) {
      await this.waitForHealth();
      return;
    }

    const python = this.findPython();
    const serviceScript = join(PROJECT_ROOT, "LCA", "service.py");
    this.child = spawn(
      python,
      [serviceScript, "--host", SERVICE_HOST, "--port", String(SERVICE_PORT)],
      {
        cwd: PROJECT_ROOT,
        env: {
          ...process.env,
          PYTHONPATH: join(PROJECT_ROOT, "LCA"),
          PYTHONUTF8: "1",
        },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    this.child.stdout?.on("data", (data) => console.log(`[LCAgent] ${data}`));
    this.child.stderr?.on("data", (data) => console.error(`[LCAgent] ${data}`));
    this.child.on("error", (error) => console.error("启动 Python 服务失败", error));
    await this.waitForHealth();
  }

  async stop(): Promise<void> {
    if (!this.child || this.child.exitCode !== null) return;
    this.child.kill();
    this.child = undefined;
  }

  async request(path: string, method = "GET", body?: unknown): Promise<unknown> {
    await this.start();
    const response = await fetch(`${SERVICE_URL}${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const parsed = (await response.json()) as unknown;
    // 业务错误仍交给渲染进程按统一 envelope 展示，避免把整段 JSON 包进异常字符串。
    return parsed;
  }

  private async waitForHealth(): Promise<void> {
    const deadline = Date.now() + 15_000;
    let lastError = "服务尚未响应";
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`${SERVICE_URL}/api/health`);
        if (response.ok) return;
      } catch (error) {
        lastError = String(error);
      }
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
    }
    throw new Error(`LCAgent Python 服务启动超时：${lastError}`);
  }
}

const pythonService = new PythonServiceManager();

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1480,
    height: 920,
    minWidth: 1100,
    minHeight: 720,
    title: "LCAgent",
    backgroundColor: "#10131a",
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env.ELECTRON_RENDERER_URL) {
    void window.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    void window.loadFile(join(__dirname, "../renderer/index.html"));
  }
  return window;
}

app.whenReady().then(async () => {
  ipcMain.handle("service:request", async (_event, request: { path: string; method?: string; body?: unknown }) => {
    if (!request || typeof request.path !== "string" || !request.path.startsWith("/api/")) {
      throw new Error("非法服务请求路径");
    }
    return pythonService.request(request.path, request.method, request.body);
  });
  ipcMain.handle("app:info", () => ({
    projectRoot: PROJECT_ROOT,
    serviceUrl: SERVICE_URL,
  }));

  createWindow();
  try {
    await pythonService.start();
  } catch (error) {
    console.error("LCAgent Python 服务启动失败", error);
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  void pythonService.stop();
});
