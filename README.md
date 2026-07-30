# 📅 每日计划机

一个带 AI 助手的桌面学习/工作计划管理器。在日历上按天组织待办事项，连接到 DeepSeek / OpenAI 大模型分析你的计划——AI 会预览计划中的论文链接、复盘昨日工作、播报当日天气。

![界面示意：左侧日历 + 图片装饰，右侧计划列表 + Agent 面板](docs/screenshot.png)

## 主要功能

### 日常工作流

启动应用后自动加载今天的计划，计划中包含的 URL 会一键在浏览器中打开（可关闭）。键入新计划、勾选完成、拖拽排序——所有改动即时自动保存。

### 🚀 自动启动配置

可配置启动时自动打开的文件夹和程序（如编辑器、浏览器），支持自启动递归防护——不会把自身加入自动启动列表。

### ⚙️ 可配置

所有设置（API 提供商、模型、System Prompt、存储路径、自动启动项）都可通过 UI 中的设置对话框直接修改，无需手动编辑配置文件。

### 🤖 AI 计划分析

点击"将今日计划发送给 agent"，后台执行以下步骤后把完整上下文发送给大模型：

1. **提取计划中的 URL**，自动抓取内容
2. **获取当日天气**（通过 wttr.in，免 API Key）
3. **加载昨天计划**
4. **加载其他日期的计划**作为长期上下文参考

收到 AI 回复后显示在聊天面板中。初次分析后可以**继续追问**——Agent 会记住对话上下文，支持多轮深入讨论。Agent 还会自动引用**记忆库**中的历史日记、计划和对话，记忆范围可调（本周 / 近两周 / 近一月 / 全部）。

### 📄 论文阅读辅助

Agent 会：
- 论文只取 Abstract + Introduction，普通网页取正文前 5000 字
- 自动识别链接类型，PDF 链接回退到 Abstract 页抓取
- HTML 版论文自动定位 Abstract 和 Introduction 章节
- 超出长度自动截断，防止 token 浪费

### 🔍 网页搜索

搜索栏可直接输入关键词，通过 DuckDuckGo 搜索结果摘要，不依赖任何 API Key。

### 📎 浏览器标签页抓取

一键抓取 Chrome / Edge / Firefox 当前打开的一个标签页 URL，自动加入今日计划——把浏览器变成你的"待读列表"收集器。基于 Windows UI Automation 实现，无需浏览器插件。

### 📝 日记

每个日期附带一个独立的日记入口，每次保存自动加盖时间戳追加到文件顶部

### 🖼️ 图片装饰

左下角可导入任意图片，启动时从 `图片/` 目录随机展示，给学习空间来点个性化的背景。

---

## 代码结构

```
study-launcher/
├── study-gui/                # 应用源码（PyQt5）
│   ├── main.py               # 主窗口：UI 构建、计划编辑、Agent 交互、自动启动
│   ├── agent.py              # AI Agent：LLM 调用、网页抓取、天气获取、搜索
│   ├── plan_store.py         # 数据层：按日期读写 JSON 计划文件
│   ├── memory_store.py       # 记忆库：SQLite 存储日记/计划/对话，支持按周查询
│   ├── calendar_view.py      # 日历控件：继承 QCalendarWidget，有计划的日期画圆点
│   └── settings_dialog.py    # 设置对话框：编辑 config.yaml 的图形界面
├── config.yaml               # 入口配置（API Key、模型、System Prompt、记忆库、自动启动）
├── build.py                  # PyInstaller 打包脚本（单文件夹模式）
├── plans/                    # 计划数据（每天一个 .json，自动创建）
├── 日记/                     # 日记文件（每天一个 .txt，自动创建）
├── 图片/                     # 图片装饰目录（随机选择展示）
├── 记忆库/                   # Agent 记忆数据库（SQLite，自动创建）
└── dist/                     # 打包输出目录
```

### 模块详解

#### [main.py](study-gui/main.py) — 主窗口

`MainWindow` 继承 `QMainWindow`，是整个应用的入口和 UI 控制器。约 1800 行，分为以下几个区域：

| 区域 | 行号范围 | 职责 |
|------|----------|------|
| 浏览器标签抓取 | 60–146 | PowerShell + UI Automation 获取 Chrome/Edge/Firefox 标签页 URL |
| 自定义计划列表 | 152–343 | `PlanItemDelegate` 绘制序号/文本/勾选框，`PlanListWidget` 处理拖拽排序、长按多选、右侧点击勾选 |
| UI 构建 | 395–591 | 布局左右分栏（日历 + Agent），包含 QSplitter、输入框、按钮、日记面板 |
| 主题系统 | 596–1051 | 亮色/暗色双主题（Arco Design 风格），通过 `setStyleSheet` 切换 |
| 日期选择 | 1053–1101 | 日历点击 → 加载当日计划，刷新日历圆点标记 |
| 计划编辑 | 1113–1204 | 添加、保存、删除、浏览器标签抓取的全套 CRUD |
| 日记功能 | 1220–1244 | 日记加载和保存，带时间戳追加 |
| Agent 交互 | 1250–1510 | 聊天式多轮对话：初次发送计划 → AI 回复 → 追问输入 → 记忆范围切换 → 聊天记录管理 |
| 辅助功能 | 1460–1523 | 随机图片加载、图片导入、状态栏消息 |
| 自启动防护 | 1525–1610 | 多层防御：环境变量 + 标记文件，检测自身 spawn 的子进程 |
| 入口 | 1613–1627 | `main()` 创建 `QApplication` 并启动窗口 |

