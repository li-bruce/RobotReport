# 部署、备份和数据迁移

[English](DEPLOYMENT.md) | 简体中文 · [README](../README.zh-CN.md)

适用于 v2.19。测试与生产使用同一源码版本和 `requirements.lock`；已验证运行环境为 Python 3.10。

## 配置项

| 配置 | 用途 |
| --- | --- |
| `ROBORT_STATE_DIR` | 私有状态目录的绝对路径，默认是项目内 `state/local`。 |
| `ROBORT_LIVE_SEND` | `1`/`true` 开启真实发送；`0`/`false` 或留空关闭，无效值阻止启动。 |
| `ROBORT_PUBLIC_BASE_URL` | 生成机器人回调的公共地址，例如 `https://club.example.org`。 |
| `ROBORT_ADMIN_TOKEN` | 可选初始化 Bearer 令牌；通常不设置，使用初始化脚本生成的私有凭据。 |
| 后台“公共访问地址” | 业务配置 `public_base_url`，用于注册和媒体链接，应与公共域名一致。 |
| 后台 AI 设置 | 密钥、模型、开关及确认有效期；支持 1–1440 分钟，默认 5 分钟。 |

应用自动读取 **`.env.local`**，也可用 `ROBORT_ENV_FILE` 指定源码目录之外的持久配置文件。公开模板全部留空，AI 密钥及机器人连接配置见[本地配置说明](ENVIRONMENT.zh-CN.md)。非空进程变量优先于文件；后台修改的相关设置会写回本地文件。业务日期和提醒使用 **Asia/Shanghai，UTC+08:00**。默认 09:30 提醒报名、17:30 提醒即将结束、18:00 发名单；符合条件的场次全天可报名。

## 新环境安装

1. 安装带 `venv` 的 Python 3.10，取得源码，创建虚拟环境并安装 `requirements.lock`。
2. 准备独立服务账户及目录，例如：

   ```text
   /srv/robort/current/       源码
   /srv/robort/venv/          Python 环境
   /srv/robort/state/         由服务账户持有的私有数据目录
   /srv/robort/state/.env.local     私有服务环境配置
   ```

3. 以服务账户初始化**空生产目录**，不加 `--demo`：

   ```bash
   cd /srv/robort/current
   /srv/robort/venv/bin/python -m scripts.bootstrap --state /srv/robort/state
   /srv/robort/venv/bin/python -m scripts.set_admin_password --state /srv/robort/state
   ```

4. 将 `.env.example` 复制为 `/srv/robort/state/.env.local`，仅服务账户可读写；填写下面几项，其余可留空：

   ```ini
   ROBORT_STATE_DIR=/srv/robort/state
   ROBORT_LIVE_SEND=0
   ROBORT_PUBLIC_BASE_URL=https://club.example.org
   ```

5. 使用 [英文部署页的 systemd 模板](DEPLOYMENT.md#new-installation)，将账户和路径替换为实际配置，保存为 `/etc/systemd/system/robort.service`。
6. 重载 systemd，仅启动本项目服务。配置 HTTPS 反向代理，登录后台，设置公共访问地址、场地、时段和机器人。
7. 检查接入配置后，把 `ROBORT_LIVE_SEND` 改为 `1` 并重启本服务；逐个机器人发送 `帮助` 验证回复。

一个状态目录只运行一个实例、一个 worker。测试实例必须使用独立状态目录。

## 反向代理与网络

金山服务端需要能访问回调，用户需要能打开注册页。建议由 HTTPS 反向代理对外提供服务，9874 仅监听本机。主机防火墙、宝塔等面板防火墙和云安全组存在时，放行实际使用的 HTTPS 端口。

在已经配置证书的 Nginx HTTPS 站点内，可使用：

```nginx
location / {
    proxy_pass http://127.0.0.1:9874;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 11m;
    proxy_read_timeout 120s;
    access_log off;
}
```

代理不在本机时，应同步调整 Uvicorn 信任的代理地址。回调和本人注册链接含凭据，访问日志不要保留完整 URL。回调不使用管理员网页登录，但必须保留自身令牌与 `robot_key` 校验。

## 数据目录

```text
state/
  registry.sqlite3                 机器人、群、认证和接入凭据
  admin.local.json                 私有脚本/API Bearer 令牌
  workspace/
    business.sqlite3              会员、场次、赛事、会籍、AI 会话、审计
    uploads/                      上传文件和照片
    tables/                       群消息表格图片
  bots/<bot_id>/
    transport.sqlite3             收发队列、去重、AI 方案和对话状态
    callback.local.json           私有回调凭据
```

旧环境可能另有迁移文件和历史数据库，应完整保留在备份中。该目录包含个人资料和密钥；文件权限用于保护访问，应用本身不提供静态数据加密。整个目录不进入 Git。

## 备份和搬迁

1. 记录源码版本、依赖锁定文件和服务配置。
2. 在约定的短暂维护窗口内，只停止 Robort 服务。
3. 将**完整状态目录**和服务配置打包至私有备份位置，计算 SHA-256 并验证压缩包可读取。仅备份时，完成后恢复原服务。
4. 新主机安装相同源码和依赖，恢复到新的状态目录，保持正确所有者和私有权限。不要对恢复的数据执行演示初始化或重置脚本。
5. 首次启动关闭真实发送，核对数据库完整性、会员/场次/赛事数量、登录及附件。验证尽量只读；模拟模式办理业务仍会写入。
6. 域名变化时，更新本地配置或后台公共地址（程序同步保存），并更新金山端回调。仅更换主机不需要重新生成原发送密钥和回调凭据。
7. 停止旧实例，再开启新实例的真实发送，检查健康接口和一条真实机器人回复，避免两个实例同时提醒或处理业务。

SQLite 备份 API 可生成单库在线副本，但分别复制多个库**不自动构成跨库一致恢复点**。服务运行中不要只拷贝 `business.sqlite3`。

新服务接收过报名或比分后，旧快照回滚可能丢失增量；先保存现状，再制定恢复方案。

## 升级与验收

先在独立副本验证。单个旧机器人业务库迁移可运行：

```bash
python -m scripts.verify_workspace_migration \
  --source /path/to/consistent-legacy-snapshot \
  --destination /path/to/new-isolated-copy \
  --source-is-snapshot
```

目标目录必须不存在；多个独立旧业务库需单独制定映射，不会静默合并。`scripts/reset_business_data.py` 是明确的破坏性维护工具，不是部署或升级步骤。

执行 `python -m pytest -q`、前端检查和相关[浏览器测试](../scripts/mobile_browser/README.md)。上线后检查 `/healthz`、管理员登录、两版使用说明、回调验证和受控机器人回复，并核对原有报名及比分保留。
