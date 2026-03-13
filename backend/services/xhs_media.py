"""小红书视频/图片解析服务

通过 XHS API (/api/sns/web/v1/feed) 获取笔记详情，解析：
- 视频：多画质 CDN 直链
- 图片：有水印/无水印版本
"""
import asyncio
import os
from typing import Dict, List

from playwright.async_api import async_playwright

from collector.xhs.client import XhsApiClient

_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "..", "libs", "stealth.min.js"
)

_XHS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Origin": "https://www.xiaohongshu.com",
    "Referer": "https://www.xiaohongshu.com",
    "Content-Type": "application/json;charset=UTF-8",
}


def _parse_cookie_str(cookie_str: str) -> dict:
    result = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _convert_to_original_image_url(url: str) -> str:
    """尝试获取无水印图片 URL。

    注意：XHS CDN 签名覆盖完整 URL（含 !nd_xxx 后缀），
    直接去掉后缀会导致 403。所以这里直接返回原始 URL。
    真正的无水印版需要从 info_list 中获取不同 scene 的 URL。
    """
    return url


def _parse_video_streams(video_data: Dict) -> Dict[str, str]:
    """从 note_card 的 video 字段解析多画质视频直链

    API 返回 snake_case 字段: master_url, backup_urls, width, height
    """
    result = {
        "video_url_1080p": "",
        "video_url_720p": "",
        "video_url_480p": "",
        "video_url_default": "",
    }

    media = video_data.get("media", {}) or {}
    stream = media.get("stream", {}) or {}

    # 优先 h265（画质选择更多），fallback h264
    streams = (
        stream.get("h265", [])
        or stream.get("h264", [])
        or stream.get("av1", [])
    )

    if not streams:
        # fallback: media.video.url 或 video_data 顶层
        video_info = media.get("video", {}) or {}
        url = video_info.get("url", "")
        if not url:
            for key in ["url", "master_url", "masterUrl"]:
                url = video_data.get(key, "")
                if url:
                    break
        result["video_url_default"] = url
        return result

    def _get_url(s: Dict) -> str:
        """从 stream 条目提取 URL，兼容两种命名"""
        url = (
            s.get("master_url", "")
            or s.get("masterUrl", "")
        )
        if not url:
            backups = s.get("backup_urls", []) or s.get("backupUrls", [])
            if backups:
                url = backups[0]
        return url

    def _get_width(s: Dict) -> int:
        return s.get("width", 0) or s.get("videoWidth", 0)

    sorted_streams = sorted(
        streams,
        key=lambda x: _get_width(x) * (x.get("height", 0) or x.get("videoHeight", 0)),
        reverse=True,
    )

    for s in sorted_streams:
        w = _get_width(s)
        url = _get_url(s)
        if not url:
            continue
        if w >= 1080 and not result["video_url_1080p"]:
            result["video_url_1080p"] = url
        elif w >= 720 and not result["video_url_720p"]:
            result["video_url_720p"] = url
        elif w >= 480 and not result["video_url_480p"]:
            result["video_url_480p"] = url

    result["video_url_default"] = (
        result["video_url_1080p"]
        or result["video_url_720p"]
        or result["video_url_480p"]
        or _get_url(sorted_streams[0])
    )
    return result


def _parse_image_list(image_list: List[Dict]) -> List[Dict]:
    """解析图片列表，兼容 API (下划线) 和 HTML (驼峰) 两种字段格式"""
    result = []
    for idx, img in enumerate(image_list):
        # 兼容两种命名: urlDefault / url_default
        url_default = (
            img.get("url_default", "")
            or img.get("urlDefault", "")
            or img.get("url_pre", "")
            or img.get("urlPre", "")
            or img.get("url", "")
        )
        # 从 info_list 获取不同版本
        info_list = img.get("info_list", []) or img.get("infoList", [])
        url_original = url_default  # 默认与 url_default 相同
        for info in info_list:
            scene = info.get("image_scene", "") or info.get("imageScene", "")
            info_url = info.get("url", "")
            if not info_url:
                continue
            # WB_DFT = 默认带水印, CRD_WM = 卡片水印
            # WB_PRV = 预览, CRD_PRV = 卡片预览
            if scene in ["WB_DFT", "CRD_WM"]:
                url_default = info_url
            # 尝试找更高清的版本作为 original
            if scene in ["WB_DFT", "CRD_WM", "WB_PRV"]:
                url_original = info_url
        # 如果 url_original 还是空，用 url_default
        if not url_original:
            url_original = url_default
        result.append({
            "index": idx,
            "url_watermark": url_default,
            "url_original": url_original,
            "width": img.get("width", 0),
            "height": img.get("height", 0),
        })
    return result


