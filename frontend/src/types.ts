export type View = "orders" | "customers" | "inventory" | "managers";
export type OrderStatus =
  "pending" | "confirmed" | "picking" | "fulfilled" | "cancelled";
export type Priority = "low" | "normal" | "high";
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
}
export interface Manager {
  id: number;
  name: string;
  active: boolean;
}
export interface Contact {
  name: string;
  email: string;
  phone: string;
}
export interface Customer extends Contact {
  id: number;
  version: number;
  created_at: string | null;
}
export interface CustomerRow extends Customer {
  orders: number;
  paid_total: string;
}
export interface Note {
  id: number;
  body: string;
  kind: string;
  order_id: number | null;
  created_at: string;
}
export interface OrderRow extends Contact {
  id: number;
  invoice: string;
  total: string;
  status: OrderStatus;
  payment_status: "paid" | "unpaid";
  customer_id: number;
  priority: Priority;
  manager_id: number | null;
  manager: string | null;
  created_at: string | null;
  version: number;
  delivery_status: string;
  tracking_number: string | null;
}
export interface ProductImage {
  name: string;
  image_url: string | null;
}
export interface OrderDetail extends OrderRow {
  currency: string;
  payment_method: string;
  delivery_address: string;
  items: (ProductImage & {
    sku: string;
    variant_id: number;
    quantity: number;
    unit_price: string;
  })[];
  customer: Customer;
  notes: Note[];
  audit: { event: string; at: string; note: string }[];
  inventory: { variant_id: number; quantity: number; state: string }[];
}
export interface CustomerDetail extends Customer {
  orders: OrderRow[];
  notes: Note[];
}
export interface StockRow extends ProductImage {
  variant_id: number;
  product_id: number;
  sku: string;
  active: boolean;
  available: number;
  reserved: number;
  on_hand: number;
}
export interface StockDetail extends StockRow {
  reservations: { order_id: number; quantity: number }[];
  movements: {
    id: number;
    order_id: number | null;
    event: string;
    available_delta: number;
    reserved_delta: number;
    reason: string;
    at: string;
  }[];
}
export interface Summary {
  orders: number;
  statuses: Partial<Record<OrderStatus, number>>;
  awaiting_payment: number;
  customers: number;
}
export interface StockSummary {
  skus: number;
  available: number;
  reserved: number;
  on_hand: number;
}
export type DetailTarget = {
  type: "orders" | "customers" | "inventory";
  id: number;
};
export type EditorTarget =
  | { kind: "customer"; data?: Customer }
  | { kind: "manager"; data?: Manager }
  | { kind: "stock"; data: StockDetail }
  | { kind: "bank" | "waybill" | "delivery"; data: OrderDetail };
