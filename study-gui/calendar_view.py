"""
自定义日历组件
- 继承 QCalendarWidget
- 有计划的日期用圆点标记
- 点击日期发出信号
"""
from PyQt5.QtWidgets import QCalendarWidget
from PyQt5.QtCore import QDate, Qt, pyqtSignal
from PyQt5.QtGui import QTextCharFormat, QColor, QBrush, QPen, QPainter
from datetime import date


class PlanCalendar(QCalendarWidget):
    """带计划标记的日历组件"""

    date_clicked_with_plans = pyqtSignal(str, bool)  # date_str, has_plans

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan_dates: set = set()
        self._today_str = date.today().isoformat()

        # 样式
        self.setGridVisible(True)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setFirstDayOfWeek(Qt.Monday)

        # 今天高亮
        self._highlight_today()

        self.clicked.connect(self._on_date_clicked)

    def set_plan_dates(self, dates: set):
        """设置有计划标记的日期集合 {'2026-07-27', ...}"""
        self._plan_dates = dates
        self.updateCells()

    def _highlight_today(self):
        """用浅蓝色背景高亮今天"""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#e8f3ff"))
        fmt.setForeground(QColor("#1a1a2e"))
        today = QDate.currentDate()
        self.setDateTextFormat(today, fmt)

    def _on_date_clicked(self, qdate: QDate):
        date_str = qdate.toString("yyyy-MM-dd")
        has_plans = date_str in self._plan_dates
        self.date_clicked_with_plans.emit(date_str, has_plans)

    def paintCell(self, painter: QPainter, rect, qdate: QDate):
        """重写绘制：有计划的日子画圆点标记"""
        super().paintCell(painter, rect, qdate)

        date_str = qdate.toString("yyyy-MM-dd")
        if date_str in self._plan_dates:
            painter.save()
            # 在日期格底部画小圆点
            cx = rect.center().x()
            cy = rect.bottom() - 6
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#165dff"))
            painter.drawEllipse(int(cx - 3), int(cy - 3), 6, 6)
            painter.restore()
