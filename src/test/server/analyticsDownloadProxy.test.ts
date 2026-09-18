import { describe, expect, it, vi } from "vitest";

import { proxyAnalyticsDownload } from "../../../server/analyticsDownloadProxy.mjs";

describe("analytics download proxy", () => {
  it("streams the signed CSV response with safe download headers", async () => {
    const response = {
      writeHead: vi.fn(),
      write: vi.fn(() => true),
      end: vi.fn(),
    };
    const analyticsFetch = vi.fn().mockResolvedValue(new Response("ticket_id\r\nD-1\r\n", {
      status: 200,
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": 'attachment; filename="defect-records-fp.csv"',
        "cache-control": "no-store",
      },
    }));

    await proxyAnalyticsDownload({
      pathname: "/api/agent/analytics/defects/export/download",
      searchParams: new URLSearchParams({ token: "download-token" }),
      response,
      analyticsFetch,
    });

    expect(analyticsFetch).toHaveBeenCalledWith(
      "http://127.0.0.1:3003/api/agent/analytics/defects/export/download?token=download-token",
    );
    expect(response.writeHead).toHaveBeenCalledWith(200, {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="defect-records-fp.csv"',
      "Cache-Control": "no-store",
    });
    expect(response.write).toHaveBeenCalledTimes(1);
    expect(response.end).toHaveBeenCalledTimes(1);
  });
});