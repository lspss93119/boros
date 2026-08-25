import { describe, expect, it, vi } from "vitest";
import { dashboardFetch, DashboardApiError } from "./api";

describe("dashboard API", () => {
  it("returns the data from a successful envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true, data: { value: 1 }, meta: { ts: 1 } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(dashboardFetch<{ value: number }>("/api/dashboard/health")).resolves.toEqual({
      ok: true,
      data: { value: 1 },
      meta: { ts: 1 },
    });
  });

  it("rejects HTTP and API envelope errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            error: { code: "snapshot_invalid", message: "dashboard request failed" },
            meta: { ts: 1 },
          }),
          { status: 503 },
        ),
      ),
    );

    await expect(dashboardFetch("/api/dashboard/opportunities")).rejects.toBeInstanceOf(
      DashboardApiError,
    );
  });

  it("fails closed on an incomplete error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: false }), { status: 503 }),
      ),
    );

    await expect(dashboardFetch("/api/dashboard/positions")).rejects.toBeInstanceOf(
      DashboardApiError,
    );
  });
});
