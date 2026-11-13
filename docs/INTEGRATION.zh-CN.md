# 机器人、API 和 AI 对接

[English](INTEGRATION.md) | 简体中文 · [README](../README.zh-CN.md)

## 通用 JSON Webhook

符合通用 JSON 契约的端点可以直接接入，其他平台通过消息适配器转换。在 `.env.local` 配置 `ROBORT_WEBHOOK_JSON_ENDPOINTS=https://adapter.example.org/messages` 后重启，再在机器人连接编辑页填写完整发送 URL（可携带适配器需要的查询凭据）。生成的回调为 `/webhooks/{bot_id}/{token}`，其中随机令牌属于连接凭据，请保密。

适配器应验证平台事件签名，从可信的平台事件提取用户和群 ID，不能采用用户文字中自称的身份；将 @ 后的内容转换为纯指令。回调令牌授权该适配器代表本机器人的群。一个机器人仍只绑定一个群，回调不能把既有连接自动切换到其他群。

向回调发送 POST JSON：

```json
{"event_id":"unique-message-123","group_id":"example-group","user_id":"example-user","text":"帮助","display_name":"示例会员"}
```

`event_id` 是稳定且唯一的消息编号，允许 1–128 位英文字母、数字和 `_ . : -`；重试同一消息时使用同一个编号。`display_name` 选填。用户和群 ID 为 1–100 位英文字母、数字及 `_ @ . : -`，`text` 为 1–4000 字。不要传入角色、权限或发送目的地声明。返回 `{"result":"ok"}` 表示已处理，业务回复另外通过发送端点投递。

`text_format` 默认 `command`，只传业务正文，不含呼叫机器人的前缀。直接传平台原文时，可使用 `mentions` 并提供 `start`/`end`/`kind` 提及范围；旧版原文可选 `legacy`。所有业务指令使用同一解析流程，示例、Unicode 位置和兼容规则见[统一消息解析](MESSAGE_PARSING.zh-CN.md)。

后台发送的 JSON：

```json
{"type":"markdown","content":{"text":"**活动提醒**"},"event_id":"42","group_id":"example-group"}
```

类型包含 `text`、`markdown`、`image`、`file`。`content` 对象保留消息字段：纯文本为 `content`，Markdown 为 `text`，媒体按附件字段处理。`event_id` 标识出站消息。端点返回任意 2xx（包括 204）即表示已接收，拒绝时返回非 2xx；5xx、超时或结果不明保留待核实，不自动重放。各平台的 Markdown、@ 与文件上传格式仍需在适配层转换。

## 金山协作 / WPS 接入

需要区分两个地址：

| 地址 | 方向 | 获取方式 |
| --- | --- | --- |
| 发送 Webhook | 本后台 → 金山群 | 从群机器人中复制，填入后台。 |
| 回调 URL | 金山 → 本后台 | 从后台生成并复制，填入群机器人设置。 |

新建或选择群后添加机器人，同时配置两种地址。群 ID 不知道时可留空，由第一条有效回调自动识别；仅发送成功不能证明回调来自哪个群。一个机器人只有一个当前群，一个群可有多个机器人，各自保留独立凭据。

当前 `app/security.py::validate_webhook` 允许：

```text
https://xz.wps.cn/api/v1/webhook/send?key=<YOUR_KEY>
```

私有部署请在本地 `ROBORT_WEBHOOK_ENDPOINTS` 填写准确 HTTPS 域名和路径（不含 key），重启后生效，见[本地配置](ENVIRONMENT.zh-CN.md)；不接受回调正文指定任意发送目标。连接流程从发送 URL 提取 key，不要把完整回调地址当作 Robot Key。

### 回调协议

路径为 `/kingsoft/callback/{bot_id}/{token}`，其中 token 是私有凭据。

- **GET**：有效且启用的机器人返回 `{"result":"ok"}`，用于金山保存地址时验证。
- **POST**：校验回调 token、`robot_key`、群绑定和启用状态，处理内容并将回复放入发送队列。
- 返回 `{"result":"ok"}` 表示接收确认，业务回复另行通过发送 Webhook 投递。

