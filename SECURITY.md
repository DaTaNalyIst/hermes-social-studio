# Security

This project holds the keys to your public accounts. It is designed so that a leaked repo, log,
notebook or chat transcript does not leak them, and so that the AI agent never holds them at all.

## Who can do what

| Component | Holds | Can |
|---|---|---|
| You, in the notebook's *Connect accounts* cell | the secrets you type | connect accounts (writes the encrypted vault) |
| Hermes on Kaggle | a GitHub token for **this repo only** (Contents: read/write) | render media, queue posts, approve only when you say so; it never needs platform tokens and does not publish |
| GitHub Actions publisher | vault passphrase (repo secret), its own short-lived `GITHUB_TOKEN` | decrypt the vault, publish due posts, refresh tokens, read engagement |
| Approval bot | its own `GITHUB_TOKEN` | apply `/approve` etc. from the repo owner and collaborators only |

The notebook reads `SOCIAL_VAULT_PASSPHRASE` from Kaggle secrets so that *you* can connect accounts
there, and keeps it out of the environment that Hermes and its tools inherit. Any code running in the
session could still ask Kaggle for the notebook's attached secrets, so for strict separation detach
`SOCIAL_VAULT_PASSPHRASE` from the notebook (Add-ons → Secrets) once your accounts are connected;
nothing else on Kaggle needs it.

## Where secrets live

| Secret | Where | Never in |
|---|---|---|
| Meta app secret, Page token, Threads token, Google client secret + refresh token, Bluesky app password, Telegram bot token, bucket keys | `state/vault.enc` (encrypted, committed) | plain files, config, logs, chat |
| Vault passphrase | GitHub repo secret + Kaggle secret | the repo, Hermes' chats |
| GitHub fine-grained token | Kaggle secret, then `~/.git-credentials` (mode 600) inside the session | the repo |
| Kaggle API token (autopilot, `/studio`) | GitHub repo secret `KAGGLE_API_TOKEN` | the repo, Kaggle runs |
| Hermes chat bot token, for sessions started from GitHub/Telegram | the vault, plus a **private** Kaggle dataset `hermes-studio-keys` that launched runs attach | the repo, notebook code |
| Control bot token | the vault | the Cloudflare Worker (it replies without a token) |
| Worker's GitHub token (Actions: read/write only) and webhook secret | Cloudflare Worker secrets | the repo |

- **Encryption:** Fernet (AES-128-CBC + HMAC-SHA256), key from the passphrase via scrypt
  (n = 2^15, r = 8, p = 1) with a fresh random salt on every save. The names of secrets are
  encrypted too. Passphrases shorter than 16 characters are refused.
- **Why the vault is committed:** the publisher on GitHub needs it, and rotating tokens (Threads)
  must be written back. Old versions stay in git history, encrypted. Treat the passphrase as the
  real secret; if it leaks, rotate everything (below).
- **Entry:** secrets are typed into hidden prompts (`social auth …`, `social secrets set NAME`), never
  passed as arguments. OAuth flows that need a redirect use PKCE and a random `state`; you paste the
  redirect address back into a hidden prompt.
- **Output:** every secret read from the vault is registered with a redaction filter; logs, errors and
  CLI output also mask anything shaped like a Meta, Threads, Google, GitHub, Telegram, JWT or presigned-URL
  credential and `token=`-style pairs. Raw tracebacks only with `SOCIAL_DEBUG=1`.
- **On the wire:** HTTPS only. Meta calls carry `appsecret_proof`. Media links for Instagram/Threads are
  either an unpublished Page photo deleted after use, or presigned bucket links that expire in
  `url_ttl_minutes` and are deleted right after publishing. Media downloaded by the publisher is
  checked against the SHA-256 recorded when it was queued.
- **Least privilege:** the GitHub token is fine-grained to one repository; workflows request only
  `contents` and `issues`; bucket keys should be limited to one bucket; Meta and Google scopes are the
  minimum for posting and reading engagement.
- **Approvals:** comments are obeyed only from users with write access (`OWNER`, `MEMBER`,
  `COLLABORATOR`); the default `approval = "required"` means Hermes alone can never publish.
- **Duplicate protection:** a target is marked `publishing` before the upload. If a run dies midway it
  becomes `unknown` and is never retried automatically, so a half-finished run cannot double-post.

## Runs started from GitHub (autopilot, `/studio`)

These Kaggle runs get a snapshot of the repo **without** `state/vault.enc`, and no GitHub token or
vault passphrase. They hand posts back as files in their output (the outbox), and the publisher queues
them on GitHub. A compromised or misbehaving run can therefore stage posts, but only for your approval
under the rules in `autopilot.toml`. It cannot publish, read platform tokens or change the repo.

The control bot only obeys the user ids in `control.allowed_users`. The Worker checks them, and the
GitHub workflow checks them again from the vault. Telegram's webhook secret header stops anyone else
from posting fake updates to the Worker. If `WEBHOOK_SECRET` leaks, run `social auth control` again
and update the Worker.

## Repository hygiene

- `.gitignore` excludes plaintext secrets, credentials files and media; only `state/vault.enc` is
  allowed through.
- `pre-commit` with **gitleaks** blocks commits containing credentials: `pip install pre-commit && pre-commit install`.
- CI runs gitleaks on every push and pull request (`state/vault.enc` and test fixtures are allow-listed).
- Keep the repository **private**. It works public too (Actions are then unlimited), but your queue,
  captions and schedule would be public, and media releases are visible to anyone.

## If something leaks

1. Rotate at the source: Meta (App settings → reset app secret; then `social auth facebook` /
   `social auth threads`), Google Cloud (delete and recreate the OAuth client; revoke at
   <https://myaccount.google.com/permissions>), Bluesky (delete the app password), Telegram (`/revoke`
   in @BotFather), Backblaze (delete the application key), GitHub (revoke the fine-grained token).
2. If the passphrase may be exposed: `social secrets change-passphrase`, then update the secret on
   GitHub and Kaggle. Because old encrypted vaults remain in git history, also rotate the platform
   credentials in step 1.
3. Purge any plaintext secret from git history (`git filter-repo`); rotating is still required.

## Reporting

Open a private security advisory on the GitHub repository (Security → Report a vulnerability).
