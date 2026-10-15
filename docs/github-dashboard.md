# GitHub 榜单与 SSH CPU 监视

目标板：Waveshare ESP32-S3-Touch-LCD-4.3C-BOX，16 MB Flash、8 MB OPI PSRAM、800×480。新固件在整个屏幕上展示左右榜单，替换原来的小章鱼界面；原固件完整备份可用于恢复。

## 日常运行

先按根目录说明执行 `./setup.command`，安装 GitHub CLI 并使用 `gh auth login` 登录具有目标组织仓库读取权限的账号。

```sh
.venv/bin/python github_bridge.py
# 等价的 Finder/终端入口：
./run-github.command
```

首次立即扫描，之后每次成功刷新后等待 1800 秒（30 分钟）再次获取。按 **Ctrl-C** 退出并关闭串口。停止后板子保留最后收到的数据、时间范围和刷新状态；超过 30 分钟显示数据可能过期。断电后需再次运行脚本恢复数据。

点击屏幕任意位置，在 GitHub 和 CPU 页面之间切换。默认通过本机已有 SSH 配置登录 `dynamic`，运行前先用 `ssh dynamic` 完成首次主机密钥确认和密钥登录。同步器使用 `BatchMode=yes`，不弹出密码提示。GitHub 查询在独立进程运行，CPU 采样和屏幕切换不受其耗时影响。启动时会先显示本机缓存的榜单，然后立即后台获取新数据。

CPU 页面规则：

