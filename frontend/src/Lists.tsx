import { ClipboardList, Pencil } from "lucide-react";
import { Badge, date, labels, money } from "./ui";
import type {
  CustomerRow,
  DetailTarget,
  EditorTarget,
  Manager,
  OrderRow,
  StockRow,
  View,
} from "./types";

export const titles: Record<View, string> = {
  orders: "Замовлення",
  customers: "Клієнти",
  inventory: "Склад",
  managers: "Відповідальні",
};
export const createLabels: Partial<Record<View, string>> = {
  customers: "Новий клієнт",
  managers: "Додати відповідального",
};
export function List({
  view,
  rows,
  open,
  edit,
  create,
}: {
  view: View;
  rows: (OrderRow | CustomerRow | StockRow | Manager)[];
  open: (target: DetailTarget) => void;
  edit: (target: EditorTarget) => void;
  create: () => void;
}) {
  if (!rows.length)
    return (
      <div className="empty">
        <ClipboardList aria-hidden="true" />
        <h2>
          {
            {
              orders: "Замовлень не знайдено",
              customers: "Клієнтів не знайдено",
              inventory: "Товарів не знайдено",
              managers: "Відповідальних ще немає",
            }[view]
          }
        </h2>
        {createLabels[view] && (
          <button className="primary" onClick={create}>
            {createLabels[view]}
          </button>
        )}
      </div>
    );
  if (view === "managers")
    return (
      <>
        {(rows as Manager[]).map((m) => (
          <div key={m.id} className="manager-row">
            <div>
              <strong>{m.name}</strong>
              <span className="sub">
                {m.active ? "Активний" : "Неактивний"}
              </span>
            </div>
            <button
              className="icon"
              data-action="edit-manager"
              data-id={m.id}
              title="Редагувати"
              aria-label={"Редагувати " + m.name}
              onClick={() => edit({ kind: "manager", data: m })}
            >
              <Pencil />
            </button>
          </div>
        ))}
      </>
    );
  if (view === "inventory")
    return (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Товар / SKU</th>
              <th>На складі</th>
              <th>Резерв</th>
              <th>Доступно</th>
            </tr>
          </thead>
          <tbody>
            {(rows as StockRow[]).map((v) => (
              <tr key={v.variant_id}>
                <td>
                  <button
                    className="text-button cell-title"
                    data-action="stock"
                    data-id={v.variant_id}
                    onClick={() =>
                      open({ type: "inventory", id: v.variant_id })
                    }
                  >
                    {v.name}
                  </button>
                  <span className="sub">
                    {v.sku}
                    {!v.active && " · Приховано"}
                  </span>
                </td>
                <td>{v.on_hand}</td>
                <td>{v.reserved}</td>
                <td>
                  <strong>{v.available}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  if (view === "customers")
    return (
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Клієнт</th>
              <th className="hide-small">Контакти</th>
              <th>Замовлення</th>
              <th>Оплачено</th>
            </tr>
          </thead>
          <tbody>
            {(rows as CustomerRow[]).map((c) => (
              <tr key={c.id}>
                <td>
                  <button
                    className="text-button cell-title"
                    data-action="customer"
                    data-id={c.id}
                    onClick={() => open({ type: "customers", id: c.id })}
                  >
                    {c.name}
                  </button>
                  <span className="sub mobile-only">{c.phone}</span>
                </td>
                <td className="hide-small">
                  {c.phone}
                  <span className="sub">{c.email}</span>
                </td>
                <td>{c.orders}</td>
                <td className="money">{money(c.paid_total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Замовлення</th>
            <th>Клієнт</th>
            <th>Сума / оплата</th>
            <th className="hide-small">Статус</th>
            <th className="hide-medium">Доставка</th>
            <th className="hide-medium">Відповідальний</th>
          </tr>
        </thead>
        <tbody>
          {(rows as OrderRow[]).map((o) => (
            <tr key={o.id}>
              <td>
                <button
                  className="text-button cell-title"
                  data-action="order"
                  data-id={o.id}
                  onClick={() => open({ type: "orders", id: o.id })}
                >
                  {o.invoice}
                </button>
                <span className="sub hide-small">{date(o.created_at)}</span>
                <span className="mobile-only">
                  <Badge value={o.status} />
                </span>
                {o.priority === "high" && (
                  <span className="sub">
                    <span className="priority-dot" />
                    Високий
                  </span>
                )}
              </td>
              <td>
                <button
                  className="text-button cell-title"
                  data-action="customer"
                  data-id={o.customer_id}
                  onClick={() => open({ type: "customers", id: o.customer_id })}
                >
                  {o.name}
                </button>
                <span className="sub">{o.phone}</span>
              </td>
              <td>
                <span className="money cell-title">{money(o.total)}</span>
                <Badge value={o.payment_status} />
              </td>
              <td className="hide-small">
                <Badge value={o.status} />
              </td>
              <td className="hide-medium">
                {labels[o.delivery_status] || o.delivery_status}
                <span className="sub">{o.tracking_number}</span>
              </td>
              <td className="hide-medium">{o.manager || "Не призначено"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
