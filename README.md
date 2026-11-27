# OnePlus 13T ColorOS 17（国行）内核云端构建（SukiSU Nova / 原生 SUSFS）

在免费 GitHub runner 上，用一加官方源码与官方构建入口编译 **一加 13T 国行 ColorOS 17**（PKX110 / sun / sm8750）内核：
SukiSU 采用 **Nova `builtin-new`** 并编进内核，原生 **SUSFS v2.4.0** 一并编入，同时启用
**KPM、版本信息自定义、AVC 日志伪装** 三项扩展。对应管理器包名为 `com.sukisu.nova`。

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
| SukiSU | `SukiSU-Nova/SukiSU-Nova` @ `5a3ec4145059`（`builtin-new`） |
| SUSFS 上游 | `cctv18/susfs4oki` @ `c7f303f504b3`（`oki-android15-6.6`，v2.4.0） |
| 内置扩展 | KPM、版本信息自定义（`version_spoof`）、AVC 日志伪装（`avc_spoof`） |
| 管理器 | SukiSU Nova，包名 `com.sukisu.nova`（本仓库不打包 APK） |

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

### 内核里启用了什么

- **KPM**：`CONFIG_KPM=y`。Nova 的 Kconfig 改为 `depends on 64BIT` 且默认 y——common 树的配置阶段还看不到
  `CONFIG_KSU`，沿用 `depends on KSU` 会让整项被丢弃；KPM 自身 `select KALLSYMS/KALLSYMS_ALL`。
  运行期经 `ksu_handle_sys_reboot` 钩子转发，入口符号为 `do_kpm`。
  注意：**编进内核只代表桥接就绪**，要让 `kpm supported=1` 还需管理器把 KernelPatch 载荷 `kpimg`
  注入当前槽位 boot 并重新刷入（见下文「KPM 使用」）。
- **版本信息自定义**：Nova 自带 `extension/version_spoof.c`，导出 `ksu_extension_version_spoof_io/wipe`，无需额外改动。
- **AVC 日志伪装**：`feature/avc_spoof.c`（builtin-new 已包含），注册 `KSU_FEATURE_AVC_SPOOF=10003`，
  改写入口 `ksu_avc_spoof_rewrite_tsid`。
- **SUSFS**：`CONFIG_KSU_SUSFS=y`，`CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER` 默认 y（v2.4.0 已提供对应实现）。
- 内置 Zygisk（NyaZygisk）属于管理器/用户态组件，不在本内核构建范围内。

## 这套流程的依据

全部来自本地与云端已验证的实测结果：

- 官方 manifest 的 **35 个依赖**，共 3.01 GiB，官方入口 `oplus_build_kernel.sh sun perf`；
  一加源码零修改，SukiSU 与 SUSFS 以补丁方式接入。
- 云端实测 run `45949122615`（提交 `c2467e53`）：全流程 **47m35s** 成功，产出 `boot.img` 与 `Image`。
- 本地 8 核全量基线耗时 **3h12m12s**，退出码 0，产出 `boot.img` 100,663,296 字节（与出厂 boot_a 同尺寸）。
- 未改基线内核已在真机验证：`boot_completed=1`、602 个模块加载、`disagrees about version` 为 0。

## 关键取舍

**跳过 vendor 模块编译**（`RECOMPILE_EXT_MODULE=0`）。

本地 3h12m 里约 2 小时在编 359 个 vendor ko，而 `boot.img` 只含内核 Image。只编内核才能让 4 核 runner
在 6 小时上限内完成。

**`CONFIG_MODULE_SIG_PROTECT=n`**：vendor 模块用 OPPO 私钥签名，自编译内核没有对应 trusted key，
`mod->sig_ok` 为 false 会让 `gki_is_module_protected_export()` 拒绝导出，导致
rfkill → cfg80211 → mac80211 → qca_cld3 整链加载失败（WiFi/蓝牙不可用）。关掉后整链放行，
代价是**减少导出保护**，属于已验证基线的取舍。

**跳过 16K 变体**：`dist16k` 在整条流程里没有消费者——打包、`prepare_vendor.sh`、产物校验与上传清单都只读 4K `dist/`。
`patches/nova/skip-16k.patch` 通过环境变量 `KSU_SKIP_16K` 让 `build_with_bazel.py` 不再枚举 16K 目标，
实测省 25–40 分钟；只影响目标枚举，不碰内核配置、KPM、SUSFS 或 KMI。

