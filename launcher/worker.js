// Hermes Social Studio - Telegram control bot relay (Cloudflare Workers, free plan).
//
// Telegram sends every message for your control bot here (webhook). This Worker:
//   1. checks Telegram's secret header (WEBHOOK_SECRET) and that the sender is in ALLOWED_USERS,
//   2. answers at once ("On it..."), using Telegram's reply-in-response feature, so it needs
//      no bot token,
//   3. starts the `telegram` workflow in your GitHub repo, which does the real work and replies.
//
// Settings (Worker > Settings > Variables and Secrets):
//   WEBHOOK_SECRET  secret  the value `social auth control` gave you
//   ALLOWED_USERS   text    your Telegram user id (comma-separated for several)
//   GITHUB_TOKEN    secret  fine-grained token: this repo only, Actions: Read and write
//   GITHUB_REPO     text    you/hermes-social-studio
//   GITHUB_REF      text    main (optional)

export function normalize(update) {
  if (update.callback_query) {
    const c = update.callback_query;
    const m = c.message || {};
    return {
      type: "callback", data: String(c.data || "").slice(0, 64), user_id: String(c.from.id),
      chat_id: String((m.chat && m.chat.id) || c.from.id), message_id: m.message_id || null,
      callback_id: c.id,
    };
  }
  const m = update.message;
  if (m && m.text && m.from) {
    return { type: "message", text: String(m.text).slice(0, 500), user_id: String(m.from.id),
             chat_id: String(m.chat.id) };
  }
  return null;
}

function json(body) {
  return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
}

export async function handle(request, env, fetchImpl = fetch) {
  if (request.method !== "POST") return new Response("Hermes Social Studio control relay", { status: 200 });
  if (!env.WEBHOOK_SECRET || request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
    return new Response("forbidden", { status: 403 });
  }
  let update;
  try {
    update = await request.json();
  } catch {
    return new Response("bad request", { status: 400 });
  }
  const u = normalize(update);
  if (!u) return json({});
  const allowed = String(env.ALLOWED_USERS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!allowed.includes(u.user_id)) {
    return u.type === "message"
      ? json({ method: "sendMessage", chat_id: u.chat_id, text: "This bot only answers its owner." })
      : json({});
  }
  const res = await fetchImpl(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/telegram.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "hermes-social-studio-relay",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { update: JSON.stringify(u) } }),
    },
  );
  const ok = res.status === 204;
  if (u.type === "callback") {
    return json({ method: "answerCallbackQuery", callback_query_id: u.callback_id,
                  text: ok ? "On it..." : "GitHub did not accept the request" });
  }
  return json({
    method: "sendMessage", chat_id: u.chat_id,
    text: ok ? "On it... (about a minute)"
             : `GitHub refused the request (HTTP ${res.status}). Check GITHUB_TOKEN and GITHUB_REPO in the Worker settings.`,
  });
}

export default { fetch: (request, env) => handle(request, env) };
