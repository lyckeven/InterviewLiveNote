---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 085864f8e2380e58c14cbcb129ac0e68_760ce8d7e1d511f19919525400638852
    ReservedCode1: uC4W7SiPQ4sDPt2h6aEJjlFH6LdxRGWbvyMrro+8eCmU114PhZcWKXrOgo51RBiA26w8bLeAysxq2s+pX1YjmqDaCSvtx4Yzl/KGLH4s1IPd7zs0XVXl24gygAhE+1j0ScHIUf/cZK9Ryp7pxtnWWS+8XGQNrY3X8cgBYeozdGSV2AbIEUZRuuDL7LM=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 085864f8e2380e58c14cbcb129ac0e68_760ce8d7e1d511f19919525400638852
    ReservedCode2: uC4W7SiPQ4sDPt2h6aEJjlFH6LdxRGWbvyMrro+8eCmU114PhZcWKXrOgo51RBiA26w8bLeAysxq2s+pX1YjmqDaCSvtx4Yzl/KGLH4s1IPd7zs0XVXl24gygAhE+1j0ScHIUf/cZK9Ryp7pxtnWWS+8XGQNrY3X8cgBYeozdGSV2AbIEUZRuuDL7LM=
---

# 面试听记（InterviewLiveNote）

给别人答题时**实时听记 + 实时标记 + AI 点评**的本地工具。全程离线转写，录音来源是**电脑系统内录**（WASAPI Loopback），不需要麦克风、不需要虚拟声卡，电脑静音也能录。

---

## 一、启动

双击桌面 **「面试听记」** 图标，或运行：

```
E:\Software\InterviewLiveNote\启动面试听记.bat
```

浏览器会自动打开 `http://127.0.0.1:7788`。若已在运行，则只打开界面，不会重复启动服务。

关闭：直接关浏览器标签页即可，服务留在后台；需要彻底停止时在任务管理器结束对应 python 进程，或运行 `停止服务.bat`。

---

## 二、一次完整使用流程

1. **填题目**（左栏）
   - 可手动输入题干、题型、参考要点；也可点「从题库选」直接选题，或点「导入」把题库文件一次性导入。
2. **确认录音源**（顶栏下拉）
   - 默认「系统默认输出（自动）」，即抓电脑正在播放的声音。播放课程音频/视频会议/VoIP 时用这个。
   - 若插了耳机，程序自动跟随默认输出设备的 Loopback。
3. **点「开始听记」**
   - 别人开口答题，逐字稿按**段落**实时滚出：每段显示「第 N 段 + 段落标签 + 起止时间 + 用时 + 字数 + 语速」，段内再逐句列出（每句约 2–4 秒成句）。
   - 切段规则：句间停顿超过 1.5 秒、或本段已满 55 秒 / 240 字，即收段另起一段；话题词（第一、其次、因此、总之等）也会触发切段。
   - 计时在顶栏显示，右上角红点闪烁表示正在采集。
4. **边听边标记**（实时）
   - 每句右侧按钮：`亮点` `硬伤` `追问` `口头禅` `备注` `AI速评` `删除`。
   - 也可先点句子本身选中，再按键盘 **1 / 2 / 3 / 4** 打四个标记，**空格**开始/停止录音。
   - 备注：点「备注」输入文字回车保存，用于记录具体问题。
   - 右侧「标记汇总」实时累加，点汇总条目可跳回对应句子。
5. **段落点评**（每段讲完即可评）
   - 每段头部右侧有「本段点评」按钮，只针对这一段点评，比如「重分析-原因」这一段刚讲完，就单独评这段的分析有没有讲透，不等全场答完。
   - 在「设置」里打开「段落即时点评」后，段落一收段就自动点评，无需手点（费 token，默认关闭）。
   - 点评内容会挂在对应段落下方，存档的 `.md` 里也按段落记录。

