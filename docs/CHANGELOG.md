# Changes

## v1.0 — 2026-10-19

- Centralize inbound addressing in `message_parser.py` for callback, simulator, connection checks and AI command execution.
- Support explicit command bodies and structured Unicode mention ranges in generic JSON callbacks; retain legacy raw-text callbacks.
- Preserve user mentions and literal arguments inside the business body. Reject unresolved/partial addresses instead of guessing the command.
- Preserve callback authentication, membership and role checks, message deduplication, existing data and configuration.
- Document the parsing contract in English and Chinese and add transport, command-family and authorization regressions.

## v1.0 — earlier fixes

- Handle complete bot display names containing spaces when parsing legacy registration messages.
- Add reviewed member-profile changes, configurable compatible Webhook/AI integrations, and private local environment configuration.
