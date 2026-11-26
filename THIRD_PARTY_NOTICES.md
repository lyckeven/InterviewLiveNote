# 面试点评器（InterviewLiveNote）第三方开源组件声明

> 本文件列出「面试点评器」在运行与分发过程中使用的第三方开源组件、其版权归属与许可证条款。
> 作者：慕夕制作　| 声明版本：v2.6.0　| 最后更新：2026-11-26
>
> 分发本软件时，**必须**将本文件与 `LICENSES/` 目录一并随附（安装包与绿色版均已包含）。
> 各许可证全文见 `LICENSES/<SPDX 标识>.txt`，软件内也可通过「设置 → 关于 → 开源许可」查看。

---

## 一、运行时直接依赖

| 组件 | 版本 | 许可证（SPDX） | 在本软件中的用途 | 上游地址 |
| --- | --- | --- | --- | --- |
| Flask | 3.1.3 | BSD-3-Clause | 本机 HTTP 服务（仅监听 127.0.0.1 / 局域网手机接入） | https://github.com/pallets/flask |
| Werkzeug | 3.1.9 | BSD-3-Clause | Flask 的 WSGI 实现与静态资源服务 | https://github.com/pallets/werkzeug |
| Jinja2 | 未锁定 | BSD-3-Clause | Flask 模板渲染引擎 | https://github.com/pallets/jinja |
| MarkupSafe | 3.0.4 | BSD-3-Clause | Jinja2 的 HTML 转义依赖 | https://github.com/pallets/markupsafe |
| click | 8.5.1 | BSD-3-Clause | Flask 命令行入口依赖 | https://github.com/pallets/click |
| itsdangerous | 2.2.0 | BSD-3-Clause | Flask 会话签名依赖 | https://github.com/pallets/itsdangerous |
| requests | 未锁定 | Apache-2.0 | 调用 AI 服务接口（OpenAI 兼容协议）、查询余额 | https://github.com/psf/requests |
| urllib3 | 未锁定 | MIT | requests 的 HTTP 连接池实现 | https://github.com/urllib3/urllib3 |
| certifi | 未锁定 | MPL-2.0 | requests 使用的 CA 根证书包 | https://github.com/certifi/python-certifi |
| charset-normalizer | 未锁定 | MIT | requests 的响应编码识别 | https://github.com/jawah/charset_normalizer |
| idna | 未锁定 | BSD-3-Clause | requests 的国际化域名支持 | https://github.com/kjd/idna |
| numpy | 2.3.5 | BSD-3-Clause（内含 0BSD / Zlib / MIT / CC0 片段） | 音频数据缓冲与重采样 | https://numpy.org |
| scipy | 未锁定 | BSD-3-Clause | 高质量音频重采样（`scipy.signal.resample_poly`） | https://scipy.org |
| sherpa-onnx / sherpa-onnx-core | 1.12.19 | Apache-2.0 | 本地离线语音识别引擎（ASR、VAD、标点） | https://github.com/k2-fsa/sherpa-onnx |
| PyAudioWPatch | 未锁定 | MIT（PortAudio 为 MIT） | WASAPI 环回录音（抓取扬声器声音） | https://github.com/s0d3s/PyAudioWPatch |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | 生成手机接入用的本地自签 HTTPS 证书 | https://github.com/pyca/cryptography |
| qrcode | 8.2 | BSD | 生成手机接入二维码 | https://github.com/lincolnloop/python-qrcode |
| python-docx | 未锁定 | MIT | 导出 Word 格式的点评稿 / 备课稿 | https://github.com/python-openxml/python-docx |
| lxml | 未锁定 | BSD-3-Clause | python-docx 的 XML 处理依赖 | https://lxml.de |
| **PyMuPDF（MuPDF）** | 未锁定 | **AGPL-3.0**（或 Artifex 商业许可） | 导入 PDF 题目时提取文字层 | https://github.com/pymupdf/PyMuPDF |
| pywebview | 6.2.1 | BSD-3-Clause | 桌面窗口外壳（内嵌 WebView2 显示界面） | https://github.com/r0x0r/pywebview |
| pythonnet | 3.2.1 | MIT | pywebview 在 Windows 上调用 .NET / WebView2 | https://github.com/pythonnet/pythonnet |
| clr-loader | 0.3.1 | MIT | pythonnet 的 .NET 运行时加载器 | https://github.com/pythonnet/clr-loader |

