# Unified inbound message parsing

[简体中文](MESSAGE_PARSING.zh-CN.md) · [Integration](INTEGRATION.md)

## Pipeline

```text
Authenticated callback / authenticated simulator
  → MessageInput (text + declared format + optional mention spans)
  → normalize_message (validate raw mention offsets, clean styles, remove the leading bot address)
  → CommandText (business text, no identity or authority)
  → parse_selection (venue / date / session)
  → Commands (fixed business commands / AI workflow)
  → existing capability, scope, membership and permission checks
```

`app/message_parser.py` owns addressing and input validation. `app/message_styles.py` removes chat presentation; `app/input_formats.py` reads numeric tokens and explicit dates. `app/selection_parser.py` owns activity selectors; `app/score_parser.py` owns score syntax. Domain modules keep business validation. Adding a command must not add a separate cleanup expression.

`Commands.run` refreshes the authenticated context, normalizes once and dispatches. Connection checks use this same path. The old `app.commands.parse` and `strip_mention` imports remain compatibility facades. Backend-generated AI commands use the internal `LiteralCommand(text)` type, preserving already resolved names and tokens without a second cleanup. This type cannot be selected through callback metadata. Semantic AI operations continue calling authorized domain APIs. Admin AI navigation/test endpoints never infer chat identity from prose.

## Adapter contract

Use the generic callback fields documented in [Integration](INTEGRATION.md). Optional `text_format` values:

| Format | Meaning |
| --- | --- |
| `command` (default) | The adapter already extracted the body. Clean presentation without removing a leading person mention. |
| `mentions` | Original text plus explicit mention spans. Validate offsets before cleanup, protect every span, then remove only the leading bot span. Surrounding style wrappers are allowed. |
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

1. Decode HTML entities once and remove a complete leading `<at ...>label</at>` when present.
2. Recognize Unicode whitespace at a known or structurally unambiguous address boundary; do not jump to later whitespace in the command. Structured spans remain the preferred format for names containing arbitrary spaces.
3. Old plain-text clients support a single name token, including spaced hyphen separators such as `@喂 - 一组专用 注册`. The configured name is an optional compatibility hint, never a required identity match. One optional colon or comma separates the address from the command.
4. Preserve every business argument and reject multiple leading recipients. Never scan arguments for a later command keyword or infer identity from a name.

There is no aliases property. Backend and group names can differ. If a platform flattens an ambiguous multiword mention into ordinary spaces, its adapter must send `command`, `<at>` or `mentions` metadata instead of guessing the name boundary. Both callback routes accept the validated format metadata. Incoming mention labels affect this reply only and are not saved as robot configuration.

## Trust, state and regression coverage

- Callback credentials, bot/group binding and sender ID are validated separately before command normalization. Mention names, span kinds and labels cannot grant roles or switch the authenticated sender.
- All business families retain their capability, venue/session, membership and role checks. AI confirmations retain sender/bot/group binding, expiry and permission rechecks.
- Legacy deduplication keeps the original raw-content hash. Generic callbacks retain stable `event_id` deduplication. Formatting normalization never creates a new retry identity.
- No schema or configuration migration is needed. Registration, event and permission records are retained. Parsing does not log raw messages or credentials.
- Tests cover renamed/spaced/punctuated/emoji bot names, all command families, both callback routes, simulator, AI input, embedded user mentions, entity handling, malformed spans, rejected ambiguous prefixes, duplicate events and permission denial.

For business help see `/guide` and `/guide/admin`; this document is for maintainers and adapter authors.

## Formatting and input variants

All three callback text formats and the authenticated simulator share the cleaner. It accepts bold, italic, strike-through, underline/color HTML wrappers, headings, quotations, list markers, inline code, fenced code, and visible link captions. HTML entities are decoded once. It does not fetch link destinations or extract commands from attributes, images, scripts, comments or hidden content. Unsupported non-text content is rejected. User mention IDs, raw URLs, account underscores and emoji joiners are preserved; Markdown escapes keep punctuation literal. Unknown tags remain text rather than disappearing into an executable command.

| Context | Accepted examples |
| --- | --- |
| Styling | `**报名**`, `<font color="blue">名单</font>`, a code-wrapped command |
| Whitespace | Spaces around date separators, ordinal/clock/score components; `报 名 ＋ 两 接 龙`, `取消 报名` |
| Numeric fields | Arabic/fullwidth digits, Chinese numerals, financial Chinese numerals and circled 1–20, such as `第２场`, `第贰场`, `第②场` |
| Explicit dates | `2026-11-12`, `2026/11/12`, `2026.11.12`, `20261112`, `二〇二六年十一月十二日` |
| Relative dates | Today, tomorrow, the day after and yesterday using the existing Chinese command words |
| 24-hour times | `20:30`, `２０：３０`, `二十点半`, `二十时三十分` |
| Scores | `周三赛，第贰场２１－１９`; colon, fullwidth colon, common hyphen/dash/minus characters, slash and `比` |
| Levels / guest counts | `自评 叁 级`, `报名＋两`; existing business limits still apply |

Numeric conversion is restricted to recognized numeric fields. It does not rewrite `A＋２杯`, member names or arbitrary punctuation in event titles. A title such as `21-19挑战赛` is retained. Invalid dates and contradictory selectors fail validation. Month/day-only dates, decimal/Roman numerals and ambiguous merged Arabic match/score numbers are not guessed. For example, use `12场21:19` or `12 21:19`, not `1221:19`. Separate paragraphs or list items are not automatically executed as a sequence; use the existing confirmed AI workflow for multi-step requests.
