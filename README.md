# polaris-kernel

Xiaomi Mi Mix 2S (polaris / sdm845) 官方内核编译，基于小米官方 `dipper-q-oss` 源码
（Android 10 / MIUI 12.5，内核 4.9.186，与官方 MIUI 内核同源）。

## 编译方式

GitHub Actions 云端编译：

- 工具链：AOSP 预编译 `aarch64-linux-android-4.9`（android10-release 分支）
- 源码：MiCode/Xiaomi_Kernel_OpenSource 的 `dipper-q-oss` 分支（含 polaris 板级文件）
- 配置：`polaris_user_defconfig`

## 产物

编译成功后，在 Actions 页面的 "Artifacts" 下载：

- `Image.gz-dtb` — 内核 + 源码树自带 DTB（**仅供验证，不要直接刷**）
- `Image.gz` — 纯内核（用于配合手机提取的原厂 DTB 打包）
- `polaris_user.config` — 实际使用的内核配置

## 打包成可刷的 boot.img

`Image.gz-dtb` 里只有源码树自带的 DTB（约 26 个），
而手机需要原厂 65 个 DTB 才能通过引导器匹配。因此最终 boot.img 应：

1. 取本地产物 `Image.gz`（纯内核）
2. 拼接手机提取的「原厂 65 个 DTB 块」
3. 复用手机原厂 boot.img 的 header + ramdisk（或 Magisk ramdisk）+ 尾部

此步骤在本地完成（需要手机提取的文件），不在云端做。
