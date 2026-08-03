"""
聊天总结图片渲染器

使用 Jinja2 渲染 HTML 模板，再通过宿主内置的 ``ctx.render.html2png`` 能力
把 HTML 渲染为 PNG（直接返回 base64，无需临时文件、无需自带 Playwright/Chromium）。

设计要点：
- 手写字体（ZCOOL KuaiLe / Patrick Hand）以 @font-face base64 形式内嵌，离线可用，
  不依赖 Google Fonts CDN（在国内/服务器环境更稳定）。
- QQ 头像在渲染前由 Python 进程预下载并转成 base64 data URL 内嵌进 HTML，
  渲染时 ``allow_network=False`` 完全离线，避免浏览器联网拉头像导致 ``load`` 事件
  迟迟不触发而整图渲染超时。头像拉不到时优雅降级（置空），模板走 SVG 占位。
- 显式传入 ``timeout_ms``，避免宿主默认 0（无限等待）挂死。
"""

import os
import time
import base64
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .event_digest import partition_events

# 可选 HTTP 库：优先 httpx（MaiBot 主程序在用），其次 aiohttp。两者皆无则禁用预取。
try:
    import httpx  # type: ignore

    _HTTP_BACKEND = "httpx"
except Exception:  # pragma: no cover
    httpx = None  # type: ignore
    try:
        import aiohttp  # type: ignore

        _HTTP_BACKEND = "aiohttp"
    except Exception:  # pragma: no cover
        aiohttp = None  # type: ignore
        _HTTP_BACKEND = ""

# 目录定位
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE_DIR = os.path.join(_PLUGIN_DIR, "templates", "scrapbook")
_FONTS_DIR = os.path.join(_PLUGIN_DIR, "fonts")

# 渲染参数
_VIEWPORT_WIDTH = 1000
_VIEWPORT_HEIGHT = 800
_DEVICE_SCALE = 2.0
# 略低于宿主能力调用的 ~30 秒 RPC 硬超时，让 Playwright 先干净失败而非触发 RPC 超时
_RENDER_TIMEOUT_MS = 25000

# 打包字体：family -> 文件名
_BUNDLED_FONTS = [
    ("ZCOOL KuaiLe", "ZCOOLKuaiLe-Regular.woff2"),
    ("Patrick Hand", "PatrickHand-Regular.woff2"),
]


def _build_font_face_css() -> str:
    """把打包字体编码为 @font-face base64 CSS（模块加载时构建一次）。"""
    faces = []
    for family, filename in _BUNDLED_FONTS:
        path = os.path.join(_FONTS_DIR, filename)
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            faces.append(
                f"@font-face{{font-family:'{family}';"
                f"src:url(data:font/woff2;base64,{b64}) format('woff2');"
                f"font-weight:normal;font-style:normal;font-display:swap;}}"
            )
        except Exception:
            # 字体缺失时静默跳过，模板会回退到系统字体
            pass
    if not faces:
        return ""
    return "<style>" + "".join(faces) + "</style>"


# 内嵌字体 CSS（仅构建一次）
_FONT_FACE_CSS = _build_font_face_css()


# ==================== QQ 头像预取（下载为 base64 data URL，离线内嵌） ====================

# QQ 头像 URL 模板
_AVATAR_URL_TMPL = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"
# 单个头像请求超时（秒）
_AVATAR_REQUEST_TIMEOUT = 4.0
# 一批头像预取的总超时（秒），超时后未拿到的头像降级置空
_AVATAR_TOTAL_TIMEOUT = 8.0
# 进程内头像缓存上限与过期时间，避免每次渲染都重下全部头像
_AVATAR_CACHE_TTL = 6 * 60 * 60  # 6 小时
_AVATAR_CACHE_MAX = 2000

# 模块级缓存：qq -> (data_url, 写入时间戳)。data_url 为 "" 表示该 QQ 已尝试但失败。
_AVATAR_CACHE: Dict[str, "tuple[str, float]"] = {}


