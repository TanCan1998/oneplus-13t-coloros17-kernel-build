# OnePlus 13T ColorOS 17（国行）内核云端构建（SukiSU / 原生 SUSFS）

在免费 GitHub runner 上，用一加官方源码与官方构建入口编译 **一加 13T 国行 ColorOS 17**（PKX110 / sun / sm8750）
内核，把 SukiSU 编进内核，并可选地集成**原生 SUSFS**（编进内核，不是 LKM）。

> **适用系统：ColorOS V17.0.0，国行 CN 版本（`PKX110_17.0.0.104(CN01)`）。**
> **不适用于国际版 / OxygenOS 机型，也不保证适用于 16.x 或其他 sm8750 设备。**

仓库只产出构建产物：**不含刷机脚本、不含任何私钥、不含手机备份**。

## 基于哪个官方版本

| 项目 | 值 |
|---|---|
| 目标系统 | **ColorOS V17.0.0（国行 CN，非国际版 OxygenOS）**，`PKX110_17.0.0.104(CN01)`，`ro.build.version.oplusrom=V17.0.0` |
| 机型 | OnePlus 13T，型号 PKX110，市场名「一加 13T」，代号 sun，SoC sm8750 |
| 官方 manifest 分支 | `oneplus/sm8750_b_16.0.0_oneplus_13t`（一加开源仓库当前提供的 16.0.0 分支） |
| common | `OnePlusOSS/android_kernel_common_oneplus_sm8750` @ `4d611c32bedc11cf3ae46aa5e815dfe35707b93f` |
| msm-kernel | `OnePlusOSS/android_kernel_oneplus_sm8750` @ `745702d5ed2c0435f2b1d3bb78bc4f48422d4191` |
| modules / devicetree | `OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750` @ `6a196f79f77acd55cf302e1696c038e4cad99254` |
| CLO 侧依赖版本 | 标签 `clo-deps-16.0.10.500`（7 个原站包的镜像，见 `checksums.txt`） |
| 依赖总数 | 35（3 个 OnePlusOSS + 25 个 CodeLinaro GitHub 镜像 + 7 个 codelinaro 原站） |
| 内核版本串 | `6.6.118-android15-8-o-4k`，4K 页，Android clang 11368308 / 18.0.0（r510928） |
| SukiSU | `SukiSU-Ultra/SukiSU-Ultra` @ `7ea0dd2b09bfc60e6b51a6a93fc9f49b349a9b2a` |
| SUSFS 上游 | `cctv18/susfs4oki` @ `3f6aae536724af0df3dfa15f932034d14a5dbc5c`（`oki-android15-6.6`） |

固定清单以 `susfs-native.lock.json`、`susfs-inputs.lock.json`、`deps.json`、`checksums.txt` 为准；
每个文件的 SHA256 都记录在锁文件里，构建时逐一核对，不匹配即失败。

### 为什么是 ColorOS 17 系统 + 16.0.0 源码

一加开源仓库目前给的是 `16.0.0` 分支，没有 `17.0.0` 分支；而设备系统已升到 **ColorOS 17 国行**。
两者内核串同为 `6.6.118-android15-8-o-4k`，实测该 16.0.0 源码构建出的 boot 在
`PKX110_17.0.0.104(CN01)` 上能正常开机、Root 可用（见下文实测记录）。

但**跨大版本不能假定等价**：vendor 模块、ABI/CRC、安全补丁与 17.0.0 原厂内核的差异未逐项核对。
若后续要跟进 17.0.0 源码，替换锁文件里的 commit 与 SHA256 即可，流程不变。

### 适用范围

| 场景 | 是否适用 |
|---|---|
| 一加 13T 国行，ColorOS 17（`PKX110_17.0.0.104(CN01)` 实测） | 已验证可开机 |
| 一加 13T 国行，ColorOS 16.x | 未验证；源码分支匹配，但 boot/模块组合未实测 |
| 一加 13T **国际版 / OxygenOS** | **不适用**（分区布局、vbmeta、模块签名与国行不同） |
| 其他 sm8750 机型（13、13R 等） | **不适用**，机型耦合（sun devicetree） |

## 这套流程的依据

全部来自本地已验证的实测结果：

- 官方 manifest 的 **35 个依赖**，共 3.01 GiB，官方入口 `oplus_build_kernel.sh sun perf`，源码零修改。
- 本地 8 核全量构建耗时 **3h12m12s**，退出码 0，产出 `boot.img` 100,663,296 字节（与出厂 boot_a 同尺寸）。
- 该未改基线内核已在真机验证：`boot_completed=1`、602 个模块加载、`disagrees about version` 为 0。

## 关键取舍

**跳过 vendor 模块编译**（`RECOMPILE_EXT_MODULE=0`）。

