import asyncio
import logging
import time
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
        start_time = time.time()
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
                logger.info(f"[Douyin] 收到搜索响应: {resp.url}, 数据键: {list(data.keys()) if data else '无数据'}")
                # 调试：检查状态码和数据结构
                status_code = data.get("status_code")
                logger.info(f"[Douyin] 响应状态码: {status_code}")
                aweme_list_field = data.get("aweme_list")
                data_field = data.get("data")
                if aweme_list_field is not None:
                    logger.info(f"[Douyin] aweme_list 类型: {type(aweme_list_field)}, 长度: {len(aweme_list_field) if isinstance(aweme_list_field, list) else '非列表'}")
                if data_field is not None:
                    logger.info(f"[Douyin] data 字段类型: {type(data_field)}, 长度: {len(data_field) if isinstance(data_field, list) else '非列表'}")
            except Exception as e:
                logger.warning(f"[Douyin] 解析搜索响应失败: {e}")
                return

            # 处理抖音API返回的数据结构，data字段可能为null
            data_field = data.get("data")
            aweme_list_field = data.get("aweme_list")
            items = []
            if data_field is not None:
                items = data_field if isinstance(data_field, list) else []
            elif aweme_list_field is not None:
                items = aweme_list_field if isinstance(aweme_list_field, list) else []
            # 如果两个字段都是None，items保持为空列表
            if items:
                logger.info(f"[Douyin] 提取到 {len(items)} 个 items，第一个 item 键: {list(items[0].keys()) if isinstance(items[0], dict) else '非字典'}")
            for item in items:
                # 抖音搜索API返回的数据结构可能是嵌套的aweme_info，也可能是直接的视频对象
                aweme_info = item.get("aweme_info")
                target = aweme_info if aweme_info is not None else item

                aweme_id = target.get("aweme_id", "")
                if not aweme_id or aweme_id in seen_ids:
                    continue
                seen_ids.add(aweme_id)
                posts.append(parse_aweme(target))

            if not data.get("has_more", True) or not items:
                no_more = True

            collected.set()

        self.page.on("response", on_response)
        try:
            search_url = f"https://www.douyin.com/search/{keyword}?type=video"
            page_load_start = time.time()
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            page_load_time = time.time() - page_load_start
            await asyncio.sleep(1.5)
            logger.info(f"[Douyin] 页面加载完成: 耗时={page_load_time:.2f}秒")

            collected.set()  # 允许首轮立即检查

            scroll_attempts = 0
            max_scrolls = 15
            scroll_start_time = time.time()

            while len(posts) < max_count and not no_more and scroll_attempts < max_scrolls:
                collected.clear()
                await self.page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                try:
                    await asyncio.wait_for(collected.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                scroll_attempts += 1
                await asyncio.sleep(0.8)

            scroll_total_time = time.time() - scroll_start_time
            logger.info(f"[Douyin] 滚动完成: 滚动次数={scroll_attempts}, 耗时={scroll_total_time:.2f}秒, 每次滚动平均={scroll_total_time/scroll_attempts if scroll_attempts else 0:.2f}秒")

        finally:
            self.page.remove_listener("response", on_response)

        total_time = time.time() - start_time
        logger.info(f"[Douyin] 搜索完成: 关键词='{keyword}', 获取视频={len(posts)}, 耗时={total_time:.2f}秒, 平均每个视频={total_time/len(posts) if posts else 0:.2f}秒")

        return posts[:max_count]

    async def get_video_comments(
        self, aweme_id: str, max_count: int = 30
    ) -> List[Dict]:
        """
        访问视频详情页，拦截评论 API 响应，返回评论列表。
        """
        start_time = time.time()
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
                logger.info(f"[Douyin] 收到评论响应: {resp.url}, 数据键: {list(data.keys()) if data else '无数据'}")
            except Exception as e:
                logger.warning(f"[Douyin] 解析评论响应失败: {e}")
                return

            comments_field = data.get("comments")
            comment_items = []
            if comments_field is not None and isinstance(comments_field, list):
                comment_items = comments_field
            # 如果 comments_field 为 null 或不是列表，comment_items 保持为空列表

            for item in comment_items:
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
            page_load_start = time.time()
            await self.page.goto(video_url, wait_until="domcontentloaded", timeout=15000)
            page_load_time = time.time() - page_load_start
            await asyncio.sleep(1.5)
            logger.info(f"[Douyin] 视频页面加载完成: aweme_id={aweme_id}, 耗时={page_load_time:.2f}秒")

            scroll_attempts = 0
            max_scrolls = 5
            scroll_start_time = time.time()

            while len(comments) < max_count and not no_more and scroll_attempts < max_scrolls:
                collected.clear()
                await self.page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                try:
                    await asyncio.wait_for(collected.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                scroll_attempts += 1
                await asyncio.sleep(0.8)

            scroll_total_time = time.time() - scroll_start_time
            logger.info(f"[Douyin] 评论滚动完成: 滚动次数={scroll_attempts}, 耗时={scroll_total_time:.2f}秒, 每次滚动平均={scroll_total_time/scroll_attempts if scroll_attempts else 0:.2f}秒")

        finally:
            self.page.remove_listener("response", on_response)

        total_time = time.time() - start_time
        logger.info(f"[Douyin] 评论采集完成: aweme_id={aweme_id}, 获取评论={len(comments)}, 耗时={total_time:.2f}秒, 平均每条评论={total_time/len(comments) if comments else 0:.2f}秒")

        return comments[:max_count]