async def _create_xhs_client(cookie_str: str):
    """创建 XhsApiClient 实例（含 Playwright 上下文）"""
    cookie_dict = _parse_cookie_str(cookie_str)
    headers = {**_XHS_HEADERS, "Cookie": cookie_str}

    pw = await async_playwright().start()
    # 尝试使用系统 Chrome，避免下载 Chromium
    try:
        browser = await pw.chromium.launch(headless=True, channel="chrome")
    except Exception as e:
        logger.warning(f"无法使用系统 Chrome: {e}，尝试默认启动")
        browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=headers["User-Agent"],
    )
    if os.path.exists(_STEALTH_JS):
        await context.add_init_script(path=_STEALTH_JS)
    await context.add_cookies([
        {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
        for k, v in cookie_dict.items()
    ])
    page = await context.new_page()
    await page.goto(
        "https://www.xiaohongshu.com/explore",
        wait_until="domcontentloaded",
    )
    await asyncio.sleep(2)

    client = XhsApiClient(
        headers=headers,
        playwright_page=page,
        cookie_dict=cookie_dict,
    )
    return client, browser, pw


def _build_result_from_note_card(note_id: str, note_card: Dict) -> Dict:
    """从 API 返回的 note_card 构建统一的解析结果"""
    note_type = note_card.get("type", "normal")
    title = note_card.get("display_title", "") or note_card.get("title", "")

    # 封面图 — 兼容两种命名
    cover = note_card.get("cover", {}) or {}
    image_list = note_card.get("image_list", []) or note_card.get("imageList", [])
    if not cover and image_list:
        cover = image_list[0]
    cover_url = (
        cover.get("url_default", "")
        or cover.get("urlDefault", "")
        or cover.get("url", "")
    )

    result = {
        "success": True,
        "note_id": note_id,
        "title": title,
        "type": note_type,
        "cover_url": cover_url,
    }

    if note_type == "video":
        video_data = note_card.get("video", {}) or {}
        video_streams = _parse_video_streams(video_data)
        media = video_data.get("media", {}) or video_data
        video_info = media.get("video", {}) or video_data
        result["video"] = {
            **video_streams,
            "duration": video_info.get("duration", 0),
            "width": (video_info.get("width", 0)
                      or cover.get("width", 0)),
            "height": (video_info.get("height", 0)
                       or cover.get("height", 0)),
        }
    else:
        result["images"] = _parse_image_list(image_list)

    return result


async def parse_xhs_note_media(
    cookie_str: str,
    note_id: str,
    xsec_token: str = "",
    xsec_source: str = "pc_search",
) -> Dict:
    """
    通过 XHS API 解析笔记的媒体资源（视频/图片）

    Args:
        cookie_str: 小红书 cookie
        note_id: 笔记 ID
        xsec_token: 笔记的 xsec_token（采集时保存）
        xsec_source: xsec 来源，默认 pc_search
    """
    if not cookie_str:
        return {"success": False, "note_id": note_id, "error": "Cookie 未配置"}

    client = browser = pw = None
    try:
        client, browser, pw = await _create_xhs_client(cookie_str)
        print(f"[XHS] API get_note_by_id: {note_id}")
        note_card = await client.get_note_by_id(
            note_id, xsec_source, xsec_token,
        )
        if not note_card:
            return {
                "success": False,
                "note_id": note_id,
                "error": "API 返回空数据，笔记可能已删除或 Cookie 失效",
            }

        print(f"[XHS] Got note_card: type={note_card.get('type')}, "
              f"title={note_card.get('display_title', '')[:50]}")
        return _build_result_from_note_card(note_id, note_card)

    except Exception as e:
        print(f"[XHS] parse_xhs_note_media error: {e}")
        return {"success": False, "note_id": note_id, "error": str(e)}
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def batch_parse_xhs_notes_media(
    cookie_str: str,
    note_items: List[Dict],
    interval: float = 2.0,
) -> List[Dict]:
    """
    批量解析，复用同一个浏览器实例。

    note_items: [{"note_id": "xxx", "xsec_token": "xxx"}, ...]
    """
    if not cookie_str:
        return [{"success": False, "error": "Cookie 未配置"}]

    client = browser = pw = None
    results = []
    try:
        client, browser, pw = await _create_xhs_client(cookie_str)
        for item in note_items:
            nid = item["note_id"]
            token = item.get("xsec_token", "")
            try:
                print(f"[XHS] API get_note_by_id: {nid}")
                note_card = await client.get_note_by_id(
                    nid, "pc_search", token,
                )
                if not note_card:
                    results.append({
                        "success": False, "note_id": nid,
                        "error": "API 返回空数据",
                    })
                else:
                    r = _build_result_from_note_card(nid, note_card)
                    print(f"[XHS] OK: {nid} type={r.get('type')}")
                    results.append(r)
            except Exception as e:
                print(f"[XHS] Error {nid}: {e}")
                results.append({
                    "success": False, "note_id": nid, "error": str(e),
                })
            await asyncio.sleep(interval)
    except Exception as e:
        print(f"[XHS] batch init error: {e}")
        results.append({"success": False, "error": str(e)})
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    return results
