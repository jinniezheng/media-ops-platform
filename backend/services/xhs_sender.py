"""小红书评论发送服务

依赖 Playwright 进行签名，复用 collector/xhs/sign.py
"""
import asyncio
import json
import logging
import os
from typing import Dict

from playwright.async_api import async_playwright, Page
import httpx

# 复用现有签名模块
from collector.xhs.sign import sign_with_playwright

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "libs", "stealth.min.js"
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

_HOST = "https://edith.xiaohongshu.com"


def _parse_cookie_str(cookie_str: str) -> dict:
    """将 cookie 字符串解析为 dict"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def send_xhs_comment(
    cookie_str: str,
    note_id: str,
    content: str,
    target_comment_id: str = "",
) -> Dict:
    """
    发送小红书评论

    Args:
        cookie_str: 小红书 cookie 字符串
        note_id: 笔记 ID
        content: 评论内容
        target_comment_id: 回复的评论 ID（为空表示一级评论）

    Returns:
        API 响应 dict
    """
    if not cookie_str:
        return {"code": -1, "msg": "Cookie 未配置", "success": False}

    cookie_dict = _parse_cookie_str(cookie_str)
    a1 = cookie_dict.get("a1", "")

    if not a1:
        return {"code": -1, "msg": "Cookie 中缺少 a1", "success": False}

    headers = {**_XHS_HEADERS, "Cookie": cookie_str}

    # 构建请求数据
    uri = "/api/sns/web/v1/comment/post"
    payload = {
        "note_id": note_id,
        "content": content,
    }
    if target_comment_id:
        payload["target_comment_id"] = target_comment_id

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
        await asyncio.sleep(2)

        try:
            # 获取签名 (复用现有签名模块)
            signs = await sign_with_playwright(
                page=page, uri=uri, data=payload, a1=a1, method="POST"
            )

            headers.update({
                "X-S": signs["x-s"],
                "X-T": signs["x-t"],
                "x-S-Common": signs["x-s-common"],
                "X-B3-Traceid": signs["x-b3-traceid"],
            })

            # 发送请求
            json_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_HOST}{uri}",
                    content=json_str,
                    headers=headers,
                )

            result = resp.json()
            logger.info(f"XHS comment response: {result}")

            return result

        except Exception as e:
            logger.error(f"Send XHS comment failed: {e}", exc_info=True)
            return {"code": -1, "msg": str(e), "success": False}
        finally:
            await browser.close()


async def send_xhs_reply(
    cookie_str: str,
    note_id: str,
    content: str,
    target_comment_id: str,
) -> Dict:
    """
    回复小红书评论（send_xhs_comment 的别名，显式指定 target_comment_id）
    """
    return await send_xhs_comment(
        cookie_str=cookie_str,
        note_id=note_id,
        content=content,
        target_comment_id=target_comment_id,
    )


async def check_xhs_cookie(cookie_str: str) -> Dict:
    """
    检测小红书 cookie 是否有效

    调用 /api/sns/web/v1/user/selfinfo 轻量接口验证。
    Returns:
        {"valid": True/False, "nickname": "...", "msg": "..."}
    """
    if not cookie_str:
        return {"valid": False, "msg": "Cookie 为空"}

    cookie_dict = _parse_cookie_str(cookie_str)
    a1 = cookie_dict.get("a1", "")
    if not a1:
        return {"valid": False, "msg": "Cookie 中缺少 a1"}

    headers = {**_XHS_HEADERS, "Cookie": cookie_str}
    uri = "/api/sns/web/v1/user/selfinfo"

    async with async_playwright() as pw:
        # 尝试使用系统 Chrome，避免下载 Chromium
        try:
            browser = await pw.chromium.launch(headless=True, channel="chrome")
        except Exception as e:
            logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
            browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=headers["User-Agent"])

        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)

        await context.add_cookies([
            {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
            for k, v in cookie_dict.items()
        ])

        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        try:
            signs = await sign_with_playwright(
                page=page, uri=uri, data={}, a1=a1, method="GET"
            )
            headers.update({
                "X-S": signs["x-s"],
                "X-T": signs["x-t"],
                "x-S-Common": signs["x-s-common"],
                "X-B3-Traceid": signs["x-b3-traceid"],
            })

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_HOST}{uri}", headers=headers)

            if resp.status_code != 200:
                return {"valid": False, "msg": f"HTTP {resp.status_code}"}

            data = resp.json()
            logger.info(f"XHS cookie check response: {data}")

            # 正常响应结构: {"code": 0, "success": true, "data": {"nickname": "...", ...}}
            if data.get("success") or data.get("code") == 0:
                nickname = data.get("data", {}).get("nickname", "")
                return {"valid": True, "nickname": nickname, "msg": "Cookie 有效"}
            else:
                return {"valid": False, "msg": data.get("msg", "Cookie 已失效")}

        except Exception as e:
            logger.error(f"Check XHS cookie failed: {e}", exc_info=True)
            return {"valid": False, "msg": f"检测失败: {str(e)}"}
        finally:
            await browser.close()
