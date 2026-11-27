# 面试点评器（InterviewLiveNote）

[![License](https://img.shields.io/badge/License-MIT-green.svg)](#许可证与第三方组件)
[![Release](https://img.shields.io/github/v/release/lyckeven/InterviewLiveNote)](https://github.com/lyckeven/InterviewLiveNote/releases)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078d4)](#下载安装)

给别人答题时**实时听记 + 实时标记 + AI 点评**的 Windows 本地工具。声音来源是**电脑系统内录**（WASAPI Loopback），不需要麦克风、不需要虚拟声卡，电脑静音也能听；转写在本地离线完成，录音不出本机。

## 功能特性

- **系统内录转写**：直接抓电脑正在播放的声音，说完一句约 0.4 秒上屏；也可切换为麦克风抓现场。
- **段落级计时**：按停顿、篇幅与话题词自动切段，每段记录起止时间、用时、字数、语速，可只评这一段。
- **实时标记**：亮点 / 硬伤 / 追问 / 口头禅 / 备注，键盘 `1`–`4` 一键打在选中句上，右侧汇总可点回原句。
- **两档点评**：配置 API Key 走 AI 点评（对照参考答案与五环节问题清单）；不配 Key 走本地量化诊断（语速、口头禅、结构信号词、停顿、关键词覆盖）。
- **精简稿与要点**：去语气词、口头禅后的精简逐字稿（可按答题环节标注），并可一键提取学生答题要点。
- **题库外部导入**：题库不随软件内置，需自行导入或新建，首次启动题库为空；支持文本 / Word / PDF 导入，自动切题并识别题型与答题结构，入库前可逐题核对修改。
- **多学生与历史**：按学生分会话、锁定录音归属；历史可按题型 / 学生 / 日期 / 关键词检索，可导出 Word 复盘稿。
- **手机接入**：局域网扫码用手机看逐字稿与标记（HTTPS 自签证书，手机浏览器可正常使用麦克风）。

## 界面

主界面三栏：左侧「本题信息 + 题库」，中间「逐字稿 / 精简版」，右侧「标记汇总 + 点评」；顶栏放声音来源、开始 / 停止、计时、搜题库、快速记录、精简要点、历史记录与设置入口。

## 下载安装

安装包与绿色版都发布在 [Releases](https://github.com/lyckeven/InterviewLiveNote/releases) 页面，按需下载：

| 方式 | 适合 | 特点 |
| --- | --- | --- |
| 安装包（`面试点评器_安装程序_*.exe`） | 长期在固定电脑上使用 | 有安装向导，自动建桌面快捷方式与开始菜单，装到当前用户目录，不需要管理员权限 |
| 绿色版（`面试点评器_绿色版_*.zip`） | 需要拷到多台电脑、或不能装软件 | 解压即用，不写注册表，删掉文件夹即等于卸载 |

- **运行环境**：Windows 10 / 11（x64）。
- **数据目录**：安装版与绿色版共用 `%LOCALAPPDATA%\InterviewReviewer\data`（题库、存档、设置都在这里，卸载前请自行备份）。
- **首次启动**：双击图标后浏览器自动打开 `http://127.0.0.1:7788`，界面即可用；此时题库为空，需要自己导入或新建题目。

源码方式启动：

```
python -m pip install -r requirements.txt
python server.py
```

## 快速开始

1. **填题目**：左栏手动输入题干、题型、参考要点，或点「从题库选」/「导入」。
2. **选声音来源**：顶栏默认「系统默认输出（自动）」，即抓电脑正在播放的声音；插了耳机也会跟随默认输出设备的 Loopback。
3. **点「开始听记」**：逐字稿按段落实时滚出，每段显示段号、段落标签、起止时间、用时、字数、语速。
4. **边听边标记**：选中句子后按 `1`–`4` 打标记，或点句子右侧按钮；「备注」可写文字说明。
5. **点评与存档**：点「生成点评」得到三段式点评（时间 → 优点 → 问题，问题带段号与秒级位置）；点「停止」自动存档到数据目录的 `records\`，生成 `.md`（可直接给学生）与 `.json`（可回看）。

## 题库导入

题库不随软件内置，需自行导入或新建，首次启动题库为空。左栏点「导入」，粘贴纯文本，按下面的格式（多题用 `===` 或直接以 `1. ` 另起一行分隔）：

```
1. 你单位要组织一次基层调研，领导让你负责，你怎么开展？
类型：组织管理
参考要点：明确调研目的与对象；科学设计问卷与提纲；组建队伍分工；深入一线访谈；形成报告并提出对策

1. 有人说基层干部要"能干"，有人说要"能说"，你怎么看？
类型：综合分析
参考要点：辩证统一；能干是根本；能说是沟通能力；两者结合；结合岗位谈落实
```

也支持直接粘贴 JSON 数组：`[{"question":"...","qtype":"综合分析","ref":"..."}]`

- **导入 Word / PDF**：点「导入 Word/PDF」选择文件，系统自动识别「题目正文 / 类型：xxx / 参考要点：xxx」结构并批量切题，先在预览里核对、补齐缺失项再入库（`.doc` 老格式请先另存为 `.docx`）。
- **单题新增**：题库内点「＋新建」，粘题干后自动识别题型与答题结构，确认后写入。
- **删除**：单题「×」只删这一题（会二次确认）；题库名右侧「×」删除整个题库文件（不可恢复）。
- **存放位置**：导入后存为数据目录下 `bank\题库名.json`，可长期复用。

## 接入 AI

顶部「设置」：

| 字段 | 说明 |
| --- | --- |
| 接口地址 | OpenAI 兼容地址，默认 `https://api.deepseek.com` |
| 模型名 | 默认 `deepseek-flash`；也可填 `qwen-plus`、`deepseek-reasoner` 等 |
| API Key | 填自己的 Key，保存后不显示明文 |
| 段落即时点评 | 打开后每段答完自动调 AI 评本段（费 token，默认关闭） |
| 切段停顿 / 单段上限 | 停顿超过多少秒切段（默认 1.5 秒）、单段最长多少秒 / 多少字（默认 55 秒 / 240 字） |

填完点「测试连通」，通过后即可生成 AI 点评；不配 Key 时全部功能仍可用，只是点评与题库解析回退到本地规则。

## 快捷键

| 按键 | 作用 |
| --- | --- |
| 空格 | 开始 / 停止录音（输入框内不触发） |
| 1 / 2 / 3 / 4 | 给选中句打 亮点 / 硬伤 / 追问 / 口头禅 |
| Enter | 备注输入时保存 |
| Esc | 取消备注输入 |

## 目录结构

```
InterviewLiveNote\
├─ server.py              Flask 服务（默认端口 7788，仅本机与局域网）
├─ launcher.py            桌面启动壳（pywebview 窗口）
├─ asr_engine.py          系统内录采集 + VAD 断句 + SenseVoice 识别 + 标点恢复
├─ ai_client.py           OpenAI 兼容客户端（点评 / 逐句速评）
├─ local_analyzer.py      本地量化诊断（不依赖 AI）
├─ paragraphizer.py       按段落切分与计时（停顿 / 篇幅 / 话题词三重规则）
├─ simplifier.py          精简稿（去语气词与口头禅）
├─ points_extract.py      精简要点提取
├─ question_analyzer.py   题干解析与题型判定
├─ corrector.py           识别结果纠错
├─ cert_util.py / firewall_util.py / log_util.py   手机接入证书、防火墙、日志
├─ static\                前端（index.html / style.css / app.js / mobile.*）
├─ default_data\          随包默认数据：question_types.json（题型结构）、config.json（默认配置）
├─ LICENSES\              第三方许可证全文
├─ THIRD_PARTY_NOTICES.md 第三方开源组件声明
└─ _packaging\            打包脚本（PyInstaller spec / Inno Setup iss / 图标）
```

运行期数据（不在仓库内）：`data\`（`config.json`、`bank\` 题库、`records\` 存档）、`logs\`。

技术栈：Python 3.13（便携版）+ pyaudiowpatch（WASAPI Loopback）+ sherpa-onnx（SenseVoice int8 离线中文识别 + Silero VAD + CT-Transformer 标点）+ Flask + 原生前端。

## 常见问题

**1. 逐字稿为空 / 不识别**
确认电脑确实在播放声音（系统音量不静音）；顶栏换一个 Loopback 设备试；查看 `logs\` 下当天日志有无报错。

**2. 识别串进无关声音**
内录抓的是整个系统输出，录制时不要同时播放音乐、视频或语音提示。

**3. 个别字识别错（同音字）**
当前用 int8 量化模型，取舍在速度；需要更高精度可换非量化 `model.onnx`（约 900MB）放入模型目录。

**4. 题库是空的 / 找不到题目**
题库不随软件内置，需自行导入或新建，首次启动题库为空；导入入口在左栏「导入」与「导入 Word/PDF」。

**5. 端口被占用**
7788 被占用时改 `server.py` 末尾的端口号，同步改启动脚本里的地址。

**6. 想让点评更贴教学口径**
改 `ai_client.py` 里的 `SYSTEM_PROMPT` / `REVIEW_TEMPLATE`，把评分维度（审题、结构、内容、语言、岗位匹配）写进去即可。

## 自行构建打包

```
python -m pip install -r requirements.txt
python -m pip install pyinstaller

:: 1) 生成 onedir 目录（产物：dist\面试点评器）
pyinstaller _packaging\interview_reviewer.spec

:: 2) 压缩成绿色版，或用 Inno Setup 编译 _packaging\setup.iss 生成安装包
```

- 识别模型（SenseVoice）、VAD 与标点模型不打入仓库，需按 `interview_reviewer.spec` 中的路径放置后打包。
- 打包前确认 `_packaging\default_data\` 只含 `question_types.json` 与 `config.json`，不得放题目数据。
- 版本号需同步四处：`server.py` 与 `launcher.py` 的 `APP_VERSION`、`static\index.html` 的版本标识、`_packaging\version_info.txt`、`_packaging\setup.iss`。

## 许可证与第三方组件

- 本项目以 **MIT** 许可证发布。
- 第三方组件（Flask、sherpa-onnx、pyaudiowpatch、numpy、PyMuPDF、pywebview 等）各自遵循其许可证，清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，全文见 `LICENSES\`，软件内也可通过「设置 → 关于 → 开源许可」查看。

## 项目地址与反馈

- 项目主页：<https://github.com/lyckeven/InterviewLiveNote>
- 问题反馈：<https://github.com/lyckeven/InterviewLiveNote/issues>
- 版本下载：<https://github.com/lyckeven/InterviewLiveNote/releases>
- 软件内「设置 → 关于」可直接打开项目地址，或点「检查更新」比对 GitHub 最新 Release。

## 致谢

感谢 sherpa-onnx、SenseVoice、Silero VAD、CT-Transformer 等开源语音项目的作者，以及 Flask、pyaudiowpatch、PyMuPDF、pywebview 等组件维护者，本工具的声音采集、离线识别与界面能力都建立在这些开源工作之上。
