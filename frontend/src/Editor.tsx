import { useRef, useState, type FormEvent } from "react";
import { ApiClient, ApiError, errorMessage } from "./api";
import { Modal, money } from "./ui";
import type { Customer, DetailTarget, EditorTarget } from "./types";

export function Editor({
  target,
  api,
  onClose,
  onSaved,
}: {
  target: EditorTarget;
  api: ApiClient;
  onClose: () => void;
  onSaved: (open?: DetailTarget) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const key = useRef(crypto.randomUUID());
  const titles = {
    customer: "Контакти клієнта",
    manager: "Відповідальний",
    stock: "Фактичний перерахунок",
    bank: "Звірка з банківською випискою",
    waybill: "Наявна ТТН Нової пошти",
    delivery: "Перевірений статус ТТН",
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || conflict) return;
    const fields = new FormData(event.currentTarget),
      value = (name: string) => String(fields.get(name) || "");
    setBusy(true);
    setError("");
    try {
      let open: DetailTarget | undefined;
      if (target.kind === "customer") {
        const c = target.data;
        const data = await api.crm<Customer>(
          "/customers" + (c ? "/" + c.id : ""),
          c ? "PATCH" : "POST",
          {
            name: value("name"),
            email: value("email"),
            phone: value("phone"),
            ...(c ? { version: c.version } : {}),
          },
        );
        open = { type: "customers", id: data.id };
      } else if (target.kind === "manager") {
        const m = target.data;
        await api.crm(
          "/managers" + (m ? "/" + m.id : ""),
          m ? "PATCH" : "POST",
          { name: value("name"), active: fields.has("active") },
        );
      } else if (target.kind === "stock") {
        const v = target.data;
        await api.inventory("/" + v.variant_id, "PATCH", {
          on_hand: Number(value("on_hand")),
          expected_available: v.available,
          expected_reserved: v.reserved,
          reason: value("reason"),
          request_key: key.current,
        });
      } else {
        const path = "/api/admin/ops/orders/" + target.data.id;
        if (target.kind === "bank")
          await api.request(path + "/bank-confirm", "POST", {
            transaction_id: value("transaction_id"),
            amount: value("amount"),
            currency: "UAH",
            reference: value("reference"),
            attestation: "BANK_STATEMENT_CHECKED",
          });
        if (target.kind === "waybill")
          await api.request(path + "/nova-poshta/waybill", "POST", {
            tracking_number: value("tracking_number"),
            attestation: "WAYBILL_CHECKED_IN_NOVA_POSHTA",
          });
        if (target.kind === "delivery")
          await api.request(path + "/nova-poshta/status", "PATCH", {
            status: value("status"),
            attestation: "STATUS_CHECKED_IN_NOVA_POSHTA",
          });
      }
      if (api.active) onSaved(open);
    } catch (e) {
      if (api.active) {
        setError(errorMessage(e));
        // A stale snapshot must be reopened, never silently overwritten on retry.
        if (
          e instanceof ApiError &&
          e.status === 409 &&
          ["stock", "customer"].includes(target.kind)
        )
          setConflict(true);
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      id="editor"
      title={titles[target.kind]}
      onClose={onClose}
      busy={busy}
    >
      <form id="editor-form" onSubmit={submit}>
        <fieldset disabled={busy}>
          {target.kind === "customer" && (
            <div className="form-grid">
              <div className="full">
                <label htmlFor="contact-name">Ім’я</label>
                <input
                  id="contact-name"
                  name="name"
                  defaultValue={target.data?.name}
                  required
                  minLength={2}
                  maxLength={180}
                />
              </div>
              <div>
                <label htmlFor="contact-phone">Телефон</label>
                <input
                  id="contact-phone"
                  name="phone"
                  type="tel"
                  defaultValue={target.data?.phone}
                  required
                  minLength={5}
                  maxLength={50}
                />
              </div>
              <div>
                <label htmlFor="contact-email">Email</label>
                <input
                  id="contact-email"
                  name="email"
                  type="email"
                  defaultValue={target.data?.email}
                  required
                  maxLength={254}
                />
              </div>
            </div>
          )}
          {target.kind === "manager" && (
            <>
              <label htmlFor="manager-name">Ім’я</label>
              <input
                className="wide"
                id="manager-name"
                name="name"
                defaultValue={target.data?.name}
                required
                minLength={2}
                maxLength={100}
              />
              <label className="check">
                <input
                  id="manager-active"
                  name="active"
                  type="checkbox"
                  defaultChecked={target.data?.active !== false}
                />
                Активний
              </label>
            </>
          )}
          {target.kind === "stock" && (
            <>
              <p>{target.data.sku}</p>
              <label htmlFor="stock-on-hand">На складі, шт.</label>
              <input
                id="stock-on-hand"
                name="on_hand"
                type="number"
                min={target.data.reserved}
                max={1000000}
                step="1"
                defaultValue={target.data.on_hand}
                required
              />
              <p className="sub">У резерві: {target.data.reserved}</p>
              <label htmlFor="stock-reason">Підстава / документ</label>
              <textarea
                id="stock-reason"
                name="reason"
                minLength={3}
                maxLength={300}
                required
              />
            </>
          )}
          {target.kind === "bank" && (
            <>
              <div className="form-grid">
                <div>
                  <label htmlFor="bank-tx">ID транзакції з виписки</label>
                  <input
                    id="bank-tx"
                    name="transaction_id"
                    required
                    minLength={6}
                    maxLength={120}
                  />
                </div>
                <div>
                  <label htmlFor="bank-amount">Сума надходження, UAH</label>
                  <input
                    id="bank-amount"
                    name="amount"
                    type="number"
                    step="0.01"
                    min="0.01"
                    required
                  />
                </div>
                <div className="full">
                  <label htmlFor="bank-ref">
                    Призначення платежу з виписки
                  </label>
                  <textarea
                    id="bank-ref"
                    name="reference"
                    required
                    minLength={6}
                    maxLength={300}
                  />
                </div>
              </div>
              <p className="sub">
                Рахунок {target.data.invoice} · {money(target.data.total)}
              </p>
              <label className="check">
                <input type="checkbox" required />
                Надходження перевірено за реальною банківською випискою
              </label>
            </>
          )}
          {target.kind === "waybill" && (
            <>
              <label htmlFor="waybill">Номер ТТН</label>
              <input
                className="wide"
                id="waybill"
                name="tracking_number"
                inputMode="numeric"
                pattern="[0-9]{14}"
                required
                minLength={14}
                maxLength={14}
              />
              <label className="check">
                <input type="checkbox" required />
                ТТН цього замовлення перевірено в кабінеті Нової пошти
              </label>
            </>
          )}
          {target.kind === "delivery" && (
            <>
              <p>{target.data.tracking_number}</p>
              <label htmlFor="delivery-state">Статус у Новій пошті</label>
              <select id="delivery-state" name="status">
                <option
                  value={
                    target.data.delivery_status === "created"
                      ? "handed_over"
                      : "delivered"
                  }
                >
                  {target.data.delivery_status === "created"
                    ? "У дорозі"
                    : "Доставлено"}
                </option>
              </select>
              <label className="check">
                <input type="checkbox" required />
                Статус цієї ТТН перевірено в кабінеті Нової пошти
              </label>
            </>
          )}
          {error && (
            <p id="editor-error" className="inline-error" role="alert">
              {error}
            </p>
          )}
          {conflict && (
            <p className="inline-error">
              Дані змінилися. Закрийте форму й оновіть картку перед наступним
              редагуванням.
            </p>
          )}
          <div className="form-actions">
            <button type="button" id="cancel-edit" onClick={onClose}>
              Скасувати
            </button>
            <button
              className="primary"
              type="submit"
              id="save-edit"
              disabled={conflict}
            >
              {busy ? "Збереження..." : "Зберегти"}
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