6. **点「生成点评」**
   - 点评固定三段：**一、时间 → 二、优点 → 三、问题**；问题条目统一为「问题 + 具体问题地点 + 改进建议」，地点精确到第几段、第几秒、原话。
   - 已配置 AI Key：AI 对照参考答案与点评标准（审题 / 提观点 / 重分析 / 抓落实 / 结尾五环节问题清单）输出诊断；客观指标表由本地算法给出。
   - 未配置 Key：走本地量化诊断，输出语速、口头禅、结构信号词、停顿、题干关键词覆盖、参考分数与一分钟提升清单。
7. **自动存档**
   - 点「停止」时自动保存到 `data\records\`，生成 `时间.md`（可直接给学生）和 `时间.json`（可回看）。
   - 「历史」可打开既往场次。

---

## 三、快捷键

| 按键 | 作用 |
| --- | --- |
| 空格 | 开始 / 停止录音（输入框内不触发） |
| 1 / 2 / 3 / 4 | 给选中句打 亮点 / 硬伤 / 追问 / 口头禅 |
| Enter | 备注输入时保存 |
| Esc | 取消备注输入 |

---

## 四、接入 AI（可选但推荐）

顶部「设置」：

| 字段 | 说明 |
| --- | --- |
| 接口地址 | OpenAI 兼容地址，默认 `https://api.deepseek.com/v1` |
| 模型名 | 默认 `deepseek-chat`；也可填 `qwen-plus`、`deepseek-reasoner` 等 |
| API Key | 填自己的 Key，保存后不显示明文 |
| 段落即时点评 | 打开后，每段答完自动调 AI 评本段（费 token，默认关闭） |
| 切段停顿 / 单段上限 | 停顿超过多少秒切段（默认 1.5 秒）、单段最长多少秒 / 多少字（默认 55 秒 / 240 字） |

填完点「测试连通」，通过后即可生成 AI 点评。

配置存在 `data\config.json`。

---

## 五、题库格式

点左栏「导入」，粘贴纯文本，按下面格式（多题用 `===` 或直接以 `1. ` 另起一行分隔）：

```
1. 你单位要组织一次基层调研，领导让你负责，你怎么开展？
类型：组织管理
参考要点：明确调研目的与对象；科学设计问卷与提纲；组建队伍分工；深入一线访谈；形成报告并提出对策

1. 有人说基层干部要"能干"，有人说要"能说"，你怎么看？
类型：综合分析
参考要点：辩证统一；能干是根本；能说是沟通能力；两者结合；结合岗位谈落实
```

也支持直接粘贴 JSON 数组：`[{"question":"...","qtype":"综合分析","ref":"..."}]`

导入后存为 `data\bank\题库名.json`，可长期复用。

**导入 Word**：点「导入 Word题库」，选择 `.docx`，系统自动识别「题目正文 / 类型：xxx / 参考要点：xxx」这样的结构并批量入库（`.doc` 老格式请先在 Word 里另存为 `.docx`）。

**删除**：
- 单题删除：点开某个题库，题目右侧「×」，只删这一道题（会二次确认）。
- 整库删除：题库列表名称右侧「×」，删除整个题库文件（不可恢复）。

**已内置题库**：`2026江西事业单位陪练解析.json`，134 题，每题带题型、出处、答题思路、审题要点、作答骨架。

---

## 六、目录结构

```
E:\Software\InterviewLiveNote\
├─ 启动面试听记.bat        双击启动
├─ server.py              Flask 服务（端口 7788）
├─ asr_engine.py          系统内录采集 + VAD 断句 + SenseVoice 识别 + 标点
├─ ai_client.py           OpenAI 兼容客户端（点评 / 逐句速评）
├─ local_analyzer.py      本地量化诊断（不依赖 AI）
├─ paragraphizer.py       按段落切分与计时（停顿 / 篇幅 / 话题词三重规则）
├─ static\                前端（index.html / style.css / app.js）
├─ data\
│  ├─ config.json         AI 配置
│  ├─ bank\               题库
│  └─ records\            存档（.md 给人看，.json 回看）
└─ logs\                  运行日志
```

