"""抖音视频媒体解析"""
import asyncio
import logging
import os
from typing import Dict, Optional

from playwright.async_api import async_playwright

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


def _parse_cookie_str(cookie_str: str) -> dict:
    """解析 Cookie 字符串为字典"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def parse_douyin_video_media(
    cookie_str: str,
    aweme_id: str,
    video_url: Optional[str] = None,
) -> Dict:
    """
    解析抖音视频媒体资源，获取高质量视频链接。

    Args:
        cookie_str: 抖音账号 Cookie
        aweme_id: 抖音视频ID
        video_url: 现有的视频链接（可能已过期）

    Returns:
        {
            "success": True/False,
            "aweme_id": "...",
            "video_url": "...",  # 最新视频链接
            "cover_url": "...",  # 封面链接
            "message": "...",
        }
    """
    if not cookie_str:
        return {"success": False, "message": "Cookie 未提供"}

    cookie_dict = _parse_cookie_str(cookie_str)

    result = {
        "success": False,
        "aweme_id": aweme_id,
        "video_url": video_url or "",
        "cover_url": "",
        "message": "未获取到视频链接",
    }

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, channel="chrome")
        except Exception as e:
            logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
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

        try:
            # 访问视频页面
            video_page_url = f"https://www.douyin.com/video/{aweme_id}"
            logger.info(f"[Douyin Media] 访问视频页面: {video_page_url}")

            await page.goto(
                video_page_url,
                wait_until="domcontentloaded",
                timeout=30000
            )
            await asyncio.sleep(3)

            # 检查页面状态
            content = await page.content()
            if "验证" in content or "captcha" in content.lower():
                logger.warning("[Douyin Media] 页面可能包含验证码")
                result["message"] = "页面包含验证码，请手动验证"
                return result

            # 尝试拦截视频相关的API响应
            video_responses = []

            async def on_response(resp):
                url = resp.url
                # 捕获视频相关API
                if "/aweme/v1/web/aweme/detail/" in url:
                    try:
                        data = await resp.json()
                        logger.info(f"[Douyin Media] 捕获视频详情API: {url}")
                        video_responses.append(data)
                    except Exception as e:
                        logger.warning(f"[Douyin Media] 解析响应失败: {e}")
                # 捕获视频播放地址
                elif "playaddr" in url or "play_addr" in url:
                    logger.info(f"[Douyin Media] 捕获视频播放地址: {url}")
                    # 可以记录URL，但通常视频播放地址在详情API中

            page.on("response", on_response)

            # 等待API响应
            await asyncio.sleep(5)

            # 移除监听器
            page.remove_listener("response", on_response)

            # 如果有视频详情响应，尝试提取视频信息
            for resp_data in video_responses:
                if resp_data.get("aweme_detail"):
                    aweme_detail = resp_data["aweme_detail"]
                    video_info = aweme_detail.get("video", {})

                    # 提取视频播放地址
                    for key in ["play_addr", "download_addr", "play_addr_h264"]:
                        addr = video_info.get(key, {})
                        urls = addr.get("url_list", [])
                        if urls:
                            result["video_url"] = urls[0]
                            result["success"] = True
                            result["message"] = "成功获取视频链接"
                            break

                    # 提取封面
                    cover = video_info.get("cover", {})
                    cover_urls = cover.get("url_list", [])
                    if cover_urls:
                        result["cover_url"] = cover_urls[0]

                    if result["success"]:
                        break

            # 如果API方式失败，尝试通过页面元素获取
            if not result["success"]:
                # 查找视频元素
                video_elements = await page.query_selector_all("video")
                for video_elem in video_elements:
                    src = await video_elem.get_attribute("src")
                    if src and "http" in src:
                        result["video_url"] = src
                        result["success"] = True
                        result["message"] = "从页面元素获取视频链接"
                        break

        except Exception as e:
            logger.error(f"[Douyin Media] 解析视频失败: {e}", exc_info=True)
            result["message"] = f"解析失败: {str(e)}"

        finally:
            await browser.close()

    return result


async def batch_parse_douyin_videos(
    cookie_str: str,
    aweme_ids: list[str],
    interval: float = 3.0,
) -> list[Dict]:
    """
    批量解析抖音视频媒体资源

    Args:
        cookie_str: 抖音账号 Cookie
        aweme_ids: 抖音视频ID列表
        interval: 请求间隔（秒）

    Returns:
        每个视频的解析结果列表
    """
    results = []
    for aweme_id in aweme_ids:
        try:
            result = await parse_douyin_video_media(cookie_str, aweme_id)
            results.append(result)
        except Exception as e:
            logger.error(f"[Douyin Media] 解析视频 {aweme_id} 失败: {e}")
            results.append({
                "success": False,
                "aweme_id": aweme_id,
                "message": f"解析失败: {str(e)}",
            })

        # 间隔避免频繁请求
        await asyncio.sleep(interval)

    return results