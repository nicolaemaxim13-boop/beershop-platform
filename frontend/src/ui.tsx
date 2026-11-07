import { useEffect, useRef, useState, type ReactNode } from "react";
import { RefreshCw, X } from "lucide-react";
import { errorMessage } from "./api";
import type { ProductImage } from "./types";

export const labels: Record<string, string> = {
  pending: "Нове",
  confirmed: "Підтверджено",
  picking: "Комплектується",
  reserved: "У резерві",
  shipped: "Списано",
  released: "Резерв знято",
  fulfilled: "Виконано",
  cancelled: "Скасовано",
  paid: "Оплачено",
  unpaid: "Не оплачено",
  not_created: "Без ТТН",
  created: "ТТН зареєстровано",
  handed_over: "У дорозі",
  delivered: "Доставлено",
  high: "Високий",
  normal: "Звичайний",
  low: "Низький",
};
export const money = (value: string | number) =>
  new Intl.NumberFormat("uk-UA", { style: "currency", currency: "UAH" }).format(
    Number(value),
  );
export const date = (value: string | null) =>
  value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("uk-UA", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(value))
    : "Дата не зафіксована";
export const invoice = (id: number) => "BS-" + String(id).padStart(6, "0");
export function Badge({ value }: { value: string }) {
  return <span className={"badge " + value}>{labels[value] || value}</span>;
}
export function Photo({ item }: { item: ProductImage }) {
  const [failed, setFailed] = useState(false);
  let url: URL;
  try {
    url = new URL(item.image_url || "", location.origin);
  } catch {
    return null;
  }
  if (failed || !item.image_url || !["http:", "https:"].includes(url.protocol))
    return null;
  return (
    <img
      className="product-photo"
      src={url.href}
      alt={item.name}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}
export function Modal({
  id,
  title,
  children,
  onClose,
  onRefresh,
  busy = false,
}: {
  id: "detail" | "editor";
  title: string;
  children: ReactNode;
  onClose: () => void;
  onRefresh?: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = ref.current!;
    const previous = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => {
      element.close();
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      id={id}
      className={id === "detail" ? "drawer" : "editor"}
      aria-labelledby={id + "-title"}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header className="drawer-head">
        <h2 id={id + "-title"}>{title}</h2>
        <div className="actions">
          {onRefresh && (
            <button
              type="button"
              className="icon"
              title="Оновити картку"
              aria-label="Оновити картку"
              onClick={onRefresh}
            >
              <RefreshCw />
            </button>
          )}
          <button
            type="button"
            className="icon"
            data-close={id}
            title="Закрити"
            aria-label={id === "detail" ? "Закрити картку" : "Закрити форму"}
            disabled={busy}
            onClick={onClose}
          >
            <X />
          </button>
        </div>
      </header>
      {children}
    </dialog>
  );
}
export function ErrorPanel({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error-panel" role="alert">
      <p>{message}</p>
      {retry && <button onClick={retry}>Повторити</button>}
    </div>
  );
}

export function useQuery<T>(
  key: string,
  loader: (signal: AbortSignal) => Promise<T>,
): { data?: T; error?: string; loading: boolean } {
  const [result, setResult] = useState<{
    key: string;
    data?: T;
    error?: string;
  }>();
  useEffect(() => {
    const controller = new AbortController();
    loader(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ key, data });
      },
      (error) => {
        if (!controller.signal.aborted)
          setResult({ key, error: errorMessage(error) });
      },
    );
    return () => controller.abort();
  }, [key, loader]);
  return result?.key === key
    ? { ...result, loading: false }
    : { loading: true };
}