本地 3h12m 里约 2 小时在编 359 个 vendor ko，而 `boot.img` 只含内核 Image。只编内核才能让 4 核 runner
在 6 小时上限内完成。

**`CONFIG_MODULE_SIG_PROTECT=n`**：vendor 模块用 OPPO 私钥签名，自编译内核没有对应 trusted key，
`mod->sig_ok` 为 false 会让 `gki_is_module_protected_export()` 拒绝导出，导致
rfkill → cfg80211 → mac80211 → qca_cld3 整链加载失败（WiFi/蓝牙不可用）。关掉后整链放行，
代价是**减少导出保护**，属于已验证基线的取舍。

**配置门禁**：编译前单独 `bazel build //common:kernel_aarch64_config //msm-kernel:sun_perf_config`，
检查两份 `.config` 都有 KSU/SUSFS 所需选项、`CONFIG_MODULE_SIG_PROTECT` 未开启，不满足立即失败，
避免白跑 3 小时。

**两棵树都要打补丁**：`sus16k_perf_dist` 的 boot.img 来自 msm-kernel 树；只改 common 会以
17 个 `susfs_*` 符号未定义在链接阶段失败（run 39532454585 即此原因）。

## 工作流

| 工作流 | 用途 |
|---|---|
| `build-susfs-kernel` | 固定源码门禁 → 装配 35 依赖 → 安装原生 SUSFS → 双配置门禁 → 编译 → 产物与符号复验 |
| `build-ksu-kernel` | 早期 SukiSU 内置版本，`enable_susfs` 可选 |
| `susfs-preflight` / `susfs-msm-probe` | 只读探测：补丁可应用性、msm-kernel 目标文件与 common 是否逐字节相同 |

手动触发 `build-native-susfs-kernel`，`skip_build=true` 只做装配与配置验证（约 15–25 分钟）。
产物含 `boot.img`、`Image`、`System.map`、`SHA256SUMS.txt`、两份 `.config` 与全部构建/复验日志。

## 已验证的结果

### 一、静态：构建与产物复验（run 39595181403，提交 `197ba2d`）

- 独立复验 **526 项检查**通过：两个 ZIP 的字节数/摘要/CRC 一致；在新建目录重放 common、msm-kernel、
  SukiSU 补丁零拒绝、零 fuzz、零偏移；common 与 msm-kernel 各 69 项源码门禁通过。
- boot 为 Android v4 头，96 MiB，空 ramdisk；其中嵌入的内核与独立 `Image` 逐字节一致。
- 从实际 ARM64 Image 解出内嵌配置，与归档配置分别通过所需选项与 4K 页检查。
- System.map 独立计数：KSU 336 个符号、SUSFS/桥接 79 个；新接口 `susfs_is_sus_ino` 有已链接定义。

| 文件 | 字节 | SHA256 |
|---|---:|---|
| boot.img | 100663296 | `5df9c1dde13ab90f26ec39fc0f318b297cd674a06e25eb746a9b83822a6288ff` |
| Image | 36538880 | `1920f722896c0262e35d1a0be14ed1150d607fb7d88619fa8afb093222737699` |
| System.map | 4807243 | `705a748edac09f85dbb5f386383c94d61d0ace8ce3e28d88a9959c2ea71b27b8` |

### 二、刷机：只写 boot_b 并成功开机

- 刷前：fastbootd `current-slot=b`、`is-userspace=yes`、`snapshot-update-status=none`；boot_b 分区 96 MiB；
  原 boot_b 已完整备份（SHA256 `f4b921ac0c58ec9611131fca1252e3297908d734bdae40df1647b1c2f660a980`）。
- 实际只执行 `fastboot flash boot_b boot.img` 与 `fastboot reboot`，未写其他分区、未切槽、未解锁、未清数据。
- 刷后：`sys.boot_completed=1`、槽位仍 `_b`、`uname -r` 为 `6.6.118-android15-8-o-4k`、
  `su -c id` 返回 `uid=0(root)` / `context=u:r:ksu:s0`、`/dev/block/by-name/boot_b` 复算摘要等于新镜像。

### 三、运行：SUSFS 接口与 Root 读取

- SUSFS 接口 `status=true`、`version=v2.3.0`。
- MT 管理器成功读取受保护模块的说明文件，源文件 SHA256 前后不变；Root shell 读取受保护二进制正常。

### 四、运行：隐藏效果（未通过，样本有局限）

- 经同意临时标记一个 `/sdcard/Android/data/...` 目录后，Duck Detector 的两种目录旁路命中数前后均为 **9→9**。
- 但目标目录在标记前就未被普通 Directory Listing 命中，**缺少阳性基线**，因此不能据此断言 readdir 增量无效，
  也不能宣称隐藏已通过验收。
- 单目录临时标记未持久化，随重启清除；持久配置未被改动。

