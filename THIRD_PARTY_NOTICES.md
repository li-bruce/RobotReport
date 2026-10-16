# Third-party notices / 第三方组件说明

The root [MIT License](LICENSE) covers Robort's own code and documentation. It does not replace licenses of bundled assets or installed dependencies.

根目录 MIT 许可证适用于本项目代码与文档，不替代以下资源及依赖的原有许可证。

| Component / 组件 | Included material / 使用内容 | License / 许可证 |
| --- | --- | --- |
| [Noto CJK](https://github.com/notofonts/noto-cjk) | `app/assets/fonts/NotoSansCJKsc-Regular.otf`, Chinese table rendering / 中文表格图片 | [SIL Open Font License 1.1](app/assets/fonts/LICENSE.txt); copyright metadata remains embedded in the font / 字体保留版权元数据 |
| [holiday-cn](https://github.com/NateScarlet/holiday-cn) | `app/data/holidays/2026.json` and the annual data source / 内置及年度节假日数据源 | [MIT, Copyright (c) 2019 NateScarlet](app/data/holidays/LICENSE.txt) |

Python packages are installed through [requirements.lock](requirements.lock). Their distribution metadata supplies their respective license terms. Browser-test tooling is installed separately, not bundled into the application frontend.

Python 依赖通过锁定文件安装，各依赖保留发行包中的许可声明。浏览器测试工具单独安装，不打包进应用前端。
