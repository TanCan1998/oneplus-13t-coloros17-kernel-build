# OnePlus 13T 原生 SUSFS 适配

## 范围

从已实机验证的 CI 39450775101 出发，保留 SukiSU 原有 tracepoint/syscall hook、preinit、挂载隐藏、SELinux 隐藏、管理器、审计与扩展。新增 common 内核的原生 SUSFS 文件系统逻辑和 reboot 控制接口，**不是 SUSFS LKM，也不包含自动刷机步骤**。

源码、适配补丁 SHA256 和目标文件清单以 `susfs-native.lock.json` 为准：

- common：`4d611c32bedc11cf3ae46aa5e815dfe35707b93f`
- SukiSU：`7ea0dd2b09bfc60e6b51a6a93fc9f49b349a9b2a`
- SUSFS：`3f6aae536724af0df3dfa15f932034d14a5dbc5c`

SukiSU 版本固定为已验证基线 41028，完整版本标识为 `native-susfs-7ea0dd2b`。Kbuild 不再查询移动 main/最新 Release，正式管理器包名和原有签名参数在源码内固定。

## 为什么不直接应用上游 KSU 补丁

诊断 CI 39529093355 显示 common 2 个 hunk 和 SukiSU 13 个 hunk 不匹配。上游补丁还会整体替换 hook，不能只消除拒绝文件就宣称兼容。

本次适配：

1. `fs/proc/base.c` 补齐 SUSFS 头文件。
2. `task_mmu.c` 保留当前 LTS 的新版 Case 4，排除仅用于旧版源码的替代 hunk。
3. 映射重定向直接沿用当前 OnePlus 的 `__fold_filemap_fixup_entry` 调用，消除上游备选代码中的裸 `else` 及潜在 SRCU 锁早退。
4. 保留现有 root/input/exec/read/stat/UID hook，避免双重处理。原生 SUS_KSTAT 与 uname 伪装仍保留。
5. 不移植上游额外的 SELinux 隐藏框架，避免与 SukiSU 已有动态隐藏叠加；保留 SUSFS 的 AVC 日志伪装。
6. 新增最小适配层：SID 缓存、应用隐藏策略标志、初始化/解密监测和 root-only SUSFS reboot 命令。
7. 复用现有成功 setresuid 后的回调及卸载流程；保留二参数 ABI、管理器 fd/追踪和 seccomp 处理。额外 SUSFS 工作在原卸载路径返回后调度。
8. 普通提权、init 与 adb root 路径清除继承的隐藏标志；未解析的零 SID 不允许匹配。
9. `TIF_PROC_UMOUNTED` 沿用上游隐藏策略标志含义，不代表所有挂载已实际卸载成功。

## 验证层级

- 本地仅做补丁应用和源码检查，不编译内核。
- 云端 `build-susfs-kernel.yml` 先检查固定源码、零 fuzz/零偏移应用和基线保留。
- 源码门禁成功后，装配官方 35 依赖，再次核对真实 common/KSU 文件。
- **两棵完整内核树都必须打原生补丁**（common 与 msm-kernel）。run 39532454585 已证明 boot.img 来自 `//msm-kernel:sun16k_perf_dist`：该树 vmlinux 链接时 17 个 `susfs_*` 符号未定义，构建在 68 分钟后失败。只改 common 不足以让运行时内核具备隐藏逻辑。
- run 39541952386 探测确认：msm-kernel 的 18 个补丁目标文件与 common 逐字节相同，同一份补丁在其上零拒绝、零 fuzz、零偏移，且不存在新文件冲突。
- common 与 sun_perf 的实际生成配置都必须具备内置 KSU、原生 SUSFS 全部选项，并关闭 MODULE_SIG_PROTECT。
- 默认 y 的选项不追加至 defconfig，保持严格 savedefconfig 一致性。
- 仅门禁通过才编译；失败传播使用 `set -euo pipefail`，完整日志始终上传。
- 编译后检查 boot v4/分区大小、实际嵌入 Image、精确链接符号、运行时字符串和 SHA256。

**源码检查通过不等于编译通过；编译通过不等于可开机、隐藏有效、ABI 完整兼容或长期稳定。**

MODULE_SIG_PROTECT 关闭会减少导出保护。这是已验证基线的取舍；此前关于所有原厂模块签名归属的推断不作为依据。原拒绝条件位于导出保护检查，不能误写为普通导入失败。

现有手机和私有备份不在该仓库或工作流内。后续实机验证仍需单独授权并确认 USB 回滚路径。
