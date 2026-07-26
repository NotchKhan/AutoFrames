export function formatMilliseconds(milliseconds: number): string {
  const sign = milliseconds < 0 ? "−" : "";
  let value = Math.abs(Math.round(milliseconds));
  const hours = Math.floor(value / 3_600_000);
  value %= 3_600_000;
  const minutes = Math.floor(value / 60_000);
  value %= 60_000;
  const seconds = Math.floor(value / 1_000);
  const millis = value % 1_000;
  return `${sign}${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function humanFileSize(bytes: number): string {
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}
