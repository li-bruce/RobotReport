# Bot, API and AI integration

English | [简体中文](INTEGRATION.zh-CN.md) · [README](../README.md)

## Kingsoft Collaboration / WPS

There are **two different URLs**:

| URL | Direction | Where it comes from |
| --- | --- | --- |
| Outgoing webhook | Robort → Kingsoft group | Copy from the group's webhook bot into the Robort admin page. |
| Callback | Kingsoft → Robort | Copy from Robort into that bot's group settings. |

Create/select a named group, add a bot and configure both URLs. Leave an unknown group ID blank: the first authenticated incoming message discovers it. A send-only webhook does not prove which group is calling back. Each bot belongs to one current group; several bots may share the group, using separate credentials.

The current outgoing allowlist in `app/security.py::validate_webhook` is:

```text
https://xz.wps.cn/api/v1/webhook/send?key=<YOUR_KEY>
```

For a private deployment, add its exact HTTPS host/path (without the key) to `ROBORT_WEBHOOK_ENDPOINTS` in the local file and restart; see [local configuration](ENVIRONMENT.md). Do not permit arbitrary callback-provided destination URLs. The outgoing `key` is extracted by the connection workflow; do not paste an entire callback URL into a key field.

### Incoming protocol

Callback path: `/kingsoft/callback/{bot_id}/{token}`. The generated token is a credential.

- **GET** validates the URL and returns `{"result":"ok"}` for a valid, active bot.
- **POST** validates the token, `robot_key`, group binding and enabled state, processes the message and queues the reply.
- `{"result":"ok"}` acknowledges receipt; the reply is sent separately through the outgoing webhook.

Illustrative POST body (placeholders only):

```json
{
  "chatid": "12345",
  "creator": "67890",
  "content": "@机器人 帮助",
  "robot_key": "<SAME_KEY_AS_OUTGOING_WEBHOOK>",
  "ctime": 1792022400
}
```

`chatid` and `creator` identify the group and sender. The official callback also includes optional reply/context fields. If a display name is supplied by the deployment, Robort may use it; registration collects the member's actual name. Duplicate incoming events are deduplicated per transport. Unknown execution/delivery outcomes are retained for inspection rather than automatically replayed.

