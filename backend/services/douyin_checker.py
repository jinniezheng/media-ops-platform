"""抖音 Cookie 有效性检测"""
import asyncio
import logging
import os
from typing import Dict

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(os.path.dirname(__file__), "..", "libs", "stealth.min.js")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 抖音登录态检测接口（页面加载时自动调用，无需手动签名）
_ACCOUNT_INFO_PATTERN = "/passport/web/account/info/"


def _parse_cookie_str(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def check_douyin_cookie(cookie_str: str) -> Dict:
    """
    检测抖音 Cookie 是否有效。
    通过 Playwright 打开抖音首页，拦截账号信息接口响应判断登录状态。
    Returns:
        {"valid": True/False, "nickname": "...", "msg": "..."}
    """
    if not cookie_str:
        return {"valid": False, "msg": "Cookie 为空"}

    cookie_dict = _parse_cookie_str(cookie_str)
    if not cookie_dict:
        return {"valid": False, "msg": "Cookie 格式不正确"}

    result: Dict = {"valid": False, "msg": "未获取到账号信息，Cookie 可能已失效"}
    detected = asyncio.Event()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1280, "height": 800},
        )

        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)

        await context.add_cookies([
            {"name": k, "value": v, "domain": ".douyin.com", "path": "/"}
            for k, v in cookie_dict.items()
        ])

        page = await context.new_page()

        async def on_response(resp):
            if _ACCOUNT_INFO_PATTERN not in resp.url:
                return
            try:
                data = await resp.json()
            except Exception:
                return

            status_code = data.get("status_code", -1)
            # status_code 0 表示已登录
            if status_code == 0:
                user = data.get("data", {})
                nickname = user.get("name", "") or user.get("nickname", "")
                result.update({"valid": True, "nickname": nickname, "msg": "Cookie 有效"})
            else:
                msg = data.get("description", "") or data.get("message", "Cookie 已失效")
                result.update({"valid": False, "msg": msg})
            detected.set()

        page.on("response", on_response)

        try:
            await page.goto(
                "https://www.douyin.com/", wait_until="domcontentloaded", timeout=30000
            )
            # 等待账号信息接口响应，最多 10 秒
            try:
                await asyncio.wait_for(detected.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                # 若接口未触发，尝试通过页面内容判断
                content = await page.content()
                if "退出登录" in content or "个人中心" in content:
                    result.update({"valid": True, "msg": "Cookie 有效（页面检测）"})
                elif "登录" in content and "注册" in content:
                    result.update({"valid": False, "msg": "未登录，Cookie 已失效"})
        except Exception as e:
            logger.error(f"Douyin cookie check error: {e}", exc_info=True)
            result.update({"valid": False, "msg": f"检测失败: {str(e)}"})
        finally:
            await browser.close()

    return result
