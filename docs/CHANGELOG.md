# Changes

## v1.0 · 2026-11-13 · First appearances in match sequences

- Prioritize members who have not yet played when selecting teams and ordering pending matches after roster changes. Prefer an experienced participant for an odd-roster bye.
- Resume partner/opponent coverage after first appearances; retain completed games and the existing eligibility rules.
- Update member and administrator instructions.

## v1.0 · 2026-11-12 · Unified robot creation settings

- Keep name and business modules at the top of the creation form; switch notifications, venue/session scope, user conditions and opening hours in the shared settings layout.
- Preserve values between panels, reveal invalid fields before submitting, and provide the same controls on mobile.
- Update the illustrated administrator guide.

## v1.0 · 2026-11-12 · Pre-activity admission and personal profiles

- Keep external registrations in reserve until the source robot’s configurable pre-start cutoff (30 minutes by default), independently of announcements. Preserve admitted seats and history.
- Hide ratings in enrollment receipts and rosters by default. Members choose visibility in their personal form; eligibility and team balancing retain the reviewed rating.
- Add `修改注册信息` with an authenticated personal form, reviewed profile updates and immediate display preferences. Reload when a second personal link opens in the same browser tab.
- Update desktop/mobile controls and illustrated usage guides.

## v1.0 · 2026-11-12 · Reserve policy and batch membership periods

- Configure same-day external reserve promotion per robot, using the booking origin rather than the querying robot. Preserve admitted seats and historical rosters.
- Add batch validity-period entry with member selection, a conflict preview and atomic confirmation. Changed records require a new preview; existing renewal history stays intact.
- Update robot settings, member administration and usage instructions for desktop and mobile.

## v1.0 · 2026-11-12 · Enrollment and daily queues

- Include populated rosters in scheduled opening and closing reminders; place registration totals and free formal seats after the table image.
- Fill current-day formal vacancies from the shared booking queue, retaining existing formal reservations, booking origins and historical records. Future external bookings stay in the reserve queue.
- Recommend an eligible enrollment robot when a member uses the wrong one, respecting current permissions, time windows and session scope.
- Accept bookings until the activity ends. Existing daily competition draws and scores stay fixed; the organizer or an event administrator can delete and rebuild the competition from the latest roster.
- Update member and administrator instructions and the editable reply templates.

## v1.0 · 2026-11-11 · Message formatting

- Share Markdown and HTML presentation cleanup across callbacks and the simulator, preserving raw mention offsets, sender identity and event deduplication.
- Accept common numeric glyphs, explicit date formats, spacing and score separators in their appropriate command fields; retain names and literal internal commands.
- Expand parsing guidance and regression coverage for formatted registration, scores, AI input and permission checks.

## v1.0 · 2026-11-11

- Expand robot settings within the robot row, with section navigation, a configuration area and shared footer actions; adapt the drawer to mobile screens.
- Place feedback notification categories inside business functions and show the group directory beside its connection graph.
- Remove the alias setting. Authenticated bot identity stays independent of displayed names; mention metadata preserves command boundaries and the current reply label.
- Refresh the administrator guide and screenshots. Add previewed, transaction-based offline membership maintenance with backup verification and an audit record.

## v1.0 · 2026-11-10

- 网页报名可自由选择日期、场地和场次；本人链接进入，经群内确认后可设置会员密码。
- 群内“报名”统一选择当天最近可报的主负责或已选替补场次。
- 反馈通知按类型独立设置；消息中心公告覆盖全部连接群，每群一份。
- 机器人群内别名、分区设置抽屉和群卡片；会员及管理员说明与示例截图同步。

## v1.0 — 2026-11-09 · Independent member profile notifications

- Add the opt-in “会员资料变更通知” bot capability for group, level and profile-review notices, independent of registration and group management.
- Select one enabled sender per member group; retain waiting notices when no sender is available and recheck the setting and time window before sending. Already sent notices are not replayed.
- Keep registration receipts with their original registration bot and management-role notices with management-enabled bots. Approval permissions remain independent.

## v1.0 — 2026-11-09 · Selected external substitute registrations

- Let each robot opt into substitute registration for explicitly selected activity slots outside its main service scope; an enabled rule requires at least one selection.
- Support registration, guests, targeted rosters, cancellation and AI registration flows for those slots while preserving the normal default, broadcasts, membership eligibility and event-management scope.
- Keep external entries outside formal seats and share one queue calculation across robots, calendars and daily competition participant selection. Full substitute lists overflow into the waiting list.

## v1.0 — 2026-11-09 · Personal form recipients