技术栈：Python 3.13（便携版）+ pyaudiowpatch（WASAPI Loopback）+ sherpa-onnx（SenseVoice int8 离线中文识别 + Silero VAD）+ Flask + 原生前端。识别模型复用 `E:\Software\TMSpeech\external-recognizer\` 下已装好的模型。

---

## 七、常见问题

**1. 逐字稿为空 / 不识别**
- 确认电脑确实在播放声音（系统音量不静音）。
- 顶栏下拉换一个 Loopback 设备试（有多个输出设备时）。
- 看 `logs\server.log` 有无报错。

**2. 识别串进无关声音**
- 内录抓的是整个系统输出，录制时不要同时开音乐、视频、微信语音提示。

**3. 个别字识别错（同音字）**
- 当前是 int8 量化模型，取舍在于速度。需要更高精度可换非量化 `model.onnx`（约 900MB）放到模型目录并改 `asr_engine.py` 里的路径。

**4. 端口被占用**
- 7788 被别的程序占用时，改 `server.py` 末尾的端口号，同步改 `启动面试听记.bat` 里的地址。

**5. 想让点评更贴教学口径**
- 改 `ai_client.py` 里的 `SYSTEM_PROMPT` / `REVIEW_TEMPLATE`，把粉笔面试的评分维度（审题、结构、内容、语言、岗位匹配）写进去即可。

---

## 八、2026-11-17 改造说明

1. **逐字稿改按段落计时**：新增 `paragraphizer.py`，句子进来自动归段，段落记 `t_start / t_end / chars / 用时 / 语速`；前端逐字稿改为段落卡片；旧存档（只有逐句）打开时自动按停顿重建段落，不用手工迁移。
2. **题库支持单题删除与 Word 导入**：新增 `/api/bank/item/delete`、`/api/bank/import_docx` 两个接口，前端加「导入 Word题库」与单题「×」。
3. **点评结构改为时间 → 优点 → 问题**：`ai_client.py` 的点评/段落点评提示词与 `local_analyzer.py` 的输出模板同步改版，问题条目统一「问题 + 具体问题地点 + 改进建议」，问题清单取自粉笔五环节（审题 / 提观点 / 重分析 / 抓落实 / 结尾），段落点评先给出段落功能标签（表态 / 重分析-原因 / 抓落实-监管 / 结尾收束等）。
4. **题库落地**：导入《2026年江西事业单位·陪练 解析》134 题，自动标注题型与答题要点（题型分布：应急应变 33、社会现象 28、组织管理 28、态度观点 17、人际关系 13、漫画类 5、现场模拟 5、串词讲故事 2、演讲类 1、其他 2）。

**回滚**：改版前的 `server.py`、`static\app.js`、`static\index.html` 备份在 `E:\Software\InterviewLiveNote\_backup_20261117_v2\`，直接覆盖回去即可。

---

## 九、可继续升级的方向

- 导出 Word / PDF 点评单
- 多考生轮次对比（同一题横向打分）
- 换非量化模型提升识别准确率
- 考场模式：全屏大字逐字稿 + 只显示计时与标记

## 十、v1.1.0 实时转写优化（2026-11-17）

1. **增量上屏**：`asr_engine.py` 重写为 v1.1，新增 `_partial_loop` 线程，每 0.3 秒把当前说话段送识别一次，通过 `on_partial(text, t_start, t_end, key)` 回调 → `server.py` 的 `_on_partial` → SSE `partial` 事件 → `static/app.js` 的 `renderLive()` 渲染「识别中」实时行；取消旧引擎的 pending 挂起合并，每段识别完立即定稿。首句上屏 4.92s → 0.36s。
2. **标点恢复**：接入 sherpa-onnx CT-Transformer 标点模型（`models\punct\model.onnx`，int8 72 MB，`IR_PUNCT_MODEL` 可覆盖），`tidy_punct()` 清理重复标点；模型缺失时退回 `add_punct()` 轻量规则。标点密度 1.6/百字 → 9.0/百字。
3. **切分优化**：VAD `min_silence_duration` 0.4 → 0.32，新增 `PREROLL_SEC=0.25` 前置缓冲与 `PREROLL_GUARD_SEC=0.35` 起止保护，减少句子被切断；CER 8.30% → 7.51%。量化实验证明 int8 与 fp32 整段 CER 仅差 2.77%，故继续使用 int8 模型。
4. **模型迁移**：`_migrate_models()` 扩展为同时迁移识别、VAD、标点三个模型到 ASCII 缓存目录，规避非 ASCII 路径下 `No graph was found in the protobuf`。
5. **版本**：`server.py` APP_VERSION、`static/index.html`、`_packaging/version_info.txt`、`_packaging/setup.iss` 统一升至 1.1.0。

回滚位置：未单独建立备份目录（当时未留存改动前文件），如需回滚须手动对照旧版 asr_engine.py / server.py / static/app.js。

*（内容由AI生成，仅供参考）*

## 十一、v1.2.0 精简版（去冗余逐字稿，2026-11-17）

1. **去冗余**：新增 `simplifier.py`（纯本地规则，不调 AI）。按标点切分句逐一清理——删单字语气词（嗯/啊/呃/哦/诶/哎/唉/哈等，含夹在词间的“讲解诶这个”）、口头禅（就是说/然后/那么/反正/对吧/是吧/怎么说呢等）、独立出现的「这个/那个」、结巴与无意义重复（三连字、易结巴字叠字、2~6 字词重复），并丢弃只剩填充词的空分句。
2. **按段实时产出**：`server.py` 在 `pz.close_para()`、段落速评回写、`_on_final` 追句、`_rebuild_paragraphs` 重建、`api_stop` 收尾等处调用 `simplifier.apply()`，通过 SSE `plain` 事件（`_plain_event`）即时推送到前端，段落一收段立刻出现该段精简版，无需等整场结束。
3. **纯文字无时间戳**：精简版只写 `plain` 字段（纯文字）；面板与整篇复制内容均不含 `HH:MM`、秒数或段落用时。
4. **结构环节标注**：`stage_of()` 输出五环节（审题/提观点/重分析/抓落实/结尾）+ 小标题（表态、原因、影响、对策、宣传、监管、考核、收束等），判定优先级为 段首复述题干→结尾语→提观点强信号→已有段落标签（沿用 `paragraphizer.TAG_RULES` 与 AI 回写的 `【段落标签】`）→关键词计分→位置兜底，与既有段落标签逻辑一致。
5. **一键复制与存档**：新增 `GET /api/plain/text?stage=1|0`（带/不带结构标注）；前端 `#btnCopyPlain` 复制整篇、`#btnTogglePlain` 收起展开。存档 Markdown 增加「## 精简版（去冗余 · 按结构环节，无时间戳）」区块；历史记录回看时若存档无 `plain` 字段则按规则只读补齐后渲染。
6. **版本**：`server.py` APP_VERSION 升至 1.2.0。回滚备份见 `_backup_20261117_v5`。

