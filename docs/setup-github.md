# Set up your GitHub repo (free)

Your repo holds the queue, the schedule, the encrypted vault and (as releases) the media waiting to be
posted. GitHub Actions in the same repo is the always-on publisher.

## 1. Create the repo and push this code

1. On GitHub: **New repository** → name `hermes-social-studio` → **Private** → no README → Create.
2. The zip already contains a git repository with one commit, so you only add your remote and push
   (any terminal; GitHub Codespaces works too):

```bash
unzip hermes-social-studio.zip && cd hermes-social-studio
git remote add origin https://github.com/YOU/hermes-social-studio.git
git push -u origin main
```

   GitHub only accepts pushes that include `.github/workflows/` from a login allowed to change
   workflows. Signing in with `gh auth login` (or Git Credential Manager) works; a classic token needs
   the `workflow` scope.

   Or from the git bundle: `git clone hermes-social-studio.bundle hermes-social-studio`, then
   `cd hermes-social-studio && git remote set-url origin https://github.com/YOU/hermes-social-studio.git && git push -u origin main`.

## 2. Repository secret for the publisher

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `SOCIAL_VAULT_PASSPHRASE` | a passphrase of 16+ characters, e.g. four random words. Use the same one on Kaggle. |
| `KAGGLE_API_TOKEN` | for autopilot and `/studio`: <https://www.kaggle.com/settings> → API → create token ([autopilot.md](autopilot.md)) |

The workflows ask for exactly the permissions they need (`contents: write`, `issues: write`) in their
own files, so the default *Workflow permissions* setting can stay as it is. Check that **Actions** are
enabled (Settings → Actions → General → Allow all actions).

## 3. A token for Kaggle (fine-grained, one repo)

<https://github.com/settings/personal-access-tokens/new>

- **Token name:** `kaggle-social-studio`; **Expiration:** up to a year (put a reminder in your calendar)
- **Repository access:** *Only select repositories* → `hermes-social-studio`
- **Permissions → Repository permissions → Contents:** *Read and write* (Metadata: read is added automatically)

Copy the token and add it on Kaggle as the secret `GITHUB_TOKEN`. It lets the notebook push queued posts
and upload media releases. It cannot change the workflow files, so nothing running on Kaggle can
alter how or when the publisher posts.

## 4. What runs, and what it costs

| Workflow | When | Typical time |
|---|---|---|
| `publish` | every 30 min (`:07` and `:37`), and on demand: publishes, collects Kaggle runs, starts the daily autopilot run | about a minute |
| `telegram` | each control-bot command (started by the Worker) | about a minute |
| `studio` | by hand: start a chat session or an autopilot run, or copy the chat token to Kaggle | about a minute |
| `approvals` | when a post is queued, and on each `/command` comment | under a minute |
| `insights` | daily 03:23 UTC | 1-2 minutes |
| `ci` | when you change code | 3-5 minutes |

Public repositories have unlimited free Actions minutes. A private repository on GitHub Free gets
2,000 minutes a month and every run counts as at least one minute, so publishing every 30 minutes uses
roughly 1,500. To use fewer, edit the cron line in `.github/workflows/publish.yml` to `"7 * * * *"`
(hourly).

GitHub can start scheduled runs late when it is busy, and it pauses schedules in public repos after 60
days without activity (the studio's own commits count as activity).

## 5. Getting notified

When a publish run fails to post something, the workflow run fails and GitHub emails you (Settings →
Notifications → Actions). Approval issues notify you like any issue; the GitHub mobile app is the
easiest place to answer them.
