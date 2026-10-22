import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("lcagent", {
  request: (path: string, method?: string, body?: unknown) =>
    ipcRenderer.invoke("service:request", { path, method, body }),
  appInfo: () => ipcRenderer.invoke("app:info"),
});
