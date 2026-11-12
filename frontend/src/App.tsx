import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowLeft,
  ArrowRight,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Store,
} from "lucide-react";
import { ApiClient, errorMessage } from "./api";
import { crmStaffLoginEnabled, signInCrmStaff, StaffLoginError } from "./staffAuth";
import type { StaffCrmRole } from "./staffAuth";
import { Details } from "./Details";
import { Editor } from "./Editor";
import { createLabels, List, titles } from "./Lists";
import { ErrorPanel, labels, useQuery } from "./ui";
import type {
  CustomerRow,
  DetailTarget,
  EditorTarget,
  Manager,
  OrderRow,
  Page,
  StockRow,
  StockSummary,
  Summary,
  View,
} from "./types";

type Filters = {
  status: string;
  payment: string;
  manager: string;
  priority: string;
  availability: string;
};
const defaultFilters: Filters = {
  status: "all",
  payment: "all",
  manager: "all",
  priority: "all",
  availability: "all",
};
function Header({
  view,
  select,
  logout,
  staffRole,
}: {
  view?: View;
  select?: (view: View) => void;
  logout?: () => void;
  staffRole?: StaffCrmRole;
}) {
  return (
    <header className="topbar">
      <a className="brand" href="/crm">
        <Store size={26} />
        BeerShop <span>CRM</span>
      </a>
      {select && (
        <nav aria-label="Розділи CRM">
          {(Object.keys(titles) as View[]).map((v) => (
            <button
              key={v}
              data-view={v}
              className={"tab" + (view === v ? " selected" : "")}
              aria-current={view === v ? "page" : undefined}
              onClick={() => select(v)}
            >
              {titles[v]}
            </button>
          ))}
        </nav>
      )}
      <div className="top-actions">
        {staffRole ? <a href="/staff-v9">Персональна безпека</a> : <a href="/manage">Адмінпанель</a>}
        {logout && (
          <button
            id="logout"
            className="icon"
            title="Вийти"
            aria-label="Вийти"
            onClick={logout}
          >
            <LogOut />
          </button>
        )}
      </div>
    </header>
  );
}

