# 手机页面浏览器验收

使用隔离的本地测试数据检查管理后台、注册表单和使用说明。默认覆盖
320×740、360×800、390×844、768×1024、844×390（横屏）、1280×900。

在项目根目录、已安装项目依赖的 Python 环境中运行：

```shell
python scripts/mobile_browser/run.py
```

仅验收报名名单与逐条取消同行（同样覆盖六个视口）：

```shell
python scripts/mobile_browser/run.py --suite proxy
```

专项检查姓名/性别/名额列、代报名负责人、访客性别、取消确认及仅选中条目改变，
并保存 `proxy-report.json` 和截图。所有新增、取消操作仅作用于本次合成数据。

机器人业务组合与同群绑定专项验收：

```shell
python scripts/mobile_browser/run.py --suite bots
```

覆盖 320、390、844 横屏及 1280 像素宽度，实际操作业务勾选、分身份帮助、
机器人管理/群关联分页、连接图的键盘与缩放、暂停及解除连接、群增删改、仅接收连接、无手机号导入的逐条/批量确认与同名选择。
结果保存为 `bots-report.json`，不接触真实群。

`bots` 套件通过 `check_bot_onboarding.py` 运行 `check_relations.py`，使用本地模拟发送接口，不向真实群发消息。

浏览器测试需要可选依赖 Playwright。缺少时可手动安装：

资料调整与审核可单独运行 `python scripts/mobile_browser/run.py --suite profile`，覆盖本人表单、提交、后台审批和历史记录，在 320、390、844、1280 像素宽度检查交互与页面溢出。

```shell
python -m pip install playwright
python -m playwright install chromium
```

也可使用已安装的 Chromium 或 Edge：将环境变量 `ROBORT_BROWSER_EXECUTABLE`
设为浏览器可执行文件的绝对路径；此时无需下载 Playwright Chromium。
运行脚本不会安装依赖或下载浏览器。

- 默认使用 `127.0.0.1:9888`，端口被占用时直接退出；可传 `--port 9889`。
- 每次运行新建模拟会员、机器人、赛事和六个独立注册链接，关闭发送与定时任务。
- 浏览器及服务只允许本地连接，不调用真实群机器人、模型或生产服务。
- `fixture.py`、`check.py` 由 `run.py` 启动，使用同一 Python 解释器。
- 等新服务和当前运行的元数据就绪后才开始检查；结束或中断时关闭本次子进程树。
- 日志、截图、JSON 报告和合成数据保留在已被 Git 忽略的
  `test-results/mobile-browser/<运行编号>/`，失败时优先查看 `mobile-report.json`
  和 `fixture-server.log`。

检查包括页面横向溢出、手机导航与焦点、弹窗尺寸、触控表单字号和高度、
注册断网恢复与重试、说明页搜索/复制/无 JavaScript 阅读。
执行正常且全部断言通过时退出码为 `0`；任何失败返回非零退出码。

新增 `--suite operations`：实际操作 Excel 列识别导入、无ID预登记、导入姓名确认注册、定时播报、机器人功能时间规则；覆盖320/390/844/1280宽度。

`--suite confirmation` 验证通知预览和幂等排队、补全手机号关联现有会员、批量关联、姓名预填和无级别直接注册；`--suite profile` 验证会员档案统一待审、原值对比和审批历史。均使用隔离数据、禁用外网与真实群发送。
