# Unified inbound message parsing

[简体中文](MESSAGE_PARSING.zh-CN.md) · [Integration](INTEGRATION.md)

## Pipeline

```text
Authenticated callback / authenticated simulator
  → MessageInput (text + declared format + optional mention spans)
  → normalize_message (remove only the leading bot address)
  → CommandText (business text, no identity or authority)
  → parse_selection (venue / date / session)
  → Commands (fixed business commands / AI workflow)
  → existing capability, scope, membership and permission checks
```

`app/message_parser.py` owns addressing, input validation and format handling. `app/selection_parser.py` owns literal activity selectors; `app/score_parser.py` owns score syntax. Domain modules keep business validation. Adding a command must not add a new mention-removal expression.

`Commands.run` refreshes the authenticated context, normalizes once and dispatches. Connection checks use this same path. The old `app.commands.parse` and `strip_mention` imports remain compatibility facades. Backend-generated AI commands explicitly use `MessageInput(text)` (command format) so their arguments are not treated as chat markup. Semantic AI operations continue calling authorized domain APIs. Admin AI navigation/test endpoints accept plain prose and never infer chat identity from it.

## Adapter contract

Use the generic callback fields documented in [Integration](INTEGRATION.md). Optional `text_format` values:

| Format | Meaning |
| --- | --- |
| `command` (default) | The adapter already extracted the command body. Trim outer whitespace only; preserve mentions, entities and names in the body. |
| `mentions` | Original text plus explicit mention spans. Remove only the first span when it is a bot address at the start, allowing leading whitespace. |
| `legacy` | Raw text using the legacy address syntax below. Prefer the other formats for new integrations. |

Example independent of the bot's configured/display name:

```json
{
  "event_id": "message-124",
  "group_id": "example-group",
  "user_id": "example-user",
  "text": "@Club A 注册",
  "text_format": "mentions",
  "mentions": [{"start": 0, "end": 7, "kind": "bot"}]
}
```

Spans use zero-based **Unicode code-point** offsets, end exclusive, in the original `text`. JavaScript adapters must convert UTF-16 offsets, especially around emoji. Each span has exactly `start`, `end`, `kind`; kind is `bot` (the receiving bot) or `user`. Spans must be sorted, non-overlapping and within the text, with at most 32 spans. Other fields and kinds are rejected. Only `mentions` format accepts non-empty spans.

The adapter must obtain spans from the platform event, not guess them by looking for business words. A bot mention inside the command and all user mentions remain untouched. Multiple consecutive leading recipients are rejected. `text_format=command` means there is no transport address left to remove; clients previously sending raw mentions must select `legacy` or provide spans.

The simulator accepts the same optional fields but defaults to `legacy` to retain its existing raw-message input. Raw string calls to `Commands.run` also use `legacy`. For new Python integrations, construct `MessageInput` or use `message_input` for validated adapter data.

## Legacy compatibility

1. Decode HTML entities once.
2. A complete leading `<at ...>label</at>` is the legacy protocol's recipient boundary; its display label is irrelevant. Never remove later tags. Platforms where the first tag can refer to someone else must use the structured adapter contract.
3. Otherwise, match the complete configured bot name after `@`/`＠`. Allow horizontal whitespace differences, escape punctuation literally, and require a whitespace, `:`/`：`, `,`/`，` or end boundary. Never match across a newline.
4. Remove at most one address and one optional address delimiter. A bare address opens help.
5. If a leading plain-text address cannot be resolved, return a specific reselect-the-bot message without creating a member or running a command. Do not drop the first word, search ahead for “注册”, or treat an unrelated bot as this one.

Plain text alone cannot reliably reveal where an arbitrary display name ends. Name matching is therefore a compatibility fallback, not the primary protocol or an identity mechanism. For that fallback, keep the configured name aligned with the group display name; structured/command formats do not depend on the name.

## Trust, state and regression coverage

- Callback credentials, bot/group binding and sender ID are validated separately before command normalization. Mention names, span kinds and labels cannot grant roles or switch the authenticated sender.
- All business families retain their capability, venue/session, membership and role checks. AI confirmations retain sender/bot/group binding, expiry and permission rechecks.
- Legacy deduplication keeps the original raw-content hash. Generic callbacks retain stable `event_id` deduplication. Formatting normalization never creates a new retry identity.
- No schema or configuration migration is needed. Registration, event and permission records are retained. Parsing does not log raw messages or credentials.
- Tests cover renamed/spaced/punctuated/emoji bot names, all command families, both callback routes, simulator, AI input, embedded user mentions, entity handling, malformed spans, rejected ambiguous prefixes, duplicate events and permission denial.

For business help see `/guide` and `/guide/admin`; this document is for maintainers and adapter authors.
