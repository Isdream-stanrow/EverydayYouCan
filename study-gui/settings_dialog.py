"""
设置对话框 - 编辑 config.yaml 的 GUI
"""
import os
from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QFormLayout, QGroupBox, QFileDialog, QListWidget,
    QListWidgetItem, QMessageBox, QComboBox, QCheckBox,
)
from PyQt5.QtCore import Qt
import yaml


class SettingsDialog(QDialog):
    """可配置大模型 API、自动启动项等的设置对话框"""

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.setWindowTitle("⚙️ 设置")
        self.setMinimumSize(600, 500)
        self.resize(650, 550)
        self._init_ui()

    def _load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ═══════════════════════════════════════════
    #  UI
    # ═══════════════════════════════════════════

    def _init_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._llm_tab(), "🤖 大模型")
        self.tabs.addTab(self._agent_tab(), "📝 Agent")
        self.tabs.addTab(self._autolaunch_tab(), "🚀 自动启动")
        layout.addWidget(self.tabs)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("💾 保存")
        save_btn.clicked.connect(self._on_save)
        save_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; "
            "border-radius: 4px; padding: 8px 24px; font-weight: bold; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        btn_layout.addWidget(save_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    # ── LLM 选项卡 ─────────────────────────────

    def _llm_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        api_group = QGroupBox("API 配置")
        form = QFormLayout(api_group)

        llm = self.config.get("llm", {})

        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["openai", "deepseek", "custom"])
        self.llm_provider.setCurrentText(llm.get("provider", "deepseek"))
        form.addRow("提供商:", self.llm_provider)

        self.llm_api_key = QLineEdit(llm.get("api_key", ""))
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_api_key.setPlaceholderText("sk-...")
        form.addRow("API Key:", self.llm_api_key)

        self.llm_base_url = QLineEdit(llm.get("base_url", "https://api.deepseek.com"))
        form.addRow("Base URL:", self.llm_base_url)

        self.llm_model = QLineEdit(llm.get("model", "deepseek-v4-pro"))
        form.addRow("模型:", self.llm_model)

        layout.addWidget(api_group)

        params_group = QGroupBox("请求参数")
        form2 = QFormLayout(params_group)

        self.llm_timeout = QSpinBox()
        self.llm_timeout.setRange(10, 300)
        self.llm_timeout.setValue(llm.get("timeout", 60))
        self.llm_timeout.setSuffix(" 秒")
        form2.addRow("超时:", self.llm_timeout)

        self.llm_max_tokens = QSpinBox()
        self.llm_max_tokens.setRange(100, 32000)
        self.llm_max_tokens.setSingleStep(500)
        self.llm_max_tokens.setValue(llm.get("max_tokens", 2000))
        form2.addRow("Max Tokens:", self.llm_max_tokens)

        self.llm_temperature = QDoubleSpinBox()
        self.llm_temperature.setRange(0.0, 2.0)
        self.llm_temperature.setSingleStep(0.1)
        self.llm_temperature.setValue(llm.get("temperature", 0.7))
        form2.addRow("Temperature:", self.llm_temperature)

        layout.addWidget(params_group)
        layout.addStretch()
        return w

    # ── Agent 选项卡 ────────────────────────────

    def _agent_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        agent_cfg = self.config.get("agent", {})
        storage_cfg = self.config.get("storage", {})

        prompt_group = QGroupBox("系统提示词")
        prompt_layout = QVBoxLayout(prompt_group)
        self.agent_prompt = QTextEdit()
        self.agent_prompt.setPlainText(agent_cfg.get("system_prompt", ""))
        self.agent_prompt.setMinimumHeight(200)
        self.agent_prompt.setPlaceholderText("输入 AI 系统提示词...")
        prompt_layout.addWidget(self.agent_prompt)
        layout.addWidget(prompt_group)

        storage_group = QGroupBox("存储")
        form = QFormLayout(storage_group)
        self.storage_plans_dir = QLineEdit(storage_cfg.get("plans_dir", "./plans"))
        form.addRow("计划目录:", self.storage_plans_dir)
        layout.addWidget(storage_group)

        # ── 记忆库设置 ──
        memory_cfg = self.config.get("memory", {})
        memory_group = QGroupBox("🧠 记忆库")
        memory_form = QFormLayout(memory_group)

        self.memory_db_path = QLineEdit(
            memory_cfg.get("db_path", "./记忆库/memory.db")
        )
        memory_form.addRow("数据库路径:", self.memory_db_path)

        self.memory_default_range = QComboBox()
        self.memory_default_range.addItems(["本周", "近两周", "近一月", "全部"])
        self.memory_default_range.setCurrentText(
            memory_cfg.get("default_range", "本周")
        )
        memory_form.addRow("默认记忆范围:", self.memory_default_range)

        self.memory_retention = QSpinBox()
        self.memory_retention.setRange(0, 3650)
        self.memory_retention.setSingleStep(30)
        self.memory_retention.setSuffix(" 天 (0=永久)")
        self.memory_retention.setValue(memory_cfg.get("conversation_retention_days", 90))
        memory_form.addRow("对话保留:", self.memory_retention)

        layout.addWidget(memory_group)

        layout.addStretch()
        return w

    # ── 自动启动选项卡 ──────────────────────────

    def _autolaunch_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        al = self.config.get("autolaunch", {})

        # 文件夹
        folders_group = QGroupBox("📁 自动打开的文件夹")
        folders_layout = QVBoxLayout(folders_group)
        self.folders_list = QListWidget()
        for f in al.get("folders", []):
            self.folders_list.addItem(f)
        folders_layout.addWidget(self.folders_list)
        fbtn = QHBoxLayout()
        add_folder_btn = QPushButton("➕ 添加文件夹")
        add_folder_btn.clicked.connect(self._add_folder)
        fbtn.addWidget(add_folder_btn)
        del_folder_btn = QPushButton("🗑 删除选中")
        del_folder_btn.clicked.connect(lambda: self._delete_selected(self.folders_list))
        fbtn.addWidget(del_folder_btn)
        fbtn.addStretch()
        folders_layout.addLayout(fbtn)
        layout.addWidget(folders_group)

        # 程序
        programs_group = QGroupBox("🚀 自动启动的程序")
        programs_layout = QVBoxLayout(programs_group)
        self.programs_list = QListWidget()
        for p in al.get("programs", []):
            self.programs_list.addItem(p)
        programs_layout.addWidget(self.programs_list)
        pbtn = QHBoxLayout()
        add_prog_btn = QPushButton("➕ 添加程序")
        add_prog_btn.clicked.connect(self._add_program)
        pbtn.addWidget(add_prog_btn)
        del_prog_btn = QPushButton("🗑 删除选中")
        del_prog_btn.clicked.connect(lambda: self._delete_selected(self.programs_list))
        pbtn.addWidget(del_prog_btn)
        pbtn.addStretch()
        programs_layout.addLayout(pbtn)
        layout.addWidget(programs_group)

        # 开关
        toggle_group = QGroupBox("开关")
        toggle_layout = QVBoxLayout(toggle_group)
        self.chk_auto_open_links = QCheckBox("启动时自动打开当日计划中的链接")
        self.chk_auto_open_links.setChecked(al.get("auto_open_links", True))
        toggle_layout.addWidget(self.chk_auto_open_links)
        self.chk_auto_launch = QCheckBox("启动时自动打开文件夹和程序")
        self.chk_auto_launch.setChecked(al.get("auto_launch", False))
        toggle_layout.addWidget(self.chk_auto_launch)
        layout.addWidget(toggle_group)

        layout.addStretch()
        return w

    # ── 列表操作 ────────────────────────────────

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self.folders_list.addItem(path)

    def _add_program(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序", "",
            "可执行文件 (*.exe *.bat *.cmd *.ps1);;所有文件 (*.*)"
        )
        if path:
            self.programs_list.addItem(path)

    def _delete_selected(self, lst: QListWidget):
        row = lst.currentRow()
        if row >= 0:
            lst.takeItem(row)

    # ── 保存 ────────────────────────────────────

    def _on_save(self):
        # ── 防御：检查是否将自身设置为自动启动程序 ──
        import sys
        from pathlib import Path as _Path
        exe_path = _Path(sys.executable).resolve()
        exe_name = exe_path.stem.lower()

        # 收集程序列表（先校验再写 config）
        programs = []
        for i in range(self.programs_list.count()):
            prog = self.programs_list.item(i).text()
            try:
                pp = _Path(prog).resolve()
                pp_name = pp.stem.lower()
                # 检查 1：路径完全相同
                if pp == exe_path:
                    QMessageBox.warning(
                        self, "不能自启动", f"不能将每日计划机自身设置为自动启动程序：\n{prog}\n\n该路径已自动跳过。"
                    )
                    continue
                # 检查 2：同目录同名字
                if pp.parent == exe_path.parent and pp_name == exe_name:
                    QMessageBox.warning(
                        self, "不能自启动", f"不能将每日计划机自身设置为自动启动程序：\n{prog}\n\n该路径已自动跳过。"
                    )
                    continue
                # 检查 3：stem 包含 "每日计划机"
                if "每日计划机" in pp.stem and "每日计划机" in exe_name:
                    QMessageBox.warning(
                        self, "不能自启动", f"检测到路径指向每日计划机自身：\n{prog}\n\n已自动跳过。"
                    )
                    continue
                programs.append(prog)
            except (OSError, ValueError):
                programs.append(prog)  # 解析不了的不拦截

        # LLM
        self.config["llm"] = {
            "provider": self.llm_provider.currentText(),
            "api_key": self.llm_api_key.text(),
            "base_url": self.llm_base_url.text(),
            "model": self.llm_model.text(),
            "timeout": self.llm_timeout.value(),
            "max_tokens": self.llm_max_tokens.value(),
            "temperature": self.llm_temperature.value(),
        }

        # Agent
        self.config["agent"] = {
            "system_prompt": self.agent_prompt.toPlainText(),
        }

        # Storage
        self.config["storage"] = {
            "plans_dir": self.storage_plans_dir.text(),
        }

        # Memory
        self.config["memory"] = {
            "db_path": self.memory_db_path.text(),
            "default_range": self.memory_default_range.currentText(),
            "conversation_retention_days": self.memory_retention.value(),
        }

        # Auto-launch
        folders = []
        for i in range(self.folders_list.count()):
            folders.append(self.folders_list.item(i).text())

        self.config["autolaunch"] = {
            "auto_open_links": self.chk_auto_open_links.isChecked(),
            "auto_launch": self.chk_auto_launch.isChecked(),
            "folders": folders,
            "programs": programs,
        }

        self._save_config()
        QMessageBox.information(self, "已保存", "设置已保存，部分更改将在下次启动时生效。")
        self.accept()