### 五、补写本文件时的复测（只读）

- 系统：**ColorOS V17.0.0，国行**，`PKX110_17.0.0.104(CN01)`，`ro.build.version.oplusrom=V17.0.0`，
  市场名「一加 13T」，locale `zh-CN`；fingerprint `OnePlus/PKX110/OP60F5L1:17/...`。
- 槽位 `_b`，`/dev/block/by-name/boot_b` 摘要仍为 `5df9c1dd…`（即上面的 v4 镜像），`su` 正常，内核串不变。

**以上都不代表**：长期稳定、完整 ABI 兼容、所有 Root 应用可用、任意应用检测通过或服务端风控放行。

## 刷机条件

要把自编译 boot 刷进这台机器，需同时满足：

1. **设备必须是国行 ColorOS 17 的一加 13T（PKX110）**。国际版 / OxygenOS、其他机型不适用。
2. **bootloader 已解锁**。系统内属性可能显示 locked/green，需以 fastbootd 实际只读查询 `unlocked=yes` 为准。
3. **vbmeta 校验已禁用**（`--disable-verity --disable-verification`），否则 AVB 链不匹配会进 recovery：
   kleaf 用 AOSP testkey 签 AVB footer（公钥 `2597c218…`），而 vbmeta 里 `boot` 是 Chain Partition、
   用一加私钥签名（公钥 `60ce3de1…`）。**没有一加私钥，永远签不出能过校验的 footer**，只能禁用校验。
4. **先备份** `boot`（对应槽位）与 `vbmeta`，并确认能用 fastboot 恢复；分区大小必须容纳 96 MiB 镜像。
5. 只刷 `boot`（`boot_a`/`boot_b` 对应当前槽位），**不要**顺带刷 vendor_boot、dtbo、super 或切换槽位。
6. 禁用 dm-verity 后运行时完整性校验失效；`MODULE_SIG_PROTECT=n` 后 vendor 模块靠 vermagic 匹配加载。

参考步骤（本仓库不提供脚本，需自行确认设备与槽位）：

```bash
fastboot --disable-verity --disable-verification flash vbmeta vbmeta_a.img   # 一次性，若尚未禁用
fastboot flash boot_b boot.img                                              # 只写当前槽位的 boot
fastboot reboot
```

刷后自查：`getprop sys.boot_completed`、`getprop ro.build.display.id`（应含 `PKX110_17.0.0.104(CN01)`）、
`getprop ro.boot.slot_suffix`、`uname -r`、`su -c id`，并复算分区摘要与镜像一致。
启动失败时只允许回刷本次备份的 boot。

## 私钥与签名

**本仓库不包含任何私钥。** 已核对：仓库全部文件里没有 `.pem`/`.key`/`.jks`/`.keystore`、没有
`-----BEGIN … PRIVATE KEY-----` 或 OpenSSH 私钥块，工作流没有引用 `secrets.*`、签名口令或 token 变量，
`permissions: contents: read` 且 `persist-credentials: false`。

- `device.config` 里的 `CONFIG_MODULE_SIG_KEY="certs/signing_key.pem"` 是**构建时由内核构建系统现场生成**的
  模块签名密钥，只存在于 runner 的临时工作目录，不进仓库、不上传产物，每次构建都不同。
- AVB footer 使用 **AOSP testkey**（公开测试密钥，非机密）。
- 一加私钥（vbmeta 里 `boot` 链的公钥对应）**不可得**，这也是必须先禁用 vbmeta 校验的原因。
- 管理器身份由 SukiSU 源码内固定的包名/签名参数决定，仓库不存储任何证书文件。

## 风险

- 仅面向国行 ColorOS 17 的 PKX110；国际版 / OxygenOS 与其他机型**不可直接套用**。
- 刷机前请自行备份 `vbmeta`、`boot`，并确认能通过 fastboot 恢复；本仓库没有任何回滚自动化。
- 禁用 vbmeta/dm-verity 后完整性校验失效；关闭 MODULE_SIG_PROTECT 减少导出保护。
- 源码是 16.0.0 分支，系统是 ColorOS 17：vendor 模块、ABI 与安全补丁差异未逐项核对。
- 隐藏能力已被实测**部分未通过**（见上文第四节），不要假定任何 Root/Hook 检测已被完全规避。
- 源码检查通过不等于编译通过；编译通过不等于可开机、隐藏有效、ABI 完整兼容或长期稳定。

## 说明

- 仓库原名 `oneplus-13t-kernel-build`，为标明目标系统已改名为 `oneplus-13t-coloros17-kernel-build`；
  GitHub 会把旧链接 301 到新名，`deps.json` 里指向旧仓库的 release 下载地址仍然可用（为避免动到已校验的装配输入，未改写）。
- 手机侧备份与私有文件不在本仓库与工作流内。
