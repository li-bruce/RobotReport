# 本地环境配置

[English](ENVIRONMENT.md) | 简体中文 · [README](../README.zh-CN.md)

仓库只提供全部留空的 [`.env.example`](../.env.example)。程序默认读取**项目目录内的 `.env.local`**，真实配置、数据库和备份不上传 Git。

## 创建本地配置

已有环境可直接导出当前配置，保留原密钥：

```bash
python -m scripts.export_env --state /完整路径/state --output /完整路径/state/.env.local
```

导出内容包括现有 API 令牌、AI 配置、各机器人发送地址、Robot Key 和本地保留的回调令牌，不在终端输出明文。默认关闭真实发送；准备正式运行时加 `--live`。文件已存在时，先备份再使用 `--replace`。导出属于维护操作，请从一致的状态副本导出，或短暂停止本应用后操作。

新环境先初始化数据和管理员密码，再导出；也可以复制 `.env.example` 为 `.env.local` 后手动填写。空模板不会自动创建管理员，初始化步骤见[部署说明](DEPLOYMENT.zh-CN.md)。

配置文件可以放在源码之外，更新代码时继续使用：

```bash
export ROBORT_ENV_FILE=/srv/robort/state/.env.local
python -m uvicorn app.main:app --host 127.0.0.1 --port 9874 --workers 1 --no-access-log
```

```powershell
$env:ROBORT_ENV_FILE = 'C:\Robort\state\.env.local'
python -m uvicorn app.main:app --host 127.0.0.1 --port 9874 --workers 1 --no-access-log
```

明确指定的文件必须存在。`ROBORT_ENV_FILE=-` 表示只使用进程环境，不读取文件。此变量的相对路径按项目目录解析，数据目录建议始终填写绝对路径。

## 配置项

| 名称 | 用途 / 留空行为 |
| --- | --- |
| `ROBORT_STATE_DIR` | 私有数据目录；留空使用项目内 `state/local`。 |
| `ROBORT_LIVE_SEND` | `1`/`true` 真实发送，`0`/`false` 模拟发送；留空为模拟。 |
| `ROBORT_PUBLIC_BASE_URL` | 对外访问地址，通常使用 HTTPS；同时用于回调、注册和图片链接。留空保留已存业务地址，回调根据请求地址生成。 |
| `ROBORT_ADMIN_TOKEN` | 可选初始化 API 令牌；留空保留已有授权。导出脚本会复制现有初始化令牌。 |
| `ROBORT_INITIAL_ADMIN_PASSWORD` | 可选首次密码，12–1024 字符；只在尚未设置密码时使用，不能重置已有密码。建议使用隐藏输入的密码设置脚本，本项留空。 |
| `ROBORT_AI_API_KEY` | 模型服务 API Key；空值保留已存值，新状态无密钥。 |
| `ROBORT_AI_MODEL` | 模型标识；应支持 Chat Completions 工具调用。 |
| `ROBORT_AI_BASE_URL` | OpenAI 兼容接口的基础地址，自定义端点在 ROBORT_AI_ENDPOINTS 配置。 |
| `ROBORT_AI_ENABLED` | `1`/`true` 开启、`0`/`false` 关闭；留空保留已有设置，新环境关闭。 |
| `ROBORT_AI_ENDPOINTS` | 额外允许的 HTTPS 模型基础地址，逗号分隔，不含密钥或查询参数。 |
| `ROBORT_WEBHOOK_JSON_ENDPOINTS` | 通用 JSON 适配器的 HTTPS 发送端点，逗号分隔，不含查询凭据。 |
| `ROBORT_WEBHOOK_ENDPOINTS` | 私有部署的 HTTPS 发送端点，以逗号分隔，**不含 `?key=`**；公开 WPS 端点已内置。 |
| `ROBORT_BOTS_JSON` | 按现有机器人 UUID 保存各机器人私有连接参数；留空保留当前连接，建议由导出脚本或管理页面维护。 |

值按字面读取，`$`、`#`、`!` 不会触发变量替换。支持不加引号、单引号，或 JSON 转义的双引号值；程序自动保存时负责转义。不支持行尾注释、`export` 前缀和多行值。未知或重复字段会报错，但不输出密钥内容。

**非空进程环境变量优先于文件。** 被进程环境接管的字段，不能在管理页面改成不同值；需要页面维护时，先移除对应进程变量。systemd 使用 `Environment=ROBORT_ENV_FILE=/srv/robort/state/.env.local`，不要再用 `EnvironmentFile=` 重复加载这个文件。

## 私有金山协作接入

先在本地配置中填写准确的发送域名和路径，重启后再添加机器人：

```ini
ROBORT_WEBHOOK_ENDPOINTS=https://collab.example.org/woa/api/v1/webhook/send
```

在机器人连接编辑框粘贴群里提供的完整发送地址（包含 key）。允许列表只放端点，不放 key，要求 HTTPS 443 端口。回调消息不能指定任意发送地址。

`ROBORT_BOTS_JSON` 只保存 `webhook_url`、`robot_key`、`callback_token`，对应已经存在的机器人；不负责重建群、业务组合和会员数据。先在页面创建机器人及群连接，再填参数。迁移环境应恢复**整个数据目录和本地 env**。

## 修改与备份

- 启动时校验并应用非空配置。空值保留原值；清除已有 AI 密钥请在管理页面操作。
- 页面修改 AI、公共地址、机器人连接时，会同步写回本地文件；回调轮换、移除连接、删除机器人或群也会更新文件。
- 服务账户需要文件及所在目录的写权限，以便原子替换。程序生成的 Linux 文件权限为 `0600`；Windows 请限制目录 ACL。
- 手动改文件后重启生效；手动替换或导出配置前先停本服务。同一数据目录只运行一个服务进程。
- 运行数据库和兼容文件中仍保留业务使用的凭据，env 改造不提供加密，也不自动移除这些副本。备份时一并保护。

`.env.local`、`state/`、`backups/`、`deploy-private/` 已加入 Git 忽略规则，不应上传公开仓库。
