# Bot, API and AI integration

English | [简体中文](INTEGRATION.zh-CN.md) · [README](../README.md)

## Generic JSON Webhook

Robort can connect directly to a compatible JSON endpoint or through a platform adapter. Configure `ROBORT_WEBHOOK_JSON_ENDPOINTS=https://adapter.example.org/messages` in `.env.local` and restart, then paste the full sending URL (including any adapter-specific query credential) in the bot connection editor. The generated callback is `/webhooks/{bot_id}/{token}`. Keep this unguessable credential private.

The adapter must validate the chat platform's event signature and obtain user/group IDs from authenticated platform events, never from user-written text. Map mentions to plain commands. The callback token authorizes this adapter to represent the bot's group. A bot remains bound to one group; a different group cannot replace an established binding through a callback.

Send POST JSON to the callback:

```json
{"event_id":"unique-message-123","group_id":"example-group","user_id":"example-user","text":"帮助","display_name":"示例会员"}
```

`event_id` is a stable unique string (1–128 ASCII letters, digits, `_ . : -`) for deduplication; reuse it when retrying an event. `display_name` is optional. `group_id` and `user_id` must be 1–100 ASCII letters, digits or `_ @ . : -`. `text` is 1–4000 characters. Do not send role, permission or destination claims. The response `{"result":"ok"}` acknowledges processing; replies arrive separately at the sending endpoint.

`text_format` defaults to `command`: send only the business body, with no bot address. For original platform text, use `mentions` plus explicit `start`/`end`/`kind` spans; `legacy` supports older raw text. All business commands use the same parsing pipeline. See [Unified message parsing](MESSAGE_PARSING.md) for examples, Unicode offsets and compatibility rules.

Outgoing JSON:

```json
{"type":"markdown","content":{"text":"**Activity reminder**"},"event_id":"42","group_id":"example-group"}
```

Types are `text`, `markdown`, `image` and `file`. The content object retains the message fields (`content` for plain text, `text` for Markdown; media fields depend on the attachment). `event_id` identifies an outbox delivery. Treat any 2xx response, including 204, as accepted; return a non-2xx response on refusal. A 5xx, timeout or uncertain response is held for inspection without automatic replay. This contract does not automatically translate every platform's Markdown, mention or file-upload protocol; adapt these at the integration boundary.

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

Capabilities are `registration`, `member_notifications`, `enrollment`, `daily_events`, `large_events`, `match_records`, `statistics`, `messages`, `ai` and `management`. New bots enable all except `member_notifications`, `large_events`, `ai` and `management` by default; each can be changed. `member_notifications` is opt-in for existing bots as well, including legacy unspecified capability lists. Save it in the bot’s `capabilities` array; it can also have an independent availability window.

Bot capability, venue/session scope, member eligibility and user permissions are separate checks. Help reflects those settings. Registration receipts use the original registration bot. Group, level and profile-review notices use `member_notifications` bots, once per member group; without a sender, they remain waiting. Management-role notices use a management-enabled bot. Notification capability does not grant approval authority. Event notifications use selected event channels and deduplicate the same physical group. A normal chat reply goes to its source group.

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

Configure an OpenAI-compatible Chat Completions provider that supports tool calling. Add its exact HTTPS base URL to local `ROBORT_AI_ENDPOINTS`, restart, and set the URL, model and key in the admin page. Enable AI on each intended bot. The backend validates the endpoint against this local list; incoming messages cannot change it. Standard tool calls use [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai); non-standard provider extensions need an adapter.

```text
AI-prefixed request → trusted identity + permitted feature context
                    → structured intent / ordered plan
                    → direct query OR numbered change preview
                    → same user's 确认 in the originating bot/group
                    → authorization recheck → sequential execution → results
```

`ai_catalog.py` describes business features; `ai_planner.py` and `ai_operations.py` define permitted plans and operations; `ai_plan_store.py` / `ai_plan_flow.py` persist confirmation and execution state. The model does not execute SQL, shell commands or arbitrary code. An AI-generated role claim cannot grant permission.

Each user has one current plan. A revised AI message replaces it. The confirmation timeout is configurable (default five minutes). At most eight steps are planned; execution stops on failure and reports completed steps. The configured model provider receives the request and the selected business context needed for supported operations.

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


