import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "./api";
import {
  crmStaffLoginEnabled,
  signInCrmStaff,
  StaffLoginError,
} from "./staffAuth";

afterEach(() => vi.unstubAllGlobals());

const credential = { username: "operator_v91", password: "Synthetic-Only-MFA-Passphrase-2026", otp: "123456" };
const fixtureToken = "staging-only-" + "a".repeat(44);
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("BeerShop own-CRM staging MFA", () => {
  it("enables MFA login only for an explicit server feature flag", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json({ staff_crm_login: false, own_crm_only: false }))
      .mockResolvedValueOnce(json({ staff_crm_login: true, own_crm_only: true }));
    vi.stubGlobal("fetch", fetcher);
    expect(await crmStaffLoginEnabled()).toBe(false);
    expect(await crmStaffLoginEnabled()).toBe(true);
    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/v91/features",
      expect.objectContaining({ cache: "no-store", credentials: "omit", redirect: "error" }));
  });

  it("creates a short MFA bearer session with no cookies and rejects other roles", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(json({ access_token: fixtureToken, role: "operations", expires_in: 900 }))
      .mockResolvedValueOnce(json({ access_token: fixtureToken, role: "catalog", expires_in: 900 }))
      .mockResolvedValueOnce(json({ revoked: true }));
    vi.stubGlobal("fetch", fetcher);
    const authorized = await signInCrmStaff(credential.username, credential.password, credential.otp);
    expect(authorized).toEqual({
      username: credential.username, role: "operations", expiresIn: 900, accessToken: fixtureToken,
    });
    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/admin/v9/staff/login",
      expect.objectContaining({
        method: "POST",
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        body: JSON.stringify(credential),
      }));
    await expect(signInCrmStaff(credential.username, credential.password, credential.otp))
      .rejects.toThrow(StaffLoginError);
    expect(fetcher).toHaveBeenNthCalledWith(3, "/api/admin/v9/staff/logout",
      expect.objectContaining({ method: "POST", headers: expect.objectContaining({
        Authorization: "Bearer " + fixtureToken,
      }) }));
  });

  it("never offers an admin-token bypass after a rejected MFA login", async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ detail: "Invalid credentials or MFA" }, 401));
    vi.stubGlobal("fetch", fetcher);
    await expect(signInCrmStaff(credential.username, "wrong", credential.otp))
      .rejects.toThrow("Неправильний логін");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await expect(signInCrmStaff(credential.username, credential.password, "oops"))
      .rejects.toThrow("Перевірте логін");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("releases the in-memory client token and sends a revocation to the same origin", async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ revoked: true }));
    vi.stubGlobal("fetch", fetcher);
    const client = new ApiClient(fixtureToken, vi.fn());
    await client.revokeStaffSession();
    expect(client.active).toBe(false);
    await expect(client.crm("/orders")).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledWith("/api/admin/v9/staff/logout",
      expect.objectContaining({
        method: "POST",
        credentials: "omit",
        redirect: "error",
        headers: expect.objectContaining({ Authorization: "Bearer " + fixtureToken }),
      }));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
