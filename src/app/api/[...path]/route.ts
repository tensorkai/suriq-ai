import type { NextRequest } from "next/server";

async function proxy(request: NextRequest, context: RouteContext<"/api/[...path]">) {
  const { path } = await context.params;
  if (!path.length || !["health", "projects", "batches", "documents"].includes(path[0])) {
    return Response.json({ detail: "Unknown local API route." }, { status: 404 });
  }
  if (path.some((segment) => segment === "." || segment === ".." || /[/\\\x00-\x1f]/.test(segment))) {
    return Response.json({ detail: "Invalid route." }, { status: 400 });
  }
  const host = request.headers.get("host") ?? "";
  const origin = request.headers.get("origin");
  const method = request.method;
  if (
    !/^(localhost|127\.0\.0\.1)(:\d{1,5})?$/.test(host) ||
    request.headers.get("sec-fetch-site") === "cross-site" ||
    (origin && origin !== `http://${host}`) ||
    (method !== "GET" && request.headers.get("x-suriq-request") !== "1")
  ) {
    return Response.json({ detail: "Use the local SuriQ workspace." }, { status: 403 });
  }
  const port = process.env.SURIQ_API_PORT ?? "8765";
  if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    return Response.json({ detail: "Invalid local API port." }, { status: 503 });
  }
  const url = `http://127.0.0.1:${port}/${path.map(encodeURIComponent).join("/")}`;
  const headers = new Headers();
  for (const key of ["content-type", "content-length"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  const limit = Number(process.env.SURIQ_MAX_UPLOAD_MB ?? "20") * 1024 * 1024 + 65_536;
  if (Number(headers.get("content-length")) > limit) {
    return Response.json({ detail: "Request exceeds the upload limit." }, { status: 413 });
  }
  let size = 0;
  const body = request.body?.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      size += chunk.byteLength;
      if (size > limit) controller.error(new Error("Upload limit exceeded"));
      else controller.enqueue(chunk);
    },
  }));
  try {
    const init: RequestInit & { duplex?: "half" } = {
      method, headers, cache: "no-store", redirect: "error",
      ...(body ? { body, duplex: "half" } : {}),
    };
    const upstream = await fetch(url, init);
    const responseHeaders = new Headers({
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Cross-Origin-Resource-Policy": "same-origin",
    });
    for (const key of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(key);
      if (value) responseHeaders.set(key, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json(
      { detail: size > limit ? "Request exceeds the upload limit." : "The local processing service is unavailable." },
      { status: size > limit ? 413 : 503 },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
