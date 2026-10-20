# OnePlus 13T 内核云端构建（SukiSU 内置）

在免费 GitHub runner 上完整构建 OnePlus 13T（PKX110 / sun）内核，并把 SukiSU 编进内核。

## 这套流程的依据

全部来自本地已验证的实测结果：

- 官方 manifest 的 **35 个依赖**（3 个 OnePlusOSS + 32 个 CodeLinaro），共 3.01 GiB
- 官方入口 `oplus_build_kernel.sh sun perf`，源码零修改
- 本地 8 核全量构建耗时 **3h12m**，产出 `boot.img` 100,663,296 字节
- 该内核已在真机验证：`boot_completed=1`、602 模块加载、`disagrees about version` 为 0

## 关键取舍

**跳过 vendor 模块编译**（`RECOMPILE_EXT_MODULE=0`）。

本地 3h12m 里有约 2 小时在编 359 个 vendor ko，而那部分对 `boot.img` 没有贡献——`boot.img` 只含内核 Image。
只编内核能让 4 核 runner 在 6 小时上限内完成。

**配置门禁**：编译前先单独 `bazel build //msm-kernel:sun_perf_config`，检查 `.config` 里有没有
`CONFIG_KSU=y`。没生效就直接失败，避免白跑 3 小时。

## 依赖下载源

runner 在海外，三个源都是直连，不使用任何加速前缀：

| 源 | 数量 |
|---|---|
| `github.com/OnePlusOSS` | 3 |
| `github.com/CodeLinaro-mirror` | 25 |
| `git.codelinaro.org`（原站） | 7 |

那 7 个在 GitHub 镜像上确实不存在，只能走原站：
`prebuilts/build-tools`、`clang-tools`、`jdk/jdk11`、`rust`、`ndk/r26`、
`tradefederation/prebuilts`、`prebuilts-master/clang`。

## 用法

Actions 页面手动触发 `build-ksu-kernel`：

- `ksu_ref`：SukiSU 分支或 tag，默认 `main`
- `skip_build=true`：只做下载装配 + 配置验证，约 15 分钟，用来确认 `CONFIG_KSU=y` 生效

产物 `kernel-out` 含 `boot.img`、`Image`、`System.map`、`SHA256SUMS.txt`、`config-check.txt`、`build.log`。

## 刷机前必读

本仓库只产出 `boot.img`，**不含任何刷机脚本**。

这台设备刷自编译 boot 的前提是先禁用 vbmeta 校验，否则 AVB 签名链不匹配会导致 RUS 进 recovery：

```bash
fastboot --disable-verity --disable-verification flash vbmeta vbmeta_a.img
fastboot flash boot boot.img
```

原因：kleaf 用 AOSP testkey 签 AVB footer（公钥 `2597c218…`），而 vbmeta 里 `boot` 是 Chain Partition、
公钥为 `60ce3de1…`（一加私钥），没有私钥就永远签不出能过校验的 footer。

## 风险

- 刷机前请自行备份 `vbmeta_a`、`boot_a`，并确认能通过 fastboot 恢复
- 禁用 dm-verity 后运行时完整性校验失效
- 本流程产出的内核尚未在真机验证过 SukiSU 内置效果
