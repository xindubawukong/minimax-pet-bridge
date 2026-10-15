# 来源与许可范围

本仓库是用于 MiniMax SOSP 小章鱼桌宠的独立 Mac 端配套项目，不是 MiniMax、Waveshare 或 OpenAI 官方发布的软件。

- 原创 Python 同步程序、辅助脚本、测试及本仓库新写的文档采用 [MIT License](LICENSE)。
- `firmware/original-flash-2026-10-13.bin` 是从一台 MiniMax SOSP 活动赠送设备读取的原始 Flash 镜像。本项目没有原固件源码，也没有随镜像取得完整的上游许可证清单。镜像中的程序、图片、字体、商标及第三方组件权利归各自权利人；本仓库的 MIT 许可不覆盖或重新授权这些内容。
- 原始镜像逐字节保留，包括 NVS 和其他数据分区。它是设备快照，不是去除配置后的通用发行镜像；不能把本仓库的校验通过等同于配置或许可审计。

品牌名称仅用于说明兼容的设备和数据来源。

`firmware/github_dashboard/src/` 基于 Waveshare 官方 ESP32-S3-Touch-LCD-4.3C 示例，来源提交 `44e412f08c1e68c0da826bc5f51436f156a02eea`，包含 Espressif 的触摸驱动代码；这些文件采用 Apache License 2.0，副本见 `firmware/github_dashboard/WAVESHARE-LICENSE.txt`。修改过的驱动文件在文件头标注了修改内容。新增榜单 UI、主机统计和桥接代码采用本仓库 MIT 许可。
