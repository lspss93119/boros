export function isLocalMode(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1";
}
