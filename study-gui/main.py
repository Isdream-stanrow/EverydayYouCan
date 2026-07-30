"""
学习计划管理器 - 主窗口
┌──────────────────────────────────────────┐
│  📅 学习计划管理器                        │
├──────────────┬───────────────────────────┤
│              │  选定日期: 2026-07-27      │
│  [日历组件]  │  ┌─────────────────────┐  │
│              │  │ 计划列表 (可编辑)    │  │
│              │  │ □ 项目1             │  │
│              │  │ □ 项目2             │  │
│              │  └─────────────────────┘  │
│              │  [新计划输入____] [+添加]  │
│              │  [💾 保存] [🗑 删除]       │
│              │  ───── Agent ─────        │
│              │  [AI 建议输出区域]        │
│              │  [🤖 发送给 AI] [🔗 打开链接]│
└──────────────┴───────────────────────────┘
"""
import os
import sys
import subprocess
import random
from pathlib import Path
from datetime import date, datetime, timedelta

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QLabel,
    QSplitter,
    QGroupBox,
    QFileDialog,
    QFrame,
    QStyledItemDelegate,
    QAbstractItemView,
    QStyle,
    QComboBox,
)
from PyQt5.QtCore import Qt, QTimer, QEvent, pyqtSignal, QDate, QPointF, QRectF, QSize, QItemSelection, QItemSelectionModel
from PyQt5.QtGui import (
    QFont, QColor, QPixmap,
    QPainter, QPen,
)

from calendar_view import PlanCalendar
from plan_store import PlanStore
from agent import StudyAgent
from memory_store import MemoryStore
from settings_dialog import SettingsDialog


# ── 浏览器标签页抓取（Win32 EnumWindows → UIA FromHandle → Edit.Value）──

PS_GRAB_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'

# ── Win32 API 声明 ──
$code = @'
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
[DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, System.Text.StringBuilder lpClassName, int nMaxCount);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
'@
Add-Type -MemberDefinition $code -Name WinAPI -Namespace WinNS -ErrorAction SilentlyContinue
[void][System.Reflection.Assembly]::LoadWithPartialName('UIAutomationClient')

# ── 步骤 1：Win32 EnumWindows 找出浏览器窗口句柄 ──
$handles = New-Object 'System.Collections.Generic.List[IntPtr]'
$cb = [WinNS.WinAPI+EnumWindowsProc]{
    param($h, $l)
    if ([WinNS.WinAPI]::IsWindowVisible($h)) {
        $cb2 = New-Object System.Text.StringBuilder(256)
        [WinNS.WinAPI]::GetClassName($h, $cb2, 256) | Out-Null
        if ($cb2.ToString() -ne 'Chrome_WidgetWin_1') { return $true }
        $procId = 0
        [WinNS.WinAPI]::GetWindowThreadProcessId($h, [ref]$procId) | Out-Null
        if ($procId -eq 0) { return $true }
        try {
            $pn = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            if ($pn -match '^(msedge|chrome|firefox)$') { $handles.Add($h) }
        } catch {}
    }
    return $true
}
[WinNS.WinAPI]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null

# ── 步骤 2：UIA FromHandle → 找 Edit 控件 → 取 Value ──
$editCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit
)
$seen = @{}
foreach ($h in $handles) {
    $el = try { [System.Windows.Automation.AutomationElement]::FromHandle($h) } catch { $null }
    if (-not $el) { continue }
    # 先扫顶层 Edit，再扫 ToolBar 子 Edit（Chromium 地址栏在 ToolBar 里）
    $edits = @(try { $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond) } catch { @() })
    foreach ($e in $edits) {
        try {
            $vp = $e.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            $v = $vp.Current.Value
            if ($v -and $v -match '^https?://[^\s]{4,}' -and -not $seen[$v]) {
                $seen[$v] = $true
                Write-Output $v
            }
        } catch {}
    }
}

