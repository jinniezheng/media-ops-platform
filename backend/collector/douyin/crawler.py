import asyncio
import logging
import os
from typing import Dict, List

from playwright.async_api import async_playwright

from collector.base import AbstractCrawler
from .client import DouyinApiClient

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "..", "libs", "stealth.min.js"
)

_DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

# 每个视频最多采集评论数
_COMMENTS_PER_VIDEO = 20


def _parse_cookie_str(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


class DouyinCrawler(AbstractCrawler):
    platform = "douyin"

    async def collect(self, task, cookie_str: str = "") -> dict:
        if task.task_type == "keyword":
            return await self.search(task.keyword, task.max_count, cookie_str=cookie_str)
        raise ValueError(f"不支持的任务类型: {task.task_type}")

    async def search(self, keyword: str, max_count: int, cookie_str: str = "") -> dict:
        """
        关键词搜索视频并采集评论。
        返回 {"posts": [...], "comments": [...]}
        """
        if not cookie_str:
            raise ValueError("Cookie 未提供，请在账号管理中配置抖音账号")

        cookie_dict = _parse_cookie_str(cookie_str)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=_DOUYIN_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )

            # 注入 stealth.js 防检测
            if os.path.exists(_STEALTH_JS):
                await context.add_init_script(path=_STEALTH_JS)

            # 设置 Cookie
            await context.add_cookies([
                {"name": k, "value": v, "domain": ".douyin.com", "path": "/"}
                for k, v in cookie_dict.items()
            ])

            page = await context.new_page()
            client = DouyinApiClient(page)

            try:
                posts = await self._search_videos(client, keyword, max_count)
                comments = await self._fetch_all_comments(client, posts)
            finally:
                await browser.close()

        return {"posts": posts, "comments": comments}

    async def _search_videos(
        self, client: DouyinApiClient, keyword: str, max_count: int
    ) -> List[Dict]:
        logger.info(f"[Douyin] 搜索关键词: {keyword}, 目标数量: {max_count}")
        posts = await client.search_videos(keyword, max_count)
        logger.info(f"[Douyin] 搜索完成，获取 {len(posts)} 个视频")
        return posts

    async def _fetch_all_comments(
        self, client: DouyinApiClient, posts: List[Dict]
    ) -> List[Dict]:
        comments: List[Dict] = []
        # 仅对前 10 个视频采集评论，避免运行时间过长
        for post in posts[:10]:
            aweme_id = post.get("aweme_id", "")
            if not aweme_id:
                continue
            try:
                raw = await client.get_video_comments(aweme_id, max_count=_COMMENTS_PER_VIDEO)
                comments.extend(raw)
                logger.info(f"[Douyin] 视频 {aweme_id} 获取 {len(raw)} 条评论")
            except Exception as e:
                logger.warning(f"[Douyin] 视频 {aweme_id} 评论采集失败: {e}")
            await asyncio.sleep(2.0)
        return comments
