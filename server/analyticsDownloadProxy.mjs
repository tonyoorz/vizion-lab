const ANALYTICS_API_BASE = "http://127.0.0.1:3003";

function buildAnalyticsUrl(pathname, searchParams) {
  const analyticsUrl = new URL(pathname, ANALYTICS_API_BASE);
  searchParams.forEach((value, key) => analyticsUrl.searchParams.append(key, value));
  return analyticsUrl.toString();
}

function downloadHeaders(analyticsResponse) {
  const headers = {
    "Content-Type": analyticsResponse.headers.get("content-type") || "application/octet-stream",
    "Cache-Control": analyticsResponse.headers.get("cache-control") || "no-store",
  };
  const contentDisposition = analyticsResponse.headers.get("content-disposition");
  if (contentDisposition) {
    headers["Content-Disposition"] = contentDisposition;
  }
  return headers;
}

function writeChunk(response, chunk) {
  if (response.write(chunk)) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const onDrain = () => {
      response.off("error", onError);
      resolve();
    };
    const onError = (error) => {
      response.off("drain", onDrain);
      reject(error);
    };
    response.once("drain", onDrain);
    response.once("error", onError);
  });
}

export async function proxyAnalyticsDownload({
  pathname,
  searchParams,
  response,
  analyticsFetch = globalThis.fetch,
}) {
  const analyticsResponse = await analyticsFetch(buildAnalyticsUrl(pathname, searchParams));
  response.writeHead(analyticsResponse.status, downloadHeaders(analyticsResponse));
  if (!analyticsResponse.body) {
    response.end();
    return;
  }
  for await (const chunk of analyticsResponse.body) {
    await writeChunk(response, chunk);
  }
  response.end();
}