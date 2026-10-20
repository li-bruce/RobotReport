# Changes

## v1.0 — 2026-10-20 · Personal approval notifications

- Route membership, profile, level and group results through registration bots; keep management-role changes on management bots.
- Preserve approval permissions, one notice per related group, current capability windows and no replay after a sent or uncertain result.
- Recheck the recorded event and destination at delivery; disabling unrelated management features does not cancel a personal receipt.

## v1.0 — 2026-10-20 · Empty registration notices

- Use a single “暂时无人报名” line for empty roster queries and daily reminders, without venue, session, or time headings.
- Preserve populated rosters and occupancy notices; evaluate only sessions handled by the receiving bot.

## v1.0 — 2026-10-19 · Imports and availability

- Recognize Excel/CSV roster columns and incomplete records; review personal completions while preserving phone-only account linking.
- Generate pre-registration identifiers on the server; remove account ID fields from member forms.
- Use compact empty rosters and business-focused bot receipts.
- Configure daily message types, sender bots and date/calendar conditions.
- Restrict each bot's supported capabilities by date and time, including commands, AI confirmation, personal forms, help and queued delivery.
- Add phone-friendly rule editors and concise member/admin guides.

## v1.0 — 2026-10-19

- Centralize inbound addressing in `message_parser.py` for callback, simulator, connection checks and AI command execution.
- Support explicit command bodies and structured Unicode mention ranges in generic JSON callbacks; retain legacy raw-text callbacks.
- Preserve user mentions and literal arguments inside the business body. Reject unresolved/partial addresses instead of guessing the command.
- Preserve callback authentication, membership and role checks, message deduplication, existing data and configuration.
- Document the parsing contract in English and Chinese and add transport, command-family and authorization regressions.

## v1.0 — earlier fixes

- Handle complete bot display names containing spaces when parsing legacy registration messages.
- Add reviewed member-profile changes, configurable compatible Webhook/AI integrations, and private local environment configuration.
