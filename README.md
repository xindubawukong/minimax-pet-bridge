# minimax-pet-bridge

通过 USB 将 Mac 上的本地 Codex token 用量和当前时间同步到 **MiniMax SOSP 小章鱼桌宠**。保留设备原有的画面、动画、触摸切换和每日运势。

本项目的使用方式是 **USB Serial/JTAG 有线同步**，按下方步骤安装并运行即可。

本仓库包含可运行的 Mac 同步程序、安装与启动脚本、测试，以及 **直接纳入 Git 的原始 16 MiB Flash `.bin`**。固件文件不是 Git LFS 指针，克隆仓库即可获得。原小章鱼固件的 C++ 源码不在本仓库中。

## 硬件与兼容性

| 项目 | 规格／要求 |
| --- | --- |
| 已实测设备 | MiniMax SOSP 活动的小章鱼桌宠 |
| 板子型号 | Waveshare **ESP32-S3-Touch-LCD-4.3C-BOX**，SKU **33630** |
| 处理器 | ESP32-S3R8，Xtensa LX7 双核，最高 240 MHz |
| 存储 | 16 MB Flash、8 MB PSRAM |
| 屏幕 | 4.3 英寸、800 × 480、电容触摸 |
| 通信 | USB Serial/JTAG |
| 设备固件 | 实测启动标识 `sosp-v11`，USB protocol 9（`SOSP9` / `SYNC9`） |
| 主机 | macOS；Python 3.10+；安装脚本使用 uv |
| 数据 | 本机已有 Codex 会话日志，默认 `~/.codex` |
| 线材 | 支持数据传输的 USB 线 |

**硬件相同不等于固件相同。** 请根据设备状态选择：

- **已经显示小章鱼，且支持 SOSP9**：直接按下面的快速开始操作，不需要烧录。
- **同款板子，但运行微雪出厂示例或其他固件**：当前程序不能直接控制那个界面。可参考 [固件备份与恢复](docs/firmware.md) 写入随仓库提供的完整镜像；跨设备写入尚未实测，先备份接收设备自己的 Flash。
- **4.3、4.3B、其他 ESP32-S3 或其他尺寸屏幕**：不是本镜像已确认的目标，不应仅凭芯片相同就刷入。

硬件规格来自 [微雪产品文档](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/)。无外壳 4.3C 版本的完整兼容性也未单独验证。

## 快速开始：已有小章鱼固件

### 1. 获取项目并安装依赖

克隆或下载本仓库，进入 `minimax-pet-bridge` 目录。尚未安装 uv 时，按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 操作；已有 Homebrew 的 Mac 可以运行 `brew install uv`。

```sh
cd minimax-pet-bridge
./setup.command
```

安装脚本会在本目录创建 `.venv`，选择 Python 3.10+ 并安装固定版本的 `pyserial`。uv 在必要时可能下载 Python。脚本复用已有环境，不自动清空它，也不安装开机启动服务。

### 2. 检查本机数据

```sh
./run.command --json
```

这一步不打开 USB，输出四个时间窗口的统计。第一次需要扫描日志，可能耗时几秒。若提示没有会话或用量记录，先在这台 Mac 上使用 Codex，或指定实际的数据目录。

### 3. 连接 USB 并同步

```sh
./run.command --once
```

成功时会看到类似输出，示例数值仅用于说明：

```text
SOSP9 HELLO id=<device-id> timezone=Asia/Shanghai
12:00:00 Confirmed: hour 1.00M / day 2.00M / week 3.00M / month 4.00M
SOSP9 ACK <unix-seconds> 1000000 2000000 3000000 4000000 state=2 date=<日期>
```

程序只有在收到与本次五个数值完全匹配的 ACK 后才显示 `Confirmed`。设备仍需正常运行并显示对应数据，串口回执不替代实际屏幕检查。

### 4. 持续使用

```sh
./run.command
```

也可以在 Finder 中双击 `run.command`。保持终端打开，默认在每次成功同步后等待 10 秒，再更新下一次。按 **Ctrl-C** 停止；关闭程序或 Mac 休眠后不再持续发送用量。连接出错时程序会报告错误并重试。

点击屏幕上方的 Codex 用量卡片可以切换今日、本周、本月。切换由板子固件处理，Mac 每次会同时发送全部时间窗口的数据。

## 参数与串口选择

以下命令均在仓库根目录执行：

