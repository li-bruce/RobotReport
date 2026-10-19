# Member imports and time rules

## Import and registration

Upload `.xlsx`, `.xls` or `.csv` in **Members → Bulk registration**. The preview recognizes common Chinese and English column names, finds a header in the first 20 rows, ignores unrelated columns, and combines visible roster worksheets. Check the column mapping and rows before confirming. Limits: 2 MiB, 1,000 people, 100 columns and 20 worksheets. Spreadsheet macros are not executed. For `.xlsx`, formulas in mapped fields must first be pasted as values.

A row needs a name or a phone number. Missing information does not prevent pre-registration. Blank phone numbers remain blank; no account or phone number is fabricated. Re-importing an identical file skips previously imported rows without phones. A different file containing the same names requires administrator review; names never establish identity.

**Add member** creates a pre-registration record with a server-generated internal identifier. No platform account ID is entered. Members send `注册` or `补全资料` to the bot and follow their personal link. The callback supplies the account identity. Name and gender are required on submission; rating (1–9) and phone are optional.

Only an exact normalized phone match associates an imported record. If an imported row has no phone, an administrator first adds it through **Complete details**. A matched incomplete record is reserved for that account and submitted for normal membership review. Self-supplied ratings remain pending review. Complete, already reviewed imports retain the existing direct-association behavior. Neither import path grants management roles. Already approved members submit changes through the profile approval queue.

## Scheduled announcements

Open **Settings → Scheduled messages**. Add rules selecting:

- Activity reminder, closing reminder and/or roster.
- One or more bots with enrollment capability.
- Beijing time, optional inclusive date bounds and weekdays.
- Every day, workdays including statutory makeup days, calendar weekends, or rest days; optionally skip or include only statutory holidays.

Rules can be disabled or deleted. The master switch pauses all daily broadcasts. An empty list sends none. Existing installations retain the original three reminders until rules are first saved. One physical group receives at most one identical session/type/time announcement, even if several selected bots serve it. Bots only report sessions they manage. Large-event notices remain part of event management.

Queued broadcasts are cancelled when their rule changes, their sender becomes ineligible, or the day ends. Restarting the scheduler does not replay missed broadcasts. For holiday-dependent conditions, an unknown annual calendar does not match; fetch that year's calendar under **Holiday arrangements**.

## Bot feature availability

Open **Bots and groups → Edit settings → Feature availability**. Choose only that bot's enabled capabilities and add date/time conditions.

- **Open only during these periods**: selected capabilities are unavailable outside the union of their enabled opening rules.
- **Close during these periods**: matching closing rules override opening rules.
- With no enabled rule for a capability, it remains available whenever the bot supports it.

Start time is inclusive; end time is exclusive. Blank times mean all day. An end time earlier than the start means overnight; the date/weekday conditions refer to the starting day. For example, Monday 22:00–02:00 includes early Tuesday until 02:00. Date bounds are inclusive. All times use Asia/Shanghai.

These conditions apply to commands, current help, AI plans and confirmation, personal form submission and message delivery. They restrict existing capabilities and never grant membership or administrator permissions. Removing a base capability also removes its availability rules.

## Integration fields

- `POST /api/member-imports/preview`: multipart file; returns `mappings`, `skipped_sheets`, masked rows, errors and a short-lived confirmation token. `POST /api/member-imports/commit` confirms it once. `PATCH /api/member-imports/records/{id}` edits an unlinked pre-registration.
- `POST /api/members` without `user_id`: creates a pre-registration. The authenticated legacy account-specific API remains available for trusted administrators.
- `PATCH /api/config`: `broadcast_rules` is an array of `{id,name,enabled,kinds,bot_ids,time,when}`. `null` preserves legacy defaults; `[]` disables all rules. `kinds`: `open`, `closing`, `roster`.
- `POST/PATCH /api/bots`: `capability_rules` is an array of `{id,name,enabled,capabilities,action,when}`. `action`: `open` or `closed`.
- Shared `when`: `start_date`, `end_date`, `weekdays` (Monday=0), `day_type` (`all`, `workday`, `weekend`, `restday`), `holidays` (`any`, `exclude`, `only`). Capability rules additionally accept `start_time`, `end_time`.

Administrative authentication and the existing role checks apply. Public registration accepts only profile fields; a supplied account ID, role or approval claim has no authority.
