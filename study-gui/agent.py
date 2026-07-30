"""
学习 Agent 模块
- 从计划文本中提取 URL 链接
- 抓取链接内容 / 搜索网络
- 将计划内容发送给大模型 API 获取建议
"""
import re
import json
import threading
from typing import Tuple, List, Optional
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup


class StudyAgent:
    """学习助手 Agent：链接提取 + LLM 调用"""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.llm_config = self.config.get("llm", {})
        self.agent_config = self.config.get("agent", {})

    def _load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def reload_config(self):
        self.config = self._load_config()
        self.llm_config = self.config.get("llm", {})
        self.agent_config = self.config.get("agent", {})

    # ─── URL 提取 ───────────────────────────────────────

    URL_PATTERN = re.compile(
        r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+",
        re.IGNORECASE,
    )

    @classmethod
    def extract_urls(cls, text: str) -> List[str]:
        """从文本中提取所有 URL"""
        return cls.URL_PATTERN.findall(text)

    @classmethod
    def strip_urls(cls, text: str) -> str:
        """从文本中移除所有 URL"""
        return cls.URL_PATTERN.sub("", text).strip()

    @classmethod
    def parse_plan_items(cls, items: list) -> Tuple[List[str], List[str]]:
        """
        解析计划项列表
        返回: (urls, content_lines)
          - urls: 所有提取到的链接
          - content_lines: 去除链接后的计划文本行（保留非空行）
        """
        all_urls = []
        content_lines = []

        for item in items:
            urls = cls.extract_urls(item)
            all_urls.extend(urls)
            stripped = cls.strip_urls(item)
            if stripped:
                content_lines.append(stripped)

        # 去重保持顺序
        seen = set()
        unique_urls = []
        for u in all_urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        return unique_urls, content_lines

    # ─── 网页抓取 ───────────────────────────────────────

    # 论文的章节标题模式（arxiv HTML 版用 h2/h3 标记）
    SECTION_HEADINGS = [
        "abstract", "introduction", "conclusion",
        "摘要", "引言", "绪论", "结论",
    ]

    @classmethod
    def fetch_url(cls, url: str, timeout: int = 15) -> str:
        """抓取网页/论文内容。arxiv 论文只取 abstract + introduction。"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        url_lower = url.lower()

        # ── arxiv abs 页 → 换到 HTML 全文版 ──
        arxiv_match = re.match(
            r"https?://arxiv\.org/abs/([\w.]+)", url, re.IGNORECASE
        )
        if arxiv_match:
            paper_id = arxiv_match.group(1)
            html_url = f"https://arxiv.org/html/{paper_id}"
            return cls._fetch_paper_sections(html_url, headers, timeout)

        # ── arxiv HTML 版直接处理 ──
        if "arxiv.org/html/" in url_lower:
            return cls._fetch_paper_sections(url, headers, timeout)

        # ── arxiv PDF → 只能抓 abs 页 ──
        pdf_match = re.match(
            r"https?://arxiv\.org/pdf/([\w.]+)", url, re.IGNORECASE
        )
        if pdf_match:
            abs_url = f"https://arxiv.org/abs/{pdf_match.group(1)}"
            return cls._fetch_paper_abstract(abs_url, headers, timeout)

        # ── 通用网页：去噪后取前部 ──
        return cls._fetch_generic_page(url, headers, timeout)

    @classmethod
    def _fetch_generic_page(cls, url: str, headers: dict, timeout: int) -> str:
        """通用网页抓取：找 main/article 区域，取前 5000 字符"""
        resp = requests.get(url, headers=headers, timeout=(5, timeout))
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        # 优先取 <article> 或 <main>
        content = soup.find("article") or soup.find("main") or soup.body
        if content is None:
            return f"📄 {url}\n(无法解析页面内容)"
        text = content.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        body = "\n".join(lines)[:5000]
        return f"📄 {url}\n{body}"

    @classmethod
    def _fetch_paper_abstract(cls, url: str, headers: dict, timeout: int) -> str:
        """抓取 arxiv abs 页的 abstract"""
        try:
            resp = requests.get(url, headers=headers, timeout=(5, timeout))
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            abstract = soup.find("blockquote", class_="abstract")
            if abstract:
                text = abstract.get_text(separator="\n").strip()
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                return f"📄 {url}\nAbstract:\n" + "\n".join(lines)[:3000]
            return f"📄 {url}\n(无法提取摘要)"
        except Exception:
            return f"📄 {url}\n(抓取失败)"

    @classmethod
    def _fetch_paper_sections(
        cls, url: str, headers: dict, timeout: int
    ) -> str:
        """抓取 arxiv HTML 论文，只取 abstract + introduction 两个章节"""
        try:
            resp = requests.get(url, headers=headers, timeout=(10, timeout))
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            # HTML 版不可用时回退到 abs 页
            paper_id = re.search(r"arxiv\.org/html/([\w.]+)", url)
            if paper_id:
                abs_url = f"https://arxiv.org/abs/{paper_id.group(1)}"
                return cls._fetch_paper_abstract(abs_url, headers, timeout)
            return f"📄 {url}\n(无法抓取论文)"

        # 找出所有 h2/h3，定位 Abstract 和 Introduction
        headings = soup.find_all(["h2", "h3"])
        result_parts = []

        for h in headings:
            label = h.get_text(strip=True).lower()
            # 匹配 "Abstract" 或 "1. Introduction" 或 "1 Introduction" 等
            is_abstract = any(
                label.startswith(w) or label == w
                for w in ["abstract", "摘要"]
            )
            is_intro = any(
                label.startswith(w) or label.startswith(f"{i} ")
                for w in cls.SECTION_HEADINGS[1:4]  # introduction / 引言 / 绪论
                for i in range(0, 5)
            )

            if is_abstract or is_intro:
                # 收集该标题之后的兄弟节点文本，直到下一个 h2/h3
                parts = []
                for sibling in h.find_next_siblings():
                    if sibling.name in ("h2", "h3"):
                        break
                    t = sibling.get_text(separator="\n").strip()
                    if t:
                        parts.append(t)
                section_text = "\n".join(parts)
                # 修剪长度
                max_len = 3000 if is_abstract else 4000
                if len(section_text) > max_len:
                    section_text = section_text[:max_len] + "\n... (已截断)"
                tag = "Abstract" if is_abstract else "Introduction"
                result_parts.append(f"[{tag}]\n{section_text}")

            if len(result_parts) >= 2:
                break

        if result_parts:
            body = "\n\n".join(result_parts)
        else:
            # 没找到标准标题，回退：取 body 文本前部
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            body = soup.body
            if body is None:
                return f"📄 {url}\n(无法解析论文结构)"
            text = body.get_text(separator="\n")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            body = "\n".join(lines)[:5000]

        return f"📄 {url}\n{body}"

    @classmethod
    def search_web(cls, query: str) -> str:
        """搜索网络：用 DuckDuckGo 的 HTML 版（免 API key），返回摘要文本"""
        try:
            url = "https://html.duckduckgo.com/html/"
            resp = requests.post(
                url,
                data={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
                timeout=(5, 15),
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = soup.select(".result__body")
            if not results:
                return f"(搜索「{query}」暂无结果)"
            lines = [f"🔍 搜索「{query}」结果："]
            for i, r in enumerate(results[:5], 1):
                title_el = r.select_one(".result__title")
                title = title_el.get_text(strip=True) if title_el else "(无标题)"
                snippet_el = r.select_one(".result__snippet")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                link_el = r.select_one(".result__url")
                link = link_el.get_text(strip=True) if link_el else ""
                lines.append(f"{i}. {title}")
                if snippet:
                    lines.append(f"   {snippet}")
                if link:
                    lines.append(f"   🔗 {link}")
            return "\n".join(lines)
        except Exception as e:
            return f"(搜索「{query}」失败: {e})"

    @classmethod
    def fetch_weather(cls) -> str:
        """获取今日逐时天气预报（wttr.in，免 API key），
        包含 11:00 和 17:00 附近的天气信息。"""
        try:
            url = "https://wttr.in/?format=j1"
            resp = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                },
                timeout=(5, 12),
            )
            resp.raise_for_status()
            data = resp.json()

            cur = data.get("current_condition", [{}])[0]
            weather = data.get("weather", [{}])[0]
            hourly = weather.get("hourly", [])

            lines = ["🌤 今日天气："]
            lines.append(
                f"  当前：{cur.get('temp_C', '?')}°C，"
                f"{cur.get('weatherDesc', [{}])[0].get('value', '?')}，"
                f"湿度 {cur.get('humidity', '?')}%，"
                f"风速 {cur.get('windspeedKmph', '?')} km/h"
            )
            lines.append("  逐时预报：")

            # wttr.in 每小时一个条目；重点标出 11:00 和 17:00 附近
            wanted_hours = {10, 11, 12, 16, 17, 18}
            for h in hourly:
                time_str = h.get("time", "")
                try:
                    hour = int(time_str) // 100  # "1100" → 11, "1700" → 17
                except (ValueError, TypeError):
                    continue
                temp = h.get("tempC", "?")
                desc = h.get("weatherDesc", [{}])[0].get("value", "?")
                marker = " ◀◀" if hour in wanted_hours else ""
                lines.append(f"    {hour:02d}:00 — {temp}°C, {desc}{marker}")

            return "\n".join(lines)
        except Exception as e:
            return f"(获取天气失败: {e})"

    # ─── LLM 调用 ───────────────────────────────────────

    def call_llm(
        self,
        plan_content: str,
        on_success=None,
        on_error=None,
        on_progress=None,
        urls: Optional[List[str]] = None,
    ) -> None:
        """
        异步调用大模型 API（在后台线程中运行）
        会先抓取 urls 中的链接内容，一并发送给 LLM
        on_success(text), on_error(msg), on_progress(msg) 在后台线程中被调用
        """
        api_key = self.llm_config.get("api_key", "")
        base_url = self.llm_config.get("base_url", "https://api.openai.com/v1")
        model = self.llm_config.get("model", "gpt-4o")
        timeout = self.llm_config.get("timeout", 60)
        max_tokens = self.llm_config.get("max_tokens", 2000)
        temperature = self.llm_config.get("temperature", 0.7)
        system_prompt = self.agent_config.get("system_prompt", "你是一个学习助手。")

        if not api_key or api_key == "your-api-key-here":
            if on_error:
                on_error("请先在 config.yaml 中配置 API Key")
            return

        def _progress(msg: str):
            if on_progress:
                on_progress(msg)

        def _run():
            try:
                # 抓取链接内容
                url_contents = []
                if urls:
                    for url in urls:
                        _progress(f"正在抓取 {url[:60]}...")
                        try:
                            text = self.fetch_url(url)
                            url_contents.append(text)
                        except Exception:
                            url_contents.append(f"📄 {url}\n(无法抓取此页面内容)")

                # 拼装最终 prompt
                final_content = plan_content
                if url_contents:
                    final_content += "\n\n---\n以下是你计划中的链接内容，供参考：\n\n"
                    final_content += "\n".join(url_contents)

                _progress("正在请求 AI 分析...")
                url = f"{base_url.rstrip('/')}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": final_content},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=(10, timeout)
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]

                if on_success:
                    on_success(text)

            except requests.exceptions.Timeout:
                if on_error:
                    on_error(f"请求超时（{timeout}s），请检查网络或 API 地址")
            except requests.exceptions.RequestException as e:
                if on_error:
                    on_error(f"API 请求失败: {e}")
            except Exception as e:
                if on_error:
                    on_error(f"未知错误: {e}")

        threading.Thread(target=_run, daemon=True).start()

    # ─── 多轮对话 API ──────────────────────────────────

    def call_llm_messages(
        self,
        messages: list,
        on_success=None,
        on_error=None,
        on_progress=None,
    ) -> None:
        """
        异步调用大模型，直接传入完整的 messages 列表（OpenAI 格式）。
        与 call_llm 的区别：
        - 不自动添加 system prompt（调用方已在 messages[0] 中提供）
        - 不自动抓取 URL（调用方已处理好内容）
        - 支持多轮对话历史

        messages 格式: [{"role":"system","content":"..."},
                        {"role":"user","content":"..."},
                        {"role":"assistant","content":"..."}, ...]
        """
        api_key = self.llm_config.get("api_key", "")
        base_url = self.llm_config.get("base_url", "https://api.openai.com/v1")
        model = self.llm_config.get("model", "gpt-4o")
        timeout = self.llm_config.get("timeout", 60)
        max_tokens = self.llm_config.get("max_tokens", 2000)
        temperature = self.llm_config.get("temperature", 0.7)

        if not api_key or api_key == "your-api-key-here":
            if on_error:
                on_error("请先在 config.yaml 中配置 API Key")
            return

        def _run():
            try:
                if on_progress:
                    on_progress("正在请求 AI...")
                url = f"{base_url.rstrip('/')}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=(10, timeout)
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                if on_success:
                    on_success(text)
            except requests.exceptions.Timeout:
                if on_error:
                    on_error(f"请求超时（{timeout}s），请检查网络或 API 地址")
            except requests.exceptions.RequestException as e:
                if on_error:
                    on_error(f"API 请求失败: {e}")
            except Exception as e:
                if on_error:
                    on_error(f"未知错误: {e}")

        threading.Thread(target=_run, daemon=True).start()