```sh
# 查看所有参数
./run.command --help

# 每 60 秒刷新
./run.command --interval 60

# 列出本机串口及设备信息
.venv/bin/python -m serial.tools.list_ports -v

# 多块板子同时连接时，指定实际端口
./run.command --port /dev/cu.usbmodemXXXX

# 指定 Codex 数据目录
./run.command --codex-dir /path/to/.codex
```

默认只在检测到 **恰好一个** Espressif USB Serial/JTAG 端口（VID `303a`、PID `1001`）时自动选择。多个端口会报错，避免连接错设备；其他 VID/PID 的串口需显式指定，并仍须通过 SOSP9 握手。

数据目录优先级为：`--codex-dir` > 已有的 `CODEX_HOME` 环境变量 > `~/.codex`。安装、运行均使用仓库相对路径，不依赖原开发者的用户名或目录。

## 用量和时间的含义

| 字段 | 统计范围 | 屏幕显示 |
| --- | --- | --- |
| `hour` | 最近 1 小时 | 随报告发送，当前卡片不单独展示 |
| `day` | 最近 24 小时 | 今日用量 |
| `week` | 最近 7 天 | 本周用量 |
| `month` | 最近 30 天 | 本月用量 |

- 这些是滚动时间窗口，不是从当天零点、周一或每月 1 日开始。
- 数据来自 `sessions/` 和 `archived_sessions/` 下的 JSONL。使用逐响应的 `token_usage_record.usage.total_tokens`，按响应 ID 去重；重复记录取较大的总数。
- 缺少现代记录的旧格式文件，使用 `event_msg/token_count` 累计计数的增量。同一文件含现代记录时不会再叠加旧格式；迁移前后混合格式的历史文件可能统计不完整。
- 总数包括输入与输出，不把缓存输入或推理输出重复加到总数里。多次处理长上下文可能产生很大的累计值。
- **这是本机日志中的 token 总量，不是订阅剩余额度或账单金额**；也不包含未保存在本机的远程／其他电脑日志。
- Mac 发送当前 Unix 时间戳，设备固件按 **Asia/Shanghai（北京时间）** 显示日期和切换场景。Mac 在其他时区时，屏幕日期可能不同。本同步器不能通过现有协议改成别的时区。
- 运势由设备固件内置逻辑处理，Mac 不生成“宜／忌”。停止同步后的跨日刷新和断电保时行为未实测。

## 数据与隐私边界

同步器扫描本地日志，但只提取用量、时间及用于去重的标识，不复制或发送对话正文，不读取认证文件，不调用 OpenAI API 或网络服务。USB 数据包只有时间戳和四个非负整数。

`.venv`、个人日志、环境文件和用户自行生成的 `local-backups/` 不进入 Git。**随仓库提供的原始固件是例外：它按原样追踪在 `firmware/`，包括 NVS 等设备数据分区，未做配置清理。** 其范围和来源见 [固件说明](docs/firmware.md) 与 [NOTICE](NOTICE.md)。

## 工作原理与 USB 协议

```text
Mac 本地 Codex 日志 → usage.py 统计 → bridge.py
                                      ⇅ USB 串口
                            小章鱼固件 → LCD / 触摸 / 动画
```

USB 是双向通道。程序主动发送查询和数据，设备返回日志及确认。当前 Mac 程序不把设备触摸事件转换为电脑操作。

串口参数：115200 baud、8N1、DTR 开启、RTS 关闭。每条消息以换行结束。

```text
主机：sosp.hello\n
设备：SOSP9 HELLO id=<device-id> timezone=Asia/Shanghai

主机：SYNC9 <unix_seconds> <hour_tokens> <day_tokens> <week_tokens> <month_tokens>\n
设备：SOSP9 ACK <unix_seconds> <hour_tokens> <day_tokens> <week_tokens> <month_tokens> state=<n> date=<日期>
```

上面的 `\n` 表示换行字节，不是发送反斜杠和字母 n。报告为 ASCII，回执中的中文日期按 UTF-8 解码。程序保留跨串口超时的分段消息，拒绝过期或数值不匹配的 ACK，并对 `SOSP9 ERROR` 报错。协议来自这台设备的日志和实测，不是 ESP32 通用标准。

## 原始固件与恢复

- 文件：[firmware/original-flash-2026-10-13.bin](firmware/original-flash-2026-10-13.bin)
- 大小：**16,777,216 字节（16 MiB）**；完整 Flash 读回镜像，起始地址 `0x0`。
- 来源：2026-10-13 从一台 MiniMax SOSP 小章鱼设备用 esptool 只读导出；原始字节未修改。
- 分区、硬件和验证状态：[firmware/manifest.json](firmware/manifest.json)。
- SHA-256：`efd0e6b663ad80853622d849f3ab7e6ab6fd298f83a0eb4c427b4cff06580a12`。

