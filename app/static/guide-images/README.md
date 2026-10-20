# 操作说明截图

这些 PNG 由实际管理页面和手机表单截图生成，使用隔离的虚构会员、名册、群和赛事；不读取生产数据，不发送群通知。图中日期、人数、手机号和群名仅作示例。

重新采集：`python scripts/mobile_browser/run.py --suite guide`。输出文件用于网页及仓库两版说明。核对图片内容、菜单和按钮后运行 `python scripts/export_guides.py` 同步 Markdown；再运行 `python scripts/mobile_browser/check_guide_editions.py` 检查不同屏宽的加载和阅读。

网页说明是内容来源：`app/static/guide.html` 和 `app/static/guide-admin.html`。更新页面后，同时更新对应操作文字及截图。