**编译前熔断**：`ci-check-susfs-config.py` 在真正编译前核对两份 `.config` 的 19 项必需开关，
并额外检查 `KALLSYMS`、`KALLSYMS_ALL`、`CFI_CLANG`、`KPM`、`KSU_SUSFS` 五项扩展依赖。
KPM 运行期要靠 CFI 保留的 `__arm64_sys_truncate` 找入口，CFI 被关就失效——这类错误 5 秒内报出，不白跑一小时。

**两棵树都要打补丁**：`sun_perf_dist` 的 boot.img 来自 msm-kernel 树；只改 common 会以
`susfs_*` 符号未定义在链接阶段失败（run 39532454585 即此原因）。v2.4.0 的补丁覆盖 27 个文件，
另外 `fs/susfs.c`、`include/linux/susfs{,_def}.h` 是独立源文件，需按锁文件哈希单独复制进两棵树。

**Nova 的 reboot 钩子**：Nova 的 `ksu_handle_sys_reboot()` 对非 KSU magic 返回 `-EINVAL`，
不能照搬 Ultra 的「负值即已处理」。`patches/nova/ksu-avc-kpm.patch` 改为仅在 `magic1 == 0xDEADBEEF`
时接管，且只在返回非负值时短路，避免普通 `reboot()` 被拦成 `-EINVAL`。

## 工作流

| 工作流 | 用途 |
|---|---|
| `build-susfs-kernel` | 单个 job：固定源码门禁 → 装配 35 依赖 → 安装原生 SUSFS → 双配置门禁 → 编译 → 产物与扩展复验 |
| `build-ksu-kernel` | 早期 SukiSU 内置版本，`enable_susfs` 可选 |
| `susfs-preflight` / `susfs-msm-probe` | 只读探测：补丁可应用性、msm-kernel 目标文件与 common 是否逐字节相同 |
| `watch-official-source` | 每日只读巡检：官方 13T 分支 HEAD 是否偏离锁定提交、是否出现 17.x 分支；有变化开 issue（`source-watch` 标签） |

手动触发 `build-native-susfs-kernel`，`skip_build=true` 只做装配与配置验证（约 15–25 分钟）。
产物 `kernel-susfs-out`（约 31 MiB）含 `boot.img`、`Image`、`System.map`、`SHA256SUMS.txt`、两份 `.config`、
`Module.symvers` / `vmlinux.symvers` / `modules.builtin`，以及装配、安装、配置、编译与复验日志。

## KPM 使用

1. 刷入本仓库产物后，Root 与 SUSFS 正常，但 `kpm supported` 仍为 0：内核只有桥接，还没有 KernelPatch 载荷。
2. 打开 SukiSU Nova 管理器的 KPM 修补入口（`PatchKpmBoot`）。未选择外部文件时，它读取**当前槽位**的 boot，
   因此可以直接基于本仓库的自定义内核打补丁，不需要先恢复官方原镜像。
3. 把生成的新 boot 刷回同一槽位并重启，`kpm supported` 变为 1，之后即可安装 `.kpm`。
4. 补丁会改变 boot 摘要；回滚仍使用刷机前的备份。

## 已验证的结果

### 一、静态：构建与产物复验（run `45949122615`，提交 `c2467e53`）

- 全流程 47m35s（06:00:29–06:48:04 UTC），单 job 成功；产物 `kernel-susfs-out` 32,541,088 字节。
- `ci-verify-extensions.py` 在 System.map 中命中全部扩展符号：`do_kpm`、`ksu_extension_kpm_io/get`、
  `ksu_extension_version_spoof_io/wipe`、`ksu_avc_spoof_init/late_init`、`ksu_avc_spoof_rewrite_tsid`。
- 配置门禁 19 项 × common/msm-kernel 两棵树全部为 y，`MODULE_SIG_PROTECT` 未开启；
  `susfs_add_sus_path` 等 SUSFS 链接符号存在。
- boot 为 Android v4 头、96 MiB、空 ramdisk；其中嵌入的内核与独立 `Image` 逐字节一致。

### 二、刷机与启动

- 仅执行 `fastboot flash boot_b` 与 `fastboot reboot`，未写其他分区、未切槽、未解锁、未清数据。
- 第一版 Nova 镜像 `18f321c4ac9ca8a3dc3664bd846c8faeef9dfce1e6bc08058798fab544f3fcea`（100,663,296 B）正常开机。
- 第二版 KPM 镜像 `17ee02f3281a027c058b1151f27e1e7294fd33040019586c7d7b6fd5bc9ff8b5`（同尺寸）同样正常开机，
  `sys.boot_completed=1`，槽位仍为 `_b`，`/dev/block/by-name/boot_b` 摘要与镜像一致。
- 系统 `PKX110_17.0.0.104(CN01)`，内核串 `6.6.118-android15-8-o-4k`。
- 回滚镜像为升级前的 SukiSU Ultra 构建 `5df9c1dde13ab90f26ec39fc0f318b297cd674a06e25eb746a9b83822a6288ff`。

