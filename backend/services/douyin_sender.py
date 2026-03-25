"""抖音评论发送服务

使用 Playwright 模拟用户操作发送评论，通过拦截 API 响应确认发送结果。
"""
import asyncio
import logging
import os
from typing import Dict

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "libs", "stealth.min.js"
)

_DOUYIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

# 抖音评论发布 API 路径特征（路径包含其中之一即为评论提交接口）
_COMMENT_API_PATTERNS = [
    "/comment/publish",
    "/comment/post",
    "aweme/comment/add",
]


def _parse_cookie_str(cookie_str: str) -> dict:
    """将 cookie 字符串解析为 dict"""
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def send_douyin_comment(
    cookie_str: str,
    aweme_id: str,
    content: str,
    reply_to_cid: str = "",
    reply_to_text: str = "",
    reply_to_nickname: str = "",
    **kwargs,
) -> Dict:
    """
    发送抖音评论

    Args:
        cookie_str: 抖音 cookie 字符串
        aweme_id: 视频 ID
        content: 评论内容
        reply_to_cid: 回复的评论 ID（为空表示一级评论，暂不支持回复）
        reply_to_text: 回复的评论内容（用于日志记录）
        reply_to_nickname: 回复的用户昵称（用于日志记录）
        **kwargs: 其他关键字参数（用于向前兼容）

    Returns:
        成功返回 {"success": True, "code": 0, "message": "评论成功"}
        失败返回 {"success": False, "code": -1, "message": "错误信息"}
    """
    if not cookie_str:
        return {"success": False, "code": -1, "message": "Cookie 未配置"}

    cookie_dict = _parse_cookie_str(cookie_str)

    logger.info(f"[Douyin] 开始发送评论，视频ID: {aweme_id}, 内容长度: {len(content)}")

    async with async_playwright() as pw:
        _launch_args = [
            "--disable-save-password-bubble",
            "--password-store=basic",
            "--disable-features=PasswordManager",
        ]
        try:
            browser = await pw.chromium.launch(headless=True, channel="chrome", args=_launch_args)
            logger.info("[Douyin] 使用系统 Chrome 启动浏览器")
        except Exception as e:
            logger.warning(f"[Douyin] 无法使用系统 Chrome: {e}，尝试默认启动")
            browser = await pw.chromium.launch(headless=True, args=_launch_args)

        # 使用 1920x1080 视口：stealth.js 会使评论区渲染到页面右侧（x>1400），
        # 1280 宽的视口点击坐标超出范围，必须用 1920+ 才能覆盖评论区
        context = await browser.new_context(
            user_agent=_DOUYIN_HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
        )

        # 注入 stealth.js 降低被抖音识别为机器人的概率
        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)
            logger.info("[Douyin] 已注入 stealth.js")

        # 设置 Cookie
        logger.info(f"[Douyin] 设置 {len(cookie_dict)} 个 Cookie")
        await context.add_cookies([
            {"name": k, "value": v, "domain": ".douyin.com", "path": "/"}
            for k, v in cookie_dict.items()
        ])

        page = await context.new_page()

        # ── 拦截评论 API 响应，作为最可靠的成功/失败判据 ──────────────────
        api_result: Dict = {"fired": False, "success": False, "message": "", "url": "", "needs_verify": False}
        comment_api_event = asyncio.Event()

        async def on_response(response):
            url = response.url
            if not any(p in url for p in _COMMENT_API_PATTERNS):
                return
            logger.info(f"[Douyin] 捕获评论API响应: {url[:80]} status={response.status}")
            api_result["url"] = url
            api_result["fired"] = True

            # ── 优先检查二次身份验证拦截（HTTP 200 但 body 为空）────────────
            verify_header = response.headers.get("x-tt-verify-passport-decision", "")
            if verify_header and '"account_flow":"verify"' in verify_header:
                api_result["success"] = False
                api_result["needs_verify"] = True
                api_result["message"] = (
                    "账号需要完成身份验证才能发评论，"
                    "请在抖音 APP 中进行手机验证或扫码验证后重试"
                )
                logger.warning("[Douyin] 账号需要二次身份验证（verify_scene=comment），评论被拦截")
                comment_api_event.set()
                return

            if response.status == 403:
                api_result["success"] = False
                api_result["message"] = "评论被拒绝（HTTP 403），账号可能受限或触发风控"
                logger.warning("[Douyin] 评论API返回 403，账号可能受限")
                comment_api_event.set()
                return

            try:
                data = await response.json()
                status_code = data.get("status_code", data.get("code", -1))
                if status_code == 0:
                    api_result["success"] = True
                    api_result["message"] = "评论发送成功"
                    logger.info("[Douyin] API确认：评论发送成功")
                else:
                    status_msg = (
                        data.get("status_msg")
                        or data.get("message")
                        or f"code={status_code}"
                    )
                    api_result["success"] = False
                    api_result["message"] = status_msg
                    logger.warning(f"[Douyin] API确认：评论发送失败 - {status_msg}")
                comment_api_event.set()
            except Exception as e:
                logger.warning(f"[Douyin] 解析评论API响应失败: {e}")
                api_result["success"] = False
                api_result["message"] = f"HTTP {response.status}（响应体解析失败）"
                comment_api_event.set()

        # 同时记录所有 POST 请求以便诊断（仅 debug 级别）
        async def on_request(request):
            if request.method == "POST":
                logger.debug(f"[Douyin] POST请求: {request.url}")

        page.on("response", on_response)
        page.on("request", on_request)

        # 自动关闭所有原生 dialog
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

        async def full_screenshot(path: str):
            """截图（视口已在页面加载后调整为实际宽度，直接截图即可）"""
            # await page.screenshot(path=path, full_page=False)
            # logger.info(f"[Douyin] 截图已保存: {path}")

        try:
            # 访问视频页面
            video_url = f"https://www.douyin.com/video/{aweme_id}"
            logger.info(f"[Douyin] 访问视频页面: {video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=30000)

            # 等待页面真正渲染完成（专门等评论相关元素，最长20秒）
            try:
                await page.wait_for_selector(
                    '[class*="comment"], [class*="Comment"]',
                    timeout=20000,
                )
                logger.info("[Douyin] 评论区已渲染")
            except Exception:
                logger.warning("[Douyin] 等待评论区超时，继续尝试")
            await asyncio.sleep(2)

            # 尝试向右滚动，使评论区域可见（test_douyin_send.py 中的成功逻辑）
            await page.evaluate("window.scrollTo(2000, 0)")
            scroll_x = await page.evaluate("window.scrollX")
            scroll_y = await page.evaluate("window.scrollY")
            logger.info(f"[Douyin] 滚动后位置: scrollX={scroll_x}, scrollY={scroll_y}")
            await asyncio.sleep(1)

            # 关闭弹窗（登录提示等）
            # 策略1：ESC
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

            # 策略2：点击已知关闭文字按钮
            for modal_text in ["取消", "关闭", "不保存", "我知道了", "确定"]:
                try:
                    btn = page.get_by_text(modal_text, exact=True)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        await btn.first.click()
                        logger.info(f"[Douyin] 关闭模态框: '{modal_text}'")
                        await asyncio.sleep(0.8)
                except Exception:
                    pass

            current_url = page.url
            title = await page.title()
            logger.info(f"[Douyin] 页面标题: {title}")
            logger.info(f"[Douyin] 当前URL: {current_url}")

            # 检查页面类型
            is_video_page = "/video/" in current_url
            is_note_page = "/note/" in current_url
            logger.info(f"[Douyin] 页面类型检测: is_video_page={is_video_page}, is_note_page={is_note_page}")

            # ── 页面加载后立刻截图（调试用）+ 打印右侧评论相关元素 ───────────
            # await full_screenshot(f"douyin_loaded_{aweme_id}.png")
            try:
                debug_els = await page.evaluate("""() => {
                    const els = document.querySelectorAll('[class*="comment"],[class*="Comment"],[data-e2e*="comment"]');
                    return Array.from(els).map(el => {
                        const r = el.getBoundingClientRect();
                        return {tag: el.tagName, cls: el.className.slice(0,60),
                                e2e: el.getAttribute('data-e2e')||'',
                                x: Math.round(r.x), y: Math.round(r.y),
                                w: Math.round(r.width), h: Math.round(r.height),
                                text: el.innerText.slice(0,30).replace(/\\n/g,' ')};
                    }).filter(e => e.w > 0 && e.h > 0);
                }""")
                for el in debug_els:
                    logger.info(f"[Douyin][DOM] {el['tag']} e2e={el['e2e']!r} x={el['x']} y={el['y']} w={el['w']} text={el['text']!r}")
            except Exception as e:
                logger.warning(f"[Douyin] DOM 调试失败: {e}")

            if aweme_id not in current_url:
                logger.warning(f"[Douyin] URL 不含视频ID ({current_url})，尝试重新导航")
                await page.goto(video_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)
                current_url = page.url
                logger.info(f"[Douyin] 重新导航后 URL: {current_url}")

            # ── 第一步：找到并点击评论输入框占位符以激活输入 ──────────────────
            placeholder_clicked = False
            placeholder_texts = ["留下你的精彩评论吧", "抢首评", "说点什么", "添加评论"]

            async def click_placeholder(texts):
                """用 JS 直接获取占位符坐标并点击，避免 scroll_into_view 对不可见元素超时"""
                for text in texts:
                    try:
                        result = await page.evaluate(f"""() => {{
                            const walker = document.createTreeWalker(
                                document.body, NodeFilter.SHOW_TEXT);
                            let node;
                            while (node = walker.nextNode()) {{
                                if (node.textContent.trim() === {repr(text)}) {{
                                    const el = node.parentElement;
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 30) {{
                                        el.scrollIntoView({{block:'center'}});
                                        return {{x: r.x + r.width*0.5, y: r.y + r.height*0.5,
                                                w: r.width, h: r.height}};
                                    }}
                                }}
                            }}
                            return null;
                        }}""")
                        if result and result["w"] > 30:
                            # 第一次点击
                            await page.mouse.click(result["x"], result["y"])
                            logger.info(f"[Douyin] JS点击占位符 '{text}' at ({result['x']:.0f},{result['y']:.0f})")
                            await asyncio.sleep(1.5)

                            # 检查是否激活了输入框（contenteditable元素是否出现）
                            for check_attempt in range(2):  # 最多检查2次
                                ce_elem = await page.query_selector('div[contenteditable="true"]')
                                if ce_elem and await ce_elem.is_visible():
                                    logger.info(f"[Douyin] 点击后成功激活输入框（检查 {check_attempt + 1}/2）")
                                    return True

                                if check_attempt == 0:
                                    # 第一次检查失败，等待一下再次点击
                                    logger.info(f"[Douyin] 第一次点击后未激活输入框，尝试再次点击")
                                    await asyncio.sleep(0.5)
                                    await page.mouse.click(result["x"], result["y"])
                                    await asyncio.sleep(1.5)
                                else:
                                    await asyncio.sleep(0.5)

                            # 如果两次检查都失败，返回False让上层重试
                            logger.warning(f"[Douyin] 点击占位符后未能确认激活输入框，返回False重试")
                            return False
                    except Exception as e:
                        logger.debug(f"[Douyin] JS点击占位符 '{text}' 失败: {e}")
                return False
            # 首先尝试使用JS点击占位符（在笔记页特别有效）
            placeholder_clicked = await click_placeholder(placeholder_texts)

            # 如果JS点击失败，使用标准locator方法
            if not placeholder_clicked:
                for text in placeholder_texts:
                    try:
                        locator = page.get_by_text(text, exact=True)
                        if await locator.count() > 0:
                            # 先滚动到元素，确保坐标在视口范围内
                            await locator.first.scroll_into_view_if_needed(timeout=2000)
                            await asyncio.sleep(0.5)
                            bbox = await locator.first.bounding_box()
                            if bbox and bbox["width"] > 30:
                                cx = bbox["x"] + bbox["width"] * 0.5
                                cy = bbox["y"] + bbox["height"] * 0.5

                                # 根据页面类型选择点击策略
                                if is_video_page:
                                    # 视频页面：尝试多种点击方式
                                    click_methods = [
                                        ("single_click", lambda: page.mouse.click(cx, cy)),
                                        ("double_click", lambda: page.mouse.dblclick(cx, cy)),
                                        ("js_click", lambda: locator.first.click()),
                                        ("focus_then_click", lambda: page.evaluate(f"""(cx, cy) => {{
                                            const elem = document.elementFromPoint(cx, cy);
                                            if (elem) {{
                                                elem.focus();
                                                elem.click();
                                            }}
                                        }}""", cx, cy))
                                    ]

                                    for method_name, click_func in click_methods:
                                        try:
                                            logger.info(f"[Douyin] 尝试点击方式: {method_name} at ({cx:.0f},{cy:.0f})")
                                            await click_func()
                                            await asyncio.sleep(0.5)

                                            # 检查是否激活
                                            ce_elem = await page.query_selector('div[contenteditable="true"]')
                                            if ce_elem and await ce_elem.is_visible():
                                                placeholder_clicked = True
                                                logger.info(f"[Douyin] {method_name} 成功激活输入框")
                                                break
                                            else:
                                                logger.info(f"[Douyin] {method_name} 未激活输入框，尝试下一种方式")
                                        except Exception as method_e:
                                            logger.debug(f"[Douyin] 点击方式 {method_name} 失败: {method_e}")

                                    if not placeholder_clicked:
                                        # 如果所有方式都失败，使用默认单击并标记为已点击
                                        await page.mouse.click(cx, cy)
                                        placeholder_clicked = True
                                        await asyncio.sleep(2.0)
                                        logger.info(f"[Douyin] 使用默认单击 at ({cx:.0f},{cy:.0f})，但未确认激活")
                                else:
                                    # 笔记页或未知页面：使用原单击方式
                                    await page.mouse.click(cx, cy)
                                    placeholder_clicked = True
                                    await asyncio.sleep(1.5)
                                    logger.info(f"[Douyin] 已激活评论输入框: '{text}' (click at {cx:.0f},{cy:.0f})")

                                if placeholder_clicked:
                                    break
                    except Exception as e:
                        logger.debug(f"[Douyin] 找不到占位符 '{text}': {e}")

            # 策略3：使用XPath查找占位符（test_douyin_send.py 中的成功逻辑）
            if not placeholder_clicked:
                try:
                    # 根据用户提供的HTML结构，占位符在 .comment-input-container 内的 span 元素中
                    # 先找父容器，再找包含占位符文本的span
                    container = await page.query_selector('.comment-input-container')
                    if container:
                        # 在容器内查找包含占位符文本的span
                        for text in placeholder_texts:
                            try:
                                # 使用XPath查找包含特定文本的span
                                xpath = f".//span[contains(text(), '{text}')]"
                                placeholder_elems = await container.query_selector_all(xpath)
                                if placeholder_elems:
                                    placeholder_elem = placeholder_elems[0]
                                    text_content = await placeholder_elem.text_content()
                                    logger.info(f"[Douyin] 找到占位符元素: 容器内找到包含文本 '{text}' 的span, text='{text_content}'")
                                    # 滚动到元素并点击
                                    await placeholder_elem.scroll_into_view_if_needed()
                                    await asyncio.sleep(0.3)
                                    bbox = await placeholder_elem.bounding_box()
                                    if bbox and bbox["width"] > 30:
                                        cx = bbox["x"] + bbox["width"] * 0.5
                                        cy = bbox["y"] + bbox["height"] * 0.5
                                        await page.mouse.click(cx, cy)
                                        placeholder_clicked = True
                                        await asyncio.sleep(1.5)
                                        logger.info(f"[Douyin] 通过容器XPath点击占位符: '{text}' at ({cx:.0f},{cy:.0f})")
                                        break
                            except Exception as inner_e:
                                logger.debug(f"[Douyin] 查找占位符文本 '{text}' 失败: {inner_e}")

                    # 如果没有找到，尝试直接查找包含占位符文本的span
                    if not placeholder_clicked:
                        for text in placeholder_texts:
                            try:
                                # 在整个页面中查找包含文本的span
                                xpath = f"//span[contains(text(), '{text}')]"
                                placeholder_elems = await page.query_selector_all(xpath)
                                if placeholder_elems:
                                    # 选择最可能是评论输入框的那个（通常是x坐标较大，在右侧）
                                    elems_with_pos = []
                                    for elem in placeholder_elems:
                                        try:
                                            bbox = await elem.bounding_box()
                                            if bbox and bbox["width"] > 30:
                                                elems_with_pos.append((elem, bbox["x"], bbox["y"]))
                                        except Exception:
                                            continue

                                    if elems_with_pos:
                                        # 选择x坐标最大的（最靠右侧）
                                        elem_to_click = max(elems_with_pos, key=lambda x: x[1])[0]
                                        bbox = await elem_to_click.bounding_box()
                                        cx = bbox["x"] + bbox["width"] * 0.5
                                        cy = bbox["y"] + bbox["height"] * 0.5
                                        await page.mouse.click(cx, cy)
                                        placeholder_clicked = True
                                        await asyncio.sleep(1.5)
                                        logger.info(f"[Douyin] 通过XPath点击占位符: '{text}' at ({cx:.0f},{cy:.0f})")
                                        break
                            except Exception as inner_e:
                                logger.debug(f"[Douyin] XPath查找占位符文本 '{text}' 失败: {inner_e}")
                except Exception as e:
                    logger.warning(f"[Douyin] XPath选择器查找占位符失败: {e}")

            if not placeholder_clicked:
                for sel in [
                    '[data-e2e="comment-input"]',
                    '[data-e2e="comment-input-place"]',
                    '[class*="commentInput"]',
                    '[class*="comment-input"]',
                    '.comment-input-container',  # 根据用户提供的HTML结构
                    '.hVeIqFGi',  # 占位符span的class
                ]:
                    try:
                        elem = await page.query_selector(sel)
                        if elem and await elem.is_visible():
                            await elem.scroll_into_view_if_needed()
                            await asyncio.sleep(0.3)
                            bbox = await elem.bounding_box()
                            if bbox and bbox["width"] > 30:
                                cx = bbox["x"] + bbox["width"] * 0.5
                                cy = bbox["y"] + bbox["height"] * 0.5
                                await page.mouse.click(cx, cy)
                                placeholder_clicked = True
                                await asyncio.sleep(1.5)
                                logger.info(f"[Douyin] 通过选择器激活输入框: '{sel}'")
                                break
                    except Exception as e:
                        logger.debug(f"[Douyin] 选择器失败 '{sel}': {e}")

            # ── 第一步 fallback：点击右侧面板顶部的"评论"Tab 切换到评论视图 ──────
            if not placeholder_clicked:
                # 保持 1920px 视口，不扩缩，确保后续 JS 坐标与视口一致
                # await page.screenshot(path=f"douyin_no_placeholder_{aweme_id}.png")
                # logger.info(f"[Douyin] 截图已保存 (1920px): douyin_no_placeholder_{aweme_id}.png")
                comment_panel_clicked = False

                # JS 查找"评论"Tab
                # 第一步：全页找文字精确匹配"评论"或"评论N"的叶子元素（排除大容器）
                # 不再过滤 <a> 标签，因为抖音 Tab 可能就是 <a> 元素
                try:
                    # 先全页扫描 "评论" 文字元素，记录位置和是否在 <a> 内，供诊断
                    all_comment_els = await page.evaluate(r"""() => {
                        const tabPat = /^评论[\s\d（()万+]*$/;
                        return Array.from(document.querySelectorAll('*')).filter(el => {
                            const r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) return false;
                            const text = (el.innerText || '').trim();
                            return tabPat.test(text);
                        }).map(el => {
                            const r = el.getBoundingClientRect();
                            return {tag: el.tagName,
                                    x: Math.round(r.x + r.width/2),
                                    y: Math.round(r.y + r.height/2),
                                    w: Math.round(r.width), h: Math.round(r.height),
                                    text: (el.innerText||'').slice(0,30),
                                    inA: !!el.closest('a'),
                                    e2e: el.getAttribute('data-e2e')||''};
                        });
                    }""")
                    for c in all_comment_els:
                        logger.info(f"[Douyin][TAB-ALL] tag={c['tag']} x={c['x']} y={c['y']} w={c['w']} inA={c['inA']} e2e={c['e2e']!r} text={c['text']!r}")

                    if all_comment_els:
                        # 优先选 x 最大（最靠右侧面板）且 y 最小（最靠顶部）的
                        best = min(all_comment_els, key=lambda c: (c["y"], -c["x"]))
                        logger.info(f"[Douyin] 通过JS直接click评论Tab text={best['text']!r} inA={best['inA']}")
                        # 用 JS 直接点击，避免坐标因视口扩缩而错位
                        clicked = await page.evaluate(r"""() => {
                            const pat = /^评论[\s\d（()万+]*$/;
                            const els = Array.from(document.querySelectorAll('*')).filter(el => {
                                const r = el.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && pat.test((el.innerText||'').trim());
                            }).sort((a,b) => b.getBoundingClientRect().x - a.getBoundingClientRect().x);
                            if (els[0]) { els[0].scrollIntoView(); els[0].click(); return true; }
                            return false;
                        }""")
                        await asyncio.sleep(3)
                        current_url = page.url
                        logger.info(f"[Douyin] 点击评论Tab后URL: {current_url}")

                        # 检查是否切换到笔记页
                        if "/note/" in current_url:
                            logger.info("[Douyin] 已切换到笔记页，在笔记页尝试点击占位符")
                            # 在笔记页再次尝试JS点击占位符
                            note_placeholder_clicked = await click_placeholder(placeholder_texts)
                            if note_placeholder_clicked:
                                placeholder_clicked = True
                                comment_panel_clicked = True
                                logger.info("[Douyin] 笔记页成功激活输入框")
                            else:
                                logger.warning("[Douyin] 笔记页也无法激活输入框")
                                comment_panel_clicked = True
                        else:
                            # 仍在视频页，继续原有逻辑
                            logger.info("[Douyin] 仍在视频页，继续尝试")
                            comment_panel_clicked = True
                    else:
                        logger.info("[Douyin] 全页未找到匹配'评论'Tab的元素，记录右侧顶部所有元素")
                        diag = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('*'))
                                .filter(el => {
                                    const r = el.getBoundingClientRect();
                                    return r.width > 0 && r.height > 0
                                        && r.x > 800 && r.y >= 20 && r.y <= 300;
                                }).map(el => {
                                    const r = el.getBoundingClientRect();
                                    return {tag: el.tagName, x: Math.round(r.x),
                                            y: Math.round(r.y), w: Math.round(r.width),
                                            h: Math.round(r.height),
                                            text: (el.innerText||'').slice(0,40)};
                                }).slice(0, 40);
                        }""")
                        for d in diag:
                            logger.info(f"[Douyin][TAB-DIAG] {d['tag']} x={d['x']} y={d['y']} w={d['w']} h={d['h']} text={d['text']!r}")
                except Exception as e:
                    logger.warning(f"[Douyin] JS查找评论Tab失败: {e}")

                if comment_panel_clicked and not placeholder_clicked:
                    # await page.screenshot(path=f"douyin_panel_clicked_{aweme_id}.png")
                    # logger.info(f"[Douyin] 评论栏点击后截图: douyin_panel_clicked_{aweme_id}.png")

                    # 诊断：打印右侧面板所有可见元素和文本
                    await asyncio.sleep(1.5)
                    try:
                        diag2 = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('*'))
                                .filter(el => {
                                    const r = el.getBoundingClientRect();
                                    return r.width > 0 && r.height > 0 && r.x > 850;
                                }).map(el => {
                                    const r = el.getBoundingClientRect();
                                    const t = (el.innerText || el.textContent || '').trim().slice(0, 40);
                                    return {tag: el.tagName,
                                            e2e: el.getAttribute('data-e2e') || '',
                                            ce: el.getAttribute('contenteditable') || '',
                                            x: Math.round(r.x), y: Math.round(r.y),
                                            w: Math.round(r.width), h: Math.round(r.height),
                                            text: t};
                                }).filter(e => e.text || e.e2e || e.ce)
                                .slice(0, 60);
                        }""")
                        for d in diag2:
                            logger.info(f"[Douyin][PANEL] {d['tag']} e2e={d['e2e']!r} ce={d['ce']!r} x={d['x']} y={d['y']} w={d['w']} text={d['text']!r}")
                    except Exception as e:
                        logger.warning(f"[Douyin] 面板诊断失败: {e}")
                    try:
                        # 优先用 data-e2e 属性定位，使用与test_douyin_send.py一致的选择器
                        ce_selectors = [
                            'div[contenteditable="true"]',  # 最通用的选择器
                            '[contenteditable="true"]',  # 更通用的选择器
                            '[data-e2e="comment-input"]',
                            '[data-e2e="comment-input-place"]',
                            '.comment-input-container div',  # 在评论输入容器内找div
                            '.comment-input-inner-container div',  # 内层容器
                            '.GXmFLge7 div',  # comment-input-inner-container的class
                            '.Zm7vgtya div',  # commentInput-right-ct的class
                            '.lFk180Rt div',  # 占位符所在容器的兄弟容器
                            '.public-DraftEditor-content[contenteditable="true"]',  # 保留旧选择器
                            '.notranslate.public-DraftEditor-content[contenteditable="true"]',
                        ]
                        ce = None
                        for selector in ce_selectors:
                            try:
                                elem = await page.query_selector(selector)
                                if elem:
                                    ce = elem
                                    logger.info(f"[Douyin] 使用选择器找到contenteditable元素: {selector}")
                                    break
                            except Exception as e:
                                logger.debug(f"[Douyin] 选择器 {selector} 失败: {e}")
                        if ce:
                            try:
                                bb = await ce.bounding_box()
                                logger.info(f"[Douyin] 找到 contenteditable 元素 bbox={bb}")
                                if bb and bb["width"] > 30:
                                    await ce.scroll_into_view_if_needed()
                                    await asyncio.sleep(0.3)
                                    await page.mouse.click(bb["x"] + bb["width"] * 0.5, bb["y"] + bb["height"] * 0.5)
                                    placeholder_clicked = True
                                    await asyncio.sleep(1.5)
                                    logger.info(f"[Douyin] 直接点击 contenteditable 成功")
                                else:
                                    logger.warning(f"[Douyin] contenteditable 元素bbox无效: {bb}")
                            except Exception as e:
                                logger.warning(f"[Douyin] 点击contenteditable元素失败: {e}")
                    except Exception as e:
                        logger.warning(f"[Douyin] 直接点击评论输入框失败: {e}")

                    # 策略1：在笔记页面使用JS查找并点击占位符（test_douyin_send.py 中的成功逻辑）
                    if not placeholder_clicked:
                        async def click_placeholder_js_in_note(texts):
                            for text in texts:
                                try:
                                    result = await page.evaluate(f"""() => {{
                                        const walker = document.createTreeWalker(
                                            document.body, NodeFilter.SHOW_TEXT);
                                        let node;
                                        while (node = walker.nextNode()) {{
                                            if (node.textContent.trim() === {repr(text)}) {{
                                                const el = node.parentElement;
                                                const r = el.getBoundingClientRect();
                                                if (r.width > 30) {{
                                                    el.scrollIntoView({{block:'center'}});
                                                    return {{x: r.x + r.width*0.5, y: r.y + r.height*0.5,
                                                            w: r.width, h: r.height}};
                                                }}
                                            }}
                                        }}
                                        return null;
                                    }}""")
                                    if result and result["w"] > 30:
                                        await page.mouse.click(result["x"], result["y"])
                                        logger.info(f"[Douyin] 笔记页JS点击占位符 '{text}' at ({result['x']:.0f},{result['y']:.0f})")
                                        await asyncio.sleep(1.5)
                                        return True
                                except Exception as e:
                                    logger.debug(f"[Douyin] 笔记页JS点击占位符 '{text}' 失败: {e}")
                            return False

                        placeholder_clicked = await click_placeholder_js_in_note(placeholder_texts)

                    # 策略2：如果JS点击失败，使用XPath查找占位符（test_douyin_send.py 中的成功逻辑）
                    if not placeholder_clicked:
                        try:
                            # 在整个页面中查找包含占位符文本的span
                            for text in placeholder_texts:
                                try:
                                    # 在整个页面中查找包含文本的span
                                    xpath = f"//span[contains(text(), '{text}')]"
                                    placeholder_elems = await page.query_selector_all(xpath)
                                    if placeholder_elems:
                                        # 选择最可能是评论输入框的那个（通常是x坐标较大，在右侧）
                                        elems_with_pos = []
                                        for elem in placeholder_elems:
                                            try:
                                                bbox = await elem.bounding_box()
                                                if bbox and bbox["width"] > 30:
                                                    elems_with_pos.append((elem, bbox["x"], bbox["y"]))
                                            except Exception:
                                                continue

                                        if elems_with_pos:
                                            # 选择x坐标最大的（最靠右侧）
                                            elem_to_click = max(elems_with_pos, key=lambda x: x[1])[0]
                                            bbox = await elem_to_click.bounding_box()
                                            cx = bbox["x"] + bbox["width"] * 0.5
                                            cy = bbox["y"] + bbox["height"] * 0.5
                                            await page.mouse.click(cx, cy)
                                            placeholder_clicked = True
                                            await asyncio.sleep(1.5)
                                            logger.info(f"[Douyin] 笔记页通过XPath点击占位符: '{text}' at ({cx:.0f},{cy:.0f})")
                                            break
                                except Exception as inner_e:
                                    logger.debug(f"[Douyin] 笔记页XPath查找占位符文本 '{text}' 失败: {inner_e}")
                        except Exception as e:
                            logger.warning(f"[Douyin] 笔记页XPath查找占位符失败: {e}")

                    # 兜底：用占位符文字查找
                    if not placeholder_clicked:
                        for text in placeholder_texts:
                            try:
                                locator = page.get_by_text(text, exact=True)
                                if await locator.count() > 0:
                                    await locator.first.scroll_into_view_if_needed()
                                    await asyncio.sleep(0.5)
                                    bbox = await locator.first.bounding_box()
                                    if bbox and bbox["width"] > 30:
                                        cx = bbox["x"] + bbox["width"] * 0.5
                                        cy = bbox["y"] + bbox["height"] * 0.5
                                        await page.mouse.click(cx, cy)
                                        placeholder_clicked = True
                                        await asyncio.sleep(1.5)
                                        logger.info(f"[Douyin] 展开评论栏后激活输入框: '{text}'")
                                        break
                            except Exception as e:
                                logger.debug(f"[Douyin] 展开后找不到占位符 '{text}': {e}")

                    # 仍未激活：直接找 contenteditable 并点击
                    if not placeholder_clicked:
                        try:
                            ce = await page.query_selector('div[contenteditable="true"]')
                            if ce and await ce.is_visible():
                                bb = await ce.bounding_box()
                                if bb and bb["width"] > 30:
                                    await ce.scroll_into_view_if_needed()
                                    await asyncio.sleep(0.3)
                                    await ce.click()
                                    placeholder_clicked = True
                                    logger.info("[Douyin] 直接点击 contenteditable 激活输入框")
                        except Exception as e:
                            logger.debug(f"[Douyin] 直接点 contenteditable 失败: {e}")

            if not placeholder_clicked:
                await browser.close()
                return {
                    "success": False,
                    "code": -1,
                    "message": "找不到评论输入区域，请检查页面是否正常加载",
                }

            # ── 第二步：等待contenteditable元素出现并确保焦点 ─────────────────
            logger.info("[Douyin] 等待contenteditable元素出现")
            ce_div = None
            ce_selectors = [
                'div[contenteditable="true"]',  # 最通用的选择器（test_douyin_send.py 中的成功逻辑）
                '[contenteditable="true"]',  # 更通用的选择器
                '[data-e2e="comment-input"]',
                '[data-e2e="comment-input-place"]',
                '.comment-input-container div',  # 在评论输入容器内找div
                '.comment-input-inner-container div',  # 内层容器
                '.GXmFLge7 div',  # comment-input-inner-container的class
                '.Zm7vgtya div',  # commentInput-right-ct的class
                '.lFk180Rt div',  # 占位符所在容器的兄弟容器
                '.public-DraftEditor-content[contenteditable="true"]',  # 保留旧选择器
                '.notranslate.public-DraftEditor-content[contenteditable="true"]',
            ]

            # 重试查找contenteditable元素
            for attempt in range(3):
                for selector in ce_selectors:
                    try:
                        elem = await page.query_selector(selector)
                        if elem:
                            ce_div = elem
                            logger.info(f"[Douyin] 找到contenteditable元素: {selector}")
                            # 检查是否可见
                            if await elem.is_visible():
                                logger.info(f"[Douyin] contenteditable元素可见")
                            else:
                                logger.warning(f"[Douyin] contenteditable元素不可见，尝试滚动到视图中")
                            break  # 找到元素就跳出选择器循环
                    except Exception as e:
                        logger.debug(f"[Douyin] 选择器 {selector} 失败: {e}")

                if ce_div:
                    break
                else:
                    logger.info(f"[Douyin] 等待contenteditable元素... 尝试 {attempt + 1}/3")
                    await asyncio.sleep(1)

            if not ce_div:
                logger.warning("[Douyin] 未检测到 contenteditable div，继续尝试输入")

            # 显式点击 contenteditable 确保焦点正确（防止焦点丢失导致内容打到其他地方）
            if ce_div:
                try:
                    await ce_div.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    ce_bbox = await ce_div.bounding_box()
                    if ce_bbox and ce_bbox["width"] > 30:
                        cx = ce_bbox["x"] + ce_bbox["width"] * 0.5
                        cy = ce_bbox["y"] + ce_bbox["height"] * 0.5
                        await page.mouse.click(cx, cy)
                        await asyncio.sleep(0.5)
                        logger.info(f"[Douyin] 已点击contenteditable元素确保焦点 at ({cx:.0f},{cy:.0f})")

                        # 检查元素是否有焦点
                        is_focused = await page.evaluate("""(selector) => {
                            const el = document.querySelector(selector);
                            return el === document.activeElement;
                        }""", ce_selectors[0] if ce_selectors else 'div[contenteditable="true"]')
                        logger.info(f"[Douyin] contenteditable元素是否获得焦点: {is_focused}")
                except Exception as e:
                    logger.warning(f"[Douyin] 点击contenteditable失败: {e}")

            logger.info("[Douyin] 开始输入评论内容")
            await page.keyboard.type(content, delay=50)
            await asyncio.sleep(0.5)
            # await full_screenshot(f"douyin_typed_{aweme_id}.png")

            # ── 第三步：确认输入框中确实有文字 ─────────────────────────────
            typed_ok = False
            try:
                ce_div = await page.query_selector('div[contenteditable="true"]')
                if ce_div:
                    val = await ce_div.inner_text()
                    if content[:10] in val:
                        typed_ok = True
                        logger.info(f"[Douyin] 确认输入框内容: {repr(val[:50])}")
                    else:
                        logger.warning(f"[Douyin] 输入框内容不符: {repr(val[:80])}")
            except Exception as e:
                logger.warning(f"[Douyin] 检查输入框内容时出错: {e}")

            if not typed_ok:
                logger.warning("[Douyin] 无法确认文字已输入到评论框，尝试继续发送")

            # ── 第四步：按 Enter 提交评论 ────────────────
            logger.info("[Douyin] 按 Enter 提交评论")
            await page.keyboard.press("Enter")

            # ── 第五步：等待评论 API 响应（最权威的成功判据）──────────────
            try:
                await asyncio.wait_for(comment_api_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("[Douyin] 等待评论API响应超时（10s），降级为UI状态检测")

            await asyncio.sleep(1)

            # API 拦截到结果 → 直接使用
            if api_result["fired"]:
                await browser.close()
                result = {
                    "success": api_result["success"],
                    "code": 0 if api_result["success"] else -1,
                    "message": api_result["message"],
                }
                if api_result.get("needs_verify"):
                    result["needs_verify"] = True
                return result

            # ── API 未触发：通过 UI 状态判断（不可靠，截图留存）──────────────
            logger.warning("[Douyin] 评论API未触发，尝试通过UI状态判断")

            # 检查是否有错误弹窗
            error_selectors = [
                'text="评论失败"',
                'text="发送失败"',
                'text="操作频繁"',
                'text="请稍后"',
                'text="验证"',
                '.error-message',
            ]
            for selector in error_selectors:
                elem = await page.query_selector(selector)
                if elem:
                    try:
                        err_text = await elem.text_content()
                    except Exception:
                        err_text = selector
                    logger.warning(f"[Douyin] 检测到错误提示: {err_text}")
                    await browser.close()
                    return {
                        "success": False,
                        "code": -1,
                        "message": f"评论发送失败: {(err_text or selector)[:100]}",
                    }

            # 检查占位符是否重新出现（输入框已清空 = 提交成功）
            input_cleared = False
            for text in placeholder_texts:
                try:
                    locator = page.get_by_text(text, exact=True)
                    if await locator.count() > 0 and await locator.first.is_visible():
                        input_cleared = True
                        logger.info(f"[Douyin] 占位符重新出现 ('{text}')，可能提交成功")
                        break
                except Exception:
                    pass

            # await full_screenshot(f"douyin_submit_{aweme_id}.png")
            await browser.close()

            if input_cleared:
                logger.info("[Douyin] UI状态判断：评论可能已发送（输入框已清空）")
                return {
                    "success": True,
                    "code": 0,
                    "message": "评论可能已发送（未捕获到API响应，请人工确认）",
                }
            else:
                logger.warning("[Douyin] 评论提交状态不确定")
                return {
                    "success": False,
                    "code": -1,
                    "message": "评论API未响应，发送结果不确定，请检查页面截图",
                }

        except Exception as e:
            logger.error(f"[Douyin] 发送评论异常: {e}", exc_info=True)
            try:
                await browser.close()
            except Exception:
                pass
            return {
                "success": False,
                "code": -1,
                "message": f"发送过程中出现异常: {str(e)}",
            }


async def check_douyin_cookie(cookie_str: str) -> Dict:
    """
    检测抖音 cookie 是否有效

    Returns:
        {"valid": True/False, "nickname": "...", "message": "..."}
    """
    if not cookie_str:
        return {"valid": False, "message": "Cookie 为空"}

    cookie_dict = _parse_cookie_str(cookie_str)

    async with async_playwright() as pw:
        _launch_args = [
            "--disable-save-password-bubble",
            "--password-store=basic",
            "--disable-features=PasswordManager",
        ]
        try:
            browser = await pw.chromium.launch(headless=True, channel="chrome", args=_launch_args)
        except Exception as e:
            logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
            browser = await pw.chromium.launch(headless=True, args=_launch_args)
        context = await browser.new_context(
            user_agent=_DOUYIN_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
        )

        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)

        await context.add_cookies([
            {"name": k, "value": v, "domain": ".douyin.com", "path": "/"}
            for k, v in cookie_dict.items()
        ])

        page = await context.new_page()
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        try:
            await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            page_content = await page.content()
            if "退出登录" in page_content or "个人中心" in page_content:
                nickname = "未知用户"
                user_elements = await page.query_selector_all('div[class*="user"], span[class*="nickname"]')
                for elem in user_elements:
                    text = await elem.text_content()
                    if text and len(text) < 50:
                        nickname = text.strip()
                        break
                await browser.close()
                return {"valid": True, "nickname": nickname, "message": "Cookie 有效"}
            else:
                await browser.close()
                return {"valid": False, "message": "Cookie 可能已失效，未检测到登录状态"}
        except Exception as e:
            logger.error(f"[Douyin] Cookie 检测异常: {e}", exc_info=True)
            await browser.close()
            return {"valid": False, "message": f"检测失败: {str(e)}"}
