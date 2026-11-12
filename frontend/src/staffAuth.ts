/** BeerShop v9.1: isolated-staging MFA sign-in for our own CRM.
 * Nothing is persisted to cookies, browser storage or analytics.
 */
export type StaffCrmRole = "owner" | "operations";
export type StaffIdentity = {
  username: string;
  role: StaffCrmRole;
  expiresIn: number;
  accessToken: string;
};

export class StaffLoginError extends Error {}

function staffRequestOptions(method: string, body?: unknown, token?: string): RequestInit {
  return {
    method,
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

export async function crmStaffLoginEnabled(signal?: AbortSignal): Promise<boolean> {
  const response = await fetch("/api/v91/features", {
    ...staffRequestOptions("GET"),
    signal,
  });
  if (!response.ok) throw new StaffLoginError("Не вдалося перевірити режим входу CRM.");
  const features: unknown = await response.json();
  if (!features || typeof features !== "object")
    throw new StaffLoginError("Неправильна відповідь сервера.");
  const f = features as Record<string, unknown>;
  return f.staff_crm_login === true;
}

export async function revokeStaffToken(token: string): Promise<void> {
  if (!token) return;
  // Best effort: invalid/expired sessions are already blocked on the server.
  await fetch("/api/admin/v9/staff/logout", staffRequestOptions("POST", undefined, token));
}

export async function signInCrmStaff(
  username: string,
  password: string,
  otp: string,
): Promise<StaffIdentity> {
  if (!/^[a-z][a-z0-9_.-]{2,31}$/.test(username) || !/^\d{6}$/.test(otp))
    throw new StaffLoginError("Перевірте логін і шестизначний код MFA.");
  const response = await fetch("/api/admin/v9/staff/login",
    staffRequestOptions("POST", { username, password, otp }));
  if (!response.ok) {
    if (response.status === 401)
      throw new StaffLoginError("Неправильний логін, пароль або MFA-код.");
    if (response.status === 503)
      throw new StaffLoginError("Персональний вхід недоступний у цьому середовищі.");
    throw new StaffLoginError("Не вдалося увійти до CRM.");
  }
  const data: unknown = await response.json();
  if (!data || typeof data !== "object")
    throw new StaffLoginError("Неправильна відповідь авторизації.");
  const value = data as Record<string, unknown>;
  const accessToken = typeof value.access_token === "string" ? value.access_token : "";
  const role = value.role;
  const expiresIn = value.expires_in;
  if (accessToken.length < 32 || typeof expiresIn !== "number" ||
      !Number.isFinite(expiresIn) || expiresIn <= 0 || expiresIn > 900)
    throw new StaffLoginError("Недійсна сесія. Увійдіть ще раз.");
  if (role !== "owner" && role !== "operations") {
    await revokeStaffToken(accessToken).catch(() => {});
    throw new StaffLoginError("Для цієї ролі доступна лише панель співробітника /staff-v9.");
  }
  return { username, role, expiresIn, accessToken };
}
