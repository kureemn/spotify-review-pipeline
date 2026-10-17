// Shared Neon client. DATABASE_URL is injected by the Vercel–Neon integration (never sent to the browser).
import { neon } from "@neondatabase/serverless";

export const sql = neon(process.env.DATABASE_URL);

export function send(res, status, body) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  // Data only changes when the loader runs; let the CDN cache briefly.
  res.setHeader("Cache-Control", "public, s-maxage=300, stale-while-revalidate=600");
  res.status(status).send(JSON.stringify(body));
}

export function handler(fn) {
  return async (req, res) => {
    try {
      send(res, 200, await fn(req));
    } catch (e) {
      console.error(e);
      send(res, e.status || 500, { error: e.status ? e.message : "database error" });
    }
  };
}

export function badRequest(msg) {
  const e = new Error(msg);
  e.status = 400;
  return e;
}
