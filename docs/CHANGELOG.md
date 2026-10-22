# Changes

## v1.0 — 2026-10-22 · Full-background home

- Let the concept rendering fill the home workspace, with only the product title and one line of core shortcuts.
- Remove explanatory copy and operational cards from the home page; venue, registration, competition and bot pages remain accessible through their navigation.
- Adapt background framing, text contrast and shortcuts to phone, landscape and desktop layouts.

## v1.0 — 2026-10-22 · GatherSpace and robot broadcast settings

- Rename the product to GatherSpace (聚场), with a rendered homepage concept connecting venues, activity registration, bots and groups.
- Move scheduled broadcasts into Bots & Groups, add shortcuts from robot settings and a robot filter that retains all other rules when saving.
- Keep existing schedule and notification behavior, identify badminton as the current competition implementation, and document a staged path to reusable team and draw rules.
- Update login, guides, screenshots and bilingual README for the broader venue/activity scope.

## v1.0 — 2026-10-22 · Administrator table row numbers

- Add a shared leading row-number column to all admin data tables, including import previews, filtered lists and modal records.
- Renumber each displayed table from 1 after filtering or reordering; keep business identifiers, rankings and source-file row references intact.
- Keep the column compact on phones and update the illustrated administrator guide.

## v1.0 — 2026-10-20 · Illustrated member and administrator manuals

- Align registration instructions with current pages: imported roster approval, personal account confirmation, duplicate review, normal applications, profile/level review, renewals, and source-bot notifications.
- Update robot/group, activity, tournament, notification and role instructions to match their current navigation and buttons.
- Add responsive, expandable screenshots captured from the actual UI with synthetic data; export the same guides to Markdown for repository readers.
- Replace stale manual entry points and keep public help and personal form links consistent.

## v1.0 — 2026-10-20 · Registration invitation recovery

- Generate invitation request IDs on the server so preview and sending work on HTTP admin pages without browser UUID support.
- Mark a preview ready only after validation and rendering succeed; keep the same request ID when retrying a failed send to avoid duplicate notifications.
- Return to preview when the server rejects a stale roster, instead of repeatedly submitting an invalid preview.

## v1.0 — 2026-10-20 · Robot connections and roster review

- Separate robot settings from a responsive group connection graph; edit optional sending/receiving addresses on each edge and manage groups alongside it.
- Review duplicate roster candidates by identity-bearing fields, with individual or single-candidate bulk confirmation. Preserve original records. Account linking in this earlier release was phone-only; the current registration guide describes the later name-first confirmation workflow.
- Persist the source of personal applications so approval receipts use the applicant’s bot. Unavailable source bots wait; already sent or uncertain deliveries are not replayed.

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

## 2026-10-20 · Registration confirmation and member review

- Imported membership is pre-approved. Personal confirmation links prefer unique names, disambiguate duplicate names with phone numbers and prevent account transfers. Optional rating/groups no longer block registration.
- Completing an imported phone reconciles matching approved accounts. Bulk reconciliation and explicit registration-robot invitations provide a complete pending-roster workflow.
- Profile requests join registration and rating requests under member pending applications; review history stays with the member.
