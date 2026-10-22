export {};

declare global {
  interface Window {
    lcagent: {
      request(path: string, method?: string, body?: unknown): Promise<any>;
      appInfo(): Promise<{ projectRoot: string; serviceUrl: string }>;
    };
  }
}