# ── 回退：UIA 没拿到就去 CDP ──
if ($seen.Count -eq 0) {
    foreach ($port in @(9222, 9229, 9221, 9220)) {
        try {
            $resp = Invoke-RestMethod "http://localhost:$port/json" -TimeoutSec 2
            foreach ($tab in $resp) {
                if ($tab.url -match '^https?://' -and $tab.type -eq 'page' -and -not $seen[$tab.url]) {
                    $seen[$tab.url] = $true
                    Write-Output $tab.url
                }
            }
        } catch {}
    }
}
"""


def _capture_browser_urls(timeout: int = 15) -> list:
    """通过 PowerShell UI Automation 抓取 Chrome/Edge/Firefox 当前标签页 URL"""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", PS_GRAB_SCRIPT],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        urls = [ln.strip() for ln in p.stdout.splitlines() if ln.strip().startswith("http")]
        return urls
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
#  自定义计划列表（序号 | 文本 | 右侧勾选 + 序号拖拽 + 长按多选）
# ═══════════════════════════════════════════════════════════════════

class PlanItemDelegate(QStyledItemDelegate):
    """绘制每行：序号 → 文本 → 右侧勾选框"""
    NUM_W = 40
    CHK_W = 28

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # 从父控件获取主题状态
        w = self.parent()
        dark = w.property("dark_mode") if w else False

        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        selected = option.state & QStyle.State_Selected
        hover = option.state & QStyle.State_MouseOver
        row = index.row()
        r = option.rect

        # 背景
        if dark:
            if checked:
                bg = QColor("#1a3a24")
            elif selected:
                bg = QColor("#2a3a5c")
            elif hover:
                bg = QColor("#303050")
            elif row % 2 == 0:
                bg = QColor("#252540")
            else:
                bg = QColor("#2a2a40")
            num_color = QColor("#6a6a8a")
            text_color = QColor("#e0e0e0")
            sep_color = QColor("#3a3a5c")
        else:
            if checked:
                bg = QColor("#d4edda")
            elif selected:
                bg = QColor("#e8f3ff")
            elif hover:
                bg = QColor("#f2f3f5")
            elif row % 2 == 0:
                bg = QColor("#ffffff")
            else:
                bg = QColor("#f8f9fa")
            num_color = QColor("#86909c")
            text_color = QColor("#1d2129")
            sep_color = QColor("#e5e6eb")
        painter.fillRect(r, bg)

        # 序号（左侧 40px）
        num_rect = QRectF(r.left() + 4, r.top(), self.NUM_W - 4, r.height())
        f_num = painter.font(); f_num.setPixelSize(12)
        painter.setFont(f_num)
        painter.setPen(num_color)
        painter.drawText(num_rect, Qt.AlignCenter, str(row + 1))

        # 分隔线
        sx = r.left() + self.NUM_W
        painter.setPen(QPen(sep_color, 1))
        painter.drawLine(sx, r.top() + 8, sx, r.bottom() - 8)

        # 文本
        text = index.data(Qt.DisplayRole) or ""
        tx = r.left() + self.NUM_W + 10
        tw = r.width() - self.NUM_W - self.CHK_W - 14
        text_rect = QRectF(tx, r.top(), tw, r.height())
        f_txt = painter.font(); f_txt.setPixelSize(13)
        painter.setFont(f_txt)
        painter.setPen(text_color)
        elided = painter.fontMetrics().elidedText(text, Qt.ElideRight, int(tw))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

        # 右侧勾选框
        cb = self._chk_rect(r)
        if checked:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#00b42a"))
            painter.drawRoundedRect(cb, 3, 3)
            painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cx, cy = cb.center().x(), cb.center().y()
            painter.drawLine(QPointF(cx - 3, cy), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx, cy + 4), QPointF(cx + 4, cy - 3))
        else:
            painter.setPen(QPen(QColor("#c9cdd4") if not dark else QColor("#5a5a7a"), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(cb.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)

        painter.restore()

    def _chk_rect(self, rect):
        s = 16
        return QRectF(rect.right() - 24, rect.top() + (rect.height() - s) / 2.0, s, s)

    def sizeHint(self, option, index):
        return QSize(200, 38)


class PlanListWidget(QListWidget):
    """序号列拖拽排序 + 长按多选 + 右侧点击勾选"""
    _check_toggled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setFrameShape(QListWidget.NoFrame)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

        self._delegate = PlanItemDelegate(self)
        self.setItemDelegate(self._delegate)

        self._number_zone = False
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._start_multi_select)
        self._multi_selecting = False
        self._multi_anchor = None

    def _zone(self, pos, rect=None):
        """x 坐标命中区域: number | checkbox | text | none"""
        if rect is None:
            idx = self.indexAt(pos)
            if not idx.isValid():
                return 'none'
            rect = self.visualRect(idx)
        x = pos.x() - rect.left()
        if x < self._delegate.NUM_W:
            return 'number'
        if x > rect.width() - self._delegate.CHK_W:
            return 'checkbox'
        return 'text'

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        pos = event.pos()
        zone = self._zone(pos)
        idx = self.indexAt(pos)

        if zone == 'checkbox' and idx.isValid():
            item = self.itemFromIndex(idx)
            ns = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            item.setCheckState(ns)
            return  # 不切换选中，itemChanged 自动触发保存+重绘

        self._number_zone = (zone == 'number')

        if zone == 'text' and idx.isValid():
            self._long_press_timer.start(300)

        super().mousePressEvent(event)
        self._drag_start = pos

    def mouseMoveEvent(self, event):
        if self._multi_selecting:
            idx = self.indexAt(event.pos())
            if idx.isValid() and self._multi_anchor:
                sel = self.selectionModel()
                sel.select(QItemSelection(self._multi_anchor, idx),
                           QItemSelectionModel.ClearAndSelect)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._long_press_timer.stop()
        was_multi = self._multi_selecting
        self._multi_selecting = False
        self._multi_anchor = None
        self._number_zone = False
        super().mouseReleaseEvent(event)
        if was_multi:
            self.setSelectionMode(QAbstractItemView.SingleSelection)

    def startDrag(self, actions):
        if self._number_zone:
            super().startDrag(actions)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.viewport().update()  # 重绘序号
        self._check_toggled.emit()  # 触发 auto-save

    def _start_multi_select(self):
        self._multi_selecting = True
        self.setSelectionMode(QAbstractItemView.MultiSelection)
        idx = self.currentIndex()
        if idx.isValid():
            self._multi_anchor = idx


class MainWindow(QMainWindow):
    """主窗口"""

    # 跨线程信号：后台线程 emit → 主线程处理
    _ai_response = pyqtSignal(str)
    _ai_error = pyqtSignal(str)
    _ai_progress = pyqtSignal(str)
    _search_response = pyqtSignal(str)  # 搜索专用，避免与 AI 响应混淆
    _chat_response = pyqtSignal(str)    # 多轮追问专用
    _chat_error = pyqtSignal(str)

    def __init__(self, project_dir: str):
        super().__init__()

        self.project_dir = Path(project_dir)
        self.config_path = self.project_dir / "config.yaml"
        self.storage_config = None

        # 初始化组件
        self.plan_store = None  # 延迟初始化，需要知道 plans_dir
        self.agent = StudyAgent(str(self.config_path))
        self._init_plan_store()
        self._init_memory_store()

        # 当前选中的日期
        self._current_date = date.today().isoformat()
        self._loading = False  # 防止加载时触发 auto-save
        self._dark = False     # 主题状态

        # 多轮对话状态
        self._conversation: list = []  # OpenAI 格式 messages
        self._memory_store = None      # 延迟初始化
        self._memory_range = "本周"    # 默认记忆范围

        # 构建 UI
        self._init_ui()
        self._apply_theme()
        self._load_random_image()

        # 信号连接
        self._ai_response.connect(self._on_ai_response)
        self._ai_error.connect(self._on_ai_error)
        self._ai_progress.connect(self._on_ai_progress)
        self._search_response.connect(self._on_search_response)
        self._chat_response.connect(self._on_chat_response)
        self._chat_error.connect(self._on_chat_error)

        # 启动时加载今天的计划并打开链接
        QTimer.singleShot(500, self._startup_routine)

    def _init_plan_store(self):
        """根据 config 初始化 plan_store"""
        storage_cfg = self.agent.config.get("storage", {})
        plans_dir = storage_cfg.get("plans_dir", "./plans")
        if not Path(plans_dir).is_absolute():
            plans_dir = str((self.project_dir / plans_dir).resolve())
        self.plan_store = PlanStore(plans_dir)

    def _init_memory_store(self):
        """根据 config 初始化记忆库"""
        memory_cfg = self.agent.config.get("memory", {})
        db_path = memory_cfg.get("db_path", "./记忆库/memory.db")
        if not Path(db_path).is_absolute():
            db_path = str((self.project_dir / db_path).resolve())
        self._memory_store = MemoryStore(db_path)
        self._memory_range = memory_cfg.get("default_range", "本周")

    # ═══════════════════════════════════════════
    #  UI 构建
    # ═══════════════════════════════════════════

    def _init_ui(self):
        self.setWindowTitle("📅 每日计划机v2.1-more intelligence")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 780)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 左侧：日历 ──
        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)

        cal_label = QLabel("📅 计划日历")
        cal_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        left_panel.addWidget(cal_label)

        self.calendar = PlanCalendar()
        self.calendar.date_clicked_with_plans.connect(self._on_date_selected)
        left_panel.addWidget(self.calendar)

        # 快速跳转今天
        self.btn_today = QPushButton("📍 回到今天")
        self.btn_today.clicked.connect(self._go_today)
        left_panel.addWidget(self.btn_today)

        # 图片展示区（点击导入）
        self.img_label = QLabel()
        self.img_label.setMinimumHeight(180)
        self.img_label.setMaximumHeight(280)
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setText("🖼️ 点击此处导入图片\n装饰你的空间")
        self.img_label.setCursor(Qt.PointingHandCursor)
        self.img_label.mousePressEvent = self._on_image_click
        self.img_label.setStyleSheet(
            "border: 2px dashed #d0d0d0; border-radius: 10px; "
            "color: #aaa; font-size: 13px; padding: 16px; "
        )
        left_panel.addWidget(self.img_label)

        # 主题切换按钮（左下角）
        self.btn_theme = QPushButton("🌙 深色")
        self.btn_theme.setFixedWidth(90)
        self.btn_theme.clicked.connect(self._toggle_theme)
        left_panel.addWidget(self.btn_theme)

        left_panel.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(340)

        # ── 右侧：计划编辑 + Agent ──
        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        # 日期标题
        self.lbl_date = QLabel("")
        self.lbl_date.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #2563eb; padding: 4px;"
        )
        right_panel.addWidget(self.lbl_date)

        # 计划列表
        plan_group = QGroupBox("📋 今日计划")
        plan_layout = QVBoxLayout(plan_group)

        self.plan_list = PlanListWidget()
        self.plan_list.setProperty("dark_mode", False)
        # 用外层 QFrame 做圆角边框，避免 QListWidget 自身 stylesheet 干扰 item 绘制
        self._plan_frame = QFrame()
        self._plan_frame.setFrameShape(QFrame.NoFrame)
        frame_inner = QVBoxLayout(self._plan_frame)
        frame_inner.setContentsMargins(4, 4, 4, 4)
        frame_inner.addWidget(self.plan_list)
        self.plan_list._check_toggled.connect(self._auto_save)
        # 兼容旧的 itemChanged（勾选走 delegate 区域点击 -> setCheckState -> itemChanged）
        self.plan_list.itemChanged.connect(self._on_item_changed)
        plan_layout.addWidget(self._plan_frame)

        # 添加新计划
        add_layout = QHBoxLayout()
        self.input_new = QTextEdit()
        self.input_new.setPlaceholderText("输入新计划（可包含链接），Ctrl+Enter 添加...")
        self.input_new.setMinimumHeight(64)
        self.input_new.setMaximumHeight(96)
        self.input_new.setAcceptRichText(False)
        # Ctrl+Enter 添加
        self.input_new.installEventFilter(self)
        add_layout.addWidget(self.input_new)

        self.btn_add = QPushButton("➕ 添加")
        self.btn_add.clicked.connect(self._add_plan_item)
        add_layout.addWidget(self.btn_add)
        plan_layout.addLayout(add_layout)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("💾 保存计划")
        self.btn_save.clicked.connect(self._save_current_plan)
        self.btn_save.setProperty("cssClass", "primary")
        btn_layout.addWidget(self.btn_save)

        self.btn_capture = QPushButton("📎 抓取标签页")
        self.btn_capture.clicked.connect(self._capture_browser_tabs)
        btn_layout.addWidget(self.btn_capture)

        self.btn_delete = QPushButton("🗑 删除选中")
        self.btn_delete.clicked.connect(self._delete_selected_item)
        self.btn_delete.setProperty("cssClass", "danger")
        btn_layout.addWidget(self.btn_delete)

        self.btn_diary = QPushButton("📝 日记")
        self.btn_diary.clicked.connect(self._toggle_diary)
        btn_layout.addWidget(self.btn_diary)

        btn_layout.addStretch()
        plan_layout.addLayout(btn_layout)

        right_panel.addWidget(plan_group)

        # Agent 面板 — 聊天界面
        self.agent_group = QGroupBox("🤖 Agent")
        agent_layout = QVBoxLayout(self.agent_group)

        # 聊天显示区（替代原来的单一输出区）
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setMinimumHeight(180)
        self.chat_display.setPlaceholderText("点击「发送今日计划」开始对话，之后可以在这里继续追问...")
        agent_layout.addWidget(self.chat_display)

        # 对话输入行
        chat_input_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("输入追问... (Enter 发送)")
        self.chat_input.returnPressed.connect(self._send_chat)
        self.chat_input.setEnabled(False)
        chat_input_layout.addWidget(self.chat_input)

        self.btn_chat_send = QPushButton("📨 发送")
        self.btn_chat_send.clicked.connect(self._send_chat)
        self.btn_chat_send.setEnabled(False)
        self.btn_chat_send.setProperty("cssClass", "primary")
        chat_input_layout.addWidget(self.btn_chat_send)

        self.btn_chat_clear = QPushButton("🗑 新对话")
        self.btn_chat_clear.clicked.connect(self._clear_chat)
        chat_input_layout.addWidget(self.btn_chat_clear)

        agent_layout.addLayout(chat_input_layout)

        # 记忆范围选择器
        memory_range_layout = QHBoxLayout()
        memory_range_layout.addWidget(QLabel("🧠 记忆范围:"))
        self.memory_range_combo = QComboBox()
        self.memory_range_combo.addItems(["本周", "近两周", "近一月", "全部"])
        self.memory_range_combo.setCurrentText(self._memory_range)
        self.memory_range_combo.currentTextChanged.connect(self._on_memory_range_changed)
        self.memory_range_combo.setFixedWidth(100)
        memory_range_layout.addWidget(self.memory_range_combo)
        memory_range_layout.addStretch()
        agent_layout.addLayout(memory_range_layout)

        # 搜索栏
        search_layout = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("搜索资料...")
        self.input_search.returnPressed.connect(self._do_search)
        search_layout.addWidget(self.input_search)
        self.btn_search = QPushButton("🔍 搜索")
        self.btn_search.clicked.connect(self._do_search)
        search_layout.addWidget(self.btn_search)
        agent_layout.addLayout(search_layout)

        # Agent 操作按钮
        agent_btn_layout = QHBoxLayout()

        self.btn_send_ai = QPushButton("🤖 发送今日计划")
        self.btn_send_ai.clicked.connect(self._send_to_ai)
        self.btn_send_ai.setProperty("cssClass", "primary")
        agent_btn_layout.addWidget(self.btn_send_ai)

        self.btn_open_links = QPushButton("🔗 在浏览器中打开链接")
        self.btn_open_links.clicked.connect(self._open_links)
        agent_btn_layout.addWidget(self.btn_open_links)

        agent_btn_layout.addStretch()

        self.btn_settings = QPushButton("⚙️ 设置")
        self.btn_settings.clicked.connect(self._open_settings)
        agent_btn_layout.addWidget(self.btn_settings)

        agent_layout.addLayout(agent_btn_layout)

        right_panel.addWidget(self.agent_group)

        # 日记输入（初始隐藏）
        self.diary_group = QGroupBox("📝 日记")
        diary_layout = QVBoxLayout(self.diary_group)

        self.diary_input = QTextEdit()
        self.diary_input.setMinimumHeight(200)
        self.diary_input.setPlaceholderText("写日记...Ctrl+Enter 保存")
        self.diary_input.installEventFilter(self)
        diary_layout.addWidget(self.diary_input)

        diary_btn_layout = QHBoxLayout()
        self.btn_save_diary = QPushButton("💾 保存日记")
        self.btn_save_diary.clicked.connect(self._save_diary)
        self.btn_save_diary.setProperty("cssClass", "primary")
        diary_btn_layout.addWidget(self.btn_save_diary)
        diary_btn_layout.addStretch()
        diary_layout.addLayout(diary_btn_layout)

        self.diary_group.hide()
        right_panel.addWidget(self.diary_group)
        self._diary_visible = False

        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        # 用 QSplitter 组合左右
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        main_layout.addWidget(splitter)

    # ═══════════════════════════════════════════
    #  主题样式
    # ═══════════════════════════════════════════

    def _apply_theme(self):
        """应用主题样式"""
        if self._dark:
            self._apply_dark_theme()
        else:
            self._apply_light_theme()

    def _toggle_theme(self):
        """切换亮色/暗色主题"""
        self._dark = not self._dark
        self._apply_theme()
        self.plan_list.setProperty("dark_mode", self._dark)
        self.plan_list.viewport().update()
        self.btn_theme.setText("☀️ 浅色" if self._dark else "🌙 深色")
        self._set_status("已切换至深色主题" if self._dark else "已切换至浅色主题")

    def _apply_light_theme(self):
        """Arco Design 风格明亮主题"""
        light = """
        /* ── 全局 ── */
        QMainWindow {
            background-color: #f5f7fa;
        }
        QWidget {
            color: #1d2129;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
        }

        /* ── 卡片式分组 ── */
        QGroupBox {
            background-color: #ffffff;
            border: 1px solid #e5e6eb;
            border-radius: 10px;
            margin-top: 14px;
            padding: 20px 12px 12px 12px;
            font-weight: bold;
            color: #1d2129;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            background-color: #ffffff;
            color: #1d2129;
        }

        /* ── 列表 ── */
        QListWidget {
            color: #1d2129;
            border: none;
            outline: none;
        }
        QListWidget::item {
            padding: 6px 8px;
            border-radius: 4px;
        }

        /* ── 输入框 ── */
        QLineEdit {
            background-color: #ffffff;
            border: 1px solid #e5e6eb;
            border-radius: 8px;
            padding: 7px 12px;
            color: #1d2129;
        }
        QLineEdit:focus {
            border-color: #165dff;
        }
        QTextEdit {
            background-color: #ffffff;
            border: 1px solid #e5e6eb;
            border-radius: 8px;
            padding: 10px;
            color: #1d2129;
        }
        QTextEdit:focus {
            border-color: #165dff;
        }

        /* ── 默认按钮 ── */
        QPushButton {
            background-color: #f2f3f5;
            color: #1d2129;
            border: 1px solid #e5e6eb;
            border-radius: 6px;
            padding: 7px 18px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #e5e6eb;
        }
        QPushButton:pressed {
            background-color: #c9cdd4;
        }

        /* ── 主要按钮 ── */
        QPushButton[cssClass="primary"] {
            background-color: #165dff;
            color: #ffffff;
            border: none;
        }
        QPushButton[cssClass="primary"]:hover {
            background-color: #4080ff;
        }
        QPushButton[cssClass="primary"]:pressed {
            background-color: #0e42d2;
        }

        /* ── 危险按钮 ── */
        QPushButton[cssClass="danger"] {
            background-color: #fff2f0;
            color: #f53f3f;
            border: 1px solid #ffccc7;
        }
        QPushButton[cssClass="danger"]:hover {
            background-color: #ffccc7;
        }
        QPushButton[cssClass="danger"]:pressed {
            background-color: #f53f3f;
            color: #ffffff;
        }

        /* ── 日历 ── */
        QCalendarWidget {
            background-color: #ffffff;
            border: 1px solid #e5e6eb;
            border-radius: 10px;
            color: #1d2129;
        }
        QCalendarWidget QToolButton {
            color: #1d2129;
            background-color: transparent;
            border: none;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 14px;
        }
        QCalendarWidget QToolButton:hover {
            background-color: #f2f3f5;
        }
        QCalendarWidget QMenu {
            background-color: #ffffff;
            color: #1d2129;
        }
        QCalendarWidget QSpinBox {
            background-color: #ffffff;
            color: #1d2129;
            border: 1px solid #e5e6eb;
            border-radius: 4px;
        }
        QCalendarWidget QAbstractItemView:enabled {
            color: #1d2129;
            selection-background-color: #e8f3ff;
            selection-color: #1d2129;
        }

        /* ── 滚动条 ── */
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #c9cdd4;
            border-radius: 4px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: #a8abb2;
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #c9cdd4;
            border-radius: 4px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #a8abb2;
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            background: transparent;
        }

        /* ── 工具提示 ── */
        QToolTip {
            background-color: #1d2129;
            color: #ffffff;
            border: none;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 12px;
        }

        /* ── 其他 ── */
        QLabel {
            background: transparent;
        }
        QSplitter::handle {
            background-color: #e5e6eb;
            width: 1px;
        }
        """
        self.setStyleSheet(light)
        self.statusBar().setStyleSheet(
            "background-color: #f5f7fa; color: #86909c; "
            "border-top: 1px solid #e5e6eb; padding: 4px 8px;"
        )
        # 更新 plan_frame 边框样式（它是单独 setStyleSheet 的）
        self._update_plan_frame_style()

    def _apply_dark_theme(self):
        """暗色主题"""
        dark = """
        /* ── 全局 ── */
        QMainWindow {
            background-color: #1a1a2e;
        }
        QWidget {
            color: #e0e0e0;
            font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            font-size: 13px;
        }

        /* ── 卡片式分组 ── */
        QGroupBox {
            background-color: #252540;
            border: 1px solid #3a3a5c;
            border-radius: 10px;
            margin-top: 14px;
            padding: 20px 12px 12px 12px;
            font-weight: bold;
            color: #e0e0e0;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            background-color: #252540;
            color: #e0e0e0;
        }

        /* ── 列表 ── */
        QListWidget {
            color: #e0e0e0;
            border: none;
            outline: none;
        }
        QListWidget::item {
            padding: 6px 8px;
            border-radius: 4px;
        }

        /* ── 输入框 ── */
        QLineEdit {
            background-color: #1e1e36;
            border: 1px solid #3a3a5c;
            border-radius: 8px;
            padding: 7px 12px;
            color: #e0e0e0;
        }
        QLineEdit:focus {
            border-color: #4080ff;
        }
        QTextEdit {
            background-color: #1e1e36;
            border: 1px solid #3a3a5c;
            border-radius: 8px;
            padding: 10px;
            color: #e0e0e0;
        }
        QTextEdit:focus {
            border-color: #4080ff;
        }

        /* ── 默认按钮 ── */
        QPushButton {
            background-color: #2a2a45;
            color: #e0e0e0;
            border: 1px solid #3a3a5c;
            border-radius: 6px;
            padding: 7px 18px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #35355a;
        }
        QPushButton:pressed {
            background-color: #404070;
        }

        /* ── 主要按钮 ── */
        QPushButton[cssClass="primary"] {
            background-color: #4080ff;
            color: #ffffff;
            border: none;
        }
        QPushButton[cssClass="primary"]:hover {
            background-color: #6099ff;
        }
        QPushButton[cssClass="primary"]:pressed {
            background-color: #3060cc;
        }

        /* ── 危险按钮 ── */
        QPushButton[cssClass="danger"] {
            background-color: #3a1e1e;
            color: #ff6b6b;
            border: 1px solid #5c2a2a;
        }
        QPushButton[cssClass="danger"]:hover {
            background-color: #5c2a2a;
        }
        QPushButton[cssClass="danger"]:pressed {
            background-color: #7a3030;
        }

        /* ── 日历 ── */
        QCalendarWidget {
            background-color: #252540;
            border: 1px solid #3a3a5c;
            border-radius: 10px;
            color: #e0e0e0;
        }
        QCalendarWidget QToolButton {
            color: #e0e0e0;
            background-color: transparent;
            border: none;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 14px;
        }
        QCalendarWidget QToolButton:hover {
            background-color: #35355a;
        }
        QCalendarWidget QMenu {
            background-color: #252540;
            color: #e0e0e0;
        }
        QCalendarWidget QSpinBox {
            background-color: #1e1e36;
            color: #e0e0e0;
            border: 1px solid #3a3a5c;
            border-radius: 4px;
        }
        QCalendarWidget QAbstractItemView:enabled {
            color: #e0e0e0;
            selection-background-color: #2a3a5c;
            selection-color: #e0e0e0;
        }

        /* ── 滚动条 ── */
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #4a4a6a;
            border-radius: 4px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: #5a5a7a;
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #4a4a6a;
            border-radius: 4px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #5a5a7a;
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            background: transparent;
        }

        /* ── 工具提示 ── */
        QToolTip {
            background-color: #e0e0e0;
            color: #1a1a2e;
            border: none;
            border-radius: 4px;
            padding: 6px 10px;
            font-size: 12px;
        }

        /* ── 其他 ── */
        QLabel {
            background: transparent;
        }
        QSplitter::handle {
            background-color: #3a3a5c;
            width: 1px;
        }
        """
        self.setStyleSheet(dark)
        self.statusBar().setStyleSheet(
            "background-color: #1a1a2e; color: #8888aa; "
            "border-top: 1px solid #3a3a5c; padding: 4px 8px;"
        )
        self._update_plan_frame_style()

    def _update_plan_frame_style(self):
        """根据当前主题更新 plan_frame 边框样式（它是单独 setStyleSheet 的）"""
        if self._dark:
            self._plan_frame.setStyleSheet("""
                QFrame {
                    border: 1px solid #3a3a5c;
                    border-radius: 6px;
                    background-color: #1e1e36;
                }
            """)
        else:
            self._plan_frame.setStyleSheet("""
                QFrame {
                    border: 1px solid #e5e6eb;
                    border-radius: 6px;
                    background-color: #ffffff;
                }
            """)

    # ═══════════════════════════════════════════
    #  日期选择逻辑
    # ═══════════════════════════════════════════

    def _on_date_selected(self, date_str: str, has_plans: bool):
        """日历日期被点击"""
        self._current_date = date_str
        self._load_plan_to_ui(date_str)
        if self._diary_visible:
            self._load_diary()

    def _go_today(self):
        """跳转到今天"""
        today = date.today().isoformat()
        self._current_date = today
        self.calendar.setSelectedDate(QDate.currentDate())
        self._load_plan_to_ui(today)

    def _load_plan_to_ui(self, date_str: str):
        """将指定日期的计划加载到 UI"""
        self._loading = True  # 防止 itemChanged 触发 auto-save
        # 更新日期标签
        try:
            dt = date.fromisoformat(date_str)
            weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            wd = weekday_names[dt.weekday()]
            self.lbl_date.setText(f"📆 {date_str} {wd}")
        except ValueError:
            self.lbl_date.setText(f"📆 {date_str}")

        # 加载计划项
        self.plan_list.clear()
        plan_data = self.plan_store.load(date_str)
        for item_data in plan_data.get("items", []):
            text = item_data["text"] if isinstance(item_data, dict) else item_data
            done = item_data.get("done", False) if isinstance(item_data, dict) else False
            list_item = QListWidgetItem(text)
            list_item.setFlags(list_item.flags() | Qt.ItemIsEditable)
            list_item.setCheckState(Qt.Checked if done else Qt.Unchecked)
            self.plan_list.addItem(list_item)

        self._loading = False
        # 刷新日历标记
        self._refresh_calendar_marks()

    def _refresh_calendar_marks(self):
        dates = self.plan_store.get_dates_with_plans()
        self.calendar.set_plan_dates(dates)

    def _on_item_changed(self, item):
        """计划项变更 → 自动保存 + 重绘"""
        if self._loading:
            return
        self.plan_list.viewport().update()  # 重绘当前项背景
        self._auto_save()

    # ═══════════════════════════════════════════
    #  计划编辑操作
    # ═══════════════════════════════════════════

    def _add_plan_item(self):
        text = self.input_new.toPlainText().strip()
        if not text:
            return
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        item.setCheckState(Qt.Unchecked)
        self.plan_list.addItem(item)
        self.input_new.clear()
        self.input_new.setFocus()
        self._auto_save()

    def _get_current_items(self) -> list:
        """从列表控件获取当前所有计划文本"""
        items = []
        for i in range(self.plan_list.count()):
            items.append(self.plan_list.item(i).text().strip())
        return [it for it in items if it]

    def _get_items_for_save(self) -> list:
        """获取结构化计划项列表，用于持久化"""
        items = []
        for i in range(self.plan_list.count()):
            item = self.plan_list.item(i)
            text = item.text().strip()
            if text:
                items.append({
                    "text": text,
                    "done": item.checkState() == Qt.Checked,
                })
        return items

    def _save_current_plan(self):
        items = self._get_items_for_save()
        self.plan_store.save(self._current_date, items)

        # 如果有链接，提示打开
        urls, _ = StudyAgent.parse_plan_items([it["text"] for it in items])
        self._refresh_calendar_marks()

        msg = f"已保存 {len(items)} 条计划"
        if urls:
            msg += f"，检测到 {len(urls)} 个链接"
        self._set_status(msg)

    def _auto_save(self):
        """自动保存当前列表内容到文件"""
        if self._loading:
            return
        items = self._get_items_for_save()
        self.plan_store.save(self._current_date, items)
        self._refresh_calendar_marks()
        # 同步到记忆库
        if self._memory_store and items:
            self._memory_store.save_plan(self._current_date, items)

    def eventFilter(self, obj, event):
        """Ctrl+Enter 添加计划 / 保存日记"""
        if event.type() == QEvent.KeyPress and \
           event.key() == Qt.Key_Return and \
           event.modifiers() == Qt.ControlModifier:
            if obj is self.input_new:
                self._add_plan_item()
                return True
            if obj is self.diary_input:
                self._save_diary()
                return True
        return super().eventFilter(obj, event)

    def _delete_selected_item(self):
        """删除选中的计划项（支持多选）"""
        selected = self.plan_list.selectedItems()
        if not selected:
            self._set_status("请先在列表中选中计划")
            return
        rows = sorted([self.plan_list.row(it) for it in selected], reverse=True)
        for row in rows:
            self.plan_list.takeItem(row)
        self._set_status(f"已删除 {len(rows)} 条计划")
        self._auto_save()

    def _capture_browser_tabs(self):
        """抓取浏览器当前标签页的 URL，依次写入计划列表"""
        self._set_status("正在抓取浏览器标签页...")
        urls = _capture_browser_urls()
        if not urls:
            self._set_status("未抓到浏览器标签页（支持 Chrome/Edge/Firefox）")
            return
        for url in urls:
            item = QListWidgetItem(url)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setCheckState(Qt.Unchecked)
            self.plan_list.addItem(item)
        self._auto_save()
        self._set_status(f"已抓取 {len(urls)} 个标签页")

    def _toggle_diary(self):
        """切换 Agent 建议 / 日记输入"""
        if self._diary_visible:
            self.diary_group.hide()
            self.agent_group.show()
            self.btn_diary.setText("📝 日记")
            self._diary_visible = False
        else:
            self.agent_group.hide()
            self.diary_group.show()
            self.btn_diary.setText("🤖 Agent")
            self._diary_visible = True
            self._load_diary()

    def _load_diary(self):
        """加载当前日期的日记内容到编辑区"""
        diary_dir = self.project_dir / "日记"
        fpath = diary_dir / f"{self._current_date}.txt"
        if fpath.exists():
            self.diary_input.setPlainText(fpath.read_text(encoding="utf-8"))
        else:
            self.diary_input.clear()

    def _save_diary(self):
        """保存日记到 日记/YYYY-MM-DD.txt，顶部附上时间戳"""
        text = self.diary_input.toPlainText().strip()
        if not text:
            self._set_status("日记内容为空，未保存")
            return
        diary_dir = self.project_dir / "日记"
        diary_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:00")
        stamp = f"--- {ts} ---\n"
        fname = f"{self._current_date}.txt"
        fpath = diary_dir / fname
        existing = fpath.read_text(encoding="utf-8") if fpath.exists() else ""
        fpath.write_text(stamp + text + "\n\n" + existing, encoding="utf-8")
        self.diary_input.clear()
        self._set_status(f"日记已保存 → {self._current_date}.txt")
        # 同步到记忆库
        if self._memory_store:
            self._memory_store.save_diary(self._current_date, text)

    # ═══════════════════════════════════════════
    #  Agent 操作
    # ═══════════════════════════════════════════

    def _startup_routine(self):
        """启动时自动加载今天的计划，打开链接/文件夹/程序"""
        # ── 防御：检测是否是由自身 spawn 出的子进程 ──
        if _is_child_instance(self.project_dir):
            self._set_status("⚡ 已自动跳过自启动（检测到由自身拉起）")
            _cleanup_guard_file(self.project_dir)
            # 只加载计划，不执行任何 autolaunch
            today = date.today().isoformat()
            self._load_plan_to_ui(today)
            return

        # 启动完成后清理可能残留的旧标记文件
        _cleanup_guard_file(self.project_dir)

        today = date.today().isoformat()
        self._load_plan_to_ui(today)

        al = self.agent.config.get("autolaunch", {})

        # 自动打开今天的链接
        if al.get("auto_open_links", True):
            plan_data = self.plan_store.load(today)
            raw_items = plan_data.get("items", [])
            texts = [it["text"] if isinstance(it, dict) else it for it in raw_items]
            urls, _ = StudyAgent.parse_plan_items(texts)
            if urls:
                self._open_urls_in_browser(urls)
                self._set_status(f"已在浏览器中打开 {len(urls)} 个链接")

        # 自动打开文件夹和程序
        if al.get("auto_launch", False):
            for folder in al.get("folders", []):
                if Path(folder).exists():
                    subprocess.Popen(["explorer", folder], shell=False)
            for prog in al.get("programs", []):
                # ── 防御：跳过指向自身的程序路径 ──
                if _is_same_exe(prog):
                    self._set_status(f"⚡ 已跳过自启动程序: {prog}")
                    continue
                if Path(prog).exists():
                    # ── 标记子进程，防止无限递归 ──
                    child_env = os.environ.copy()
                    child_env[_ENV_GUARD] = "1"
                    subprocess.Popen([prog], shell=False, env=child_env)

        # ── 记忆库同步 ──
        if self._memory_store:
            # 同步所有已有的日记和计划到记忆库
            diary_dir = self.project_dir / "日记"
            n_diaries = self._memory_store.sync_all_diaries(str(diary_dir))
            n_plans = self._memory_store.sync_all_plans(self.plan_store)
            # 清理过期对话
            memory_cfg = self.agent.config.get("memory", {})
            retention = memory_cfg.get("conversation_retention_days", 90)
            self._memory_store.clean_old_conversations(retention)
            if n_diaries or n_plans:
                self._set_status(
                    f"🧠 记忆库已同步: {n_diaries} 篇日记, {n_plans} 天计划"
                )

    def _open_links(self):
        """手动打开当前日期的所有链接"""
        items = self._get_current_items()
        urls, _ = StudyAgent.parse_plan_items(items)
        if urls:
            self._open_urls_in_browser(urls)
            self._set_status(f"已在浏览器中打开 {len(urls)} 个链接")
        else:
            self._set_status("当前计划中没有链接")

    def _open_urls_in_browser(self, urls: list):
        """在 Edge 浏览器中打开 URL 列表"""
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        edge_exe = None
        for p in edge_paths:
            if Path(p).exists():
                edge_exe = p
                break

        if edge_exe:
            # 用 Edge 打开所有 URL
            cmd = [edge_exe] + urls
            subprocess.Popen(cmd, shell=False)
        else:
            # 回退：用系统默认浏览器
            import webbrowser

            for url in urls:
                webbrowser.open(url)

    def _send_to_ai(self):
        """将当前计划内容发送给大模型分析（开始新对话）。
        自动预取天气数据、抓取链接内容、附上昨天的计划供复盘、
        注入记忆库上下文。"""
        items = self._get_current_items()
        if not items:
            self._set_status("请先添加计划内容")
            return

        urls, content_lines = StudyAgent.parse_plan_items(items)
        if not content_lines:
            self._set_status("请添加非链接形式的计划内容")
            return

        # ── 构建用户消息内容 ──
        plan_text = "以下是我今天的学习计划：\n\n"
        for i, line in enumerate(content_lines, 1):
            plan_text += f"{i}. {line}\n"

        # ── 天气 ──
        weather_info = StudyAgent.fetch_weather()
        plan_text += f"\n---\n{weather_info}\n"

        # ── 昨天计划（复盘）──
        yesterday_str = (date.today() - timedelta(days=1)).isoformat()
        yesterday_data = self.plan_store.load(yesterday_str)
        yesterday_items = yesterday_data.get("items", [])
        if yesterday_items:
            plan_text += "\n---\n以下是昨天的学习计划（请你复盘总结，"
            plan_text += "如有论文请每篇提一个问题，以PI身份提问）：\n\n"
            plan_text += f"📅 {yesterday_str}\n"
            for j, it in enumerate(yesterday_items, 1):
                text = it["text"] if isinstance(it, dict) else it
                done = " ✅" if isinstance(it, dict) and it.get("done") else ""
                plan_text += f"  {j}. {text}{done}\n"
            plan_text += "\n"
            y_texts = [it["text"] if isinstance(it, dict) else it
                       for it in yesterday_items]
            y_urls, _ = StudyAgent.parse_plan_items(y_texts)
            for u in y_urls:
                if u not in urls:
                    urls.append(u)
        else:
            plan_text += "\n---\n昨天没有记录学习计划，无需复盘。\n"

        # ── 记忆库上下文 ──
        if self._memory_store:
            since = self._get_memory_since_date()
            memory_text = self._memory_store.get_memories(since)
            if memory_text:
                plan_text += memory_text

        # ── 其他日期的计划 ──
        other_dates = sorted(
            d for d in self.plan_store.get_dates_with_plans()
            if d != self._current_date and d != yesterday_str
        )
        if other_dates:
            plan_text += "\n---\n以下是我其他日期的学习计划，供你参考上下文：\n\n"
            for d in other_dates[:14]:
                pd = self.plan_store.load(d)
                pd_items = pd.get("items", [])
                if pd_items:
                    plan_text += f"📅 {d}\n"
                    for j, it in enumerate(pd_items, 1):
                        text = it["text"] if isinstance(it, dict) else it
                        plan_text += f"  {j}. {text}\n"
                    plan_text += "\n"
                    texts = [it["text"] if isinstance(it, dict) else it
                             for it in pd_items]
                    other_urls, _ = StudyAgent.parse_plan_items(texts)
                    for u in other_urls:
                        if u not in urls:
                            urls.append(u)

        # ── 抓取 URL 内容 ──
        url_contents = []
        if urls:
            for url in urls:
                try:
                    url_contents.append(StudyAgent.fetch_url(url))
                except Exception:
                    url_contents.append(f"📄 {url}\n(无法抓取)")

        final_content = plan_text
        if url_contents:
            final_content += "\n\n---\n以下是你计划中的链接内容，供参考：\n\n"
            final_content += "\n".join(url_contents)

        # ── 构建对话 messages ──
        system_prompt = self.agent.agent_config.get("system_prompt", "你是一个学习助手。")
        system_prompt += (
            "\n\n当用户消息中包含「🧠 记忆库」章节时，"
            "其中记录了用户的历史日记、计划和对话。"
            "你可以引用这些记忆来提供更个性化的分析和建议。"
        )

        self._conversation = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": final_content},
        ]

        # ── 显示用户消息 ──
        self.chat_display.clear()
        self._append_to_chat("user", f"📅 今日计划 + 天气 + 链接内容\n({len(content_lines)} 条计划, {len(urls)} 个链接)")

        # ── 发送 ──
        self.btn_send_ai.setEnabled(False)
        self.btn_send_ai.setText("⏳ 分析中...")
        self.btn_chat_send.setEnabled(False)
        self.chat_input.setEnabled(False)

        self.agent.call_llm_messages(
            messages=self._conversation,
            on_success=self._ai_response.emit,
            on_error=self._ai_error.emit,
            on_progress=self._ai_progress.emit,
        )

    # ── 多轮对话 ───────────────────────────────────────

    def _send_chat(self):
        """发送追问消息"""
        text = self.chat_input.text().strip()
        if not text:
            return
        if not self._conversation:
            self._set_status("请先点击「发送今日计划」开始对话")
            return

        self.chat_input.clear()
        self._append_to_chat("user", text)
        self._conversation.append({"role": "user", "content": text})

        self.btn_chat_send.setEnabled(False)
        self.btn_send_ai.setEnabled(False)
        self.chat_input.setEnabled(False)
        self.chat_input.setPlaceholderText("⏳ 等待回复...")

        self.agent.call_llm_messages(
            messages=self._conversation,
            on_success=self._chat_response.emit,
            on_error=self._chat_error.emit,
            on_progress=self._ai_progress.emit,
        )

    def _append_to_chat(self, role: str, content: str):
        """追加一条消息到聊天显示，同步到记忆库"""
        timestamp = datetime.now().strftime("%H:%M")
        label = f"👤 你 ({timestamp})" if role == "user" else f"🤖 Agent ({timestamp})"
        current = self.chat_display.toPlainText()
        if current:
            self.chat_display.setPlainText(current + f"\n\n{label}:\n{content}")
        else:
            self.chat_display.setPlainText(f"{label}:\n{content}")
        # 滚动到底部
        sb = self.chat_display.verticalScrollBar()
        sb.setValue(sb.maximum())
        # 同步到记忆库
        if self._memory_store:
            self._memory_store.save_conversation(self._current_date, role, content)

    def _clear_chat(self):
        """清除对话历史和显示"""
        self._conversation = []
        self.chat_display.clear()
        self.chat_display.setPlaceholderText(
            "对话已清除。点击「发送今日计划」重新开始..."
        )
        self.btn_chat_send.setEnabled(False)
        self.chat_input.setEnabled(False)
        self.chat_input.clear()
        self._set_status("对话已清除，可重新开始")

    def _get_memory_since_date(self) -> str:
        """根据 memory_range 计算记忆查询的起始日期"""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        if self._memory_range == "本周":
            return monday.isoformat()
        elif self._memory_range == "近两周":
            return (monday - timedelta(days=7)).isoformat()
        elif self._memory_range == "近一月":
            return (monday - timedelta(days=21)).isoformat()
        else:  # 全部
            return (today - timedelta(days=365)).isoformat()

    def _on_memory_range_changed(self, text: str):
        """记忆范围切换"""
        self._memory_range = text
        self._set_status(f"🧠 记忆范围已切换为: {text}（下次发送计划时生效）")

    # ── AI 响应处理 ────────────────────────────────────

    def _on_ai_progress(self, msg: str):
        self._set_status(f"⏳ {msg}")

    def _on_ai_response(self, text: str):
        """初始发送的 AI 响应"""
        self._append_to_chat("assistant", text)
        self._conversation.append({"role": "assistant", "content": text})
        self.btn_send_ai.setEnabled(True)
        self.btn_send_ai.setText("🤖 发送今日计划")
        self.btn_chat_send.setEnabled(True)
        self.chat_input.setEnabled(True)
        self.chat_input.setPlaceholderText("输入追问... (Enter 发送)")
        self.chat_input.setFocus()
        self._set_status("AI 分析完成 —— 你可以在下方输入框继续追问")

    def _on_ai_error(self, error: str):
        """初始发送出错"""
        self.chat_display.setPlainText(f"❌ {error}")
        self.btn_send_ai.setEnabled(True)
        self.btn_send_ai.setText("🤖 发送今日计划")
        self.btn_chat_send.setEnabled(False)
        self.chat_input.setEnabled(False)
        self._set_status(f"错误: {error}")

    def _on_chat_response(self, text: str):
        """追问的 AI 响应"""
        self._append_to_chat("assistant", text)
        self._conversation.append({"role": "assistant", "content": text})
        self.btn_send_ai.setEnabled(True)
        self.btn_chat_send.setEnabled(True)
        self.chat_input.setEnabled(True)
        self.chat_input.setPlaceholderText("输入追问... (Enter 发送)")
        self.chat_input.setFocus()
        self._set_status("AI 已回复")

    def _on_chat_error(self, error: str):
        """追问出错"""
        self._append_to_chat("assistant", f"❌ {error}")
        self._conversation.append({"role": "assistant", "content": f"❌ {error}"})
        self.btn_send_ai.setEnabled(True)
        self.btn_chat_send.setEnabled(True)
        self.chat_input.setEnabled(True)
        self.chat_input.setPlaceholderText("输入追问... (Enter 发送)")
        self._set_status(f"错误: {error}")

    def _on_search_response(self, text: str):
        self.chat_display.setPlainText(text)
        self.btn_search.setEnabled(True)
        self.btn_search.setText("🔍 搜索")
        self._set_status("搜索完成")

    def _do_search(self):
        query = self.input_search.text().strip()
        if not query:
            return
        self.chat_display.setPlainText(f"⏳ 搜索中: {query}")
        self.btn_search.setEnabled(False)
        self.btn_search.setText("⏳")

        def _run():
            try:
                result = StudyAgent.search_web(query)
                self._search_response.emit(result)
            except Exception as e:
                self._search_response.emit(f"搜索失败: {e}")

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _open_settings(self):
        """打开设置对话框，保存后刷新配置"""
        dlg = SettingsDialog(str(self.config_path), self)
        if dlg.exec_() == SettingsDialog.Accepted:  # noqa: E129
            self.agent.reload_config()
            self._init_plan_store()
            self._init_memory_store()
            # 更新记忆范围下拉框
            idx = self.memory_range_combo.findText(self._memory_range)
            if idx >= 0:
                self.memory_range_combo.setCurrentIndex(idx)
            self._set_status("设置已更新")

    # ═══════════════════════════════════════════
    #  辅助
    # ═══════════════════════════════════════════

    def _load_random_image(self):
        """启动时从 图片/ 目录随机读取一张图片显示在左下角"""
        img_dir = self.project_dir / "图片"
        if not img_dir.is_dir():
            return
        exts = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
        imgs = [f for f in img_dir.iterdir() if f.suffix.lower() in exts]
        if not imgs:
            return
        path = str(random.choice(imgs))
        pix = QPixmap(path)
        if pix.isNull():
            return
        scaled = pix.scaled(
            self.img_label.width() - 16, self.img_label.height() - 16,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.img_label.setPixmap(scaled)
        self.img_label.setText("")

    def _on_image_click(self, _event):
        """点击图片区导入图片"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif);;所有文件 (*.*)"
        )
        if path:
            pix = QPixmap(path)
            if pix.isNull():
                self._set_status("无法加载该图片")
                return
            scaled = pix.scaled(
                self.img_label.width() - 16, self.img_label.height() - 16,
                Qt.KeepAspectRatio, Qt.SmoothTransformation,
            )
            self.img_label.setPixmap(scaled)
            self.img_label.setText("")

    def _set_status(self, msg: str):
        """在状态栏显示消息（同时短暂显示在输出区）"""
        self.statusBar().showMessage(msg, 5000)


