/* The Vector Relay relay: passes sealed text between one PC and its phones, and can read none of it.

   The key never reaches it. What it does know: a random channel name, sizes, and when the PC and
   the phone last asked.

     PUT    /c/<channel>/state        PC, X-Relay-Write: keep a sealed copy of the feed (the first write claims the channel)
     GET    /c/<channel>/state        phone: read that copy (shown when the PC does not answer)
     POST   /c/<channel>/ask/<id>     phone: leave a sealed question (at most 30 waiting)
     POST   /c/<channel>/asks/take    PC, X-Relay-Write: read the questions and clear them (?wait=20 holds the request until one comes)
     PUT    /c/<channel>/answer/<id>  PC, X-Relay-Write: leave the sealed answer
     GET    /c/<channel>/answer/<id>  phone: read the answer once (204 while there is none; ?wait=8 holds the request until it comes)
     DELETE /c/<channel>              PC, X-Relay-Write: forget this channel

   Runs as a Cloudflare Pages Function next to the phone page, so the phone talks to one address.
   Storage: one D1 database bound as DB (schema.sql). Questions and answers live 5 minutes.
*/

const MAX_STATE = 300000;
const MAX_ASK = 1200000;
const MAX_ASKS = 30;
const MAX_ANSWER = 1600000;
const LIVE_MS = 5 * 60 * 1000;
const KEEP_MS = 30 * 24 * 3600 * 1000;
const TAKE_HOLD_S = 20, TAKE_LOOK_MS = 700;      // a held request looks into the database this often:
const ANSWER_HOLD_S = 10, ANSWER_LOOK_MS = 400;  // under the 50 queries one free request may make
const CHANNEL = /^[A-Za-z0-9_-]{16,64}$/;
const ASK_ID = /^[A-Za-z0-9_-]{8,40}$/;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-Relay-Write",
  "Access-Control-Max-Age": "86400",
};

function reply(status, body, type) {
  return new Response(body, {
    status: status,
    headers: Object.assign({ "Content-Type": type || "text/plain", "Cache-Control": "no-store" }, CORS),
  });
}