POST 示例（均为占位数据）：

```json
{
  "chatid": "12345",
  "creator": "67890",
  "content": "@机器人 帮助",
  "robot_key": "<SAME_KEY_AS_OUTGOING_WEBHOOK>",
  "ctime": 1792022400
}
```

`chatid` 和 `creator` 分别为群和发言人标识。官方协议另含回复上下文字段。部署端提供显示姓名时可辅助展示；真实会员姓名通过注册填写。相同回调会去重，执行或发送结果未知时保留状态供核查，不自动重放。

金山回调只提供**@机器人的消息**，不提供普通群聊内容。官方文档说明了 GET 验证、回调字段和 Markdown 子集；该子集未列出原生 Markdown 表格，本项目据此使用表格图片并保留文本回退。参见[官方 Webhook 文档](https://open.wps.cn/documents/app-integration-dev/guide/robot/webhook)。

在本地配置或后台填写可达的 HTTPS 公共访问地址，程序同步用于回调、注册和图片链接。详见[部署配置](DEPLOYMENT.zh-CN.md#配置项)。

### 业务和通知

机器人业务项为会员注册、会员资料变更通知、活动报名、每日赛事、大型赛事、零散对局、报名统计、消息发布、AI、群内管理。新机器人默认不启用会员资料变更通知、大型赛事、AI 和群内管理，其余默认选中，均可调整。会员资料变更通知的标识为 `member_notifications`，保存在机器人 `capabilities` 数组中，支持独立开放时段。旧机器人包括未设置能力清单的机器人也需手动开启。

机器人业务、场地/场次范围、用户适用条件和本人管理权限分别校验。帮助随配置变化。注册结果由原注册机器人通知；组别、级别和资料审核结果由开启“会员资料变更通知”的机器人发送，每个会员所在群只发一次，没有可用发送机器人时保留待发；管理角色变更由“群内管理”机器人通知。通知开关不授予审批权限；赛事通知按所选通道发送，同一物理群去重；普通对话回复原群。

## 后台 HTTP API

接口位于 `/api/`，需要认证。应用未启用 Swagger/Redoc 交互页面；请求字段以 `app/main.py`、`app/management_api.py` 的当前实现为准。

### 认证

- **网页**：`POST /auth/login`，正文 `{"password":"..."}`。成功设置 HttpOnly `robort_session` Cookie 并返回 `csrf_token`；修改类 `/api/` 请求携带 `X-CSRF-Token`。`GET /auth/session` 查看会话，`POST /auth/logout` 注销。管理页面自动处理。
- **可信脚本**：使用 `Authorization: Bearer <admin_token>`。令牌由初始化脚本写入私有状态文件 `admin.local.json`，不写入网页、URL 或公开示例。机器人级运维凭据不等于全后台业务权限。

只读示例，在项目 Python 环境中运行并设置 `ROBORT_STATE_DIR`：

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

远程调用使用 HTTPS；示例只输出场地，不输出令牌。

### 主要接口

| 模块 | 接口 |
| --- | --- |
| 配置与健康 | `GET/PATCH /api/config`；`GET /healthz` 无需登录 |
| 机器人 | `GET/POST /api/bots`；`GET /api/bots/{bot_id}/callback` |
| 会员 | `GET/POST /api/members`；`PATCH /api/members/{user_id}`；`PUT /api/members/{user_id}/roles` |
| CSV | `GET /api/member-imports/template`；`POST /api/member-imports/preview`；`POST /api/member-imports/commit` |
| 会籍 | `GET /api/members/{user_id}/membership`；`POST /api/members/{user_id}/membership/periods` |
| 场地 | `GET/POST /api/venues`；`PATCH /api/venues/{venue_id}` |
| 日历 | `GET /api/activity/calendar?month=YYYY-MM`；`GET /api/activity/day?date=YYYY-MM-DD` |
| 周期规则 | `GET/PUT /api/activity/slot-plan` |
| 非周期安排 | `GET /api/activity/date-plans?year=YYYY`；`PUT/DELETE /api/activity/date-plans/{day}?venue_id=1` |
| 节假日安排 | `GET /api/holiday-plan?year=YYYY&venue_id=1`；`PUT /api/holiday-plan/{year}/{holiday_id}?venue_id=1` |
| 刷新节假日 | `POST /api/holiday-calendar/{year}/refresh` |
| 赛事 | `GET/POST /api/tournaments`；`GET/PATCH /api/tournaments/{event_id}`；`POST /api/tournaments/{event_id}/actions` |
| 记分与组织 | `POST /api/tournaments/{event_id}/scores`；`GET/PUT /api/tournaments/{event_id}/organization` |

修改时段安排需要当前 `revision`，改分需要当前预期版本；先读取再修改。超时后先查询结果，不直接重试写操作。错误通过 JSON `detail` 返回；常见状态为 400 参数问题、401 未登录、403 拒绝、409 数据库冲突、413 正文过大和 422 请求格式错误。

CSV 流程为下载模板 → 上传预览 → 管理员核对 → 提交。支持 `姓名,手机号,性别,自评级别,组别`，预登记姓名/手机号必填，不授予管理角色。表单注册性别必填；已导入但性别缺失的档案，参加性别限定比赛前需补齐。

## AI 接入流程

接入支持 OpenAI 兼容 Chat Completions 和工具调用的模型服务。在本地 `ROBORT_AI_ENDPOINTS` 配置完整 HTTPS 基础地址并重启，然后在后台设置接口地址、模型和密钥，为对应机器人开启 AI。地址依据本地名单验证，群消息不能修改。标准工具调用使用 [LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)，非标准扩展需要适配器。

```text
AI 前缀请求 → 真实账号与可用业务上下文 → 结构化意图/有序计划
           → 直接查询 或 编号操作预览
           → 本人在原机器人/原群回复“确认”
           → 再次核验权限 → 按步骤执行 → 回执
```

`ai_catalog.py` 定义业务目录，`ai_planner.py` 和 `ai_operations.py` 定义计划与操作，`ai_plan_store.py` / `ai_plan_flow.py` 保存确认和执行状态。模型不执行 SQL、Shell 或任意代码，不能通过生成文字给用户授予权限。

每人一份当前计划；新 AI 修改意见覆盖旧计划。确认有效期默认五分钟，可配置；每次最多八步，失败停止后续执行并保留已完成结果。开启 AI 会把请求及必要的业务上下文发送到所配置的模型服务。

## 常见接入问题

| 现象 | 检查项 |
| --- | --- |
| 回调保存失败 | 域名/TLS、云安全组、主机/面板防火墙、代理路由及完整回调地址；有效 GET 应返回 `{"result":"ok"}`。 |
| 收到消息但不回复 | `ROBORT_LIVE_SEND`、发送地址、连接启用状态、业务开关和发送队列结果。 |
| 同群第二个机器人不回应 | 使用自己的发送 key 和回调，不复用第一机器人的回调。 |
| 注册或图片链接打不开 | 公共访问地址是否正确，以及用户设备的访问路径。 |
| AI 不可用 | 系统 AI 开关、机器人 AI 业务、模型/密钥、本人会员和管理权限。 |
| 修改提示版本失效 | 重新读取最新对象，核对后携带新版本提交。 |

回调和 API 测试使用合成数据与独立状态目录。生产后台内新增“测试机器人”仍会共享真实会员和赛事数据。


[名册与时间规则接口](MEMBER_OPERATIONS.zh-CN.md)：Excel/CSV列匹配、自动预登记编号、资料补全审核、定时播报与机器人功能时间窗口。

## 会员网页报名与反馈通知

“手动报名”生成20分钟有效、单次使用的本人链接 `/enroll#…`。网页通过 `POST /member/exchange` 换取 HttpOnly、SameSite=Strict 会员 Cookie 与 CSRF 令牌；会员和管理员认证相互独立。写操作必须携带 `X-CSRF-Token`，并验证同源。

`GET /member/sessions?day=YYYY-MM-DD` 返回所选日期的全部可见主场次及明确选定的范围外替补场次，支持今日至未来366天，仅返回本人的报名明细。`POST /member/enrollments` 接收 `action`（enroll/cancel/cancel_guests）、日期、整数场地/场次ID、32位十六进制 `request_id`，以及可选同行姓名/人数。同一请求重试不重复追加同行；身份从登录会话获取，不接受请求指定用户。

首次设置密码：`POST /member/account/request` 获取确认指令 → 同一用户在真实群回调确认 → `GET /member/account/status` → `POST /member/account` 保存登录名及密码。之后 `POST /member/login` 支持登录名或唯一登记手机号。密码散列保存，失败限流；密码登录会话可通过 `POST /member/bot` 切换符合本人条件的机器人。每次操作重检会员、机器人范围与用户条件、会籍和活动报名规则。

机器人新增/修改支持 `notification_types`。通知配置为 null 时继承旧业务开关规则，显式列表则按类型独立启用。目录见 `/api/bot-capabilities` 的 notifications；announcement 固定开启。消息中心向全部有效连接群各发一次。后台名称与群内名称独立，不提供 aliases 属性。机器人及群身份仅由回调凭据和通道绑定确认。

### 范围外递补与批量会籍

机器人创建、更新接口支持布尔字段 `external_auto_promote`，默认 true；新增 `external_promote_minutes`（0至1440的整数，默认30），按每场开始前的提前分钟数独立递补；0表示开始时，到点前保持替补，与定时播报无关。跨零点场次也按实际提前时间执行。来源机器人决定开关与提前时间，关闭后未递补人员保留替补，已取得的正式席位保留。

有效期批量预览：`POST /api/membership/periods/batch-preview`，提交 `user_ids`（1至200位不重复会员）及 `period`（`start_date`、`end_date`，可选每人金额字符串 `amount` 和备注 `note`）。确认：`POST /api/membership/periods/batch`，提交相同参数及预览返回的 `revision`，字段名为 `expected_revision`。需要会员管理权限；未审核、日期重叠或预览后记录变化时拒绝，整批事务保存，不替代注册审核，不覆盖已有续费记录。


### 本人资料与报名等级展示

`修改注册信息` 返回绑定本人账号的 `/register` 链接；已审核会员修改现有资料，其他状态进入注册流程。姓名、性别、手机号、等级、分组变更仍需审核。布尔字段 `show_level` 为名单展示偏好，所有会员默认 false，注册及资料表可即时保存；仅控制报名回执和名单中的等级展示，不改变后台等级、报名约束或分组算法，未经审核的自评不公开显示。`GET /public/registration/{bot_id.token}` 的 `profile.show_level` 返回当前选项，POST 可随资料提交；仅保存展示偏好时 `request_id` 为 null。身份从个人令牌恢复，不接受调用方指定其他账号或群；新链接替换旧草稿，提交结果可幂等恢复。


## Daily match sequence

Sequence replanning counts appearances from completed games only. Serve eligible members with no appearances first, ahead of partner/opponent coverage and other soft preferences; preserve completed rows. Pending games do not count as appearances.

Create a daily event with `format="match_sequence"`; `options.discipline` defaults to `doubles`. Set `options.excluded_participant_ids` to trusted signup identities selected by the UI. `GET /api/formats` accepts the same comma-separated filter for availability previews. The backend rechecks the roster inside the creation transaction.

`POST /api/tournaments/{event_id}/scores` accepts numerical `scores` or `outcome: "a" | "b"` for daily events. Supply `match_id` and `expected_version`. Outcome-only records keep an empty score list and a real winner. Every approved daily participant may record new results; corrections require organizer/event administrator permissions. Large-event scoring rules remain unchanged.

`POST /api/tournaments/{event_id}/withdraw` accepts `names` or `participant_ids` plus `expected_version`. It removes people only from the dynamic sequence, preserving activity bookings and completed games. Members can remove themselves; organizers/event administrators can remove other people. The backend uses authenticated identity and never trusts a supplied role. New activity bookings made while the activity is in progress synchronize transactionally across all robots serving that session.