## 二、随包收集的传递依赖

以下组件由上述依赖间接引入，随打包一起收集，未在本项目代码中直接调用：

| 组件 | 许可证（SPDX） | 说明 |
| --- | --- | --- |
| PyYAML（`yaml`） | MIT | sherpa-onnx 等组件读取配置文件 |
| psutil | BSD-3-Clause | 进程 / 系统信息查询 |
| Pillow（PIL） | MIT-CMU | qrcode 生成 PNG 二维码时的图像后端 |
| setuptools / pkg_resources | MIT | Python 打包运行时支持 |
| Python 解释器 3.11 | PSF-2.0 | 软件运行环境（Python Software Foundation License） |

## 三、运行平台组件（非本仓库分发）

| 组件 | 提供方 | 说明 |
| --- | --- | --- |
| Microsoft Edge WebView2 Runtime | Microsoft | 界面渲染所依赖的系统级运行时组件，随 Windows 10/11 预装或由用户系统更新维护，不随本仓库分发，受 Microsoft 许可约束。 |
| Windows 防火墙 / 系统 API | Microsoft | 通过系统命令调用的系统能力，不随本仓库分发。 |

## 四、离线语音模型（用户自备，不随仓库分发）

离线识别模型体积较大，未打入本仓库与安装包，由用户按 README「离线模型」说明自行下载放置；**这些模型不适用本项目的许可，其许可证以各模型发布方为准**：

| 模型 | 典型来源 | 许可证提示 |
| --- | --- | --- |
| Paraformer 中文识别模型（`model.int8.onnx` / `model.onnx` + `tokens.txt`） | sherpa-onnx 官方模型库（k2-fsa） | 以模型页面标注为准（多为 Apache-2.0，商用前请自行确认） |
| Silero VAD（`silero_vad.onnx`） | snakers4/silero-vad | MIT |
| 中文标点恢复模型（`punct/model.onnx`，CT-Transformer） | sherpa-onnx 官方模型库 | 以模型页面标注为准 |

## 五、需要特别说明的三点

### 1. PyMuPDF 使用 AGPL-3.0

PyMuPDF（含其底层 MuPDF）由 Artifex Software 以 **AGPL-3.0 或商业许可** 双轨发布。本项目仅在「导入 PDF 题目」时用它提取文字层，但仍属于 AGPL 意义上的「结合使用」。分发二进制时，若选择 AGPL 路径，须履行 AGPL-3.0 第 5、6、13 条义务：随分发提供完整对应源码、保留版权与许可声明。若无法满足，应改用宽松许可的替代方案（如 `pypdfium2`，Apache-2.0/BSD-3-Clause）或向 Artifex 购买商业许可。

### 2. certifi 使用 MPL-2.0

MPL-2.0 是文件级弱著佐权：可自由分发，但不得修改 certifi 的源文件后再以其他许可发布。本项目未修改 certifi 任何文件，仅原样随包分发其证书数据。

### 3. AI 接口与在线服务

AI 点评 / 题目解析能力由用户自备的第三方大模型接口提供（OpenAI 兼容协议，如 DeepSeek 等）。该接口的调用费用、数据流向与使用条款由对应服务商规定，与本项目许可证无关。本软件不内置任何 API Key，也不向作者服务器回传用户数据。

## 六、分发合规义务清单

分发「面试点评器」的二进制包（安装程序 / 绿色版）时，请确认：

- [x] 安装目录内随附本文件 `THIRD_PARTY_NOTICES.md`
- [x] 安装目录内随附 `LICENSES/` 目录及各许可证全文
- [x] 保留 Flask / Werkzeug / cryptography 等 Apache-2.0 组件的版权声明（见各 `LICENSES/*.txt`）
- [ ] 若采用 AGPL 路径分发 PyMuPDF：需同时提供完整对应源码（本项目已提供源码包 `面试点评器_源码_vX.Y.Z.zip`），或在软件内提供源码获取方式
- [ ] 若改为商业闭源分发：须替换 PyMuPDF 为宽松许可的替代库，或购买 Artifex 商业许可

## 七、变更记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-11-26 | v2.6.0 | 首次建立开源组件声明：新增本文件、`LICENSES/` 目录、软件内「开源许可」面板 |