*（内容由AI生成，仅供参考）*

## 十二、v2.3.0 界面修复与题库导入增强（2026-11-23）

1. **输入框遮挡修复 + 文本域拖拽缩放**：`static/style.css` 为文本域补 `resize:vertical; flex:0 0 auto; min-height:3.2em; max-height:none; overflow-y:auto`，文本框不再被弹层或相邻面板压住，可手动纵向拖拽调高。
2. **弹窗关闭按钮统一**：底部按钮栏改为 `.modal-body>.modal-foot`（`position:sticky; bottom:0; z-index:3`），配合 `scrollbar-gutter:stable`、`overscroll-behavior:contain`，所有弹窗只在固定位置保留唯一关闭入口，弹层栈由 `MODAL_STACK` / `openModal()` / `closeModal()` 统一管理。
3. **PDF / Word 题库导入自动解析**：`server.py` 新增 `_read_pdf_text`（pymupdf）、`_parse_fenbi_block`、`_split_bank_blocks`、`_fb_structure` 等解析链，自动识别题型、结构环节与答题要点，切题按题号锚点与分段规则，杜绝串题；前端新增导入预览弹层（逐题统计缺失项后再入库）。
4. **顶部「精简要点」按钮**：新增 `points_extract.py`、`/api/points/source`、`/api/points/analyze`，把精简纠错后的逐字稿分析成学生答题要点，只保留关键词并按结构环节分组，支持分行项目符号与纯文本两种复制；无 API Key 时自动走本地规则，AI 失败静默回退本地。
5. **版本**：`launcher.py` / `server.py` 的 APP_VERSION、`static/index.html` 关于页、`_packaging/version_info.txt`、`_packaging/setup.iss` 统一升至 2.3.0。