def _get_project_root() -> Path:
    """返回项目根目录（兼容开发模式和 PyInstaller 打包后的 exe）。"""
    if getattr(sys, 'frozen', False):
        # pyinstaller one-folder: exe 在 dist/每日计划机/ 下，可写数据都应相对于 exe
        return Path(sys.executable).parent
    else:
        # 开发模式: main.py 在 study-gui/ 下，根目录是上一层
        return Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
#  防无限递归自启动 —— 多层防御
# ═══════════════════════════════════════════════════════════════════

# 环境变量名：父进程 spawn 子进程前设置，子进程检测到后跳过 autolaunch
_ENV_GUARD = "STUDY_LAUNCHER_CHILD"

# 子进程标记文件：在项目根目录写入一个临时标记文件，比环境变量更可靠
# （环境变量在 Windows 上可能因为 spawn 方式不同而丢失）


def _get_my_exe_path() -> Path:
    """返回当前运行的可执行文件/脚本的规范化路径。

    开发模式 → python.exe 路径
    打包模式 → 每日计划机.exe 路径
    """
    return Path(sys.executable).resolve()


def _is_same_exe(candidate: str) -> bool:
    """判断 candidate 路径是否指向当前运行的 exe/脚本自身。"""
    try:
        cp = Path(candidate).resolve()
        me = _get_my_exe_path()
        # 同时检查名字（开发模式下可能是 python.exe 运行 main.py）
        if cp == me:
            return True
        # 打包模式下，检查是否指向同目录下的同一 exe
        me_dir = me.parent
        cp_dir = cp.parent
        if cp_dir == me_dir and cp.name.lower() == me.name.lower():
            return True
        # 检查 candidate 的 stem（去扩展名）是否包含 "每日计划机"
        if "每日计划机" in cp.stem and "每日计划机" in me.stem:
            return True
    except (OSError, ValueError):
        pass
    return False


