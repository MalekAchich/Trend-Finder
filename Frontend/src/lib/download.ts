import { ApiError } from "../api/client";

/** The saved file's name, from the server's Content-Disposition header. */
export function fileName(disposition: string | null, fallback: string): string {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition ?? "");
  if (star) return decodeURIComponent(star[1]);
  return /filename="?([^";]+)"?/i.exec(disposition ?? "")?.[1] ?? fallback;
}

/** Fetches a download from the app and hands it to the browser as a file (errors keep the server's reason). */
export async function saveFile(path: string, fallback: string): Promise<void> {
  const res = await fetch(path);
  if (!res.ok) {
    const detail = res.headers.get("content-type")?.includes("json") ? (await res.json()).detail : undefined;
    throw new ApiError(typeof detail === "string" ? detail : `download failed (${res.status})`, res.status);
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName(res.headers.get("content-disposition"), fallback);
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
