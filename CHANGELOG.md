# 更新日志 (Changelog)

本文件记录面试听记（InterviewLiveNote）自 v1.1.0 以来的全部已知版本改动，格式参照 [Keep a Changelog](https://keepachangelog.com/)。版本号基准取自项目根目录 `_packaging/version_info.txt` 与 `_packaging/setup.iss`。

---

## [1.1.0] - 2026-11-17

### Added
- **增量上屏**：`asr_engine.py` 重写为 v1.1，新增 `_partial_loop` 线程，每 0.3 秒把当前说话段送识别一次，通过 `on_partial(text, t_start, t_end, key)` 回调 → `server.py` 的 `_on_partial` → SSE `partial` 事件 → `static/app.js` 的 `renderLive()` 渲染「识别中」实时行；取消旧引擎的 pending 挂起合并，每段识别完立即定稿。首句上屏 4.92s → 0.36s。
- **标点恢复**：接入 sherpa-onnx CT-Transformer 标点模型（`models\punct\model.onnx`，int8 72 MB，`IR_PUNCT_MODEL` 可覆盖），`tidy_punct()` 清理重复标点；模型缺失时退回 `add_punct()` 轻量规则。标点密度 1.6/百字 → 9.0/百字。
- **切段优化**：新增 `PREROLL_SEC=0.25` 前置缓冲与 `PREROLL_GUARD_SEC=0.35` 起止保护，减少句子被切断；CER 8.30% → 7.51%。

### Changed
- VAD `min_silence_duration` 0.4 → 0.32。
- `_migrate_models()` 扩展为同时迁移识别、VAD、标点三个模型到 ASCII 缓存目录，规避非 ASCII 路径下 `No graph was found in the protobuf`。
- 量化实验证明 int8 与 fp32 整段 CER 仅差 2.77%，故继续使用 int8 模型。

> **回滚提示**：本版本改动前的代码归档于 `_backup_20261117_v2`（含识别/VAD/标点三套模型迁移逻辑），如需恢复可从中提取。

---

## [1.2.0] - 2026-11-17

### Added
- **去冗余**：新增 `simplifier.py`（纯本地规则，不调 AI）。按标点切分句逐一清理——删单字语气词（嗯/啊/呃/哦/诶/哎/唉/哈等，含夹在词间的“讲解诶这个”）、口头禅（就是说/然后/那么/反正/对吧/是吧/怎么说呢等）、独立出现的「这个/那个」、结巴与无意义重复（三连字、易结巴字叠字、2~6 字词重复），并丢弃只剩填充词的空分句。
- **按段实时产出**：`server.py` 在 `pz.close_para()`、段落速评回写、`_on_final` 追句、`_rebuild_paragraphs` 重建、`api_stop` 收尾等处调用 `simplifier.apply()`，通过 SSE `plain` 事件（`_plain_event`）即时推送到前端，段落一收段立刻出现该段精简版，无需等整场结束。
- **纯文字无时间戳**：精简版只写 `plain` 字段（纯文字）；面板与整篇复制内容均不含 `HH:MM`、秒数或段落用时。
- **一键复制与存档**：新增 `GET /api/plain/text?stage=1|0`（带/不带结构标注）；前端 `#btnCopyPlain` 复制整篇、`#btnTogglePlain` 收起展开。存档 Markdown 增加「## 精简版（去冗余 · 按结构环节，无时间戳）」区块；历史记录回看时若存档无 `plain` 字段则按规则只读补齐后渲染。

### Changed
- **结构环节标注**：`stage_of()` 输出五环节（审题/提观点/重分析/抓落实/结尾）+ 小标题（表态、原因、影响、对策、宣传、监管、考核、收束等），判定优先级为 段首复述题干→结尾语→提观点强信号→已有段落标签（沿用 `paragraphizer.TAG_RULES` 与 AI 回写的 `【段落标签】`）→关键词计分→位置兜底，与既有段落标签逻辑一致。
- **版本**：`server.py` APP_VERSION 升至 1.2.0。

> **回滚提示**：本版本改动前的代码归档于 `_backup_20261117_v5`，如需恢复可从中提取。

---

## [2.3.0] - 2026-11-23

### Added
- **输入框遮挡修复 + 文本域拖拽缩放**：`static/style.css` 为文本域补 `resize:vertical; flex:0 0 auto; min-height:3.2em; max-height:none; overflow-y:auto`，文本框不再被弹层或相邻面板压住，可手动纵向拖拽调高。
- **PDF / Word 题库导入自动解析**：`server.py` 新增 `_read_pdf_text`（pymupdf）、`_parse_fenbi_block`、`_split_bank_blocks`、`_fb_structure` 等解析链，自动识别题型、结构环节与答题要点，切题按题号锚点与分段规则，杜绝串题；前端新增导入预览弹层（逐题统计缺失项后再入库）。
- **顶部「精简要点」按钮**：新增 `points_extract.py`、`/api/points/source`、`/api/points/analyze`，把精简纠错后的逐字稿分析成学生答题要点，只保留关键词并按结构环节分组，支持分行项目符号与纯文本两种复制；无 API Key 时自动走本地规则，AI 失败静默回退本地。

### Fixed
- **弹窗关闭按钮统一**：底部按钮栏改为 `.modal-body>.modal-foot`（`position:sticky; bottom:0; z-index:3`），配合 `scrollbar-gutter:stable`、`overscroll-behavior:contain`，所有弹窗只在固定位置保留唯一关闭入口，弹层栈由 `MODAL_STACK` / `openModal()` / `closeModal()` 统一管理。

### Changed
- **版本**：`launcher.py` / `server.py` 的 APP_VERSION、`static/index.html` 关于页、`_packaging/version_info.txt`、`_packaging/setup.iss` 统一升至 2.3.0。

> **回滚提示**：本轮改动未单独建立备份目录；如需参考当时状态请查阅 `_backup_20261123_v7`（含 server.py/static/*/*.py 共 11 项源码）。

---

## [2.4.0] - 2026-11-23

### Added
- **句子级编辑**：逐字稿与精简稿支持按句拆句、相邻合并、改字、删句，编辑后可整篇重算；历史回看同样生效。
- **精简稿结构一致**：调整结构（环节归类）后再复制或导出，与界面显示完全一致，不再出现「复制内容与所见不同」。
- **要点对齐判定走 AI**：答到 / 偏题 / 遗漏的判定由 AI 完成，本地规则兜底；无 Key 或调用失败时自动回退本地，并在面板标注判定来源（AI / 本地），保证结果可追溯。
- **导入预览可编辑**：题库导入预览支持逐题改题型、改题干、增删与重命名环节、增删要点、上下移动要点、编辑参考要点，并可一键恢复原解析结果后再入库。
- **历史记录筛选与检索**：可按题型、学生姓名、日期范围、关键词（题干或逐字稿正文）组合筛选，支持一键清除与空态提示，列表显示题型、学生、日期、时长、字数、参考分。
- **一键导出复盘稿为 Word**：历史详情与点评区各有入口，导出 .docx 依序包含基本信息、时间分配表、三段式点评（时间/优点/问题，问题条目带段号与秒级坐标）、按环节分组的精简要点、精简逐字稿全文。

### Changed
- **精简要点取当前稿**：要点分析读取的是当前编辑后的精简稿，而非旧原文。
- **要点压缩为短短语**：要点输出由整句压成动作式短短语（动宾/名词短句），单条上限 20 字、软上限 12 字。
- **版本**：`launcher.py` / `server.py` 的 APP_VERSION、`static/index.html` 关于页、`_packaging/version_info.txt`、`_packaging/setup.iss` 统一升至 2.4.0。

### Fixed
- **自动切段点评去重与校准**：切片点评对重复切片与已合并切片自动跳过（相似度阈值 0.7 + 签名缓存），避免同一段重复点评。

> **回滚提示**：本版本改动前的代码归档于 `_backup_20261123_v8`，如需恢复可从中提取。

---

## [2.5.0] - 2026-11-24

### Changed
- **解析来源标注与事实一致**：导入预览与进度卡的来源标签不再凭 `parse_src` 猜测。只有真正走通 AI 的题才标「AI 解析」；AI 因 402 余额不足、API Key 无效、超时/不可用而回退本地规则的，一律标「本地解析（AI 余额不足已回退）」等带原因的红色标签；文档解析出来的标「文档自带」；未配 Key 的标「本地解析（未配置 API Key）」。回落原因由 `_imp_ai_err_kind()` 归类（balance / auth / busy / fail / off）。

### Added
- **进度卡即时提示回退原因**：新增 `_imp_note_state()`，在 AI 首次失败回退本地的瞬间就把 `ai_state` / `ai_note` 推进进度卡，进度卡文案由「AI 识别优先」改为「AI 识别中…」，失败即变红色「AI 余额不足（402），已回退本地规则」，成功则显示「AI 解析成功」，不必等整批跑完才知道。
- **三类题数统计**：预览弹层顶部与完成 toast 统一输出「AI x / 本地 x / 文档自带 x」，由 `ipSrcCount()` 与服务端 `src_stat` 同源计算，避免界面与后台口径不一致。
- **缺失字段本地模板补齐**：新增 `_imp_local_patch_missing()`，对缺审题要点 / 参考要点的题，按题型调 `_qtype_review_parts` 补审题要点，按已有答题环节与要点拼参考要点，并在预览行标注「审题要点（本地模板）」「参考要点（本地模板）」；模板补不了的仍以「缺失：参考要点」红字提示人工补齐，不静默留空。

### Fixed
- **编辑写回严格校验**：`/api/bank/item/update` 写回后按「同一 index、同一题号」双条件校验，实测断言取值不再是 `None`：原条目题号 6 → 写回后题号 6（整数）、题库条数不变、前后邻居题号与题干未位移、题号序列 1..N 完整、全文无重复副本。

### Changed
- **版本**：`server.py` / `launcher.py` 的 APP_VERSION、`static/index.html` 三处版本标识、`_packaging/version_info.txt`（FileVersion 2.5.0.0 / ProductVersion 2.5.0）、`_packaging/setup.iss`（MyAppVersion / VersionInfoVersion 2.5.0.0 / SrcDir=dist250）统一升至 2.5.0。

> **回滚提示**：本版本改动前的代码归档于 `_backup_20261124_v9`，如需恢复可从中提取。

---

## [2.5.1] - 2026-11-24

### Added
- **多学生录音归属锁定**：`server.py` api_records 接口（约第 6113 行起）改为按 `student` 筛选记录；`_session_view` 返回 rec_student / rec_student_name / rec_running / rec_t0 字段；`api_state` 暴露 session 内 rec_* 字段。切换或只读查看不同学生时不改变录音归属字段，view_only 生效。
- **精简要点历史回看按学生渲染**：`server.py` `/api/records/points`（约第 6489 行）增加 file/student 参数，取 st.get("id") 匹配学生段落再返回，前端请求带 &student=。
- **题库 AI 优化按钮**：app.js btnAiOptSel / btnAiOptAllQ / btnAiOptUndo 齐备，ai_optimize_last 正常返回，bi-diff 对比与撤销逻辑在包内。
- **导入题库可选仅本地解析或 AI 解析**：`server.py` /api/bank/import_start（约第 4452 行）支持 mode ai/local；/api/bank/import_pref（约第 4478 行）读写生效。
- **本地解析支持陪练解析 PDF 格式**：`server.py` _read_record/_prepare_record（约第 6408/6418 行）对 PDF 走本地解析；实测陪练解析 PDF（C:\Users\23576\Desktop\备课稿\BaiduSyncdisk\面试粉笔\理论知识\2026年江西事业单位 ·陪练 解析.pdf）解析出 135 题，src_stat={ai:0, doc:135, local:0}，未发起任何 AI 请求。

### Changed
- **题库外层搜索浮层样式统一**：static/app.js bankSearchInp/bankSearchPop 节点齐备，style.css qsPlace / qsWrap 等定位样式与 .qs-pop/.qs-wrap 整体统一。
- **版本**：`launcher.py` APP_VERSION="2.5.1"（第 21 行）、`server.py` APP_VERSION="2.5.1"（第 27 行）、`static/index.html` 三处版本标识升至 v2.5.1；`_packaging/version_info.txt` filevers/prodvers=(2,5,1,0)、FileVersion='2.5.1.0'、ProductVersion='2.5.1'；`setup.iss` #define MyAppVersion "2.5.1"、OutputBaseFilename=面试点评器_安装程序_v2.5.1、VersionInfoVersion=2.5.1.0、SrcDir 改 dist251。

### Fixed
- （本轮以功能新增为主，无可报告缺陷修复。）

> **回滚提示**：本版本改动前的代码归档于 `_backup_20261124_v9`～`_backup_20261124_v15`，如需恢复可从该范围中提取。

---

## [2.5.2] - 2026-11-25

### Added
- **运行日志系统**：新建 `log_util.py`（`get_logger(name)`、RotatingFileHandler 单文件 2 MB 保留 5 份、按天命名 `app-YYYYMMDD.log`、`mask_secret/short/log_stats/tail_text/open_log_dir/install_excepthooks`）。`server.py` 接入模块级 logger、`after_request` 记录方法/路径/状态码/耗时（≥400 或 >3000 ms 记 warning，≥500 记 error，`/static/` 不记）、`errorhandler(Exception)` 写 traceback；安装 `sys.excepthook` 与 `threading.excepthook`。新增接口 `GET /api/logs/info`、`GET /api/logs/tail?n=`、`POST /api/logs/open`、`POST /api/log/client`；`ai_client.py` 记录请求模型/地址/超时/状态返回；前端 `window.onerror` + `unhandledrejection` 上报；设置面板「基础设置」下新增「错误日志」行（查看目录、最近日志、大小）。
- **题库 AI 优化可强制停止**：新增 `POST /api/bank/ai_optimize_cancel`，置任务 `cancel=True` 后立即返回进度快照；前端 `#btnAiOptCancel`（danger mini，按运行态显隐），`AIO` 增加 `stopping` 状态，`aioSync/aioPoll` 统一控制按钮与 `state=cancel` 收尾。`GET /api/bank/ai_optimize_job` 返回体新增 `cancel`、`elapsed` 字段。

### Changed
- **题库题目行操作按钮竖排**：`static/app.js` `renderBankItems` 将「ai／✎／🗑」三个按钮包入 `span.bi-acts` 竖排（宽 26 px、`flex-direction:column`、gap 2 px、`align-self:flex-start`），「AI 优化」缩写为「ai」，失败态显示「重试」；`.bi-t` 保持 `flex:1`，`.bi-diff` 独占整行。`static/style.css` 补充 `.bank-item .bi-acts` 及列内按钮样式。
- **题库「编辑」与题目区「查看/编辑」统一为同一弹层**：题库行 ✎ 与题目区搜索命中编辑均改为调用 `qaOpenForBankItem()`，弹层内展示题型、审题要点、五环节结构、要点、参考要点，覆盖模式隐藏题库选择列、主按钮文案改为「保存并覆盖本题」，提交 `POST /api/bank/item/update`（`file=CUR_BANK`、`index=序号`）。新增 `qaIsOverwrite()/qaSetMode()/qaOpenForBankItem()/qaSaveOverwrite()`，`openModal/closeModal` 对 `#modalQAnalyze` 做模式复位。
- **AI 优化对比改为大窗口左右并排**：新增 `#modalAiDiff`（`.modal-box.widest` = `min(1240px, 96vw)`）、`#dfGrid` 两列 grid，左「原答案」右「优化后」，改动处 `.chg` 橙色加粗、删除处 `.chg-del` 灰色删除线，≤820 px 自动单列；由题目行 `.bi-diff` 点击/双击进入。移除原行内展开逻辑。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.5.2"`，`static/index.html` 三处版本标识升至 v2.5.2；`_packaging/version_info.txt` filevers/prodvers=(2,5,2,0)、FileVersion='2.5.2.0'、ProductVersion='2.5.2'；`_packaging/setup.iss` MyAppVersion "2.5.2"、OutputBaseFilename=面试点评器_安装程序_v2.5.2、VersionInfoVersion=2.5.2.0、SrcDir 改 dist252。
- **工程化补充**：新增 `.gitignore`（排除 data/logs/temp/dist/backup/密钥等）与 `requirements.txt`（直接依赖，未锁版本），便于源码上传 GitHub。

### Fixed
- **题库 AI 优化长时间卡住无法中断**：`_aiopt_run` 改为分批 + 可中断（每批 `max_workers=_AIOPT_WORKERS`，`wait` 间隔 0.25 s 轮询 `job["cancel"]`；单题超过 `_AIOPT_TIMEOUT+30` 秒判失败并继续）。实测取消接口耗时 0.000 s，取消后 0.20 s 进入 `status=done / state=cancel`，提示「已停止：成功 X 题，未处理 Y 题」；正常场景 `state=ok`、5/5 题、耗时 1.5 s。
- **题库编辑页与题目编辑页不一致**：见上「Changed」第三项，实测覆盖保存后 exam/structure/keys/ref 四项均写回原题。

> **打包产物**：安装包 `面试点评器_安装程序_v2.5.2.exe`（300.6 MB）、绿色版 `面试点评器_绿色版_v2.5.2.zip`（339 MB / 1463 项）、源码包 `面试点评器_源码_v2.5.2.zip`（0.45 MB / 41 项）。绿色版隔离实测启动通过，`/api/appinfo` 返回 version=2.5.2、frozen=true，`/api/logs/info` 正常。

## [2.5.4] - 2026-11-26

### Added
- **手机版「切换学生」**：`static/mobile.html` 听记页上下文卡片 `#ctxCard` 新增「切换学生」按钮与学生胶囊面板，复用 `POST /api/students/switch` 与 `switchStudent(id)`；切换后同步刷新 `#ctxStudent`、当堂统计与逐字稿归属，不再需要退回电脑端换人。

### Changed
- **平板模式改三栏**：`body[data-mode="tablet"]` 由原双列改为与电脑版同款三栏——左栏题库、中列逐字稿、右栏点评与会话；宽度自适应，窄平板左栏 176–208 px，≥1140 px 右栏分双列并 sticky，760–1139 px 右栏单列；手机竖屏仍保持标签页形态。`static/mobile.css` 新增对应断点，实测 5 种尺寸无横向溢出、元素零压盖。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.5.4"`，`static/index.html` 三处版本标识升至 v2.5.4；`_packaging/version_info.txt` filevers/prodvers=(2,5,4,0)、FileVersion='2.5.4.0'、ProductVersion='2.5.4'；`_packaging/setup.iss` MyAppVersion "2.5.4"、OutputBaseFilename=面试点评器_安装程序_v2.5.4、VersionInfoVersion=2.5.4.0、SrcDir 改 dist254。


---

## [2.5.3] - 2026-11-26

### Added
- **一键修改防火墙设置（手机端接入放行）**：新建 `firewall_util.py`，提供 `status()/allow()/remove()` 三个函数，规则显示名「面试点评器（手机端接入）」，仅对当前程序本体路径生效、`profile=Any`（域 / 专用 / 公用三类网络全适用），不改动防火墙总开关；非管理员时经 `Start-Process -Verb RunAs` 走 UAC 提权，PowerShell 结果统一落 `%TEMP%\ir_fw_*.json` 后回读、脚本以 base64(UTF-16LE) + `-EncodedCommand` 传入规避控制台编码问题。`server.py` 新增 `GET /api/system/firewall`、`POST /api/system/firewall/allow`、`POST /api/system/firewall/remove`、`GET /api/system/firewall/help` 四个接口。
- **设置面板「手机接入 · 防火墙放行」区块**：`static/index.html` 在「基础设置」新增状态徽标（已放行 / 部分放行 / 未放行）、状态描述、放行对象路径与「一键放行 / 撤销放行」按钮；`static/app.js` 新增 `fwRender()/loadFirewallInfo()/fwAction()`，打开设置时自动刷新状态，操作前弹确认框说明影响范围。
- **扫码页防火墙提示**：`static/scan.html` 新增 `#fwHint` 琥珀色提示条，仅在 `level!=all` 时出现，直接指向「设置 → 手机接入 · 防火墙放行 → 一键放行」。
- **启动自检提示**：`static/app.js` 末尾新增 `fwStartupCheck()`，启动 6 秒后静默读取防火墙状态，未完全放行时当天只提醒一次（localStorage 按日去重），不自动改动任何规则。

### Changed
- **顶栏排版重做**：`static/style.css` 顶栏改为 `flex-wrap:nowrap` + `justify-content:space-between`，`#deviceSel` 固定 164 px、`qs-inp` 改为 `clamp(132px,14vw,196px)`；新增 `1560/1400/1340/1300/720 px` 五档断点，1300 px 以下自动换两行（brand / controls / right 三段），1560 px 以下隐藏版本号、1340 px 以下压缩内边距与控件、720 px 以下隐藏状态胶囊。实测 1440/1320/1300/1180/980 五档无横向溢出、元素零压盖。
- **扫码默认走安全地址**：`static/scan.html` 默认 `qrMode='https'`，二维码地址带 `scheme=https`；HTTPS 未启用时自动回落普通地址，避免手机端打开后麦克风被浏览器禁用。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.5.3"`，`static/index.html` 三处版本标识升至 v2.5.3；`_packaging/version_info.txt` filevers/prodvers=(2,5,3,0)、FileVersion='2.5.3.0'、ProductVersion='2.5.3'；`_packaging/setup.iss` MyAppVersion "2.5.3"、OutputBaseFilename=面试点评器_安装程序_v2.5.3、VersionInfoVersion=2.5.3.0、SrcDir 改 dist253。

### Fixed
- **「被困在公用网络」的手机连不上问题**：此前本机仅存在 `Python` 程序的「公用」入站规则，切到其它 Wi-Fi 或改网络类别即被拦。实测 `status` 返回 `level=partial`、`text` 明示「仅部分网络类型放行（公用）」；`allow` 后创建 `profile=Any` 规则、`remove` 后回到 `removed=1 / left=0`，规则增删不影响系统其它防火墙项。
- **端口被其它程序占用时误判「已在运行」**：`launcher.py` 的 `_probe()` 原仅判断 `/api/appinfo` 是否返回 200，端口上任意 HTTP 服务都会让程序认定自己是重复实例并直接退出。改为解析返回体并校验 `name == APP_NAME`，非本应用一律视为未运行、继续正常启动（实测：其它服务 → False、无服务 → False、本应用 → True）。

> **打包产物**：安装包 `面试点评器_安装程序_v2.5.3.exe`（303.2 MB）、绿色版 `面试点评器_绿色版_v2.5.3.zip`（342.9 MB / 1592 项）、源码包 `面试点评器_源码_v2.5.3.zip`（0.48 MB / 45 项）。frozen 版实测 `/api/appinfo` 返回 version=2.5.3、frozen=true，`/api/system/firewall` 返回 200 且正确识别放行对象为 `面试点评器.exe`。

---

## [2.5.5] - 2026-11-26

### Added
- **「AI 优化实时输出」弹层（电脑端 / 手机端）**：点进度条即可实时查看逐题优化前后内容。后端新增 `_aiopt_live_slim()`（单题快照压缩：题干 / 题型 / 结构 / 关键点 / 参考，各字段设上限）与 `_aiopt_live_view(job, raw_max)`（按题目顺序输出 `no/index/state/sec/q/qtype/msg/raw/raw_len/raw_cut/before/after`），`GET /api/bank/ai_optimize_job` 增加 `live=1` 与 `rawmax`（上限 20000，默认 6000）参数；不带 `live` 时仍返回精简进度，不额外传输正文。电脑端新增 `#modalAiLive` 弹层与 `aioLiveOpen/aioLiveRender/aioLiveTick`（运行中 0.8s 刷新、关闭即停轮询）；手机端新增 `bqLiveOpen/bqLiveRender/bqLiveRow`（弹层未打开时只取进度，打开后带 `&live=1&rawmax=1200`，900ms 刷新）。
- **手机 / 平板端补齐完整题库功能**：`static/mobile.html` 新增题库视图 `#view-bank` 与 AI 优化区（进度条 + 优化所选 / 优化全库 / 停止），`static/mobile.js` 新增 `loadBankItems/bankItemsShow/bqItemAt/bqSelSet/bqOptTag/renderBankItems/bqSnapOf/bqDiffRows/bqOpenDiff/bqOpenEdit/bqNewBank/bqRenameBank/mSheet*` 等函数，支持题库检索、勾选、设为当前题、查看详情、编辑、优化前后对比、删除、新建 / 重命名题库。

### Changed
- **多题 AI 优化改为逐题串行**：`_aiopt_run` 改用 `ThreadPoolExecutor(max_workers=1)` 逐题提交，前题完成才处理下一题，`max_concurrent_run=1`、`max_ai_inflight=1`，避免并发把接口打满导致整体超时；单题硬超时 `_AIOPT_TIMEOUT+30` 秒，每题处理完立即落盘，中途可在不丢已完成结果的前提下停止（未处理题标记 `skip`）。
- **AI 客户端连接复用**：`ai_client.py` 的 `_chat` 改用模块级 `requests.Session`（`_HTTP.post`），省掉每题一次 TCP/TLS 握手。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.5.5"`，`static/index.html` 三处版本标识升至 v2.5.5；`_packaging/version_info.txt` filevers/prodvers=(2,5,5,0)、FileVersion='2.5.5.0'、ProductVersion='2.5.5'；`_packaging/setup.iss` MyAppVersion "2.5.5"、OutputBaseFilename=面试点评器_安装程序_v2.5.5、VersionInfoVersion=2.5.5.0、SrcDir 改 dist255。

### Fixed
- **实时态与结束态字段不一致**：任务结束后调用回看时仍返回最后一帧 `live`，未处理题显示「排队中」，现统一为 `skip`；`#bankOptBarWrap` / `#aiOptBarWrap` 增加 Enter / 空格键响应，键盘也能打开实时输出。

### 验证
- 逐题串行实测（本地 mock，3 题）：`max_concurrent_run=1`、`max_ai_inflight=1`、`ai_calls=3`、完成序单调；单题约 1.2s，`raw` 680–700 字逐帧可见。
- 中途停止实测（4 题）：第 1 题完成后立即停止，返回 `state=cancel`、`done=1`、`ok_n=1`、`total=4`、`msg="已停止：成功 1 题，未处理 3 题"`，逐题状态 `['ok','skip','wait','wait']`。
- 页面可达性：`/`、`/mobile`、`static/mobile.js`、`static/app.js`、`static/mobile.css`、`static/style.css` 全部 200；`node --check` 对 `app.js`、`mobile.js` 均通过；前端引用的 DOM id 与 HTML 逐一核对通过（动态创建的 id 已排除）。


---

## [2.6.0] - 2026-11-26

### Added
- **AI 实时返回文字（流式可见）**：`ai_client.py` 的 `_chat` 支持 `stream/on_delta`，`optimize_question` 新增 `on_delta` 参数（`stream=bool(on_delta)`，`max_tokens=9000` 一次成文）；`server.py` 的 `_aiopt_one/_aiopt_run` 逐题透传回调，AI 每吐出一段文字就写入该题 live 快照（`raw` 保留尾部 20000 字、`raw_len`、`streaming=True`），完成时置 `streaming=False` 并附 `after`；`_aiopt_live_view` 改为取 `raw[-raw_max:]` 并回传 `streaming`。电脑端 `static/app.js` 的 `aioLiveBody` 在 `state=run` 时把 AI 原始返回直接铺开（`.aol-stream` + 光标）并自动滚到最新，结束后折叠为 `<details>` 可展开全文，轮询间隔 400 ms（`live=1&rawmax=4000`）；手机端 `static/mobile.js` 的 `bqLiveRow` 同逻辑（`.ml-stream`），弹层打开时 `rawmax=3000`、500 ms 刷新。
- **「开源许可」面板（软件内标明开源项目）**：`server.py` 新增 `GET /api/oss`（26 个第三方组件：名称 / 实测版本 / SPDX 许可证 / 用途 / 上游地址，版本经 `importlib.metadata` 探测，探测不到显示「未锁定」，不写死值）、`GET /api/oss/license/<spdx>`（仅白名单内 7 种许可证可读，防路径穿越）、`POST /api/oss/open`（Windows 打开声明文件所在目录）。`static/index.html` 在「设置 → 关于」新增「开源许可」入口与 `#modalOss` 弹层（关键字筛选、许可证筛选、许可证全文查看、AGPL 组件高亮提示）；`static/mobile.html` 设置页新增「关于与开源许可」卡片，`static/mobile.js` 新增 `mossOpen/mossRender` 复用底部面板展示。
- **随包分发声明文件**：项目根新增 `THIRD_PARTY_NOTICES.md`（26 项组件清单、传递依赖、平台组件、离线模型、PyMuPDF AGPL 说明、certifi MPL-2.0 说明、分发合规义务）与 `LICENSES/`（Apache-2.0 / BSD-3-Clause / MIT / MPL-2.0 / AGPL-3.0 / 0BSD / Zlib 全文）；`_packaging/interview_reviewer.spec` 的 `datas` 增加这两项，绿色版与安装包均随包携带。

### Changed
- **进度条未处理时不显示**：电脑端 `#aiLiveBarWrap`（弹层内进度条）改为仅在已有题目处理完成时显示（`!(total>0 && done>0)` 隐藏），工具栏入口 `#aiOptBarWrap` 仅在运行中或有处理结果时显示；手机端 `#bankOptBarWrap` 同步该规则，任务未开始 / 无任何处理结果时不占位。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.6.0"`，`static/index.html` 三处版本标识与 `static/mobile.html` 关于行升至 v2.6.0；`_packaging/version_info.txt` filevers/prodvers=(2,6,0,0)、FileVersion='2.6.0.0'、ProductVersion='2.6.0'；`_packaging/setup.iss` MyAppVersion "2.6.0"、OutputBaseFilename=面试点评器_安装程序_v2.6.0、VersionInfoVersion=2.6.0.0、SrcDir 改 dist260。

### 验证
- `/api/oss` 返回 `ok=true`、`items=26`、`licenses=[0BSD, AGPL-3.0, Apache-2.0, BSD-3-Clause, MIT, MPL-2.0, Zlib]`，版本探测示例：Flask 3.1.3 / Werkzeug 3.1.9 / requests 2.34.2 / urllib3 2.8.0。
- `/api/oss/license/AGPL-3.0` 返回 34020 字符全文（首行 `GNU AFFERO GENERAL PUBLIC LICENSE`）；`/api/oss/license/MPL-2.0` 返回 16727 字符；不存在的许可证返回 404 且提示「未知许可证」。
- 页面与静态资源：`/`（含 `modalOss` / `btnOpenOss`）、`/mobile`（含 `btnMoss`）、`static/app.js`、`static/mobile.js`、`static/style.css`、`static/mobile.css` 全部 200；`node --check` 对 `app.js`、`mobile.js` 均通过。

## [2.7.0] - 2026-11-27

### Added
- **题库导入模板**：项目根新增 `templates/题库模板.json`（顶层 `name` + `items`，单题字段 `question` / `qtype` / `qtype_reason` / `exam` / `structure` / `keys` / `ref` / `source`，附 `_填写说明` 与两道示例题：社会现象「落叶缓扫」、组织管理「基层治理典型案例」）。`server.py` 新增 `GET /api/bank/template`（`?inline=1` 为页面内预览），模板按源码态 `templates/` 与打包后资源目录两级查找，文件缺失时回退最小内置模板；`_packaging/interview_reviewer.spec` 的 `datas` 收录 `templates` 目录随包分发；`static/index.html` 题库面板与 `static/app.js` 增加「下载模板」入口（`#btnBankTpl`，直接下载 `题库模板.json`）。

### Changed
- **应用图标**：`_packaging/app.ico` 换为与软件风格一致的新图标——白色麦克风 + 右侧声波弧线、青绿渐变（`#22c9b6 → #087c6c`）圆角方块底、新拟态内高光，圆角半径约 23.5%；含 16/20/24/32/40/48/64/96/128/256 共 10 个尺寸，旧图标备份为 `_packaging/app.ico.bak_v260`。exe、安装包、快捷方式与卸载项图标同步。
- **README 核对修正**：段落按钮文案按代码实际为「结构/点评」（原文写作「结构·点评」），并补充「段落右键可直接打开同一菜单」；端口说明按代码实际改写——桌面壳启动时 `pick_port(7788)` 最多顺延 40 个端口，手机端 HTTPS 端口优先取该端口 +1、被占用再顺延，直接 `python server.py` 起服务不顺延、只认 `IR_PORT`（默认 7788）。
- **`_packaging/setup.iss` 改为相对路径**：`SrcDir` 默认 `..\dist\面试点评器`、`OutputDir` 默认 `..\output`、`SetupIconFile=app.ico`，两者均可用 ISCC 命令行 `/DSrcDir=`、`/DOutputDir=` 覆盖，仓库 clone 后可直接编译。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.7.0"`；`static/index.html` 三处版本标识升至 v2.7.0；`_packaging/version_info.txt` filevers/prodvers=(2,7,0,0)、FileVersion='2.7.0.0'、ProductVersion='2.7.0'；`_packaging/setup.iss` MyAppVersion "2.7.0"、OutputBaseFilename=面试点评器_安装程序_v2.7.0、VersionInfoVersion=2.7.0.0。

### Fixed
- **Release 资产文件名**：GitHub Release 会把非 ASCII 资产名 sanitize 成 `_._v2.6.0.exe` 这类乱码短名（API 重命名亦被过滤，只有 ASCII 名能原样保留），故 v2.7.0 安装包资产统一命名为 `InterviewLiveNote_Setup_<版本>.exe`（本地构建产物仍为 `面试点评器_安装程序_<版本>.exe`，installer 内部程序名与快捷方式名不变），v2.6.0 旧资产已同步改名为 `InterviewLiveNote_Setup_v2.6.0.exe`；README「下载安装」段同步说明下载文件名与安装后程序名的差异。

### 验证
- 绿色版实测（PyInstaller onedir）：`/api/appinfo` 返回 `version=2.7.0`、`frozen=true`、`project_url` 正确；`/api/bank/template` 200 / 13048 字节；`/api/mobile/qrcode.png` 200 / image/png；`/api/mobile/info` 的 `https.enabled=true`、证书 `cert_days_left` 正常；`/api/update/check` 返回 `ok=true`、`current=2.7.0`。
- 模板回环：用模板示例题 `POST /api/bank/import` 入库成功（2 题），确认字段与真实题库一致后清理测试数据。

---

---

## [2.8.0] - 2026-11-28

### Added
- **本地功能适配更新机制**：新增 `adapt_rules.py` 与 `adapt/` 规则包。把「题目识别规则、题型答题框架、点评方式与常见问题、段落环节识别、文字处理词表、笔记取题规则、真题条目」全部抽成独立的 JSON 规则包，随包分发一份内置版本（`adapt/manifest.json` + 7 个分节文件），用户可单独导入新版规则包即时生效，**无需重新安装整个软件**。生效目录 `%LOCALAPPDATA%\InterviewReviewer\data\adapt\current`，导入前自动备份到 `backup`，历史包保留最近 5 个在 `packs`。
- **规则包校验与接口**：`boot()` / `status()` / `section()` / `reload()` / `import_pack()` / `reset()` / `export_pack()`；`GET /api/adapt/status`（版本、来源、各分节条数）、`GET /api/adapt/section?name=`（单节内容）、`POST /api/adapt/reload`、`POST /api/adapt/import`（zip 上传）、`GET /api/adapt/export`、`POST /api/adapt/reset`、`GET /api/adapt/framework?qtype=`、`GET /api/adapt/comment` 等。安全约束：白名单分节文件、单文件 ≤4MB、总解压 ≤32MB、zip 条目 ≤2000、拒绝绝对路径与 `../` 越界、正则预编译校验，任一校验失败整包拒绝。
- **笔记一键取题**：`POST /api/adapt/extract` 按适配的 `bank_parse` 规则从笔记/题本/逐字稿里抽题（题干 + 出处 + 题型 + 答题结构 + 参考答案），前端「设置 → 本地适配规则 → 从笔记取题」粘贴文本即可预览、勾选，`POST /api/adapt/bank_import` 批量入库。
- **设置面板第三页「本地适配规则」**：`static/index.html` 新增 `#setPaneAdapt`，与「基础 / 题型」并列；`static/app.js` 新增 `adpInit()` / `adpLoad()` / `adpRenderSections()` / `adpImport()` / `adpRenderItems()`，支持查看当前规则包版本与各分节条数、导入 `.zip` 规则包、导出当前规则、恢复内置、重新加载、粘贴笔记取题并勾选入库；`static/style.css` 补齐对应样式。

### Changed
- **本地解析 / 点评 / 文字处理全部改为读适配规则**：`question_analyzer.py`（题型判定取值、题型别名、题干预清洗、关键词加分改由 `detect_bonus()` 提供，低于阈值不判型）、`local_analyzer.py`（填空题、结构词、环节名走规则）、`corrector.py`（本地纠错词表 `fix_pairs()`、口头禅 `filler_words()`）、`paragraphizer.py`（环节标签走 `segment_rules()`）、`server.py`（`boot()` 启动同步内置规则、新增 8 个 `/api/adapt/*` 接口）。
- **版式**：`_packaging/interview_reviewer.spec` 的 `datas` 收录 `adapt` 目录，绿色版与安装包均随包携带内置规则包。
- **版本**：`server.py`、`launcher.py` 的 `APP_VERSION="2.8.0"`；`static/index.html` 三处版本标识升至 v2.8.0；`_packaging/version_info.txt` filevers/prodvers=(2,8,0,0)、FileVersion='2.8.0.0'、ProductVersion='2.8.0'；`_packaging/setup.iss` MyAppVersion "2.8.0"、OutputBaseFilename=面试点评器_安装程序_v2.8.0、VersionInfoVersion=2.8.0.0。

### 验证
- 规则包热更新实测：`GET /api/adapt/status` 初值 `version=2026.11.28, source=builtin`；导出 → 改 manifest 版本为 `2026.12.01` → `POST /api/adapt/import` 成功 → status 变为 `2026.12.01`、`has_user_pack=true`、`packs=1`；`POST /api/adapt/reset` 回到 `2026.11.28/builtin`。
- 取题实测：对 20 份笔记/题本共抽出 597 条，其中《2026年结构化新人培训题本（题干版+出处）》抽出 42 条，出处与答题结构自动补齐；`POST /api/adapt/bank_import` 入新建题库成功。
