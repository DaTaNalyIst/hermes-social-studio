// node launcher/worker.test.mjs
import assert from "node:assert/strict";
import { handle, normalize } from "./worker.js";

const env = { WEBHOOK_SECRET: "s3cret-value-123", ALLOWED_USERS: "42, 7", GITHUB_TOKEN: "github_pat_x",
              GITHUB_REPO: "me/hermes-social-studio" };

function req(body, secret = env.WEBHOOK_SECRET) {
  return new Request("https://relay.example/", {
    method: "POST", body: JSON.stringify(body),
    headers: { "content-type": "application/json", "X-Telegram-Bot-Api-Secret-Token": secret },
  });
}

const calls = [];
const fakeFetch = async (url, init) => { calls.push({ url, init }); return new Response(null, { status: 204 }); };

// wrong secret
let r = await handle(req({ message: { text: "/studio", from: { id: 42 }, chat: { id: 42 } } }, "nope"), env, fakeFetch);
assert.equal(r.status, 403);
assert.equal(calls.length, 0);

// stranger
r = await handle(req({ message: { text: "/studio", from: { id: 99 }, chat: { id: 99 } } }), env, fakeFetch);
assert.match((await r.json()).text, /only answers its owner/);
assert.equal(calls.length, 0);

// owner command -> dispatch
r = await handle(req({ update_id: 1, message: { text: "/studio 60", from: { id: 42 }, chat: { id: 42 } } }), env, fakeFetch);
let body = await r.json();
assert.equal(body.method, "sendMessage");
assert.equal(calls.length, 1);
assert.equal(calls[0].url, "https://api.github.com/repos/me/hermes-social-studio/actions/workflows/telegram.yml/dispatches");
const sent = JSON.parse(calls[0].init.body);
assert.equal(sent.ref, "main");
assert.deepEqual(JSON.parse(sent.inputs.update), { type: "message", text: "/studio 60", user_id: "42", chat_id: "42" });
assert.equal(calls[0].init.headers.Authorization, "Bearer github_pat_x");

// callback button
r = await handle(req({ callback_query: { id: "cb1", data: "ap:p_1010_abc123", from: { id: 7 },
                                         message: { message_id: 5, chat: { id: 7 } } } }), env, fakeFetch);
body = await r.json();
assert.equal(body.method, "answerCallbackQuery");
assert.equal(JSON.parse(JSON.parse(calls[1].init.body).inputs.update).data, "ap:p_1010_abc123");

// GitHub refuses
r = await handle(req({ message: { text: "/status", from: { id: 42 }, chat: { id: 42 } } }), env,
                 async () => new Response("{}", { status: 404 }));
assert.match((await r.json()).text, /HTTP 404/);

assert.equal(normalize({ edited_message: {} }), null);
console.log("worker tests passed");