WPS forwards messages that **mention the bot**; ordinary unmentioned group messages are not available through this interface. The official documentation describes the GET verification, callback fields and outgoing Markdown subset. Native Markdown tables are not in that documented subset, so this project renders table images where appropriate, with textual fallback. See the [official webhook reference](https://open.wps.cn/documents/app-integration-dev/guide/robot/webhook).

Configure the public HTTPS origin in the local file or the admin page; the application synchronizes callback and business-link configuration. See [Deployment](DEPLOYMENT.md#configuration).

### Capabilities and routing

Capabilities are `registration`, `enrollment`, `daily_events`, `large_events`, `match_records`, `statistics`, `messages`, `ai` and `management`. New bots enable all except `large_events`, `ai` and `management` by default; each can be changed.

Bot capability, venue/session scope, member eligibility and user permissions are separate checks. Help reflects those settings. Management notices are delivered by a management-enabled bot. Event notifications use selected event channels and deduplicate the same physical group. A normal chat reply goes to its source group.

## Administrative HTTP API

The backend exposes authenticated JSON endpoints under `/api/`. It does not provide interactive Swagger/Redoc pages. Routes in `app/main.py` and `app/management_api.py` are the source of truth for request schemas.

### Authentication

- **Browser:** `POST /auth/login` with `{"password":"..."}` sets the HttpOnly `robort_session` cookie and returns `csrf_token`. Send that token as `X-CSRF-Token` for mutating `/api/` requests. `GET /auth/session` retrieves session status; `POST /auth/logout` ends it. The web interface handles this flow.
- **Trusted scripts:** `Authorization: Bearer <admin_token>`. `scripts.bootstrap` writes this credential into the private state file `admin.local.json`. Never embed it in a frontend, URL or public example. A bot-scoped operations credential does not grant access to all shared business data.

Read-only example, run from the project environment with `ROBORT_STATE_DIR` set:

```python
import json
import os
from pathlib import Path
import httpx

state = Path(os.environ["ROBORT_STATE_DIR"])
token = json.loads((state / "admin.local.json").read_text())["admin_token"]
with httpx.Client(
    base_url="http://127.0.0.1:9874",
    headers={"Authorization": f"Bearer {token}"},
    timeout=15,
) as client:
    response = client.get("/api/venues")
    response.raise_for_status()
    print(response.json())
```

Use HTTPS for a remote server. The example prints venue data only, never the token.

### Main endpoint groups

| Area | Endpoints |
| --- | --- |
| System | `GET/PATCH /api/config`, `GET /healthz` (public) |
| Bots | `GET/POST /api/bots`, `GET /api/bots/{bot_id}/callback` |
| Members | `GET/POST /api/members`, `PATCH /api/members/{user_id}`, `PUT /api/members/{user_id}/roles` |
| CSV import | `GET /api/member-imports/template`, `POST /api/member-imports/preview`, `POST /api/member-imports/commit` |
| Membership | `GET /api/members/{user_id}/membership`, `POST /api/members/{user_id}/membership/periods` |
| Venues | `GET/POST /api/venues`, `PATCH /api/venues/{venue_id}` |
| Calendar | `GET /api/activity/calendar?month=YYYY-MM`, `GET /api/activity/day?date=YYYY-MM-DD` |
| Recurring rules | `GET/PUT /api/activity/slot-plan` |
| Date plans | `GET /api/activity/date-plans?year=YYYY`, `PUT/DELETE /api/activity/date-plans/{day}?venue_id=1` |
| Holiday plans | `GET /api/holiday-plan?year=YYYY&venue_id=1`, `PUT /api/holiday-plan/{year}/{holiday_id}?venue_id=1` |
| Holiday refresh | `POST /api/holiday-calendar/{year}/refresh` |
| Events | `GET/POST /api/tournaments`, `GET/PATCH /api/tournaments/{event_id}`, `POST /api/tournaments/{event_id}/actions` |
| Scores / organization | `POST /api/tournaments/{event_id}/scores`, `GET/PUT /api/tournaments/{event_id}/organization` |

Mutating schedule plans require the current `revision`; score corrections require the expected score version. Read the current object before changing it. Do not automatically retry a mutation after a timeout: query its outcome first. Errors use JSON `detail`; common statuses are 400 invalid input, 401 missing login, 403 denied, 409 database conflict, 413 oversized body and 422 malformed request.

CSV import is preview → administrator review → commit. Use the downloadable template; supported columns include `姓名,手机号,性别,自评级别,组别`. Name/phone are required for pre-registration. Import does not grant management roles. Form registration requires gender; imported legacy profiles may need to complete it before gender-specific competition.

## AI business workflow

Configure SiliconFlow in the admin page and enable AI on each relevant bot. The supported endpoint is fixed to `https://api.siliconflow.cn/v1`; default model: `deepseek-ai/DeepSeek-V3.2`. A different provider is a code integration, not an arbitrary URL setting. [Provider documentation](https://api-docs.siliconflow.cn/docs/api/chat-completions-post).

```text
AI-prefixed request → trusted identity + permitted feature context
                    → structured intent / ordered plan
                    → direct query OR numbered change preview
                    → same user's 确认 in the originating bot/group
                    → authorization recheck → sequential execution → results
```

`ai_catalog.py` describes business features; `ai_planner.py` and `ai_operations.py` define permitted plans and operations; `ai_plan_store.py` / `ai_plan_flow.py` persist confirmation and execution state. The model does not execute SQL, shell commands or arbitrary code. An AI-generated role claim cannot grant permission.

Each user has one current plan. A revised AI message replaces it. The confirmation timeout is configurable (default five minutes). At most eight steps are planned; execution stops on failure and reports completed steps. The model receives the request and selected business context needed for supported operations; enabling it sends that information to SiliconFlow.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Callback cannot be saved | Public DNS/TLS, cloud/host/panel firewall, proxy route and exact generated URL; a valid GET must return `{"result":"ok"}`. |
| Bot receives but does not reply | `ROBORT_LIVE_SEND`, outgoing URL, active connection, capability settings and outbox result. |
| Second bot in the same group is silent | Its own outgoing key and callback; it must not reuse the first bot's callback. |
| Registration/image links fail | Configured public origin and access from the user's device. |
| AI is unavailable | Global AI switch, bot AI capability, provider key/model and sender's membership/permissions. |
| Mutation reports a stale revision | Reload the current object, review changes and submit using the new revision. |

Use synthetic state for callback/API tests. A test bot inside production shares real membership and event data.