function Workspace({ api, logout, staff }: {
  api: ApiClient;
  logout: () => void;
  staff: { username: string; role: StaffCrmRole } | null;
}) {
  const [view, setView] = useState<View>("orders"),
    [page, setPage] = useState(1);
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState("");
  const [filters, setFilters] = useState(defaultFilters),
    [revision, setRevision] = useState(0);
  const [target, setTarget] = useState<DetailTarget | null>(null),
    [editor, setEditor] = useState<EditorTarget | null>(null);
  const [notice, setNotice] = useState(""),
    [refreshError, setRefreshError] = useState(""),
    [refreshing, setRefreshing] = useState(false);
  const editorSequence = useRef(0);
  const edit = (next: EditorTarget) => {
    // UI affordance only. The server is the source of truth for permissions.
    if (staff?.role === "operations" && (next.kind === "stock" || next.kind === "bank")) {
      setNotice("Ця операція доступна лише відповідальній ролі.");
      return;
    }
    editorSequence.current++;
    setEditor(next);
    setNotice("");
  };
  const changed = (message: string) => {
    setNotice(message);
    setRevision((n) => n + 1);
  };
  useEffect(() => {
    const timer = setTimeout(() => setNotice(""), 6500);
    return () => clearTimeout(timer);
  }, [notice]);
  const managers = useQuery(
    String(revision),
    useCallback(
      (signal) => api.crm<Manager[]>("/managers", "GET", undefined, signal),
      [api],
    ),
  );
  const params = new URLSearchParams({
    q: search,
    page: String(page),
    limit: "25",
    ...(view === "orders" ? filters : {}),
    ...(view === "inventory" ? { availability: filters.availability } : {}),
  }).toString();
  const listKey = view + params + ":" + revision;
  const loader = useCallback(
    async (signal: AbortSignal) => {
      if (view === "inventory") {
        const [data, summary] = await Promise.all([
          api.inventory<Page<StockRow>>("?" + params, "GET", undefined, signal),
          api.inventory<StockSummary>("/summary", "GET", undefined, signal),
        ]);
        return {
          rows: data.items,
          total: data.total,
          metrics: [
            ["SKU", summary.skus],
            ["На складі", summary.on_hand],
            ["У резерві", summary.reserved],
            ["Доступно", summary.available],
          ] as [string, number][],
        };
      }
      const [data, summary] = await Promise.all([
        view === "managers"
          ? api.crm<Manager[]>("/managers", "GET", undefined, signal)
          : api.crm<Page<OrderRow | CustomerRow>>(
              "/" + view + "?" + params,
              "GET",
              undefined,
              signal,
            ),
        api.crm<Summary>("/summary", "GET", undefined, signal),
      ]);
      return {
        rows: Array.isArray(data) ? data : data.items,
        total: Array.isArray(data) ? data.length : data.total,
        metrics: [
          ["Усі замовлення", summary.orders],
          ["Нові", summary.statuses.pending || 0],
          ["Очікують оплати", summary.awaiting_payment],
          ["Виконані", summary.statuses.fulfilled || 0],
          ["Клієнти", summary.customers],
        ] as [string, number][],
      };
    },
    [api, view, params],
  );
  const list = useQuery(listKey, loader);
  useEffect(() => {
    if (list.data && !list.data.rows.length && page > 1)
      setPage(Math.max(1, Math.ceil(list.data.total / 25)));
  }, [list.data, page]);
  const select = (next: View) => {
    setView(next);
    setPage(1);
    setQuery("");
    setSearch("");
    setTarget(null);
    setEditor(null);
    setNotice("");
  };
  const filter = (name: keyof Filters, value: string) => {
    setFilters((old) => ({ ...old, [name]: value }));
    setPage(1);
  };
  const create = () => {
    if (view === "customers") edit({ kind: "customer" });
    if (view === "managers") edit({ kind: "manager" });
  };
  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshError("");
    try {
      await api.crm("/index-orders", "POST", {});
      if (api.active) changed("");
    } catch (e) {
      if (api.active) setRefreshError(errorMessage(e));
    } finally {
      setRefreshing(false);
    }
  };
  const total = list.data?.total || 0;
  return (
    <>
      <a className="skip-link" href="#list">До списку результатів</a>
      <Header view={view} select={select} logout={logout} staffRole={staff?.role} />
      <main>
        <div id="workspace">
          <section className="page-head">
            <div>
              <p className="eyebrow">BEERSHOP / CRM 1.2 {staff ? " · MFA STAGING · " + staff.username + " · " + staff.role : ""}</p>
              <h1 id="page-title">{titles[view]}</h1>
            </div>
            <div className="actions">
              <button
                id="refresh"
                className="icon"
                title="Оновити"
                aria-label="Оновити"
                disabled={refreshing}
                onClick={() => void refresh()}
              >
                <RefreshCw />
              </button>
              <button
                id="create"
                className="primary"
                hidden={!createLabels[view]}
                onClick={create}
              >
                <Plus />
                <span>{createLabels[view]}</span>
              </button>
            </div>
          </section>
          {refreshError && (
            <ErrorPanel message={refreshError} retry={() => void refresh()} />
          )}
          {managers.error && (
            <ErrorPanel message={managers.error} retry={() => changed("")} />
          )}
          <section id="summary" className="summary" aria-label="Підсумок">
            {list.data?.metrics.map(([name, n]) => (
              <div key={name} className="metric">
                <span>{name}</span>
                <strong>{n}</strong>
              </div>
            ))}
          </section>
          <form
            id="filters"
            className="filters"
            hidden={view === "managers"}
            onSubmit={(e) => {
              e.preventDefault();
              setSearch(query);
              setPage(1);
              setRevision((n) => n + 1);
            }}
          >
            <div className="search">
              <Search aria-hidden="true" />
              <input
                id="query"
                aria-label="Пошук"
                placeholder={
                  view === "inventory"
                    ? "Назва товару або SKU"
                    : "Номер, ім’я, телефон або email"
                }
                maxLength={100}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <select
              id="status-filter"
              aria-label="Статус замовлення"
              hidden={view !== "orders"}
              value={filters.status}
              onChange={(e) => filter("status", e.target.value)}
            >
              <option value="all">Усі статуси</option>
              {[
                "pending",
                "confirmed",
                "picking",
                "fulfilled",
                "cancelled",
              ].map((s) => (
                <option key={s} value={s}>
                  {labels[s]}
                </option>
              ))}
            </select>
            <select
              id="stock-filter"
              aria-label="Наявність"
              hidden={view !== "inventory"}
              value={filters.availability}
              onChange={(e) => filter("availability", e.target.value)}
            >
              <option value="all">Усі залишки</option>
              <option value="available">Доступні</option>
              <option value="reserved">У резерві</option>
              <option value="out">Недоступні</option>
            </select>
            <select
              id="payment-filter"
              aria-label="Оплата"
              hidden={view !== "orders"}
              value={filters.payment}
              onChange={(e) => filter("payment", e.target.value)}
            >
              <option value="all">Будь-яка оплата</option>
              <option value="unpaid">Не оплачено</option>
              <option value="paid">Оплачено</option>
            </select>
            <select
              id="manager-filter"
              aria-label="Відповідальний"
              hidden={view !== "orders"}
              value={filters.manager}
              onChange={(e) => filter("manager", e.target.value)}
            >
              <option value="all">Усі відповідальні</option>
              <option value="unassigned">Без відповідального</option>
              {managers.data?.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                  {!m.active && " (неактивний)"}
                </option>
              ))}
            </select>
            <select
              id="priority-filter"
              aria-label="Пріоритет"
              hidden={view !== "orders"}
              value={filters.priority}
              onChange={(e) => filter("priority", e.target.value)}
            >
              <option value="all">Усі пріоритети</option>
              {["high", "normal", "low"].map((p) => (
                <option key={p} value={p}>
                  {labels[p]}
                </option>
              ))}
            </select>
            <button className="secondary" type="submit">
              <Search />
              Знайти
            </button>
          </form>
          <div className="list-meta">
            <span id="result-count">
              {list.data ? "Знайдено: " + total : ""}
            </span>
            <span id="loading" role="status" hidden={!list.loading}>
              Завантаження...
            </span>
          </div>
          {list.error && (
            <ErrorPanel message={list.error} retry={() => changed("")} />
          )}
          <section id="list" aria-label="Результати" aria-busy={list.loading}>
            {list.data && (
              <List
                view={view}
                rows={list.data.rows}
                open={setTarget}
                edit={edit}
                create={create}
              />
            )}
          </section>
          <footer
            id="pagination"
            className="pagination"
            hidden={view === "managers"}
          >
            <button
              id="prev"
              className="icon"
              title="Попередня сторінка"
              aria-label="Попередня сторінка"
              disabled={list.loading || page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <ArrowLeft />
            </button>
            <span id="page-number">
              {page} / {Math.max(1, Math.ceil(total / 25))}
            </span>
            <button
              id="next"
              className="icon"
              title="Наступна сторінка"
              aria-label="Наступна сторінка"
              disabled={list.loading || page * 25 >= total}
              onClick={() => setPage((p) => p + 1)}
            >
              <ArrowRight />
            </button>
          </footer>
        </div>
      </main>
      {target && (
        <Details
          target={target}
          api={api}
          revision={revision}
          managers={managers.data}
          managerError={managers.error}
          open={setTarget}
          edit={edit}
          changed={changed}
          staffRole={staff?.role}
          onClose={() => {
            setTarget(null);
            setNotice("");
          }}
        />
      )}
      {editor && (
        <Editor
          key={editorSequence.current}
          target={editor}
          api={api}
          onClose={() => setEditor(null)}
          onSaved={(open) => {
            setEditor(null);
            if (open) setTarget(open);
            changed("Збережено");
          }}
        />
      )}
      {notice &&
        createPortal(
          <div id="notice" role="status" aria-live="polite">
            {notice}
          </div>,
          document.getElementById("detail") || document.body,
        )}
    </>
  );
}