回滚位置：未单独建立备份目录（当时未留存改动前文件），如需回滚须手动对照旧版 `server.py` / `static\style.css` / `static\app.js` / `static\index.html` / `points_extract.py`。

*（内容由AI生成，仅供参考）*

## 十三、v2.4.0 编辑能力与复盘导出（2026-11-23）

1. **句子级编辑**：逐字稿与精简稿支持按句拆句、相邻合并、改字、删句，编辑后可整篇重算；历史回看同样生效。
2. **精简稿结构一致**：调整结构（环节归类）后再复制或导出，与界面显示完全一致，不再出现「复制内容与所见不同」。
3. **精简要点取当前稿**：要点分析读取的是当前编辑后的精简稿，而非旧原文。
4. **要点压缩为短短语**：要点输出由整句压成动作式短短语（动宾/名词短句），单条上限 20 字、软上限 12 字。
5. **要点对齐判定走 AI**：答到 / 偏题 / 遗漏的判定由 AI 完成，本地规则兜底；无 Key 或调用失败时自动回退本地，并在面板标注判定来源（AI / 本地），保证结果可追溯。
6. **导入预览可编辑**：题库导入预览支持逐题改题型、改题干、增删与重命名环节、增删要点、上下移动要点、编辑参考要点，并可一键恢复原解析结果后再入库。
7. **自动切段点评去重与校准**：切片点评对重复切片与已合并切片自动跳过（相似度阈值 0.7 + 签名缓存），避免同一段重复点评。
8. **历史记录筛选与检索**：可按题型、学生姓名、日期范围、关键词（题干或逐字稿正文）组合筛选，支持一键清除与空态提示，列表显示题型、学生、日期、时长、字数、参考分。
9. **一键导出复盘稿为 Word**：历史详情与点评区各有入口，导出 .docx 依序包含基本信息、时间分配表、三段式点评（时间/优点/问题，问题条目带段号与秒级坐标）、按环节分组的精简要点、精简逐字稿全文。
10. **版本**：`launcher.py` / `server.py` 的 APP_VERSION、`static/index.html` 关于页、`_packaging/version_info.txt`、`_packaging/setup.iss` 统一升至 2.4.0。回滚备份见 `_backup_20261123_v8`。

*（内容由AI生成，仅供参考）*

## 十四、v2.5.0 解析来源如实标注与写回校验（2026-11-24）

