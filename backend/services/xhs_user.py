"""小红书用户信息获取服务"""
import asyncio
import json
import logging
import os
import re
from typing import Dict, Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "libs", "stealth.min.js"
)

_XHS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


def _parse_cookie_str(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _extract_user_info_from_html(html: str) -> Optional[Dict]:
    """从用户主页 HTML 中提取用户信息"""
    match = re.search(
        r"<script>window.__INITIAL_STATE__=(.+?)<\/script>", html, re.M
    )
    if match is None:
        logger.warning("No __INITIAL_STATE__ found in HTML")
        # 尝试另一种匹配方式
        match = re.search(
            r'window\.__INITIAL_STATE__\s*=\s*({.+?});?\s*</script>', html, re.DOTALL
        )
        if match is None:
            logger.warning("Alternative match also failed")
            return None
    try:
        raw_json = match.group(1).replace(":undefined", ":null")
        info = json.loads(raw_json, strict=False)
        if info is None:
            return None
        user_data = info.get("user", {}).get("userPageData", {})
        if not user_data:
            logger.warning(f"userPageData not found, keys: {list(info.get('user', {}).keys())}")
        return user_data
    except Exception as e:
        logger.error(f"Failed to parse user info: {e}")
        return None


async def get_xhs_user_info(
    cookie_str: str,
    user_id: str,
) -> Dict:
    """
    获取小红书用户详细信息

    Args:
        cookie_str: 小红书 cookie 字符串
        user_id: 用户 ID

    Returns:
        用户信息 dict，包含:
        - user_id: 用户ID
        - nickname: 昵称
        - avatar: 头像
        - desc: 简介
        - fans: 粉丝数
        - follows: 关注数
        - interaction: 获赞与收藏数
    """
    if not cookie_str:
        return {"error": "Cookie 未配置"}

    cookie_dict = _parse_cookie_str(cookie_str)

    async with async_playwright() as pw:
        # 尝试使用系统 Chrome，避免下载 Chromium
        try:
            browser = await pw.chromium.launch(headless=True, channel="chrome")
        except Exception as e:
            logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
            browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_XHS_HEADERS["User-Agent"])

        # 注入 stealth.js 防检测
        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)

        # 设置 cookie
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
            for k, v in cookie_dict.items()
        ])

        page = await context.new_page()

        try:
            # 访问用户主页
            url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
            logger.info(f"Fetching user info: {url}")
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)  # 增加等待时间

            # 获取页面 HTML
            html = await page.content()
            logger.info(f"Got HTML length: {len(html)}")

            # 解析用户信息
            user_data = _extract_user_info_from_html(html)

            if not user_data:
                logger.warning(f"Unable to parse user info for {user_id}")
                return {"error": "无法解析用户信息，可能需要验证"}

            # 提取关键字段
            basic_info = user_data.get("basicInfo", {})
            interactions = user_data.get("interactions", [])
            logger.info(f"User {user_id} basicInfo keys: {list(basic_info.keys())}")
            logger.info(f"User {user_id} interactions: {interactions}")

            # 解析互动数据
            fans = 0
            follows = 0
            interaction = 0  # 获赞与收藏

            for item in interactions:
                item_type = item.get("type")
                count = item.get("count", 0)
                if isinstance(count, str):
                    # 处理 "1.2万" 这样的格式
                    count = _parse_count(count)
                if item_type == "fans":
                    fans = count
                elif item_type == "follows":
                    follows = count
                elif item_type == "interaction":
                    interaction = count

            return {
                "success": True,
                "user_id": user_id,
                "nickname": basic_info.get("nickname", ""),
                "avatar": basic_info.get("imageb", "") or basic_info.get("image", ""),
                "desc": basic_info.get("desc", ""),
                "gender": basic_info.get("gender", 0),
                "ip_location": basic_info.get("ipLocation", ""),
                "fans": fans,
                "follows": follows,
                "interaction": interaction,  # 获赞与收藏
                "raw": user_data,  # 原始数据，方便调试
            }

        except Exception as e:
            logger.error(f"Get XHS user info failed: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            await browser.close()


def _parse_count(count_str: str) -> int:
    """解析数量字符串，如 '1.2万' -> 12000"""
    if not count_str:
        return 0
    try:
        if "万" in count_str:
            return int(float(count_str.replace("万", "")) * 10000)
        elif "亿" in count_str:
            return int(float(count_str.replace("亿", "")) * 100000000)
        else:
            return int(count_str)
    except:
        return 0


async def batch_get_xhs_users_info(
    cookie_str: str,
    user_ids: list[str],
    interval: float = 2.0,
) -> list[Dict]:
    """
    批量获取小红书用户信息

    Args:
        cookie_str: cookie
        user_ids: 用户ID列表
        interval: 请求间隔（秒）
    """
    results = []
    for uid in user_ids:
        result = await get_xhs_user_info(cookie_str, uid)
        results.append(result)
        await asyncio.sleep(interval)
    return results
