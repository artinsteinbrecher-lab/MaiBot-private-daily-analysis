# 麦麦安静写书

想让麦麦在群里安静一点，又不想错过大家一天里聊过的事情，可以试试这个小插件。

它会安静地记下指定群里的消息，把一天发生的事情整理成一份日报，再悄悄送到管理员私聊。群里不会被日报打扰，麦麦也可以在你选定的群里保持安静。

面向普通用户的是 **麦麦安静写书 v3.1.0**。这是已经整理和检查过的禁言整合版。

[下载“麦麦安静写书”](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.1.0/khiqwq_daily_analysis-v3.1.0-legacy-silence.zip) ·
[打开下载页](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/tag/v3.1.0)

## 它会帮你做什么

- 记下你允许它读取的群聊；
- 把当天发生的事情整理成日报；
- 只把日报送到管理员私聊；
- 让麦麦在指定群里不再发言；
- 没有加入安静名单的群和管理员私聊，仍然照常使用。

## 安装很简单

1. 下载上面的 ZIP，然后解压。
2. 把解压出来的插件文件夹放进 MaiBot 的 `plugins` 文件夹。
3. 重启 MaiBot，或者在插件页面重新加载并启用它。

就这样，插件已经装好了。

如果你用的是常见 Docker 安装，插件文件夹通常在 `./data/MaiMBot/plugins/`。放好后，打开插件文件夹应该能直接看到 `plugin.py`，不要在外面多套一层同名文件夹。

如果已经装了这个仓库的其他版本，请先停用并移走旧版本。它们使用同一个插件身份，**不能同时安装**。

请使用上面的专用下载按钮。GitHub 自动生成的 Source code 压缩包，以及仓库当前的 `main` 源码，都不是这份普通用户安装包。

## 第一次使用

这些都直接在 MaiBot 的插件页面里完成：

1. 在“插件”里打开启用开关。
2. 在“自动总结”里填要记录的群和接收日报的 QQ。
3. 在“管理员账号”里填可以管理它的 QQ。
4. 在“绝对静默”里打开开关，先放进一个测试群。
5. 保存设置。

想确认日报是否正常，可以由管理员私聊麦麦发送 `/summary 群号 今天`。确认麦麦在测试群里安静、管理员私聊仍然正常，再慢慢加入其他群。

平时不需要手动改配置文件。更喜欢直接编辑文件的话，可以看[配置文件的简短说明](docs/LEGACY_SILENCE.md#如果你喜欢手动改配置文件)。

## 打开禁言前，轻轻提醒一下

加入安静名单后，麦麦不会只是不主动聊天，而是真的不会往那个群里发送内容。

普通回复、被 @ 后的回复、群内命令回复，以及其他插件想发到这个群里的消息，都会一起被挡住。建议先用不重要的测试群试一试，确认效果正是你想要的，再放到常用群里。

管理员私聊不会受影响。加入安静名单的群，也要同时放在日报来源群里，这样麦麦才能一边安静记录，一边好好写日报。

## 想多看一点

- [更完整的安装与注意事项](docs/LEGACY_SILENCE.md)
- [遇到问题时可以这样检查](SUPPORT.md)
- [全部说明入口](docs/README.md)

## 维护者自用的新版本

仓库还保留了 v3.6.0，这是维护者自己使用的新版，日报整理得更细，也可以让多个模型分工，但它不再内置群聊禁言。普通用户只想安装“麦麦安静写书”时，不用管这一段。

[查看 v3.6.0](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/tag/v3.6.0) ·
[普通包](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.6.0/khiqwq_daily_analysis-v3.6.0-standard.zip) ·
[多模型包](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.6.0/khiqwq_daily_analysis-v3.6.0-multimodel.zip)

维护仓库：[artinsteinbrecher-lab/MaiBot-private-daily-analysis](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis) ·
[自动检查状态](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/actions/workflows/validate.yml)
