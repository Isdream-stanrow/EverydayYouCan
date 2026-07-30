"""
计划数据存储模块
使用 JSON 文件按日期存储每日计划
"""
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional


class PlanStore:
    """按日期管理计划项的 JSON 文件存储"""

    def __init__(self, plans_dir: str):
        self.plans_dir = Path(plans_dir)
        self.plans_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, date_str: str) -> Path:
        return self.plans_dir / f"{date_str}.json"

    def load(self, date_str: str) -> dict:
        """加载指定日期的计划，返回 {'date': ..., 'items': [{'text':..., 'done':bool}, ...]}"""
        path = self._file_path(date_str)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 向后兼容：旧格式 items 是字符串列表，转为新格式
            items = data.get("items", [])
            if items and isinstance(items[0], str):
                data["items"] = [{"text": t, "done": False} for t in items]
            return data
        return {"date": date_str, "items": []}

    def save(self, date_str: str, items: list) -> None:
        """保存指定日期的计划。items 可以是字符串列表或 {'text','done'} 字典列表"""
        path = self._file_path(date_str)
        normalized = []
        for item in items:
            if isinstance(item, str):
                normalized.append({"text": item, "done": False})
            else:
                normalized.append(item)
        data = {"date": date_str, "items": normalized}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_today_str(self) -> str:
        return date.today().isoformat()

    def get_dates_with_plans(self) -> set:
        """返回所有有计划数据的日期集合"""
        dates = set()
        for f in self.plans_dir.glob("*.json"):
            dates.add(f.stem)  # filename without .json
        return dates

    def delete(self, date_str: str) -> bool:
        """删除指定日期的计划，成功返回 True"""
        path = self._file_path(date_str)
        if path.exists():
            path.unlink()
            return True
        return False
