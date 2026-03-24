import asyncio
import logging
import os
import time
from typing import Dict, List

from playwright.async_api import async_playwright

from collector.base import AbstractCrawler
from .client import DouyinApiClient

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "..", "libs", "stealth.min.js"
)

# User-Agent已直接使用更新后的版本

# 每个视频最多采集评论数
_COMMENTS_PER_VIDEO = 10
# 串行采集评论，不再需要并发控制


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
        start_time = time.time()
        if not cookie_str:
            raise ValueError("Cookie 未提供，请在账号管理中配置抖音账号")

        cookie_dict = _parse_cookie_str(cookie_str)

        async with async_playwright() as pw:
            # 尝试使用系统 Chrome，避免下载 Chromium
            try:
                # 简化浏览器启动参数，避免可能的加载问题
                browser = await pw.chromium.launch(
                    headless=True,
                    channel="chrome",
                    args=[
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
            except Exception as e:
                logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
                browser = await pw.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
            # 使用更大的viewport，类似真实浏览器
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",  # 更新User-Agent
                viewport={"width": 1920, "height": 1080},  # 更大的viewport
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True,
                device_scale_factor=1,
                is_mobile=False,
                has_touch=False,
                accept_downloads=False,
                locale="zh-CN",  # 设置语言
                timezone_id="Asia/Shanghai",  # 设置时区
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                },
            )

            # 注入 stealth.js 防检测
            if os.path.exists(_STEALTH_JS):
                await context.add_init_script(path=_STEALTH_JS)
                logger.info(f"[Douyin] 已注入 stealth.js 防检测")
            else:
                logger.warning(f"[Douyin] stealth.js 文件不存在，跳过注入")

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

        total_time = time.time() - start_time
        logger.info(f"[Douyin] 任务完成: 关键词='{keyword}', 视频数={len(posts)}, 评论数={len(comments)}, 总耗时={total_time:.2f}秒, 平均每个视频={total_time/len(posts) if posts else 0:.2f}秒")

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
        """串行采集评论，按顺序采集每个视频的评论"""
        start_time = time.time()
        comments: List[Dict] = []
        # 仅对前 5 个视频采集评论，避免运行时间过长
        target_posts = posts[:5]
        if not target_posts:
            return comments

        for post in target_posts:
            aweme_id = post.get("aweme_id", "")
            if not aweme_id:
                continue

            try:
                raw = await client.get_video_comments(aweme_id, max_count=_COMMENTS_PER_VIDEO)
                logger.info(f"[Douyin] 视频 {aweme_id} 获取 {len(raw)} 条评论")
                comments.extend(raw)
            except Exception as e:
                logger.warning(f"[Douyin] 视频 {aweme_id} 评论采集失败: {e}")
            finally:
                # 每个视频采集完成后等待0.5秒，避免请求过于密集
                await asyncio.sleep(0.5)

        total_time = time.time() - start_time
        logger.info(f"[Douyin] 评论采集完成: 视频数={len(target_posts)}, 评论数={len(comments)}, 耗时={total_time:.2f}秒, 平均每个视频={total_time/len(target_posts) if target_posts else 0:.2f}秒, 平均每条评论={total_time/len(comments) if comments else 0:.2f}秒")
        return comments