- Address registration and profile form replies to the requesting member with an account mention and the known name. When the callback has no nickname, the chat platform resolves the mention.
- Include the saved member name in profile-change invitations and preserve recipient addressing in custom reply templates.

## v1.0 — 2026-11-09 · One daily competition per session

- Enforce one daily competition per shared activity session across robots, the admin UI and AI, including concurrent creation attempts. Paused, closed, completed and archived events still occupy the session.
- Allow the event organizer or an event administrator to delete the daily competition before rebuilding it; retain activity registrations and deletion audit history.
- Show existing competitions during planning and in the admin form; exclude deleted competitions from active lists, reminders and personal match statistics.

## v1.0 — 2026-11-09 · Shared list guidance

- Keep session facts in each section and show a single enrollment example after the entire roster or scheduled reminder.
- Combine shared guidance when AI returns several lists; preserve every list, table and session heading.
- Edit empty-session status and end-of-list guidance separately; keep existing custom guidance and queued messages.

## v1.0 — 2026-11-08 · Personal robot replies

- Greet members by name and use the actual robot name in colored command examples; a mention without text now shows a concise, capability-aware welcome.
- Edit categorized reply templates inside each robot, with previews, length limits, default restoration and conflict-aware saving.
- Share the wording system across registration, rosters, event planning, match records, AI plans and proactive notices; retain trusted business facts and existing delivery routing.
- Add mobile/desktop editing guidance and a synthetic screenshot.

## v1.0 — 2026-10-27 · Consistent message headings

- Apply the shared heading style to every section in a robot message, including later sessions in rosters and scheduled broadcasts.
- Preserve separate bold time lines, command colors, code examples and table-only images; add the configured banner once per message.

## v1.0 — 2026-10-26 · Focused administration and task guides

- Group navigation around daily work, group services and settings; simplify page headings, controls and the overview with a shared neutral visual style.
- Open member and administrator instructions by task, with contextual links, searchable steps, colored command parameters and updated page screenshots.
- Keep bot help concise and scoped to each robot's enabled businesses and the caller's permissions; provide detailed syntax through category help.
- Preserve the existing login illustration, loading behavior, business rules and stored records.

## v1.0 — 2026-10-26 · Clearer registration messages

- Identify each empty session and invite participation while respecting ended or unavailable sessions.
- Use the actual session number with a separate bold time line; omit internal scheduling-rule names from rosters and registration receipts.
- Keep generated images table-only, with column headers on every image and all rows retained; remove duplicate titles and page/row counters.

## v1.0 — 2026-10-23 · Login loading transition

- Show a lightweight animated GatherSpace loading screen until the current desktop or mobile illustration is fully loaded and decoded.
- Fade in the login form, reuse loaded artwork, and offer retry or plain-background login when loading fails or takes too long.
- Respect reduced-motion preferences and keep keyboard focus and existing login sessions usable.

## v1.0 — 2026-10-23 · Transparent login layout

- Center the desktop login panel vertically on the right and use a centered form below the title on mobile.
- Use a mostly transparent glass surface and remove illustration-avoidance spacing.

## v1.0 — 2026-10-22 · Direct venue connections and glass login

- Give the login illustration six direct branches from its central hub to four sports venues and two chat groups.
- Refine badminton markings and player orientation using a court-layout reference; keep separate desktop and mobile compositions.
- Keep the login panel in place and apply translucent glass styling with a readable fallback.

## 2026-10-22 · 登录场景与场地属性

- 登录页采用独立桌面、手机背景，羽毛球作为左下前景；登录窗避让群与机器人画面。
- 场地规划支持自定义名称与内容标注，可新增、修改、删除；旧场地自动兼容。
- 管理员操作说明补充属性编辑步骤与截图。


## v1.0 — 2026-10-22 · Login background and dashboard restoration

- Apply the full concept-image background, title and single core-feature line to the sign-in page, retaining password login and its guide link.
- Restore the authenticated dashboard overview, pending applications, session lists and operational shortcuts.
- Keep sign-in framing, error feedback and logout usable on phone, landscape and desktop screens.

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


## 2026-10-22 · Inline robot settings

- Edit each robot's capabilities, venue/session scope, user conditions and function availability inside its expanded row.
- Configure announcements in the same robot, without a separate robot selector. Per-robot writes preserve shared-rule recipients and reject stale edits.
- Collapsed rows separate activity times from broadcast times and group repeated periods.
- Updated the administrator guide and illustrated workflows; mobile forms retain unsaved drafts while other robots are saved.

- Daily participants can record any new result; organizers can cancel an activity with a short command. Add partial rosters, compact four-column tables, outcome-only results, and transactional dynamic match sequences with preserved completed history.