async function ownerHash(request) {
  const token = request.headers.get("X-Relay-Write") || "";
  if (token.length < 20) return "";
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function pause(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function holdMs(url, most) {
  const asked = Number(url.searchParams.get("wait")) || 0;
  return Math.min(most, Math.max(0, asked)) * 1000;
}

export async function handle(request, env) {
  if (request.method === "OPTIONS") return reply(204, null);
  const url = new URL(request.url);
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts[0] !== "c" || !CHANNEL.test(parts[1] || "")) return reply(404, "Not found.");
  const db = env.DB;
  const channel = parts[1];
  const what = parts.slice(2).join("/");
  const method = request.method;
  const now = Date.now();

  if (what === "state" && method === "PUT") {
    const body = await request.text();
    if (!body || body.length > MAX_STATE) return reply(413, "Too large.");
    const owner = await ownerHash(request);
    if (!owner) return reply(403, "Not yours.");
    const done = await db.prepare(
      "INSERT INTO channels (name, owner, state, seen) VALUES (?1, ?2, ?3, ?4) " +
      "ON CONFLICT(name) DO UPDATE SET state = excluded.state, seen = excluded.seen WHERE channels.owner = excluded.owner"
    ).bind(channel, owner, body, now).run();
    if (!done.meta.changes) return reply(403, "Not yours.");
    if (Math.random() < 0.02) {   // now and then: drop what nobody came back for
      await db.batch([
        db.prepare("DELETE FROM asks WHERE t < ?1").bind(now - LIVE_MS),
        db.prepare("DELETE FROM answers WHERE t < ?1").bind(now - LIVE_MS),
        db.prepare("DELETE FROM channels WHERE seen < ?1").bind(now - KEEP_MS),
      ]);
    }
    return reply(200, "ok");
  }
  if (what === "state" && method === "GET") {
    const row = await db.prepare("SELECT state FROM channels WHERE name = ?1").bind(channel).first();
    return row === null ? reply(404, "Nothing here.") : reply(200, row.state);
  }
  if (what === "" && method === "DELETE") {
    const owner = await ownerHash(request);
    const gone = await db.prepare("DELETE FROM channels WHERE name = ?1 AND owner = ?2").bind(channel, owner).run();
    if (!gone.meta.changes) return reply(404, "Nothing here.");
    await db.batch([
      db.prepare("DELETE FROM asks WHERE channel = ?1").bind(channel),
      db.prepare("DELETE FROM answers WHERE channel = ?1").bind(channel),
    ]);
    return reply(200, "ok");
  }

  const id = parts[3] || "";
  if (parts[2] === "ask" && parts.length === 4 && method === "POST") {
    if (!ASK_ID.test(id)) return reply(404, "Not found.");
    const body = await request.text();
    if (!body || body.length > MAX_ASK) return reply(413, "Too large.");
    const row = await db.prepare(
      "SELECT (SELECT 1 FROM channels WHERE name = ?1) AS known, " +
      "(SELECT COUNT(*) FROM asks WHERE channel = ?1 AND t > ?2) AS waiting"
    ).bind(channel, now - LIVE_MS).first();
    if (!row.known) return reply(404, "Nothing here.");
    if (row.waiting >= MAX_ASKS) return reply(429, "The PC has not picked up the last questions yet.");
    await db.prepare("INSERT OR REPLACE INTO asks (channel, id, body, t) VALUES (?1, ?2, ?3, ?4)").bind(channel, id, body, now).run();
    return reply(200, "ok");
  }
  if (what === "asks/take" && method === "POST") {
    const owner = await ownerHash(request);
    const until = now + holdMs(url, TAKE_HOLD_S);
    for (;;) {
      const row = await db.prepare(
        "SELECT (SELECT 1 FROM channels WHERE name = ?1 AND owner = ?2) AS mine, " +
        "(SELECT COUNT(*) FROM asks WHERE channel = ?1) AS waiting"
      ).bind(channel, owner).first();
      if (!row.mine) return reply(404, "Nothing here.");
      if (row.waiting) break;
      if (Date.now() + TAKE_LOOK_MS > until) return reply(200, JSON.stringify({ asks: [] }), "application/json");
      await pause(TAKE_LOOK_MS);
    }
    const taken = await db.prepare("DELETE FROM asks WHERE channel = ?1 RETURNING id, body, t").bind(channel).all();
    const asks = taken.results.filter((row) => row.t > Date.now() - LIVE_MS).sort((a, b) => a.t - b.t)
      .map((row) => ({ id: row.id, body: row.body }));
    return reply(200, JSON.stringify({ asks: asks }), "application/json");
  }
  if (parts[2] === "answer" && parts.length === 4 && ASK_ID.test(id)) {
    if (method === "GET") {
      const until = now + holdMs(url, ANSWER_HOLD_S);
      for (;;) {
        const row = await db.prepare("DELETE FROM answers WHERE channel = ?1 AND id = ?2 RETURNING body").bind(channel, id).first();
        if (row !== null) return reply(200, row.body);
        if (Date.now() + ANSWER_LOOK_MS > until) return reply(204, null);
        await pause(ANSWER_LOOK_MS);
      }
    }
    if (method === "PUT") {
      const body = await request.text();
      if (!body || body.length > MAX_ANSWER) return reply(413, "Too large.");
      const owner = await ownerHash(request);
      const done = await db.prepare(
        "INSERT OR REPLACE INTO answers (channel, id, body, t) " +
        "SELECT ?1, ?2, ?3, ?4 WHERE EXISTS (SELECT 1 FROM channels WHERE name = ?1 AND owner = ?5)"
      ).bind(channel, id, body, now, owner).run();
      return done.meta.changes ? reply(200, "ok") : reply(404, "Nothing here.");
    }
  }
  return reply(404, "Not found.");
}

export function onRequest(context) {
  return handle(context.request, context.env);
}
