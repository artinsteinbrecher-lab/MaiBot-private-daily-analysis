# 麦麦写书插件产品包

这个文件夹放着三个互相独立的发行版本。三个版本使用同一个插件身份，不能同时放进
MaiBot 的 `plugins` 目录。

## 先看版本

### 01 麦麦安静写书 v3.1.1

旧核心的禁言整合维护包。保留原来的群聊静默和趣味个人总结，并加入日报顺序修复；
不跟随 v3.6.2 新总结核心。

### 02 麦麦认真写书 v3.6.2 standard

推荐的普通版。解压 ZIP，把插件文件夹放进 MaiBot 的 `plugins` 目录，然后在 WebUI 中
启用和配置。不需要修改 MaiBot；四个总结流程统一跟随 `replyer`。

### 03 麦麦一起写书 v3.6.2 multimodel

增强版。只安装插件时和 standard 一样；只有对实际 MaiBot 源码运行 `extras/` 检查并应用
补丁后，才能在高级模型任务里分别配置：

- `plugin_daily_extract`
- `plugin_daily_compose`
- `plugin_daily_verify`
- `plugin_user_profile`

## 安装前确认

- v3.6.2 兼容基线：MaiBot `1.1.0+`、`maibot_sdk 2.x`；
- 三个版本不要同时安装；换版本前先停用并移走旧插件目录；
- ZIP 内要看到 `plugin.py` 位于插件目录第一层；
- 先在测试群手动发起一次 `/summary 群号 今天`。

每个版本目录内同时提供 ZIP、解压后的插件目录和对应说明。`SHA256SUMS.txt` 用于核对
下载文件是否完整。
