import { useCallback, useRef, useState } from "react";
import { Pencil, Save } from "lucide-react";
import { ApiClient, errorMessage } from "./api";
import {
  Badge,
  date,
  ErrorPanel,
  invoice,
  labels,
  Modal,
  money,
  Photo,
  useQuery,
} from "./ui";
import type {
  Contact,
  CustomerDetail,
  DetailTarget,
  EditorTarget,
  Manager,
  Note,
  OrderDetail,
  OrderStatus,
  StockDetail,
} from "./types";

function Contacts({ contact }: { contact: Contact }) {
  return (
    <dl className="contact">
      <div>
        <dt>Телефон</dt>
        <dd>{contact.phone}</dd>
      </div>
      <div>
        <dt>Email</dt>
        <dd>{contact.email}</dd>
      </div>
    </dl>
  );
}
function Notes({
  notes,
  target,
  api,
  changed,
}: {
  notes: Note[];
  target: DetailTarget;
  api: ApiClient;
  changed: (message: string) => void;
}) {
  const [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  return (
    <section className="detail-section">
      <h3>Нотатки</h3>
      {notes.length ? (
        notes.map((n) => (
          <article className="note" key={n.id}>
            <small>
              {date(n.created_at)}
              {n.order_id ? " · " + invoice(n.order_id) : ""}
            </small>
            <p>{n.body}</p>
          </article>
        ))
      ) : (
        <p className="sub">Нотаток ще немає</p>
      )}
      <form
        id="note-form"
        className="note-form"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError("");
          try {
            await api.crm(
              "/" + target.type + "/" + target.id + "/notes",
              "POST",
              { body: text, request_key: key.current },
            );
            if (api.active) {
              key.current = crypto.randomUUID();
              setText("");
              changed("Нотатку збережено");
            }
          } catch (e) {
            if (api.active) setError(errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="note-text">Нова нотатка</label>
        <textarea
          id="note-text"
          required
          maxLength={2000}
          rows={3}
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
        />
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button type="submit" className="secondary" disabled={busy}>
            Додати нотатку
          </button>
        </div>
      </form>
    </section>
  );
}
const events: Record<string, string> = {
  crm_assignment_updated: "Призначення відповідального / пріоритет",
  order_status_changed: "Статус замовлення",
  bank_payment_manually_confirmed: "Оплату звірено вручну",
  nova_poshta_waybill_manually_registered: "ТТН зареєстровано",
  nova_poshta_status_manually_confirmed: "Статус доставки звірено вручну",
  nova_poshta_status_verified: "Статус доставки перевірено",
};

function Order({
  o,
  api,
  managers,
  open,
  edit,
  changed,
  staffRole,
}: {
  o: OrderDetail;
  api: ApiClient;
  managers: Manager[];
  staffRole?: "owner" | "operations";
  open: (t: DetailTarget) => void;
  edit: (t: EditorTarget) => void;
  changed: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function mutate(action: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await action();
      if (api.active) changed(typeof result === "string" ? result : message);
    } catch (e) {
      if (api.active) setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function status(next: OrderStatus) {
    if (
      next === "cancelled" &&
      !confirm("Скасувати " + o.invoice + " та повернути товари на склад?")
    )
      return;
    void mutate(
      () =>
        api.request("/api/admin/orders/" + o.id + "/status", "PATCH", {
          status: next,
        }),
      "Статус оновлено",
    );
  }
  return (
    <>
      {error && (
        <ErrorPanel message={error} retry={() => changed("Картку оновлено")} />
      )}
      <fieldset disabled={busy}>
        <section className="detail-section">
          <div className="detail-top">
            <div>
              <Badge value={o.status} /> <Badge value={o.payment_status} />
            </div>
            <strong className="detail-total">{money(o.total)}</strong>
          </div>
          <div className="actions">
            <button
              data-action="status"
              data-status="confirmed"
              disabled={o.status !== "pending"}
              onClick={() => status("confirmed")}
            >
              Підтвердити
            </button>
            <button
              data-action="status"
              data-status="picking"
              disabled={o.status !== "confirmed" || o.payment_status !== "paid"}
              onClick={() => status("picking")}
            >
              Комплектувати
            </button>
            <button
              data-action="status"
              data-status="fulfilled"
              disabled={
                o.status !== "picking" ||
                o.payment_status !== "paid" ||
                o.delivery_status !== "delivered"
              }
              onClick={() => status("fulfilled")}
            >
              Завершити
            </button>
            <button
              className="danger"
              data-action="status"
              data-status="cancelled"
              disabled={
                !["pending", "confirmed", "picking"].includes(o.status) ||
                o.payment_status === "paid" ||
                !!o.tracking_number
              }
              onClick={() => status("cancelled")}
            >
              Скасувати
            </button>
          </div>
        </section>
        <section className="detail-section">
          <h3>Клієнт</h3>
          <button
            className="text-button"
            data-action="customer"
            data-id={o.customer_id}
            onClick={() => open({ type: "customers", id: o.customer_id })}
          >
            {o.name}
          </button>
          <Contacts contact={o} />
          <dl className="contact">
            <div className="full">
              <dt>Доставка</dt>
              <dd>{o.delivery_address}</dd>
            </div>
          </dl>
        </section>
        <section className="detail-section">
          <h3>Товари</h3>
          {o.items.map((i, n) => (
            <div className="item" key={i.variant_id + ":" + n}>
              <Photo item={i} />
              <div className="item-main">
                <strong>{i.name}</strong>
                <span className="sub">
                  {i.sku} · {i.quantity} × {money(i.unit_price)}
                </span>
                <Badge
                  value={
                    o.inventory.find((r) => r.variant_id === i.variant_id)
                      ?.state || "Не звірено"
                  }
                />
              </div>
              <span className="money">
                {money(Number(i.unit_price) * i.quantity)}
              </span>
            </div>
          ))}
        </section>
        <section className="detail-section">
          <form
            id="assignment-form"
            onSubmit={(event) => {
              event.preventDefault();
              const fields = new FormData(event.currentTarget);
              void mutate(
                () =>
                  api.crm("/orders/" + o.id, "PATCH", {
                    manager_id: fields.get("manager")
                      ? Number(fields.get("manager"))
                      : null,
                    priority: fields.get("priority"),
                    version: o.version,
                  }),
                "Зміни збережено",
              );
            }}
          >
            <div className="form-grid">
              <div>
                <label htmlFor="assigned">Відповідальний</label>
                <select
                  id="assigned"
                  name="manager"
                  defaultValue={o.manager_id || ""}
                >
                  <option value="">Не призначено</option>
                  {managers
                    .filter((m) => m.active || m.id === o.manager_id)
                    .map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                        {!m.active && " (неактивний)"}
                      </option>
                    ))}
                </select>
              </div>
              <div>
                <label htmlFor="priority">Пріоритет</label>
                <select id="priority" name="priority" defaultValue={o.priority}>
                  {["normal", "high", "low"].map((p) => (
                    <option key={p} value={p}>
                      {labels[p]}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="form-actions">
              <button type="submit">
                <Save />
                Зберегти
              </button>
            </div>
          </form>
        </section>
        <section className="detail-section">
          <h3>Оплата та доставка</h3>
          <p>
            <Badge value={o.payment_status} />{" "}
            <span className="sub">
              {labels[o.delivery_status] || o.delivery_status}
              {o.tracking_number && " · " + o.tracking_number}
            </span>
          </p>
          <div className="actions">
            {(!staffRole || staffRole === "owner") && <button
              data-action="bank"
              disabled={o.payment_status === "paid" || o.status === "cancelled"}
              onClick={() => edit({ kind: "bank", data: o })}
            >
              Звірити оплату
            </button>}
            <button
              data-action="waybill"
              disabled={
                o.payment_status !== "paid" ||
                o.status === "cancelled" ||
                !!o.tracking_number
              }
              onClick={() => edit({ kind: "waybill", data: o })}
            >
              Додати ТТН
            </button>
            <button
              data-action="tracking"
              disabled={!o.tracking_number || Boolean(staffRole)}
              onClick={() =>
                void mutate(async () => {
                  const result = await api.request<{
                    review_required: boolean;
                  }>(
                    "/api/admin/connectors41/nova-poshta/sync/" + o.id,
                    "POST",
                    { attestation: "SYNC_EXISTING_WAYBILL_STATUS" },
                  );
                  return result.review_required
                    ? "Доставка потребує ручної перевірки"
                    : "Статус доставки оновлено";
                }, "Статус доставки оновлено")
              }
            >
              Перевірити доставку
            </button>
            <button
              data-action="delivery-manual"
              disabled={
                !o.tracking_number ||
                o.delivery_status === "delivered" ||
                o.status === "cancelled"
              }
              onClick={() => edit({ kind: "delivery", data: o })}
            >
              Статус вручну
            </button>
          </div>
        </section>
      </fieldset>
      <Notes
        notes={o.notes}
        target={{ type: "orders", id: o.id }}
        api={api}
        changed={changed}
      />
      <section className="detail-section">
        <details>
          <summary>Журнал змін ({o.audit.length})</summary>
          {o.audit.map((a, i) => (
            <p key={i} className="audit">
              {date(a.at)} · {events[a.event] || a.event}
              <br />
              {a.note}
            </p>
          ))}
        </details>
      </section>
    </>
  );
}

function Customer({
  c,
  api,
  open,
  edit,
  changed,
}: {
  c: CustomerDetail;
  api: ApiClient;
  open: (t: DetailTarget) => void;
  edit: (t: EditorTarget) => void;
  changed: (message: string) => void;
}) {
  return (
    <>
      <section className="detail-section">
        <Contacts contact={c} />
        <div className="actions">
          <button
            data-action="edit-customer"
            onClick={() => edit({ kind: "customer", data: c })}
          >
            <Pencil />
            Редагувати контакти
          </button>
        </div>
      </section>
      <section className="detail-section">
        <h3>Останні замовлення ({c.orders.length})</h3>
        {c.orders.map((o) => (
          <div className="item" key={o.id}>
            <div className="item-main">
              <button
                className="text-button"
                data-action="order"
                data-id={o.id}
                onClick={() => open({ type: "orders", id: o.id })}
              >
                {o.invoice}
              </button>
              <span className="sub">{labels[o.status]}</span>
            </div>
            <span className="money">{money(o.total)}</span>
            <Badge value={o.payment_status} />
          </div>
        ))}
        {!c.orders.length && <p className="sub">Замовлень ще немає</p>}
      </section>
      <Notes
        notes={c.notes}
        target={{ type: "customers", id: c.id }}
        api={api}
        changed={changed}
      />
    </>
  );
}
function Stock({
  v,
  open,
  edit,
  staffRole,
}: {
  v: StockDetail;
  open: (t: DetailTarget) => void;
  edit: (t: EditorTarget) => void;
  staffRole?: "owner" | "operations";
}) {
  const names: Record<string, string> = {
    reserved: "Резерв замовлення",
    shipped: "Передано перевізнику",
    released: "Резерв знято",
    stock_count: "Коригування залишку",
    legacy_indexed: "Історичний облік",
    legacy_reconciled: "Звірка попереднього руху",
  };
  const signed = (n: number) => (n > 0 ? "+" : "") + n;
  return (
    <>
      <section className="detail-section">
        <div className="item">
          <Photo item={v} />
          <strong>{v.sku}</strong>
        </div>
        <div className="stock-balances">
          {[
            ["На складі", v.on_hand],
            ["У резерві", v.reserved],
            ["Доступно", v.available],
          ].map(([label, n]) => (
            <div key={label}>
              <span>{label}</span>
              <strong>{n}</strong>
            </div>
          ))}
        </div>
        {(!staffRole || staffRole === "owner") && <button
          data-action="edit-stock"
          onClick={() => edit({ kind: "stock", data: v })}
        >
          <Pencil />
          Перерахунок
        </button>}
      </section>
      <section className="detail-section">
        <h3>Відкриті резерви</h3>
        {v.reservations.map((r) => (
          <div className="item" key={r.order_id}>
            <button
              className="text-button"
              data-action="order"
              data-id={r.order_id}
              onClick={() => open({ type: "orders", id: r.order_id })}
            >
              {invoice(r.order_id)}
            </button>
            <span>{r.quantity} шт.</span>
          </div>
        ))}
        {!v.reservations.length && <p className="sub">Немає резервів</p>}
      </section>
      <section className="detail-section">
        <h3>Останні рухи</h3>
        {v.movements.map((m) => (
          <article className="note" key={m.id}>
            <small>{date(m.at)}</small>
            <p>
              {names[m.event] || m.event}
              {m.order_id && " · " + invoice(m.order_id)}
            </p>
            <span className="sub">
              Доступно: {signed(m.available_delta)} · Резерв:{" "}
              {signed(m.reserved_delta)}
            </span>
            <p>{m.reason}</p>
          </article>
        ))}
        {!v.movements.length && <p className="sub">Рухів ще немає</p>}
      </section>
    </>
  );
}
export function Details({
  target,
  api,
  revision,
  managers,
  managerError,
  open,
  edit,
  changed,
  onClose,
  staffRole,
}: {
  target: DetailTarget;
  api: ApiClient;
  revision: number;
  managers?: Manager[];
  managerError?: string;
  staffRole?: "owner" | "operations";
  open: (t: DetailTarget) => void;
  edit: (t: EditorTarget) => void;
  changed: (message: string) => void;
  onClose: () => void;
}) {
  const { type, id } = target;
  const loader = useCallback(
    (signal: AbortSignal) =>
      type === "inventory"
        ? api.inventory<StockDetail>("/" + id, "GET", undefined, signal)
        : api.crm<OrderDetail | CustomerDetail>(
            "/" + type + "/" + id,
            "GET",
            undefined,
            signal,
          ),
    [api, type, id],
  );
  const result = useQuery<StockDetail | OrderDetail | CustomerDetail>(
    type + id + ":" + revision,
    loader,
  );
  const data = result.data;
  const waitingManagers = type === "orders" && !managers;
  return (
    <Modal
      id="detail"
      title={
        data
          ? "invoice" in data
            ? data.invoice
            : data.name
          : "Завантаження..."
      }
      onClose={onClose}
      onRefresh={() => changed("")}
    >
      <div id="detail-body">
        {(result.loading || (waitingManagers && !managerError)) && (
          <p className="detail-section" role="status">
            Завантаження...
          </p>
        )}
        {waitingManagers && managerError && (
          <ErrorPanel message={managerError} retry={() => changed("")} />
        )}
        {result.error && (
          <ErrorPanel message={result.error} retry={() => changed("")} />
        )}
        {data && type === "orders" && managers && (
          <Order
            key={type + id + ":" + revision}
            o={data as OrderDetail}
            api={api}
            managers={managers}
            staffRole={staffRole}
            open={open}
            edit={edit}
            changed={changed}
          />
        )}
        {data && type === "customers" && (
          <Customer
            key={type + id + ":" + revision}
            c={data as CustomerDetail}
            api={api}
            open={open}
            edit={edit}
            changed={changed}
          />
        )}
        {data && type === "inventory" && (
          <Stock v={data as StockDetail} open={open} edit={edit} staffRole={staffRole} />
        )}
      </div>
    </Modal>
  );
}
