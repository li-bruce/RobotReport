# Local configuration

English | [简体中文](ENVIRONMENT.zh-CN.md) · [README](../README.md)

The repository includes only [`.env.example`](../.env.example), with every value empty. The application reads **`.env.local` in the project directory**. Filled configuration, state and backups stay outside Git.

## Create your file

For an existing installation, export its current configuration without changing keys:

```bash
python -m scripts.export_env --state /absolute/path/to/state --output /absolute/path/to/state/.env.local
```

The export includes the existing API bearer token, AI settings, bot webhook/robot keys and available callback tokens. It does not print them. Real delivery defaults to off; pass `--live` only for an intended live installation. Back up an existing file before using `--replace`. Export is a maintenance operation: use a consistent state snapshot or briefly stop only this application's service.

For a fresh installation, initialize state and a password first, then export. Alternatively copy `.env.example` to `.env.local` and fill the required settings yourself. A blank file does not initialize an administrator; use `scripts.bootstrap` and `scripts.set_admin_password` as shown in [Deployment](DEPLOYMENT.md).

For a file outside the source checkout, point the process at it:

```bash
export ROBORT_ENV_FILE=/srv/robort/state/.env.local
python -m uvicorn app.main:app --host 127.0.0.1 --port 9874 --workers 1 --no-access-log
```

```powershell
$env:ROBORT_ENV_FILE = 'C:\Robort\state\.env.local'
python -m uvicorn app.main:app --host 127.0.0.1 --port 9874 --workers 1 --no-access-log
```

An explicitly selected file must exist. `ROBORT_ENV_FILE=-` disables file loading for an environment-only deployment. Paths in `ROBORT_ENV_FILE` resolve relative to the project; use absolute state paths for predictable service operation.

## Values

| Variable | Meaning / blank behavior |
| --- | --- |
| `ROBORT_STATE_DIR` | Private state directory. Blank: project `state/local`. |
| `ROBORT_LIVE_SEND` | `1`/`true` sends actual bot messages; `0`/`false` simulates. Blank: simulation. |
| `ROBORT_PUBLIC_BASE_URL` | Public origin, normally HTTPS. Applied to callback and business links. Blank: retain stored business configuration; callbacks use the request origin. |
| `ROBORT_ADMIN_TOKEN` | Optional private bootstrap API token. Blank: retain existing grants. The exporter copies the existing bootstrap token when available. |
| `ROBORT_INITIAL_ADMIN_PASSWORD` | Optional initial password (12–1024 characters). Only used before a password exists. Changing it never resets an existing password. Prefer the hidden password setup prompt and leave this blank. |
| `ROBORT_AI_API_KEY` | Model provider API key. Blank retains the stored value; new state has no key. |
| `ROBORT_AI_MODEL` | Model identifier; must support Chat Completions tool calling. |
| `ROBORT_AI_BASE_URL` | OpenAI-compatible base URL; configure custom endpoints in ROBORT_AI_ENDPOINTS. |
| `ROBORT_AI_ENABLED` | `1`/`true` or `0`/`false`. Blank: retain stored switch; new state is off. |
| `ROBORT_AI_ENDPOINTS` | Additional approved HTTPS model base URLs, comma-separated, without keys or query parameters. |
| `ROBORT_WEBHOOK_JSON_ENDPOINTS` | Generic JSON adapter sending endpoints, comma-separated, without query credentials. |
| `ROBORT_WEBHOOK_ENDPOINTS` | Extra approved HTTPS host/path pairs, comma-separated, **without `?key=`**. Public WPS is built in. |
| `ROBORT_BOTS_JSON` | Private per-bot connection values, keyed by existing bot UUID. Blank: retain stored connections. Use the exporter or admin page. |

Values are literal: `$`, `#`, `!` and environment references inside a value are not expanded. No inline comments or `export` prefixes. Unquoted values are accepted; single quotes surround a literal value; double quotes use JSON escaping. The application writer handles quoting automatically. Unknown/duplicate variable names and multiline values are rejected without echoing secrets.

**Nonempty process environment variables override the file.** A setting managed that way cannot be changed to a conflicting value through the admin page. Remove the duplicate process variable if the admin page should manage it. When using systemd, set `Environment=ROBORT_ENV_FILE=/srv/robort/state/.env.local`; do not also load that same file through `EnvironmentFile=`.

## Private Kingsoft deployments

Before adding a private webhook, put its exact endpoint in the local file and restart:

```ini
ROBORT_WEBHOOK_ENDPOINTS=https://collab.example.org/woa/api/v1/webhook/send
```

Then paste the full outgoing URL, including its key, into the bot's connection editor. Do not place keys in the endpoint allowlist. HTTPS port 443 is required. Arbitrary hosts supplied by incoming callbacks cannot change the outgoing destination.

`ROBORT_BOTS_JSON` contains only `webhook_url`, `robot_key` and `callback_token`. It refers to existing bots; it does not recreate names, groups, capabilities or business data. Create a bot/group in the UI first. To migrate an installation, restore the **complete state plus local env**, not the env file alone.

## Updates and persistence

- At startup, nonempty local settings are validated and applied to the private runtime store. Blank settings preserve existing values; to clear an existing AI key, use the admin page.
- Admin changes to AI settings, public origin and bot connections write back to the same local file. Callback rotation, connection removal and bot/group deletion update it too.
- The service account needs write access to the file **and its containing directory** for atomic replacement. Linux files written by the application use mode `0600`; restrict access with ACLs on Windows.
- File changes made outside the application require a restart. Stop the service before manually replacing/exporting its configuration. Run one application process per state directory.
- Operational credentials remain in private runtime databases and compatibility files. Local env configuration does not encrypt or eliminate those copies. Back up both securely.

Do not publish `.env.local`, `state/`, `backups/` or `deploy-private/`. The provided `.gitignore` excludes them, but review staged files before publishing your own deployment.