**关键设计决策：**

- **计划保存完全透明**——勾选、拖拽排序、修改文本、删除任一操作后立即自动保存，不存在"忘记保存"的问题
- **跨线程通信**使用 PyQt5 的 `pyqtSignal`（而非手动 `QMetaObject.invokeMethod`），后台工作线程 emit 信号，主线程 slot 更新 UI
- **搜索和 AI 分析使用独立信号**（`_search_response`、`_ai_response`、`_chat_response`），避免多种操作同时进行时按钮状态互相干扰
- **多轮对话**通过 `_conversation` 列表维护完整消息历史，初次发送计划时重置，追问时累积。记忆上下文以结构化文本注入用户消息（不污染 system prompt），LLM 可引用历史日记/计划/对话
- **自启动递归防护三层**：环境变量检测 → 标记文件（10 秒窗口）→ 路径比对（同目录同 exe 名、stem 包含"每日计划机"），防止 `auto_launch` 把程序自身拉起来造成无限递归
- 计划加载期间设置 `_loading` 标志位，防止 `itemChanged` 信号触发不必要的 auto-save

#### [agent.py](study-gui/agent.py) — AI Agent

`StudyAgent` 类是 Python 端的"工具层"——在发送给 LLM 之前完成所有数据预处理。约 460 行。

| 方法 | 行号 | 功能 |
|------|------|------|
| `__init__` / `reload_config` | 21–34 | 加载 YAML 配置，支持运行时刷新 |
| `extract_urls` | 43–46 | 正则提取 URL（排除中文标点，避免 URL 边界粘连） |
| `strip_urls` | 48–51 | 从文本中移除所有 URL |
| `parse_plan_items` | 53–79 | 解析计划列表，返回去重的 URL 列表和非链接文本行 |
| `fetch_url` | 89–120 | 统一入口：识别 arxiv abs/html/pdf 格式，分发到对应抓取方法 |
| `_fetch_generic_page` | 122–138 | 通用网页：去噪（移除 script/nav/footer 等标签），优先取 `<article>`/`<main>` 区域 |
| `_fetch_paper_abstract` | 140–155 | arxiv abs 页：提取 `<blockquote class="abstract">` |
| `_fetch_paper_sections` | 157–225 | arxiv HTML 全文：定位 Abstract + Introduction 章节，按 h2/h3 标题切分并收集兄弟节点文本 |
| `search_web` | 227–261 | DuckDuckGo HTML 版搜索（POST `html.duckduckgo.com/html/`），解析结果摘要 |
| `fetch_weather` | 263–308 | wttr.in JSON API 天气获取，包含当前实时 + 逐时预报，重点标注 10-12 和 16-18 时 |
| `call_llm` | 312–396 | 单轮异步 LLM 调用：后台线程抓取 URL → 拼装 prompt → POST API，通过回调通知结果 |
| `call_llm_messages` | 398–462 | 多轮异步 LLM 调用：直接接受完整 messages 列表（含历史），不做 URL 抓取或 system prompt 注入 |

**关键设计决策：**

- 所有网页抓取方法都是 `@classmethod`，无状态——Agent 配置（API Key 等）只在 `call_llm` 中使用实例属性，数据抓取不依赖任何实例状态
- arxiv 论文智能截断：Abstract 最多保留 3000 字符，Introduction 最多 4000 字符，远超内容自动标记 `(已截断)`
- HTML 版不可用时自动回退到 abs 页抓取（`_fetch_paper_sections` 的 `except` 分支）
- `call_llm` 设计为 fire-and-forget（`threading.Thread(daemon=True)`），调用方通过回调接收结果，不阻塞主线程 UI
- `call_llm_messages` 与 `call_llm` 共享底层 API 调用逻辑但职责分离：前者接受完整 messages 列表（多轮对话用），后者自动注入 system prompt 和 URL 内容（首次发送用）

#### [plan_store.py](study-gui/plan_store.py) — 数据存储

`PlanStore` 是最薄的一层，约 64 行。职责单一：按日期读写 JSON 文件。

