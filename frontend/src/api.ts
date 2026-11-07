export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Не вдалося отримати відповідь сервера. Перевірте з’єднання та повторіть спробу.";
}

// A session owns its requests. Logout aborts them and invalidates late responses.
export class ApiClient {
  private controllers = new Set<AbortController>();
  private disposed = false;
  constructor(
    private token: string,
    private unauthorized: () => void,
  ) {}
  get active() {
    return !this.disposed;
  }
  dispose() {
    this.disposed = true;
    this.token = "";
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear();
  }
  async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!path.startsWith("/api/admin/"))
      throw new Error("Only same-origin admin API paths are allowed");
    if (this.disposed) throw new DOMException("Session closed", "AbortError");
    const controller = new AbortController();
    const abort = () => controller.abort();
    this.controllers.add(controller);
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    try {
      const response = await fetch(path, {
        method,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        signal: controller.signal,
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (this.disposed || controller.signal.aborted)
        throw new DOMException("Session closed", "AbortError");
      if (response.status === 401) {
        this.dispose();
        this.unauthorized();
      }
      if (!response.ok)
        throw new ApiError(
          typeof data.detail === "string"
            ? data.detail
            : `Перевірте заповнення полів (${response.status})`,
          response.status,
        );
      return data as T;
    } finally {
      signal?.removeEventListener("abort", abort);
      this.controllers.delete(controller);
    }
  }
  crm<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal) {
    return this.request<T>("/api/admin/crm" + path, method, body, signal);
  }
  inventory<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ) {
    return this.request<T>("/api/admin/inventory" + path, method, body, signal);
  }
}
