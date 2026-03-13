import asyncio
import logging
from typing import Dict, List

from playwright.async_api import Page

logger = logging.getLogger(__name__)

# 抖音 API 路径特征
_SEARCH_API_PATTERN = "/aweme/v1/web/search/item/"
_COMMENT_API_PATTERN = "/aweme/v1/web/comment/list/"


def _pick_video_url(video: dict) -> str:
    """从 video 字段中提取播放地址"""
    for key in ("play_addr", "download_addr", "play_addr_h264"):
        addr = video.get(key, {})
        urls = addr.get("url_list", [])
        if urls:
            return urls[0]
    return ""


def _pick_cover_url(video: dict) -> str:
    cover = video.get("cover", {})
    urls = cover.get("url_list", [])
    return urls[0] if urls else ""


def _pick_avatar_url(author: dict) -> str:
    for key in ("avatar_thumb", "avatar_medium", "avatar_larger"):
        urls = author.get(key, {}).get("url_list", [])
        if urls:
            return urls[0]
    return ""


def parse_aweme(item: dict) -> dict:
    """将抖音 aweme 原始数据解析为结构化字典"""
    author = item.get("author", {})
    stats = item.get("statistics", {})
    video = item.get("video", {})
    return {
        "aweme_id": item.get("aweme_id", ""),
        "desc": item.get("desc", ""),
        "author_uid": author.get("uid", ""),
        "author_name": author.get("nickname", ""),
        "author_avatar": _pick_avatar_url(author),
        "like_count": stats.get("digg_count", 0),
        "comment_count": stats.get("comment_count", 0),
        "share_count": stats.get("share_count", 0),
        "collect_count": stats.get("collect_count", 0),
        "play_count": stats.get("play_count", 0),
        "duration": video.get("duration", 0),
        "cover_url": _pick_cover_url(video),
        "video_url": _pick_video_url(video),
        "create_time": item.get("create_time", 0),
    }


def parse_comment(item: dict, aweme_id: str) -> dict:
    """将抖音评论原始数据解析为结构化字典"""
    user = item.get("user", {})
    return {
        "comment_id": item.get("cid", ""),
        "aweme_id": aweme_id,
        "content": item.get("text", ""),
        "user_id": user.get("uid", ""),
        "nickname": user.get("nickname", ""),
        "avatar": _pick_avatar_url(user),
        "ip_location": item.get("ip_label", ""),
        "like_count": item.get("digg_count", 0),
        "reply_count": item.get("reply_comment_total", 0),
        "create_time": item.get("create_time", 0),
    }


class DouyinApiClient:
    """
    通过 Playwright 响应拦截与抖音 Web API 交互。
    浏览器自动处理 X-Bogus / _signature 等签名，无需手动实现。
    """

    def __init__(self, page: Page):
        self.page = page

    async def search_videos(self, keyword: str, max_count: int = 20) -> List[Dict]:
        """
        在抖音搜索页拦截搜索 API 响应，返回视频列表。
        通过滚动触发分页加载直到满足 max_count。
        """
        posts: List[Dict] = []
        seen_ids: set = set()
        collected = asyncio.Event()
        no_more = False

        async def on_response(resp):
            nonlocal no_more
            if _SEARCH_API_PATTERN not in resp.url:
                return
            try:
                data = await resp.json()
            except Exception as e:
                logger.warning(f"[Douyin] 解析搜索响应失败: {e}")
                return

            items = data.get("data", []) or data.get("aweme_list", [])
            for item in items:
                aweme_id = item.get("aweme_id", "")
                if not aweme_id or aweme_id in seen_ids:
                    continue
                seen_ids.add(aweme_id)
                posts.append(parse_aweme(item))

            if not data.get("has_more", True) or not items:
                no_more = True

            collected.set()

        self.page.on("response", on_response)
        try:
            search_url = f"https://www.douyin.com/search/{keyword}?type=video"
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            collected.set()  # 允许首轮立即检查

            scroll_attempts = 0
            max_scrolls = 20

            while len(posts) < max_count and not no_more and scroll_attempts < max_scrolls:
                collected.clear()
                await self.page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                try:
                    await asyncio.wait_for(collected.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                scroll_attempts += 1
                await asyncio.sleep(1.5)

        finally:
            self.page.remove_listener("response", on_response)

        return posts[:max_count]

    async def get_video_comments(
        self, aweme_id: str, max_count: int = 30
    ) -> List[Dict]:
        """
        访问视频详情页，拦截评论 API 响应，返回评论列表。
        """
        comments: List[Dict] = []
        seen_ids: set = set()
        collected = asyncio.Event()
        no_more = False

        async def on_response(resp):
            nonlocal no_more
            if _COMMENT_API_PATTERN not in resp.url:
                return
            try:
                data = await resp.json()
            except Exception as e:
                logger.warning(f"[Douyin] 解析评论响应失败: {e}")
                return

            for item in data.get("comments") or []:
                cid = item.get("cid", "")
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                comments.append(parse_comment(item, aweme_id))

            if not data.get("has_more", True):
                no_more = True

            collected.set()

        self.page.on("response", on_response)
        try:
            video_url = f"https://www.douyin.com/video/{aweme_id}"
            await self.page.goto(video_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            scroll_attempts = 0
            max_scrolls = 10

            while len(comments) < max_count and not no_more and scroll_attempts < max_scrolls:
                collected.clear()
                await self.page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                try:
                    await asyncio.wait_for(collected.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                scroll_attempts += 1
                await asyncio.sleep(1.5)

        finally:
            self.page.remove_listener("response", on_response)

        return comments[:max_count]
