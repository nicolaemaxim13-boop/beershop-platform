import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, errorMessage } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("memory-only admin API session", () => {
  it("uses same-origin authenticated no-store requests without cookies or redirects", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ ok: true })));
    vi.stubGlobal("fetch", fetcher);
    const api = new ApiClient("test-token", vi.fn());
    expect(await api.crm("/orders", "PATCH", { version: 4 })).toEqual({
      ok: true,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/admin/crm/orders",
      expect.objectContaining({
        method: "PATCH",
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        headers: {
          Authorization: "Bearer test-token",
          "Content-Type": "application/json",
        },
        body: '{"version":4}',
      }),
    );
    await expect(
      api.request("https://other.test/api/admin/orders"),
    ).rejects.toThrow("same-origin");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("aborts outstanding requests and discards responses after logout", async () => {
    let finish!: (response: Response) => void;
    let signal!: AbortSignal;
    vi.stubGlobal(
      "fetch",
      vi.fn((_path, options) => {
        signal = options.signal;
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      }),
    );
    const api = new ApiClient("test-token", vi.fn());
    const pending = api.crm("/orders");
    api.dispose();
    expect(signal.aborted).toBe(true);
    finish(new Response(JSON.stringify({ private_data: "must not render" })));
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await expect(api.crm("/orders")).rejects.toMatchObject({
      name: "AbortError",
    });
  });
  it("logs out on 401 but not on conflicts or unavailable service", async () => {
    const unauthorized = vi.fn();
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response('{"detail":"Conflict"}', { status: 409 }),
      );
    vi.stubGlobal("fetch", fetcher);
    const api = new ApiClient("test-token", unauthorized);
    await expect(api.crm("/orders")).rejects.toMatchObject({
      status: 409,
      message: "Conflict",
    });
    expect(api.active).toBe(true);
    fetcher.mockResolvedValue(new Response("Unavailable", { status: 503 }));
    await expect(api.crm("/orders")).rejects.toMatchObject({ status: 503 });
    expect(unauthorized).not.toHaveBeenCalled();
    fetcher.mockResolvedValue(
      new Response('{"detail":"Unauthorized"}', { status: 401 }),
    );
    await expect(api.crm("/orders")).rejects.toMatchObject({ status: 401 });
    expect(unauthorized).toHaveBeenCalledTimes(1);
    expect(api.active).toBe(false);
  });
  it("honors query cancellation without invalidating the session", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_path, options) =>
          new Promise((_resolve, reject) => {
            options.signal.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );
    const api = new ApiClient("test-token", vi.fn());
    const pending = api.crm("/orders", "GET", undefined, controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(api.active).toBe(true);
  });
  it("does not leak arbitrary exception contents in user-facing errors", () => {
    expect(
      errorMessage(new Error("secret in unexpected exception")),
    ).not.toContain("secret");
    expect(errorMessage(new ApiError("Перевірте суму", 422))).toBe(
      "Перевірте суму",
    );
  });
});