本地检查镜像，不连接硬件：

```sh
.venv/bin/python verify_firmware.py
```

实际备份、恢复和校验命令见 **[固件备份与恢复](docs/firmware.md)**。日常同步不需要烧录工具，也不会刷写固件。完整恢复会覆盖目标板的程序和配置；本项目尚未进行恢复或第二块板子的刷入实验。

## 官方教程与二次开发

| 资料 | 用途 |
| --- | --- |
| [微雪产品与引脚说明](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/) | 型号、内存、屏幕、接口 |
| [微雪相关资料](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/Resources-And-Documents/) | 原理图、规格书和示例下载 |
| [Arduino 开发教程](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/Arduino/) | 配置环境、LCD、触摸、LVGL 示例 |
| [ESP-IDF 开发教程](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/ESP-IDF/) | 更底层的开发框架与示例 |
| [微雪烧录说明](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/Firmware-Flashing/) | 下载模式与板级烧录说明 |
| [esptool 官方命令](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/basic-commands.html) | 读取、烧录与校验 Flash |
| [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) | 安装本仓库使用的 Python 环境管理工具 |

微雪当前 Arduino 教程使用 ESP32 Arduino Core **3.1.1** 和 LVGL **8.4.0**。开发新界面可从 `03_lcd`、`06_touch`、`12_lvgl_transplant`、`13_lvgl_btn` 开始。官方示例提供驱动和硬件初始化，不包含活动的小章鱼素材或固件源码。

若只修改统计方式或发送频率，改 Mac 端即可；要换布局、素材或增加设备协议，则需要新固件。可以把新固件做成可接收文字／图片的显示程序，以后更换内容就不必每次烧录。

## 故障排查

| 现象 | 处理方式 |
| --- | --- |
| 找不到 uv／Python 环境 | 按快速开始安装 uv，再运行 `./setup.command` |
| 复制项目后 `.venv` 失效 | 虚拟环境不随 Git 分发；将旧环境移到别处，再运行安装脚本 |
| 找不到 Espressif USB 端口 | 换支持数据的线，检查连接，再列出串口 |
| 检测到多个端口 | 使用 `--port` 指定目标板，不会自动猜第一台 |
| 屏幕正常却没有握手／ACK | 关闭其他串口监视器和重复同步器，拔下 USB 等 5 秒再插回；实测此方法恢复过通信 |
| `Could not exclusively lock port` | 已有另一个同步器占用端口，先停止它 |
| 没有 `SOSP9 HELLO` | 核实设备固件版本；微雪出厂示例不会识别这套协议 |
| 没有用量记录／统计不完整 | 核实本机有 Codex 日志，检查数据目录；程序不会把缺失日志伪装成 0 |
| 时间或“今日”与预期不同 | 设备显示北京时间，统计窗口为滚动 24 小时 |
| 开机后未自动更新 | 当前没有开机启动服务，需要运行 `run.command` |

## 测试与验证范围

```sh
.venv/bin/python -m unittest discover -v
.venv/bin/python verify_firmware.py
zsh -n setup.command run.command
```

单元测试覆盖窗口边界、去重、累计计数、现代／旧格式的重复统计、ACK 匹配及分段 UTF-8 回执等。测试使用合成日志，不依赖个人会话，不连接或刷写设备。

硬件验证记录：在一台 macOS 主机与上述原装赠品上完成 USB 握手、用量与日期同步、连续 6 次自动同步，使用者确认屏幕更新。固件备份大小和 SHA-256 已核对。**这些结果不代表已经验证跨设备烧录、断电恢复或其他操作系统。**

## 核心文件

```text
minimax-pet-bridge/
├── README.md / LICENSE / NOTICE.md
├── setup.command / run.command
├── bridge.py / usage.py
├── verify_firmware.py
├── requirements.txt / requirements-firmware.txt
├── test_*.py
├── firmware/
│   ├── original-flash-2026-10-13.bin
│   ├── SHA256SUMS
│   └── manifest.json
└── docs/
    └── firmware.md
```

原创桥接程序及文档采用 [MIT License](LICENSE)。原始固件及内含素材的来源和许可范围见 [NOTICE.md](NOTICE.md)。
