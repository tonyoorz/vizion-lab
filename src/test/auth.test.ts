import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { login } from "@/lib/auth";

describe("login", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts mobile push authentication by default", async () => {
    fetchMock.mockResolvedValue({ ok: true });

    const result = await login("q123456", "strong-pin");

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        username: "q123456",
        password: "strong-pin",
        strong_auth: true,
        strong_auth_type: "mobile",
      }),
    });
  });

  it("uses browser login only when strong authentication is disabled", async () => {
    fetchMock.mockResolvedValue({ ok: true });

    const result = await login("q123456", "tss-password", { strongAuth: false });

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/browser-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username: "q123456", password: "tss-password" }),
    });
  });

});