1. **解析来源标注与事实一致**：导入预览与进度卡的来源标签不再凭 `parse_src` 猜测。只有真正走通 AI 的题才标「AI 解析」；AI 因 402 余额不足、API Key 无效、超时/不可用而回退本地规则的，一律标「本地解析（AI 余额不足已回退）」等带原因的红色标签；文档解析出来的标「文档自带」；未配 Key 的标「本地解析（未配置 API Key）」。回落原因由 `_imp_ai_err_kind()` 归类（balance / auth / busy / fail / off）。
2. **进度卡即时提示回退原因**：新增 `_imp_note_state()`，在 AI 首次失败回退本地的瞬间就把 `ai_state` / `ai_note` 推进进度卡，进度卡文案由「AI 识别优先」改为「AI 识别中…」，失败即变红色「AI 余额不足（402），已回退本地规则」，成功则显示「AI 解析成功」，不必等整批跑完才知道。
3. **三类题数统计**：预览弹层顶部与完成 toast 统一输出「AI x / 本地 x / 文档自带 x」，由 `ipSrcCount()` 与服务端 `src_stat` 同源计算，避免界面与后台口径不一致。
4. **缺失字段本地模板补齐**：新增 `_imp_local_patch_missing()`，对缺审题要点 / 参考要点的题，按题型调 `_qtype_review_parts` 补审题要点，按已有答题环节与要点拼参考要点，并在预览行标注「审题要点（本地模板）」「参考要点（本地模板）」；模板补不了的仍以「缺失：参考要点」红字提示人工补齐，不静默留空。
5. **编辑写回严格校验**：`/api/bank/item/update` 写回后按「同一 index、同一题号」双条件校验，实测断言取值不再是 `None`：原条目题号 6 → 写回后题号 6（整数）、题库条数不变、前后邻居题号与题干未位移、题号序列 1..N 完整、全文无重复副本。
6. **版本**：`server.py` / `launcher.py` 的 APP_VERSION、`static/index.html` 三处版本标识、`_packaging/version_info.txt`（FileVersion 2.5.0.0 / ProductVersion 2.5.0）、`_packaging/setup.iss`（MyAppVersion / VersionInfoVersion 2.5.0.0 / SrcDir=dist250）统一升至 2.5.0。回滚备份见 `_backup_20261124_v9`，v2.4.0 及更早产物保持原样未动。

*（内容由AI生成，仅供参考）*

## 十五、v2.5.1 多学生录音归属锁定与精简要点学生化渲染（2026-11-24）

1. **多学生录音归属锁定**：`server.py` api_records 接口（约第 6113 行起）改为按 `student` 筛选记录；`_session_view` 返回 rec_student / rec_student_name / rec_running / rec_t0 字段；`api_state` 暴露 session 内 rec_* 字段。切换或只读查看不同学生时不改变录音归属字段，view_only 生效。
2. **精简要点历史回看按学生渲染**：`server.py` `/api/records/points`（约第 6489 行）增加 file/student 参数，取 st.get("id") 匹配学生段落再返回，前端请求带 &student=。
3. **题库外层搜索浮层样式统一**：static/app.js bankSearchInp/bankSearchPop 节点齐备，style.css qsPlace / qsWrap 等定位样式与 .qs-pop/.qs-wrap 整体统一。
4. **题库 AI 优化按钮**：app.js btnAiOptSel / btnAiOptAllQ / btnAiOptUndo 齐备，ai_optimize_last 正常返回，bi-diff 对比与撤销逻辑在包内。
5. **导入题库可选仅本地解析或 AI 解析**：`server.py` /api/bank/import_start（约第 4452 行）支持 mode ai/local；/api/bank/import_pref（约第 4478 行）读写生效。
6. **本地解析支持陪练解析 PDF 格式**：`server.py` _read_record/_prepare_record（约第 6408/6418 行）对 PDF 走本地解析；实测陪练解析 PDF（C:\Users\23576\Desktop\备课稿\BaiduSyncdisk\面试粉笔\理论知识\2026年江西事业单位 ·陪练 解析.pdf）解析出 135 题，src_stat={ai:0, doc:135, local:0}，未发起任何 AI 请求。
7. **版本**：`launcher.py` APP_VERSION="2.5.1"（第 21 行）、`server.py` APP_VERSION="2.5.1"（第 27 行）、`static/index.html` 三处版本标识升至 v2.5.1；`_packaging/version_info.txt` filevers/prodvers=(2,5,1,0)、FileVersion='2.5.1.0'、ProductVersion='2.5.1'；`setup.iss` #define MyAppVersion "2.5.1"、OutputBaseFilename=面试点评器_安装程序_v2.5.1、VersionInfoVersion=2.5.1.0、SrcDir 改 dist251。回滚提示：本轮改动前版本已归档于 _backup_20261124_v9~v15，可按需取对应编号目录恢复。

*（内容由AI生成，仅供参考）*