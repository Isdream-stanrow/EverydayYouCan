"""
Agent 记忆数据库
使用 SQLite 单表存储：日记快照、计划快照、对话记录
按 week_start 索引，支持按周/按日期范围查询
"""
import sqlite3
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import List, Optional


class MemoryStore:
    """SQLite 记忆库，单表多类型存储"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_week
                ON memory(week_start, kind)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_date
                ON memory(date)
            """)
            conn.commit()

    # ─── 工具 ──────────────────────────────────────────

    @staticmethod
    def _week_monday(date_str: str) -> str:
        """返回该日期所在周的周一 ISO 字符串"""
        dt = date.fromisoformat(date_str)
        monday = dt - timedelta(days=dt.weekday())
        return monday.isoformat()

    # ─── 日记快照 ──────────────────────────────────────

    def save_diary(self, date_str: str, content: str):
        """保存日记快照（同一天 upsert，只保留最新）"""
        if not content.strip():
            return
        ws = self._week_monday(date_str)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM memory WHERE date=? AND kind='diary'", (date_str,)
            )
            conn.execute(
                "INSERT INTO memory (date, week_start, kind, content, created_at) "
                "VALUES (?, ?, 'diary', ?, ?)",
                (date_str, ws, content, now),
            )
            conn.commit()

    def get_diaries(self, from_date: str, to_date: str) -> List[dict]:
        """获取日期范围内的日记"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT date, content FROM memory "
                "WHERE kind='diary' AND date BETWEEN ? AND ? "
                "ORDER BY date DESC",
                (from_date, to_date),
            ).fetchall()
        return [{"date": r[0], "content": r[1]} for r in rows]

    def sync_all_diaries(self, diary_dir: str) -> int:
        """批量同步 日记/ 目录下的 txt 文件到数据库，返回同步数量"""
        diary_path = Path(diary_dir)
        if not diary_path.is_dir():
            return 0
        count = 0
        for fpath in diary_path.glob("*.txt"):
            date_str = fpath.stem
            try:
                content = fpath.read_text(encoding="utf-8").strip()
                if content:
                    self.save_diary(date_str, content)
                    count += 1
            except Exception:
                pass
        return count

    # ─── 计划快照 ──────────────────────────────────────

    def save_plan(self, date_str: str, items: list):
        """保存计划快照（JSON 序列化，同一天 upsert）"""
        if not items:
            return
        ws = self._week_monday(date_str)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = json.dumps(items, ensure_ascii=False)
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM memory WHERE date=? AND kind='plan'", (date_str,)
            )
            conn.execute(
                "INSERT INTO memory (date, week_start, kind, content, created_at) "
                "VALUES (?, ?, 'plan', ?, ?)",
                (date_str, ws, content, now),
            )
            conn.commit()

    def get_plans(self, from_date: str, to_date: str) -> List[dict]:
        """获取日期范围内的计划"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT date, content FROM memory "
                "WHERE kind='plan' AND date BETWEEN ? AND ? "
                "ORDER BY date DESC",
                (from_date, to_date),
            ).fetchall()
        result = []
        for r in rows:
            try:
                items = json.loads(r[1])
            except json.JSONDecodeError:
                items = [{"text": r[1], "done": False}]
            result.append({"date": r[0], "items": items})
        return result

    def sync_all_plans(self, plan_store) -> int:
        """批量同步 PlanStore 中的所有计划到数据库，返回同步数量"""
        count = 0
        for date_str in plan_store.get_dates_with_plans():
            try:
                data = plan_store.load(date_str)
                items = data.get("items", [])
                if items:
                    self.save_plan(date_str, items)
                    count += 1
            except Exception:
                pass
        return count

    # ─── 对话记录 ──────────────────────────────────────

    def save_conversation(self, date_str: str, role: str, content: str):
        """追加对话记录（不删除旧的，保留完整历史）"""
        if not content.strip():
            return
        ws = self._week_monday(date_str)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kind = f"chat_{role}"
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO memory (date, week_start, kind, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (date_str, ws, kind, content, now),
            )
            conn.commit()

    def get_conversations(self, from_date: str, to_date: str) -> List[dict]:
        """获取日期范围内的对话"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT date, kind, content, created_at FROM memory "
                "WHERE kind IN ('chat_user', 'chat_assistant') "
                "AND date BETWEEN ? AND ? "
                "ORDER BY date ASC, id ASC",
                (from_date, to_date),
            ).fetchall()
        return [
            {"date": r[0], "role": r[1].replace("chat_", ""),
             "content": r[2], "timestamp": r[3]}
            for r in rows
        ]

    # ─── 记忆清理 ──────────────────────────────────────

    def clean_old_conversations(self, retention_days: int):
        """清理超过 retention_days 天的对话记录"""
        if retention_days <= 0:
            return
        cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM memory WHERE kind IN ('chat_user', 'chat_assistant') "
                "AND date < ?",
                (cutoff,),
            )
            conn.commit()

    # ─── 记忆上下文组装 ────────────────────────────────

    def get_memories(self, since_date: str) -> str:
        """
        获取从 since_date 到今天的记忆，格式化为 LLM 可读文本。
        返回空字符串表示无记忆。
        """
        today_str = date.today().isoformat()
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT date, kind, content FROM memory "
                "WHERE date >= ? AND date <= ? "
                "ORDER BY date ASC, id ASC",
                (since_date, today_str),
            ).fetchall()

        if not rows:
            return ""

        lines = ["\n---\n## 🧠 记忆库（历史记录，供你参考）\n"]
        current_date = None
        total_chars = 0
        max_chars = 2500  # 控制总长，防止 token 浪费

        for row_date, kind, content in rows:
            if total_chars > max_chars:
                lines.append("\n*(记忆库内容已截断，可调整记忆范围查看更近期记录)*")
                break

            if row_date != current_date:
                current_date = row_date
                lines.append(f"\n### 📅 {row_date}")

            if kind == 'diary':
                snippet = content[:300]
                lines.append(f"📝 [日记] {snippet}")
                total_chars += len(snippet)
            elif kind == 'plan':
                try:
                    items = json.loads(content)
                    lines.append("📋 [计划]")
                    for it in items[:10]:  # 每天最多 10 条
                        text = it['text'] if isinstance(it, dict) else it
                        done = " ✅" if isinstance(it, dict) and it.get('done') else ""
                        line = f"  - {text}{done}"
                        lines.append(line)
                        total_chars += len(line)
                except json.JSONDecodeError:
                    snippet = content[:200]
                    lines.append(f"📋 [计划] {snippet}")
                    total_chars += len(snippet)
            elif kind == 'chat_user':
                snippet = content[:200]
                lines.append(f"💬 [用户] {snippet}")
                total_chars += len(snippet)
            elif kind == 'chat_assistant':
                snippet = content[:200]
                lines.append(f"🤖 [AI] {snippet}")
                total_chars += len(snippet)

        return "\n".join(lines)
