# 版本与下载

这里一共保留三个版本。它们各有自己的用途，不需要全部安装。

## 麦麦安静写书

**版本：v3.1.0**

它把群聊日报、安静模式和趣味个人小结放在一起。适合希望麦麦一边安静听群聊、一边整理日报，同时还想保留 `/mysummary` 娱乐内容的使用方式。

[下载麦麦安静写书](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.1.0/khiqwq_daily_analysis-v3.1.0-legacy-silence.zip) ·
[查看版本介绍与安装](LEGACY_SILENCE.md)

这个版本的群聊日报只保留重要事件；娱乐内容通过单独的 `/mysummary` 个人手账展示。

## 麦麦认真写书

**版本：v3.6.0 标准版**

它更专注于总结质量。日报会同时保留主要事件和普通事件，核对时间、参与者、链接和引用，并在内容没有处理完整时明确说明。

它直接使用 MaiBot 当前的默认回复模型，不需要修改 MaiBot。

[下载麦麦认真写书](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.6.0/khiqwq_daily_analysis-v3.6.0-standard.zip) ·
[查看版本介绍与安装](../packaging/standard/README.md)

这个版本不再内置群聊静默，也不提供 MBTI、金句排行或娱乐评级；`/mysummary` 是有消息依据的事实型人物小结。

## 麦麦一起写书

**版本：v3.6.0 多模型版**

它和“麦麦认真写书”使用完全相同的总结核心，但多带了一套 MaiBot 修改工具。完成第二阶段安装后，可以让不同模型分别负责读取事实、整理日报、复查内容和生成人物小结。

[下载麦麦一起写书](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/download/v3.6.0/khiqwq_daily_analysis-v3.6.0-multimodel.zip) ·
[查看版本介绍与安装](../packaging/multimodel/README.md)

只复制插件文件夹时，它也能正常工作，但会和标准版一样跟随 MaiBot 默认模型。真正启用多模型分工时，还需要修改并重新构建 MaiBot。

## 怎么选

- 想把总结、安静模式和趣味个人小结放在一起：选择 **麦麦安静写书**；
- 想直接获得更完整、更可靠的日报：选择 **麦麦认真写书**；
- 想要更好的日报，并让多个模型分工：选择 **麦麦一起写书**。

如果只想让麦麦安静，不需要任何日报，可以使用从本项目分离出来的 **麦麦群安静插件**。它不属于上面三个总结版本。

## 下载前看一眼

- 三个安装包使用同一个总结插件身份，不能同时安装；
- v3.6.0 的标准版和多模型版总结核心完全相同；
- GitHub 自动生成的 Source code 压缩包不是整理好的插件安装包；
- 切换版本前先备份并移走现有插件目录；
- 安装时解压 ZIP，把其中的插件文件夹放进 MaiBot 的 `plugins` 文件夹，再通过 WebUI 启用。

[查看全部 Release](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases)

[查看 v3.6.0 Release](https://github.com/artinsteinbrecher-lab/MaiBot-private-daily-analysis/releases/tag/v3.6.0)