- 每 5 秒读取 Linux `/proc/stat`，按相邻计数器差计算 busy 时间，idle 和 iowait 不计为 busy；guest 时间已在 user/nice 中，不重复相加。依据 [Linux 内核文档](https://www.kernel.org/doc/html/latest/filesystems/proc.html)。
- 使用 **20 秒滑动平均、5 秒步长**：每 5 秒产生一个新点，取该点之前 20 秒的平均值；相邻窗口重叠 15 秒。例如 00–20 秒、05–25 秒、10–30 秒连续形成三个点。采样区间按时长加权，正常情况下为四个 5 秒区间的平均值。显示最近 **5 分钟、60 个点**；边界后的首个采样到达后才发布新点，等待时保留前一个完整窗口。
- X 轴从 `end - 5 分钟` 到 `end`，每隔一分钟显示 `HH:MM` 的实际时间，与板上时钟使用相同的美国太平洋时区（自动处理夏令时）。右端表示最新滑动窗口的结束时间。
- **100% = 占满一个逻辑 CPU**；192 个逻辑 CPU 上限为 19,200%。旁边显示折合使用的核数。这是整台服务器的利用率，不按用户区分。
- 纵轴使用分段刻度：0～1 核线性映射到下方 1/4；1～8 核按对数映射到 1/4～1/2；8～192 核按对数映射到上方 1/2。因此 10% 位于轴高 2.5%、100% 在 25%、800% 在 50%、19,200% 在顶端。刻度标注原始百分比，单核与多核任务位置固定。不是 load average。其他主机最多 8 核时，1 核以上按对数填充剩余高度；单核主机用线性轴。
- 启动时不补造历史，首个完整平均值需要约 20–25 秒。采样间隔异常、SSH 断线、计数器重置时，包含缺失区间的滑动窗口留空；断线会自动重连，屏幕提示离线。GitHub 同步可以继续。
- 最近的 5 秒区间缓存在忽略的 `private/cpu-history.json`（版本 2），重启可重建窗口内的滑动平均。旧版不重叠的 20 秒缓存无法重建滑动历史，首次升级后会重新积累。服务器只运行内存中的 Python 标准库脚本，不安装包、不写文件。
- Ctrl-C 关闭 USB、停止 SSH 采样并终止后台 GitHub 查询。板子保留最后画面，约 15 秒后提示采样停止。

```sh
# 更换组织或端口
.venv/bin/python github_bridge.py --org ucrparlay --port /dev/cu.usbmodemXXXX
# 只刷新一次
.venv/bin/python github_bridge.py --once
# 只看完整排名，不连接串口
.venv/bin/python github_bridge.py --json
# 修改间隔（至少 300 秒）
.venv/bin/python github_bridge.py --interval 1800
# 更换 Linux SSH 主机
.venv/bin/python github_bridge.py --cpu-host dynamic
# 只使用 GitHub 页面，不连接 SSH
.venv/bin/python github_bridge.py --no-cpu
# 离线使用前面抓取脚本生成的完整一周快照，必须同时有 summary.json
.venv/bin/python github_bridge.py --snapshot /path/to/export/commits.json --once
```

程序每次枚举全部可访问仓库的分支和标签，并固定 tip SHA 后查询最近 7 天的历史。不可变的提交详情缓存到忽略的 `private/github-cache/`，后续不再重复下载同一 SHA 的详情。一次查询失败时保留屏幕原有结果，五分钟后重试；USB 连接失败时复用已获取数据重连，不重复扫描 GitHub。

Mac 用 `gh` 的现有登录态访问 GitHub，板子只接收组织名、排行榜名称、数字和时间，不保存 GitHub 凭证或源码。运行数据和设备备份不进入 Git。

`--once` 和 `--json` 仅执行一次 GitHub 获取，不启动持续 CPU 采样。可单独运行 `.venv/bin/python cpu_monitor.py --host dynamic` 检查 SSH、采样和本机历史，不打开 USB。

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

双页面切换需要重绘整屏。LCD 像素时钟设为 12 MHz（约 29 Hz），并在同步大面积缓冲时每复制 8 行让出一个调度周期，给 RGB bounce-buffer DMA 留出 PSRAM 带宽，消除切页时的闪烁。原因参见 [Espressif RGB LCD 带宽说明](https://docs.espressif.com/projects/esp-idf/en/v5.3.3/esp32s3/api-reference/peripherals/lcd/rgb_lcd.html)。主机采用 32 字节分段、30 ms 间隔发送；CRC 错误或 ACK 超时会先恢复行边界，再重发同一快照，最多三次。

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
设备：GHD1 HELLO id=<mac> rows=6 width=800 height=480 cpu=2 pages=2
主机：GHD1 <CRC32的8位十六进制> <紧凑JSON>\n
设备：GHD1 ACK <同一CRC32> rows=<贡献者行数>/<仓库行数>
```

CRC32 覆盖 JSON 原始 ASCII 字节，不含帧头和换行。固件先完整校验 CRC、JSON schema、数字和排序，再持锁更新整屏，避免显示半包数据。主机仅接受 CRC 与两列行数都匹配的 ACK。坏包保留现有界面并返回 `GHD1 ERROR`。

开发调试命令 `dashboard.capture` 返回 `GHD1 FRAME 800 480 768000\n`、768000 字节小端 RGB565 帧缓冲，以及 `\nGHD1 FRAME END\n`。它验证实际固件渲染内容，不能代替观察实体 LCD 的接线、亮度或物理画质。

`dashboard.status` 返回运行时间及累计渲染帧数、变更像素数。静止画面不应每秒增加渲染帧；分钟变化只应增加少量时钟或状态栏区域的像素。

CPU 帧为 `CPU1 <CRC32> <JSON>\n`，设备完整验证后返回 `CPU1 ACK <CRC32>\n`。JSON 包含 `host`、`cpus`、`online`、`last_sample`、`generated_at`、`end`（最后滑动窗口结束的 Unix 秒）、`step:5`、`window:20`、`values`（60 项，单位为总 CPU 百分比的十分之一，无数据为 null）。曲线每 5 秒发布一个新的 20 秒平均值。滑动平均版要求设备握手含 `cpu=2`，需要重新烧录；`--no-cpu`、`--once` 和 `--json` 仍兼容原榜单固件。

调试命令 `dashboard.page cpu` / `dashboard.page github` 直接切页，`dashboard.page tap` 调用与触摸相同的切换回调；`dashboard.status` 另含 `page` 和 `switches`。它们可验证页面渲染和切换逻辑，实体触摸仍需在板子上验证。

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
