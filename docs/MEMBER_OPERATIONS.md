# Member import and scheduling operations

For task-by-task instructions with screenshots of the current Chinese interface, see the [member manual](MEMBER_GUIDE.zh-CN.md) and [administrator manual](ADMIN_GUIDE.zh-CN.md). The sections below retain the operational rules and integration fields.


## Import and registration

Upload `.xlsx`, `.xls` or `.csv` in **Members → Bulk registration**. The preview recognizes common Chinese and English column names, finds a header in the first 20 rows, ignores unrelated columns, and combines visible roster worksheets. Check the column mapping and rows before confirming. Limits: 2 MiB, 1,000 people, 100 columns and 20 worksheets. Spreadsheet macros are not executed. For `.xlsx`, formulas in mapped fields must first be pasted as values.

A row needs a name or a phone number. Missing information does not prevent pre-registration. Blank phone numbers remain blank; no account or phone number is fabricated. Re-importing an identical file skips previously imported rows without phones. A changed file is checked against name, nickname, email, student/employee/member numbers, messaging handles and document numbers. Gender, rating and groups are not identity evidence.

Review each suggested match and select **Confirm match** or **Not a match, add new**. Bulk confirmation handles only rows with a single candidate. Multiple candidates require an explicit selection. Matching preserves the original record and never binds accounts or overwrites profiles. Commit rechecks all candidates and rejects changed evidence atomically.

**Add member** creates a pre-registration record with a server-generated internal identifier. No platform account ID is entered. Members send `注册` or `补全资料` to the bot and follow their personal link. The callback supplies the account identity. Name and gender are required on submission; rating (1–9) and phone are optional.

**Importing a roster approves membership in advance.** Members send `注册确认` and follow their account-bound link. A supplied callback display name suggests a unique roster entry; otherwise the person enters their name. Matching a unique name completes membership without another approval. Duplicate names require a registered phone; existing bindings never transfer. Name and gender are required, while phone, rating and groups may be blank. A newly supplied rating remains a separate pending review. Unmatched people use normal registration review.

**Match existing members** previews unique-name matches to existing approved accounts for bulk association; conflicting phones prevent a match. Completing an imported phone also links a matching approved account directly. Duplicate roster entries that belong to an already-linked account point to that original entry, preserving the member and history. Conflicts remain explicit. Already registered callers can also reconcile their own unique imported name through `注册确认`. Complete profile data alone does not supply a platform identity: first-time members still interact with the bot once.

Select pending people and choose **Notify members to confirm**. Select a registration robot, preview and send. The invitation lists names and asks members to send `注册确认`. An imported email can produce a real mention; a name alone cannot. Callbacks do not guarantee a display name, so `注册确认 张三` is also supported. Nothing is sent automatically to all groups.

**Members → Pending applications** combines registration, rating and profile changes. Review proposed changes there and see processed history inside each member's record. Management roles remain separately granted.

Application receipts return through the bot that received the registration, profile or level request. If that bot is unavailable, delivery waits instead of switching to another bot. Legacy requests with no saved source consider only registration bots the person actually interacted with. Personal receipts require registration capability. Management-role changes use management-enabled bots. Performing an approval still requires member-management permission, and group approval commands require the management capability.

## Robot management and group connections

- **Robot management** configures names, capabilities, venues, sessions, eligibility and availability; expandable rows also provide help previews and deletion.
- **Group connections** provides group creation, renaming and deletion beside a zoomable bot-to-group graph. Select a bot or edge to edit sending Webhook and receiving callback addresses. Each bot has one current group; a group may have several bots.
- Connections can be paused or removed. Receiving-only connections return the business response in the callback HTTP `reply` field, without sending a separate message; displaying that response requires support in the calling platform. Sending-only connections require an already identified group and can send announcements after a successful send test.
- Private callback tokens authenticate receiving-only connections. Duplex connections retain the additional bot-key check. Keep callback URLs private. Only an authenticated incoming message discovers a new group; a send-test response is not trusted as group identity.

## Scheduled announcements

Open **Settings → Scheduled messages**. Add rules selecting:

- Activity reminder, closing reminder and/or roster.
- One or more bots with enrollment capability.
- Beijing time, optional inclusive date bounds and weekdays.
- Every day, workdays including statutory makeup days, calendar weekends, or rest days; optionally skip or include only statutory holidays.

Rules can be disabled or deleted. The master switch pauses all daily broadcasts. An empty list sends none. Existing installations retain the original three reminders until rules are first saved. One physical group receives at most one identical session/type/time announcement, even if several selected bots serve it. Bots only report sessions they manage. Large-event notices remain part of event management.

Queued broadcasts are cancelled when their rule changes, their sender becomes ineligible, or the day ends. Restarting the scheduler does not replay missed broadcasts. For holiday-dependent conditions, an unknown annual calendar does not match; fetch that year's calendar under **Holiday arrangements**.

## Bot feature availability

Open **Bots and groups → Robot management → Edit name and features → Feature availability**. Choose only that bot's enabled capabilities and add date/time conditions.

- **Open only during these periods**: selected capabilities are unavailable outside the union of their enabled opening rules.
- **Close during these periods**: matching closing rules override opening rules.
- With no enabled rule for a capability, it remains available whenever the bot supports it.

Start time is inclusive; end time is exclusive. Blank times mean all day. An end time earlier than the start means overnight; the date/weekday conditions refer to the starting day. For example, Monday 22:00–02:00 includes early Tuesday until 02:00. Date bounds are inclusive. All times use Asia/Shanghai.

These conditions apply to commands, current help, AI plans and confirmation, personal form submission and message delivery. They restrict existing capabilities and never grant membership or administrator permissions. Removing a base capability also removes its availability rules.

## Integration fields

- `POST /api/member-imports/preview`: multipart file; returns `mappings`, `skipped_sheets`, masked rows, errors and a short-lived confirmation token. `POST /api/member-imports/commit` confirms it once. `PATCH /api/member-imports/records/{id}` edits an unlinked pre-registration.
- `POST /api/member-imports/matches`: `{token, choices:[{source_key,target}]}`; choose a returned candidate or `new`. It stores a review decision, not a membership mutation.
- `POST /api/members` without `user_id`: creates a pre-registration. The authenticated legacy account-specific API remains available for trusted administrators.
- `PATCH /api/config`: `broadcast_rules` is an array of `{id,name,enabled,kinds,bot_ids,time,when}`. `null` preserves legacy defaults; `[]` disables all rules. `kinds`: `open`, `closing`, `roster`.
- `POST/PATCH /api/bots`: `capability_rules` is an array of `{id,name,enabled,capabilities,action,when}`. `action`: `open` or `closed`.
- Shared `when`: `start_date`, `end_date`, `weekdays` (Monday=0), `day_type` (`all`, `workday`, `weekend`, `restday`), `holidays` (`any`, `exclude`, `only`). Capability rules additionally accept `start_time`, `end_time`.

Administrative authentication and the existing role checks apply. Public registration accepts only profile fields; a supplied account ID, role or approval claim has no authority.