export default function App() {
  const [api, setApi] = useState<ApiClient | null>(null),
    [token, setToken] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const session = useRef<ApiClient | null>(null);
  const logout = useCallback(() => {
    session.current?.dispose();
    session.current = null;
    setApi(null);
    setToken("");
  }, []);
  useEffect(() => () => session.current?.dispose(), []);
  if (api) return <Workspace api={api} logout={logout} />;
  return (
    <>
      <Header />
      <main>
        <section id="login" className="login">
          <p className="eyebrow">BEERSHOP CRM</p>
          <h1>Вхід адміністратора</h1>
          <form
            id="login-form"
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy) return;
              setBusy(true);
              setError("");
              session.current?.dispose();
              const next = new ApiClient(token.trim(), () => {
                logout();
                setError("Сесію завершено. Увійдіть повторно.");
              });
              session.current = next;
              try {
                await next.crm("/index-orders", "POST", {});
                if (next.active) {
                  setToken("");
                  setApi(next);
                }
              } catch (e) {
                if (next.active) {
                  next.dispose();
                  setToken("");
                  setError(errorMessage(e));
                }
              } finally {
                setBusy(false);
              }
            }}
          >
            <label htmlFor="admin-token">Токен адміністратора</label>
            <input
              id="admin-token"
              type="password"
              autoComplete="off"
              required
              minLength={32}
              value={token}
              disabled={busy}
              onChange={(e) => setToken(e.target.value)}
            />
            <button className="primary" type="submit" disabled={busy}>
              {busy ? "Вхід..." : "Увійти"}
            </button>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
          </form>
        </section>
        <section id="list" hidden />
      </main>
    </>
  );
}
