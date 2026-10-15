# GitHub 双列榜单

目标板：Waveshare ESP32-S3-Touch-LCD-4.3C-BOX，16 MB Flash、8 MB OPI PSRAM、800×480。新固件在整个屏幕上展示左右榜单，替换原来的小章鱼界面；原固件完整备份可用于恢复。

## 日常运行

先按根目录说明执行 `./setup.command`，安装 GitHub CLI 并使用 `gh auth login` 登录具有目标组织仓库读取权限的账号。

```sh
.venv/bin/python github_bridge.py
# 等价的 Finder/终端入口：
./run-github.command
```

首次立即扫描，之后每次成功刷新后等待 1800 秒（30 分钟）再次获取。按 **Ctrl-C** 退出并关闭串口。停止后板子保留最后收到的数据、时间范围和刷新状态；超过 30 分钟显示数据可能过期。断电后需再次运行脚本恢复数据。

```sh
# 更换组织或端口
.venv/bin/python github_bridge.py --org ucrparlay --port /dev/cu.usbmodemXXXX
# 只刷新一次
.venv/bin/python github_bridge.py --once
# 只看完整排名，不连接串口
.venv/bin/python github_bridge.py --json
# 修改间隔（至少 300 秒）
.venv/bin/python github_bridge.py --interval 1800
# 离线使用前面抓取脚本生成的完整一周快照，必须同时有 summary.json
.venv/bin/python github_bridge.py --snapshot /path/to/export/commits.json --once
```

程序每次枚举全部可访问仓库的分支和标签，并固定 tip SHA 后查询最近 7 天的历史。不可变的提交详情缓存到忽略的 `private/github-cache/`，后续不再重复下载同一 SHA 的详情。一次查询失败时保留屏幕原有结果，五分钟后重试；USB 连接失败时复用已获取数据重连，不重复扫描 GitHub。

Mac 用 `gh` 的现有登录态访问 GitHub，板子只接收组织名、排行榜名称、数字和时间，不保存 GitHub 凭证或源码。运行数据和设备备份不进入 Git。

## 统计口径

- 最近 7×24 小时，按 Git **committer 时间**筛选，不是 push 时间。
- 行数为 GitHub 返回的 additions + deletions，所有具有文本行数统计的文件参与；不是净增量或仅源代码。二进制文件通常没有文本行数。
- 同一仓库多个分支或标签上的同一 SHA 只算一次。每个仓库单独计其变更行数。
- 贡献者跨仓库的同一 SHA 只算一次，避免 fork/复制历史重复计人。
- 多父提交（merge commit）不参与行数和提交次数排名，避免把已经计入普通提交的改动再计给合并者。因此榜单提交数可能少于完整导出的所有提交数。合并时独有的冲突解决改动也不单独计入。
- 作者优先使用关联的 GitHub login，否则使用 Git 作者名；没有账号关联的别名可能需要人工归并。
- 同分时按新增行数、名称排序。桌面 JSON 含全部排名，屏幕各显示前六名。
- 已删除且不可达分支、未推送提交不在统计范围。缓存不会使过期提交继续参与滚动窗口。

## 编译

```sh
.venv/bin/python build_firmware.py
```

该脚本支持 macOS，在忽略的 `build/` 内准备 Arduino CLI 1.5.1、Espressif Arduino Core 3.1.1、LVGL 8.4.0 和 ArduinoJson 7.4.2，并编译源码到 `build/github_dashboard-bin/`。Apple Silicon 上从固定 Arduino ctags 源码编译原生工具，并修复旧宏名与新 macOS SDK 的冲突，无需 Rosetta。

GPIO、RGB 时序、触摸和 LVGL 移植基于[微雪官方示例](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-4.3C)，固定来源提交 `44e412f08c1e68c0da826bc5f51436f156a02eea`。本仓库保留其 Apache 2.0 许可证和原始版权标记；修改了初始化时的 VSYNC 空句柄保护和背光开启，使用双缓冲 direct mode，仅复制发生变化的区域，并记录最后提交的帧以准确截图。配置依据[官方 Arduino 教程](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/Arduino/)与 [LVGL 8 双缓冲同步说明](https://docs.lvgl.io/8.4/porting/display.html#direct-mode)。

时钟只显示到分钟；文字没有变化时不调用 LVGL 的文字设置函数，避免每秒触发整屏重绘。实际变化先显示新缓冲，等待帧边界后把变更区域同步到另一缓冲，避免新旧画面交替。`Ctrl-C` 停止 Mac 同步，板上的时钟和数据过期提示仍会继续工作。

## 备份、烧录和恢复

先停止所有串口程序。不要覆盖现有备份，文件名每次换新。下面的端口仅为示例。

```sh
uv pip install --python .venv/bin/python -r requirements-firmware.txt
mkdir -p local-backups
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  read-flash 0 ALL local-backups/before-dashboard-UNIQUE.bin
shasum -a 256 local-backups/before-dashboard-UNIQUE.bin
```

确认备份为 16,777,216 字节并保存校验值后，写入已编译的三个文件：

```sh
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  --after no-reset write-flash \
  0x0 build/github_dashboard-bin/github_dashboard.ino.bootloader.bin \
  0x8000 build/github_dashboard-bin/github_dashboard.ino.partitions.bin \
  0x10000 build/github_dashboard-bin/github_dashboard.ino.bin
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  verify-flash \
  0x0 build/github_dashboard-bin/github_dashboard.ino.bootloader.bin \
  0x8000 build/github_dashboard-bin/github_dashboard.ino.partitions.bin \
  0x10000 build/github_dashboard-bin/github_dashboard.ino.bin
```

使用原有分区偏移，应用为 `factory` 类型，不写入 NVS、SPIFFS 或 eFuse。要恢复原界面，使用自己保存的完整 Flash 备份，按照[固件恢复说明](firmware.md)从 `0x0` 写回整个镜像，再验证。

## USB 协议

115200 baud、8N1，DTR 开启、RTS 关闭。

```text
主机：dashboard.hello\n
设备：GHD1 HELLO id=<mac> rows=6 width=800 height=480
主机：GHD1 <CRC32的8位十六进制> <紧凑JSON>\n
设备：GHD1 ACK <同一CRC32> rows=<贡献者行数>/<仓库行数>
```

CRC32 覆盖 JSON 原始 ASCII 字节，不含帧头和换行。固件先完整校验 CRC、JSON schema、数字和排序，再持锁更新整屏，避免显示半包数据。主机仅接受 CRC 与两列行数都匹配的 ACK。坏包保留现有界面并返回 `GHD1 ERROR`。

开发调试命令 `dashboard.capture` 返回 `GHD1 FRAME 800 480 768000\n`、768000 字节小端 RGB565 帧缓冲，以及 `\nGHD1 FRAME END\n`。它验证实际固件渲染内容，不能代替观察实体 LCD 的接线、亮度或物理画质。

`dashboard.status` 返回运行时间及累计渲染帧数、变更像素数。静止画面不应每秒增加渲染帧；分钟变化只应增加少量时钟或状态栏区域的像素。

```sh
# 同步器停止后读取设备实际渲染的 PNG，输出在忽略的 private/ 下
.venv/bin/python capture_dashboard.py
```

## 验证

```sh
.venv/bin/python -m unittest discover -v
.venv/bin/python build_firmware.py
zsh -n run-github.command
```
