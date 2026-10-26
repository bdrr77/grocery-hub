import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const allowed = (route === "items" && ["GET", "POST"].includes(request.method)) ||
    (route === "integrations" && request.method === "GET") ||
    (route === "items/named" && request.method === "POST") ||
    (/^items\/[A-Za-z0-9_-]{8,32}\/(search|identity)$/.test(route) && request.method === "POST") ||
    (/^items\/[A-Za-z0-9_-]{8,32}\/search-suggestions$/.test(route) && request.method === "GET") ||
    (/^integrations\/(picnic|chronodrive)\/(connect|sync|search|match|refresh)$/.test(route) && request.method === "POST") ||
    (/^integrations\/picnic\/(send-code|verify-code)$/.test(route) && request.method === "POST") ||
    (/^items\/[A-Za-z0-9_-]{8,32}$/.test(route) && ["DELETE", "PATCH"].includes(request.method));
  if (!allowed) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  const origin = request.headers.get("origin");
  if (request.method !== "GET" && origin && new URL(origin).host !== request.headers.get("host")) {
    return NextResponse.json({ detail: "Origin not allowed" }, { status: 403 });
  }
  try {
    const base = process.env.API_INTERNAL_URL || "http://api:8000";
    const response = await fetch(`${base.replace(/\/$/, "")}/api/v1/${route}`, {
      method: request.method,
      headers: { "Content-Type": "application/json" },
      body: ["POST", "PATCH"].includes(request.method) ? await request.text() : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(route.startsWith("integrations/") || route.endsWith("/search") ? 90000 : 15000),
    });
    return new Response(response.status === 204 ? null : await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ detail: "API indisponible. Vérifiez le service API et API_INTERNAL_URL." }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as DELETE, proxy as PATCH };