```python
store = PlanStore("./plans")
store.save("2026-07-30", [{"text": "读 Attention Is All You Need", "done": False}])
data = store.load("2026-07-30")
# {"date": "2026-07-30", "items": [{"text": "...", "done": false}]}
```

**向后兼容：** `load()` 检测旧格式（items 是字符串列表而非对象列表），自动转换为 `{"text": t, "done": False}`。

#### [memory_store.py](study-gui/memory_store.py) — Agent 记忆库

`MemoryStore` 是记忆系统的存储层，约 230 行。单表 SQLite 设计（WAL 模式），以 `kind` 列区分记录类型（`diary` / `plan` / `chat_user` / `chat_assistant`），避免多表 UNION 查询。

| 方法 | 功能 |
|------|------|
| `save_diary(date, content)` | 保存日记快照（upsert，同一日期覆盖） |
| `save_plan(date, items)` | 保存计划快照（upsert，items 序列化为 JSON） |
| `save_conversation(date, role, content)` | 追加对话记录 |
| `get_memories(since_date)` | 获取指定日期以来的全部记忆，拼接为结构化文本（~2500 字上限） |
| `get_diaries(since_date)` / `get_plans(since_date)` | 按类型单独查询 |
| `sync_all_diaries(diary_dir)` | 批量同步 `日记/` 目录下所有 txt 到数据库 |
| `sync_all_plans(plan_store)` | 批量同步 `plans/` 目录下所有 json 到数据库 |
| `clean_old_conversations(days)` | 清理超过保留期的对话记录 |

**关键设计：** `get_memories()` 返回的文本按日期分组、按种类分段（📝 日记 / 📋 计划 / 💬 对话），直接注入用户消息末尾供 LLM 引用。记忆范围通过组合框切换（本周 / 近两周 / 近一月 / 全部），底层转换为一周起始日期传入 `since_date` 参数。

#### [calendar_view.py](study-gui/calendar_view.py) — 日历控件

`PlanCalendar` 继承 `QCalendarWidget`，重写 `paintCell()` 在有计划的日期下方绘制蓝色（或暗色主题下的浅蓝）圆点标记。

**关键设计：** 日历不持有计划数据，只接收 `set_plan_dates(Set[str])` 设置需要标记的日期集合。数据获取由 `MainWindow._refresh_calendar_marks()` 通过 `PlanStore.get_dates_with_plans()` 触发。

#### [settings_dialog.py](study-gui/settings_dialog.py) — 设置对话框

`SettingsDialog` 继承 `QDialog`，分四个选项卡：

| 选项卡 | 配置项 |
|--------|--------|
| 🤖 大模型 | provider、API Key、Base URL、model、timeout、max_tokens、temperature |
| 📝 Agent | System Prompt（多行编辑）、计划存储目录、记忆库路径/默认范围/对话保留天数 |
| 🚀 自动启动 | 文件夹列表、程序列表、开关 toggle |

保存时自动校验：检测程序列表中的路径是否指向自身（同路径、同目录同 exe 名、stem 包含"每日计划机"），防止自启动递归。

#### [config.yaml](config.yaml) — 配置文件

应用的唯一运行态配置入口。六大块：

- **`llm`** — LLM API 连接参数
- **`agent`** — system prompt（定义 Agent 的行为：预览链接、复盘昨天、报告天气）
- **`storage`** — 计划数据目录
- **`memory`** — 记忆库路径、默认记忆范围、对话保留天数
- **`autolaunch`** — 启动行为（是否打开链接、是否自动启动程序/文件夹）
- 打包后随 `dist/` 分发，用户可直接编辑或通过设置界面修改

#### [build.py](build.py) — 打包脚本

PyInstaller 单文件夹打包，将 `study-gui/main.py` 作为入口，打包为 `每日计划机.exe`，输出到 `dist/每日计划机/`。配置文件和运行时目录（plans、日记、图片）复制到 dist 目录下供分发。

---

## 快速开始

### 开发者模式

```bash
pip install PyQt5 pyyaml requests beautifulsoup4
# 编辑 config.yaml，填入 API Key
python study-gui/main.py
```

### 一键启动（PowerShell）

```powershell
powershell -ExecutionPolicy Bypass -File launcher.ps1
```
（右键 launcher.ps1，使用powershell运行）
脚本会自动检测 PATH 中的 `python` / `python3`，然后启动 `study-gui/main.py`。

### 打包分发

```bash
python build.py
# → dist/每日计划机/ 文件夹，压缩后发给朋友
# 朋友解压后编辑 config.yaml 填入 API Key，双击 每日计划机.exe
```

## 系统要求

- Windows 10 / 11
- Python 3.10+（开发者模式）
- 浏览器标签抓取依赖 Windows UI Automation，仅 Windows 可用
- 天气功能需要网络访问 `wttr.in`，搜索需要访问 `html.duckduckgo.com`

## License

MIT