def _get_guard_file_path(project_root: Path) -> Path:
    """子进程标记文件路径。"""
    return project_root / ".launcher_child_guard"


def _mark_as_child(project_root: Path) -> None:
    """写入子进程标记文件 + 设置环境变量，双重保险。"""
    os.environ[_ENV_GUARD] = "1"
    try:
        _get_guard_file_path(project_root).write_text(
            f"child_pid={os.getpid()}\ntime={datetime.now().isoformat()}\n"
        )
    except OSError:
        pass  # 文件写入失败不阻塞


def _is_child_instance(project_root: Path) -> bool:
    """检测当前实例是否是由自身 spawn 出的子进程。"""
    # 方法 1：环境变量
    if os.environ.get(_ENV_GUARD) == "1":
        return True
    # 方法 2：标记文件（检查最近 10 秒内写入的）
    gf = _get_guard_file_path(project_root)
    if gf.exists():
        try:
            mtime = gf.stat().st_mtime
            age = datetime.now().timestamp() - mtime
            if age < 10:
                return True
            # 过期标记文件：清理掉
            if age > 30:
                gf.unlink(missing_ok=True)
        except OSError:
            pass
    return False


def _cleanup_guard_file(project_root: Path) -> None:
    """启动完成后清理标记文件。"""
    try:
        _get_guard_file_path(project_root).unlink(missing_ok=True)
    except OSError:
        pass


def main():
    project_root = _get_project_root()
    os.chdir(str(project_root))

    app = QApplication(sys.argv)
    app.setApplicationName("StudyAgent")

    window = MainWindow(str(project_root))
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
