import asyncio
import logging
import os
from typing import Dict, List

from playwright.async_api import async_playwright, BrowserContext, Page

from collector.base import AbstractCrawler
from .client import XhsApiClient
from .field import SearchSortType

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "..", "libs", "stealth.min.js"
)

_XHS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.xiaohongshu.com",
    "Referer": "https://www.xiaohongshu.com",
    "Content-Type": "application/json;charset=UTF-8",
}


def _parse_cookie_str(cookie_str: str) -> dict:
    """将 cookie 字符串解析为 dict"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _extract_note_time(note_id: str) -> int:
    """XHS note_id 前 8 位是 Unix 秒时间戳（16进制），转为毫秒返回"""
    try:
        return int(note_id[:8], 16) * 1000
    except (ValueError, IndexError):
        return 0


class XhsCrawler(AbstractCrawler):
    platform = "xhs"

    async def collect(self, task, cookie_str: str = "") -> dict:
        return await self.search(task.keyword, task.max_count, cookie_str=cookie_str)

    async def search(self, keyword: str, max_count: int, cookie_str: str = "") -> dict:
        """搜索笔记 + 采集评论，返回 {"notes": [...], "comments": [...]}"""
        if not cookie_str:
            raise ValueError("Cookie 未提供，请在账号管理中配置小红书账号")

        cookie_dict = _parse_cookie_str(cookie_str)
        headers = {**_XHS_HEADERS, "Cookie": cookie_str}

        async with async_playwright() as pw:
            # 尝试使用系统 Chrome，避免下载 Chromium
            try:
                browser = await pw.chromium.launch(headless=True, channel="chrome")
            except Exception as e:
                logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
                browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=headers["User-Agent"])
            # 注入 stealth.js 防检测
            if os.path.exists(_STEALTH_JS):
                await context.add_init_script(path=_STEALTH_JS)
            # 设置 cookie
            await context.add_cookies([
                {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                for k, v in cookie_dict.items()
            ])
            page = await context.new_page()
            await page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded")
            # 等待签名函数加载，最多 10s，避免固定 sleep
            try:
                await page.wait_for_function("typeof window.mnsv2 === 'function'", timeout=10000)
            except Exception:
                await asyncio.sleep(2)

            client = XhsApiClient(
                headers=headers, playwright_page=page, cookie_dict=cookie_dict,
            )

            try:
                notes = await self._search_notes(client, keyword, max_count)
                comments = await self._fetch_all_comments(client, notes)
            finally:
                await browser.close()

        return {"notes": notes, "comments": comments}

    async def _search_notes(
        self, client: XhsApiClient, keyword: str, max_count: int
    ) -> List[Dict]:
        notes: List[Dict] = []
        page = 1
        while len(notes) < max_count:
            data = await client.get_note_by_keyword(
                keyword, page=page, sort=SearchSortType.GENERAL,
            )
            items = data.get("items") or []
            if not items:
                break
            for item in items:
                if len(notes) >= max_count:
                    break
                note_card = item.get("note_card", {})
                note_id = item.get("id", "")
                xsec_token = item.get("xsec_token", "")
                user = note_card.get("user", {})
                interact = note_card.get("interact_info", {})
                notes.append({
                    "note_id": note_id,
                    "title": note_card.get("display_title", ""),
                    "desc": note_card.get("desc", ""),
                    "type": note_card.get("type", "normal"),
                    "user_id": user.get("user_id", ""),
                    "nickname": user.get("nickname", ""),
                    "avatar": user.get("avatar", ""),
                    "liked_count": int(interact.get("liked_count", "0")),
                    "collected_count": int(interact.get("collected_count", "0")),
                    "comment_count": int(interact.get("comment_count", "0")),
                    "share_count": int(interact.get("share_count", "0")),
                    "time": note_card.get("time") or _extract_note_time(note_id),
                    "xsec_token": xsec_token,
                })
            page += 1
            await asyncio.sleep(0.5)
        return notes

    async def _fetch_all_comments(
        self, client: XhsApiClient, notes: List[Dict]
    ) -> List[Dict]:
        comments: List[Dict] = []
        for note in notes:
            note_id = note["note_id"]
            xsec_token = note.get("xsec_token", "")
            try:
                raw = await client.get_note_all_comments(
                    note_id, xsec_token, max_count=20, crawl_interval=0.5,
                )
                for c in raw:
                    user_info = c.get("user_info", {})
                    comments.append({
                        "comment_id": c.get("id", ""),
                        "note_id": note_id,
                        "content": c.get("content", ""),
                        "user_id": user_info.get("user_id", ""),
                        "nickname": user_info.get("nickname", ""),
                        "avatar": user_info.get("image", ""),
                        "ip_location": c.get("ip_location", ""),
                        "like_count": int(c.get("like_count", "0")),
                        "sub_comment_count": c.get("sub_comment_count", 0),
                        "parent_comment_id": c.get("target_comment", {}).get("id", ""),
                        "create_time": c.get("create_time", 0),
                    })
            except Exception as e:
                logger.warning(f"获取笔记 {note_id} 评论失败: {e}")
            await asyncio.sleep(0.5)
        return comments