[Member imports and scheduling API](MEMBER_OPERATIONS.md): automatic Excel/CSV mappings, generated preregistration IDs, reviewed profile completion, broadcast rules and bot feature time windows.

## Member enrollment and robot feedback

`手动报名` returns a single-use, 20-minute personal link to `/enroll`. The browser exchanges its URL fragment at `POST /member/exchange`; the server sets an HttpOnly, SameSite=Strict member cookie and returns a CSRF token. Member and admin authentication are separate. All member mutations require `X-CSRF-Token` and a same-origin request.

`GET /member/sessions?day=YYYY-MM-DD` lists all permitted primary and selected external-substitute sessions for that day (today through 366 days ahead), including the member's own entries. `POST /member/enrollments` accepts `action` (`enroll`, `cancel`, `cancel_guests`), `day`, integer `venue_id` and `session_id`, a 32-hex `request_id`, optional `guests` and `guest_count`. The actor comes from the session; caller-supplied identities are rejected. Identical retries are atomic and do not append guests again.

Persistent login setup uses `POST /member/account/request`, a confirmation sent by the same user through an authenticated bot callback, `GET /member/account/status`, then `POST /member/account` with `login` and `password`. Login uses `POST /member/login`; passwords are hashed and login failures rate-limited. Only password-authenticated sessions can switch eligible bots using `POST /member/bot`. Recheck current membership, bot policy, scope and enrollment rules on every operation.

Bot create/update accepts `notification_types`. Null preserves legacy capability-derived delivery; an explicit list enables individual notification categories. `/api/bot-capabilities` returns the categories. Announcements are mandatory and reach every active connected group once. Backend and group display names are independent; there is no aliases property. Callback credentials and channel bindings identify the robot and group.

### External reserve promotion and bulk membership terms

`POST /api/bots` and `PATCH /api/bots/{id}` accept `external_auto_promote` (boolean, default true). `external_promote_minutes` (integer 0–1440, default 30) sets the lead time before each session starts. External entries stay reserves until that cutoff, then fill formal vacancies by queue order. This timer works independently of scheduled announcements, including cutoffs on the preceding day for midnight sessions. Disabling it retains pending external reserves; already admitted seats stay formal. Bookings store their originating robot and shared reads use its current setting.

Administrators with member-management permission can preview `POST /api/membership/periods/batch-preview` using `{"user_ids":["member-id"],"period":{"start_date":"2027-01-01","end_date":"2027-12-31"}}`. Send the same payload plus the returned `revision` as `expected_revision` to `POST /api/membership/periods/batch`. Select 1–200 distinct members. Dates and existing periods must not overlap; unreviewed members are rejected. Confirmation is atomic, records changed since preview are rejected, and renewal history is retained. Optional `amount` is a decimal string per member; `note` is text.


### Personal profile and roster rating visibility

`修改注册信息` returns an identity-bound `/register` link. Approved members edit their existing profile; other members follow registration. Profile changes still require approval. The optional boolean `show_level` defaults to false for all members. Registration and profile forms can save this personal preference immediately; it only controls rating display in enrollment receipts and rosters, not eligibility or team balancing. Ratings are displayed only after review. `GET /public/registration/{bot_id.token}` includes `profile.show_level`; POST accepts the preference with other profile fields. A preference-only save returns `request_id: null`. The personal token determines the account and group; new links replace old drafts and submitted receipts are idempotent.


## Daily match sequence

Create a daily event with `format="match_sequence"`; `options.discipline` defaults to `doubles`. Set `options.excluded_participant_ids` to trusted signup identities selected by the UI. `GET /api/formats` accepts the same comma-separated filter for availability previews. The backend rechecks the roster inside the creation transaction.

`POST /api/tournaments/{event_id}/scores` accepts numerical `scores` or `outcome: "a" | "b"` for daily events. Supply `match_id` and `expected_version`. Outcome-only records keep an empty score list and a real winner. Every approved daily participant may record new results; corrections require organizer/event administrator permissions. Large-event scoring rules remain unchanged.

`POST /api/tournaments/{event_id}/withdraw` accepts `names` or `participant_ids` plus `expected_version`. It removes people only from the dynamic sequence, preserving activity bookings and completed games. Members can remove themselves; organizers/event administrators can remove other people. The backend uses authenticated identity and never trusts a supplied role. New activity bookings made while the activity is in progress synchronize transactionally across all robots serving that session.
