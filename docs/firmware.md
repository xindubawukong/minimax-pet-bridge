# 原始固件：备份、写回和验证

## 本仓库包含什么

`firmware/original-flash-2026-10-13.bin` 是从一台 MiniMax SOSP 小章鱼桌宠读取的 **完整 Flash 镜像**，包含引导程序、分区表、应用、图片／字体资源以及 NVS 等数据分区。它不是单独的应用 `.bin`，也不是从源码重新编译的发行版。

| 项目 | 值 |
| --- | --- |
| 目标硬件 | Waveshare ESP32-S3-Touch-LCD-4.3C-BOX，SKU 33630 |
| 芯片／原设备 revision | ESP32-S3 / 0.2 |
| Flash／PSRAM | 16 MB / 8 MB |
| 屏幕 | 800 × 480 |
| 完整镜像大小 | 16,777,216 字节 |
| 写回起始地址 | `0x0` |
| 捕获日期 | 2026-10-13 |
| 读取方式 | esptool 5.5.0，`read-flash 0 ALL` |
| 固件运行标识 | `sosp-v11`，USB protocol 9，Asia/Shanghai |
| SHA-256 | `efd0e6b663ad80853622d849f3ab7e6ab6fd298f83a0eb4c427b4cff06580a12` |

镜像按原样直接存储在 Git 中，没有 LFS、下载脚本或占位文件。它包含原设备的持久配置，不是清理过 NVS 的通用镜像。芯片内的 eFuse 不在 Flash 备份中。

已验证：完整读回、镜像校验、原设备上的 USB 同步。**未验证：把这个镜像实际写回，或刷到第二块板子。** 以下命令是依据官方工具编写的恢复流程，不能视为已经完成恢复测试。

## 1. 安装可选工具并确定端口

下面所有命令从仓库根目录运行，先完成主 README 的 `./setup.command`。

```sh
uv pip install --python .venv/bin/python -r requirements-firmware.txt
.venv/bin/python -m serial.tools.list_ports -v
```

停止 `run.command` 和其他占用串口的工具，把下面占位端口改为实际端口：

```sh
PET_PORT=/dev/cu.usbmodemXXXX
.venv/bin/python -m esptool --chip esp32s3 --port "$PET_PORT" flash-id
```

确认连接的是目标设备，检测结果为 ESP32-S3、16 MB Flash，且实际板型是 4.3C-BOX。仅检测到 ESP32-S3 不能确定屏幕接线兼容。若设备启用了安全启动／Flash 加密等保护，本流程可能不适用；不要用强制选项绕过工具报错。

## 2. 先备份接收设备自己的内容

即使同型号，另一块板子的已有配置也不同。为它创建独立备份；`local-backups/` 已被 Git 忽略。

```sh
mkdir -p local-backups
PET_BACKUP="local-backups/before-restore-$(date +%Y%m%d-%H%M%S).bin"
.venv/bin/python -m esptool --chip esp32s3 --port "$PET_PORT" --baud 460800 \
  read-flash 0 ALL "$PET_BACKUP"
shasum -a 256 "$PET_BACKUP"
```

保存备份与校验值。不要覆盖已经存在的备份文件；如果重复执行时文件名相同，换一个新名字。读取会使设备进入下载模式，结束后通常重启，但不会持久写入固件。

## 3. 检查仓库镜像

```sh
.venv/bin/python verify_firmware.py
```

输出应为 `OK`，大小和 SHA-256 与上表一致。这验证文件传输完整性，不代表对镜像内容或跨设备兼容性作了审计。

## 4. 写回完整镜像

**下面的 `write-flash` 会覆盖目标板整个 16 MiB Flash 内的程序和配置。** 想保留现有界面只做日常同步时，不需要执行。

```sh
.venv/bin/python -m esptool --chip esp32s3 --port "$PET_PORT" --baud 460800 \
  --after no-reset write-flash 0x0 firmware/original-flash-2026-10-13.bin
```

`0x0` 专用于这里的完整镜像；不要套用到其他项目单独编译出的应用文件。工具会处理所写区域的擦除，不需要另外运行整片擦除命令。

本步骤暂不启动应用，便于在固件修改 NVS 之前校验整个镜像。写入成功后再运行：

```sh
.venv/bin/python -m esptool --chip esp32s3 --port "$PET_PORT" --baud 460800 \
  verify-flash 0x0 firmware/original-flash-2026-10-13.bin
```

工具默认在结束后重启。如果写入或校验失败，保留错误输出并核查，不要继续假定恢复成功。固件正常启动后可能自行更新 NVS，因此之后再做整片比较出现差异不一定代表程序损坏。

## 5. 检查实际功能

1. 设备启动后应出现小章鱼画面，触摸用量卡片应可切换显示。
2. 使用这台 Mac 自己的 Codex 日志做一次同步：

   ```sh
   ./run.command --port "$PET_PORT" --once
   ```

3. 检查 `SOSP9 HELLO`、与发送内容匹配的 ACK，以及屏幕用量、日期。
4. 运行 `./run.command --port "$PET_PORT"` 开始持续同步。

## 无法连接下载模式

先检查数据线、串口占用和端口名称。自动进入下载模式失败时，按微雪说明操作 BOOT 与 RST：按住 BOOT，短按 RST，然后释放 BOOT，再确认当前串口名称。下载模式和运行模式下的端口可能变化。

普通 Flash 重刷不会改写 ESP32 芯片 ROM 中的下载程序，但硬件故障或不可逆安全设置变更不属于本仓库能保证恢复的范围。

## 进一步开发

本仓库无法从该 `.bin` 重建原项目源码。要做新界面，可从 [4.3C Arduino 示例](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/Arduino/) 或 [ESP-IDF 示例](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/ESP-IDF/) 开始，按示例的板级参数编译和上传。新固件需要实现同样的 `SOSP9` / `SYNC9` 协议，或同时修改 Mac 端通信代码。

参考：[esptool 基本命令](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/basic-commands.html)、[微雪硬件文档](https://docs.waveshare.net/ESP32-S3-Touch-LCD-4.3C/)、[仓库许可与来源](../NOTICE.md)。