### 三、运行（真机实测）

- KPM：`supported=1`、扩展值 `1`；`ksud kpm version` 为 `3584 (19:40:23 Oct 26 2026)`；
  已装 `.kpm` 数量为 0（尚未安装）。
- SUSFS：`v2.4.0`，`builtin`，`GKI`；`version_spoof` 可用，`avc_spoof (10003)` 已启用并出现 tsid 改写记录。
- Root/模块：`ksud 4.2.1-24-gdecfaa5b`，11 个自定义模块启用；内置 Zygisk 为 NyaZygisk `v2.5`，
  状态 `fully_active`，zygote 已注入，`playintegrityfix`、`Vector`、指纹支付模块均已注入。
- 兼容性：10 个关键 vendor 模块（`msm_drm`、`qca_cld3_peach_v2`、`camera`、`camera_extension`、`btpower`、
  `ipam`、`rmnet_core`、`cnss2`、`cfg80211`、`wlan_firmware_service`）全部加载；WiFi 连接 5G、相机可用；
  `223.5.5.5` 与 `www.baidu.com` 均 0% 丢包。

### 四、隐藏效果（历史测试，未在 Nova 上复测）

- 早期 Ultra 基线上用 Duck Detector 做过一次只读对照：经同意临时标记一个 `/sdcard/Android/data/...` 目录后，
  两种目录旁路命中数前后均为 **9→9**。
- 但目标目录在标记前就未被普通 Directory Listing 命中，**缺少阳性基线**，因此不能据此断言 readdir 增量无效，
  也不能宣称隐藏已通过验收。
- 该临时标记未持久化，随重启清除；持久配置未被改动。

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
`-----BEGIN … PRIVATE KEY-----` 或 OpenSSH 私钥块；仓库没有配置任何 repository secret 或 variable，
工作流不使用签名口令，唯一凭据是 GitHub 为每次运行自动注入、运行结束即失效的临时 `GITHUB_TOKEN`
（仅供 `watch-official-source` 读分支、开 issue），仓库里既没有它的值，也不会有，
`permissions: contents: read` 且 `persist-credentials: false`。

- `device.config` 里的 `CONFIG_MODULE_SIG_KEY="certs/signing_key.pem"` 是**构建时由内核构建系统现场生成**的
  模块签名密钥，只存在于 runner 的临时工作目录，不进仓库、不上传产物，每次构建都不同。
- AVB footer 使用 **AOSP testkey**（公开测试密钥，非机密）。
- 一加私钥（vbmeta 里 `boot` 链的公钥对应）**不可得**，这也是必须先禁用 vbmeta 校验的原因。
- 管理器身份由 SukiSU Nova 源码内固定的包名 `com.sukisu.nova` 与签名参数决定，仓库不存储任何证书文件；
  KPM 载荷 `kpimg` 由管理器自带，不需要仓库提供私钥或签名。

## 风险

- 仅面向国行 ColorOS 17 的 PKX110；国际版 / OxygenOS 与其他机型**不可直接套用**。
- 刷机前请自行备份 `vbmeta`、`boot`，并确认能通过 fastboot 恢复；本仓库没有任何回滚自动化。
- 禁用 vbmeta/dm-verity 后完整性校验失效；关闭 MODULE_SIG_PROTECT 减少导出保护。
- 源码是 16.0.0 分支，系统是 ColorOS 17：vendor 模块、ABI 与安全补丁差异未逐项核对。
- KPM 编入内核不等于开箱可用：还需管理器注入 `kpimg` 并重新刷 boot；`.kpm` 模块本身的质量与稳定性不受本仓库控制。
- 隐藏能力只在早期基线上做过有局限的实测（见上文第四节），不要假定任何 Root/Hook 检测已被完全规避。
- 源码检查通过不等于编译通过；编译通过不等于可开机、隐藏有效、ABI 完整兼容或长期稳定。

## 说明

- 仓库原名 `oneplus-13t-kernel-build`，为标明目标系统已改名为 `oneplus-13t-coloros17-kernel-build`；
  GitHub 会把旧链接 301 到新名，`deps.json` 里指向旧仓库的 release 下载地址仍然可用（为避免动到已校验的装配输入，未改写）。
- `watch-official-source` 只在 GitHub API 上查询分支名与 HEAD，不下载源码、不编译、不接触设备；
  公开仓库的定时任务若 60 天无提交会被 GitHub 停用，届时手动 `workflow_dispatch` 一次即可恢复。
- 手机侧备份与私有文件不在本仓库与工作流内。