def _cache_get(qq: str) -> Optional[str]:
    """命中且未过期返回 data_url（可能为空串表示已知失败），否则返回 None。"""
    item = _AVATAR_CACHE.get(qq)
    if item is None:
        return None
    data_url, ts = item
    if time.time() - ts > _AVATAR_CACHE_TTL:
        _AVATAR_CACHE.pop(qq, None)
        return None
    return data_url


def _cache_put(qq: str, data_url: str) -> None:
    """写入缓存；超过上限时清理最旧的若干条目。"""
    if len(_AVATAR_CACHE) >= _AVATAR_CACHE_MAX:
        # 简单清理：按时间戳删除最旧的 10%
        try:
            oldest = sorted(_AVATAR_CACHE.items(), key=lambda kv: kv[1][1])
            for k, _ in oldest[: max(1, _AVATAR_CACHE_MAX // 10)]:
                _AVATAR_CACHE.pop(k, None)
        except Exception:
            _AVATAR_CACHE.clear()
    _AVATAR_CACHE[qq] = (data_url, time.time())


def _bytes_to_data_url(raw: bytes, content_type: str = "") -> str:
    """把图片字节转成 data URL；根据 content-type 猜测 mime，默认为 jpeg。"""
    mime = "image/jpeg"
    ct = (content_type or "").lower()
    if "png" in ct:
        mime = "image/png"
    elif "gif" in ct:
        mime = "image/gif"
    elif "webp" in ct:
        mime = "image/webp"
    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


async def _fetch_one_avatar_httpx(client: Any, qq: str) -> str:
    url = _AVATAR_URL_TMPL.format(qq=qq)
    resp = await client.get(url)
    resp.raise_for_status()
    raw = resp.content
    if not raw:
        return ""
    return _bytes_to_data_url(raw, resp.headers.get("content-type", ""))


async def _fetch_one_avatar_aiohttp(session: Any, qq: str) -> str:
    url = _AVATAR_URL_TMPL.format(qq=qq)
    timeout = aiohttp.ClientTimeout(total=_AVATAR_REQUEST_TIMEOUT)  # type: ignore
    async with session.get(url, timeout=timeout) as resp:
        resp.raise_for_status()
        raw = await resp.read()
    if not raw:
        return ""
    return _bytes_to_data_url(raw, resp.headers.get("content-type", ""))


async def prefetch_avatars(qqs: List[str], logger: Any = None) -> Dict[str, str]:
    """并发预下载一批 QQ 头像并转成 base64 data URL。

    - 每个请求有独立超时，整批有总超时；失败/超时的头像不收录（调用方据此置空）。
    - 命中进程内缓存的不再重复下载；成功与失败结果均写入缓存（失败缓存空串）。
    - 任何异常都被吞掉，绝不抛出、绝不挂死，保证渲染主流程不受影响。

    返回 {qq: data_url}，仅包含成功拿到非空 data URL 的条目。
    """
    result: Dict[str, str] = {}
    # 去重 + 规整为字符串
    unique: List[str] = []
    seen = set()
    for q in qqs:
        s = str(q or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        unique.append(s)

    if not unique:
        return result

    # 先吃缓存
    to_fetch: List[str] = []
    for qq in unique:
        cached = _cache_get(qq)
        if cached is None:
            to_fetch.append(qq)
        elif cached:
            result[qq] = cached
        # cached == "" 表示已知失败，跳过

    if not to_fetch:
        return result

    if not _HTTP_BACKEND:
        if logger:
            logger.warning("无可用 HTTP 库（httpx/aiohttp），跳过头像预取，头像将走占位")
        return result

    async def _fetch(qq: str, fetch_callable) -> None:
        # 头像拉不到不再静默换占位图：每个失败都按 qq 记一条日志，失败本身在图里也会显示「获取失败」
        try:
            data_url = await asyncio.wait_for(
                fetch_callable(qq), timeout=_AVATAR_REQUEST_TIMEOUT
            )
        except Exception as e:
            data_url = ""
            if logger:
                logger.warning(f"头像下载失败 qq={qq}: {type(e).__name__}: {e}")
        else:
            if not data_url and logger:
                logger.warning(f"头像下载为空 qq={qq}（服务器返回空内容）")
        _cache_put(qq, data_url)
        if data_url:
            result[qq] = data_url

    try:
        if _HTTP_BACKEND == "httpx":
            timeout = httpx.Timeout(_AVATAR_REQUEST_TIMEOUT)  # type: ignore
            async with httpx.AsyncClient(  # type: ignore
                timeout=timeout, follow_redirects=True
            ) as client:
                tasks = [
                    _fetch(qq, lambda q: _fetch_one_avatar_httpx(client, q))
                    for qq in to_fetch
                ]
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=_AVATAR_TOTAL_TIMEOUT,
                )
        else:  # aiohttp
            async with aiohttp.ClientSession() as session:  # type: ignore
                tasks = [
                    _fetch(qq, lambda q: _fetch_one_avatar_aiohttp(session, q))
                    for qq in to_fetch
                ]
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=_AVATAR_TOTAL_TIMEOUT,
                )
    except asyncio.TimeoutError:
        if logger:
            logger.warning(
                f"头像预取总超时（{_AVATAR_TOTAL_TIMEOUT}s），已拿到 {len(result)}/{len(to_fetch)} 张，"
                "其余走占位"
            )
    except Exception as e:
        if logger:
            logger.warning(f"头像预取异常（已忽略，走占位）: {e}")

    return result


class SummaryRenderer:
    """聊天总结图片渲染器（绑定插件 ctx）"""

    def __init__(self, ctx: Any, timeout_ms: int = _RENDER_TIMEOUT_MS):
        self.ctx = ctx
        self.logger = ctx.logger
        # 单次渲染超时（毫秒），可由插件配置覆盖
        self.timeout_ms = int(timeout_ms) if timeout_ms else _RENDER_TIMEOUT_MS
        self.env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # ==================== 渲染基础设施 ====================

    def _render_template(self, template_name: str, **context: Any) -> str:
        try:
            return self.env.get_template(template_name).render(**context)
        except Exception as e:
            self.logger.error(f"渲染模板 {template_name} 失败: {e}", exc_info=True)
            return ""

    @staticmethod
    def _inject_fonts(html: str) -> str:
        """在 <head> 后插入内嵌字体 CSS。"""
        if _FONT_FACE_CSS and "<head>" in html:
            return html.replace("<head>", "<head>" + _FONT_FACE_CSS, 1)
        return html

    async def _render_png_base64(self, html_content: str) -> Optional[str]:
        """通过宿主能力把 HTML 渲染为 PNG，返回纯 base64（失败返回 None）。"""
        html_content = self._inject_fonts(html_content)
        # render.html2png 是 MaiBot 自带的渲染能力（宿主 src/services/html_render_service.py，
        # 浏览器由宿主按需自动准备，各 MaiBot 安装都有，无需插件自己找/装 Chrome）。
        # 当前宿主用 render_timeout_ms 传渲染超时（宿主 cap render.py:91-93 优先读该键）。
        # 按花叶大人要求不做跨版本兼容兜底：直接用当前 MaiBot 的 render_timeout_ms 单参调用，
        # 失败就 logger.error 大声报错返回 None。
        try:
            result = await self.ctx.render.html2png(
                html=html_content,
                selector="body",
                viewport={"width": _VIEWPORT_WIDTH, "height": _VIEWPORT_HEIGHT},
                device_scale_factor=_DEVICE_SCALE,
                full_page=True,
                wait_until="load",
                allow_network=False,
                render_timeout_ms=self.timeout_ms,
            )
        except Exception as e:
            self.logger.error(f"调用渲染能力异常: {e}", exc_info=True)
            return None

        image_base64 = self._extract_image_base64(result)
        if not image_base64:
            err = result.get("error") if isinstance(result, dict) else result
            self.logger.error(f"HTML 渲染失败或缺少 image_base64: {err}")
            return None
        return image_base64

    @staticmethod
    def _extract_image_base64(result: Any) -> Optional[str]:
        """从渲染能力返回中稳健提取纯 base64。

        兼容 SDK 是否解包 envelope 的多种形态：
        - "<base64>"（已解包为字符串）
        - {"success": True, "result": {"image_base64": "..."}}（宿主原始结构）
        - {"image_base64": "..."} / {"data": {...}} / {"value": {...}}（部分解包）
        """
        if isinstance(result, str):
            return result or None
        if not isinstance(result, dict):
            return None
        if result.get("success") is False:
            return None

        # 直接命中
        direct = result.get("image_base64")
        if isinstance(direct, str) and direct:
            return direct

        # 嵌套在 result/data/value 之中
        for key in ("result", "data", "value"):
            nested = result.get(key)
            if isinstance(nested, dict):
                b64 = nested.get("image_base64")
                if isinstance(b64, str) and b64:
                    return b64
            elif isinstance(nested, str) and nested and key != "result":
                return nested
        return None

    # ==================== 群聊事件日报图片 ====================

    async def generate_event_report_images(
        self,
        *,
        group_name: str,
        group_id: str,
        report_date: datetime,
        period_text: str,
        message_count: int,
        report: Dict[str, Any],
        events_per_page: int = 4,
    ) -> List[str]:
        """把结构化事件日报渲染成一组适合 QQ 阅读的分页 PNG。"""

        major_events, _ = partition_events(report.get("events") or [])
        if not major_events:
            return []

        page_size = max(1, min(8, int(events_per_page or 4)))
        section_pages = []
        for index in range(0, len(major_events), page_size):
            section_pages.append(
                {
                    "section_title": "主要事件",
                    "section_kind": "major",
                    "events": major_events[index : index + page_size],
                    "event_offset": index,
                }
            )

        coverage = report.get("coverage") or {}
        images: List[str] = []
        for page_index, page in enumerate(section_pages, start=1):
            html_content = self._render_template(
                "event_report_template.html",
                group_name=group_name,
                group_id=group_id,
                report_date=report_date.strftime("%Y年%m月%d日"),
                period_text=period_text,
                message_count=message_count,
                analyzed_message_count=int(
                    report.get("analyzed_message_count") or message_count
                ),
                sampled=bool(report.get("sampled")) if page_index == 1 else False,
                partial=bool(report.get("partial")) if page_index == 1 else False,
                coverage=coverage if page_index == 1 else {},
                failed_ranges=(
                    coverage.get("failed_ranges") or []
                    if page_index == 1
                    else []
                ),
                overview=str(report.get("overview") or "") if page_index == 1 else "",
                section_title=page["section_title"],
                section_kind=page["section_kind"],
                events=page["events"],
                event_offset=page["event_offset"],
                page_index=page_index,
                page_count=len(section_pages),
            )
            if not html_content:
                self.logger.error(
                    f"群 {group_id} 事件日报第 {page_index} 页模板渲染为空"
                )
                continue
            image_base64 = await self._render_png_base64(html_content)
            if image_base64:
                images.append(image_base64)
        return images

    # ==================== 个人总结图片 ====================

    async def generate_user_summary_image(
        self,
        user_name: str,
        user_id: str,
        summary_text: str = "",
        message_count: int = 0,
        total_characters: int = 0,
        emoji_count: int = 0,
        portrait_data: Optional[dict] = None,
        target_date: Optional[datetime] = None,
    ) -> Optional[str]:
        """生成不含娱乐评级的事实型个人画像图片。"""
        if target_date is None:
            target_date = datetime.now()
        current_date = target_date.strftime("%Y年%m月%d日")

        # 渲染前预下载头像为 base64 data URL（离线内嵌，避免渲染联网超时）；拿不到则置空走 SVG 占位
        uid = str(user_id or "")
        avatar_data = ""
        if uid:
            avatar_map = await prefetch_avatars([uid], self.logger)
            avatar_data = avatar_map.get(uid, "")

        portrait_html = self._render_template(
            "user_portrait_module.html", portrait=portrait_data or {}
        )
        html_content = self._render_template(
            "user_summary_template.html",
            user_name=user_name,
            current_date=current_date,
            avatar_data=avatar_data,
            message_count=message_count,
            total_characters=total_characters,
            emoji_count=emoji_count,
            summary_text=summary_text,
            portrait_html=portrait_html,
        )
        if not html_content:
            self.logger.error("个人总结主模板渲染为空")
            return None

        return await self._render_png_base64(html_content)
