export function moneyUsd(value: string | number) {
  const amount = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("es-VE", {
    style: "currency",
    currency: "USD",
  }).format(Number.isFinite(amount) ? amount : 0);
}

export function shortDate(value: Date | string) {
  return new Intl.DateTimeFormat("es-VE", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
