"""图片下载器与占位图生成（性能优化 P4）。

- 封面/背景图下载走共享会话与每域名限速，支持并发批量调用；
- 已知小图会先试大图，宽度不够时再退回原图；
- 占位图改用 Pillow 生成真正的 JPG（Kodi/Jellyfin 可识别），一次内存写出。
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from providers.http_client import HTTPClient
from providers.page_parse import absolute_url, poster_fallbacks

logger = logging.getLogger(__name__)

# 达到这个宽度就采用。更小的真图仍保留，极小图视为无效。
_TARGET_WIDTH = 400
_MIN_WIDTH = 80


# 浏览器风格请求头：绕过 CDN 防盗链
def _image_headers(referer: str = "https://www.javbus.com/") -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    }


def _jpeg_width(data: bytes) -> Optional[int]:
    index = 2
    size = len(data)
    while index + 8 < size:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < size and data[index] == 0xFF:
            index += 1
        if index >= size:
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9) or index + 1 >= size:
            continue
        segment = int.from_bytes(data[index:index + 2], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 7 < size:
                return int.from_bytes(data[index + 5:index + 7], "big")
            return None
        index += segment
    return None


def _webp_width(data: bytes) -> Optional[int]:
    """从 RIFF/WEBP 文件头读取画布宽度，不依赖 Pillow。"""
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little")
    if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF
    if kind == b"VP8L" and data[20] == 0x2F:
        return (int.from_bytes(data[21:25], "little") & 0x3FFF) + 1
    return None


def _image_width(data: bytes) -> Optional[int]:
    """读文件头里的宽度。Pillow 用来补文件头没有覆盖到的格式。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big")
    if data.startswith(b"\xff\xd8"):
        width = _jpeg_width(data)
        if width:
            return width
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        return int.from_bytes(data[6:8], "little")
    webp_width = _webp_width(data)
    if webp_width:
        return webp_width
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            return int(img.width)
    except Exception:
        return None


def _to_jpeg(data: bytes) -> Optional[bytes]:
    """封面文件名是 .jpg。WebP 等其它格式先转成 JPEG 再保存。"""
    if data.startswith(b"\xff\xd8"):
        return data
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            frame = img.convert("RGB")
            buf = io.BytesIO()
            frame.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
    except Exception:
        logger.info("封面无法转为 JPEG")
        return None


async def download_image(
    url: str,
    dest_path,
    http_client: Optional[HTTPClient] = None,
    referer: str = "https://www.javbus.com/",
) -> bool:
    """下载单张图片到 dest_path（pathlib.Path）。

    先试放大后的地址。大图不存在时保留原来的小图。
    占位图和无法识别的内容不写入文件。
    http_client 为 None 时创建临时实例；批量场景应由调用方复用同一个 client。
    """
    candidates = poster_fallbacks(absolute_url(url, referer))
    if not candidates:
        logger.warning("封面地址不可用，跳过: %s", url)
        return False

    own_client = False
    if http_client is None:
        from core.proxy import create_http_client

        http_client = create_http_client()
        own_client = True

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        headers = _image_headers(referer)
        best: Optional[tuple[int, bytes]] = None
        for candidate in candidates:
            success = await http_client.download_file(candidate, dest_path, headers=headers)
            if not success or not dest_path.exists():
                logger.info("封面候选下载失败: %s", candidate)
                continue
            data = dest_path.read_bytes()
            width = _image_width(data)
            dest_path.unlink(missing_ok=True)
            if width is None:
                logger.info("封面候选不是图片: %s", candidate)
                continue
            jpeg = _to_jpeg(data)
            if not jpeg:
                logger.info("封面候选不是图片: %s", candidate)
                continue
            data = jpeg
            width = _image_width(data) or width
            if width >= _TARGET_WIDTH:
                dest_path.write_bytes(data)
                logger.info("封面已下载 (%spx): %s", width, dest_path.name)
                return True
            if best is None or width > best[0]:
                best = (width, data)
                logger.info("封面偏小 (%spx)，继续尝试: %s", width, candidate)

        if best is not None and best[0] >= _MIN_WIDTH:
            dest_path.write_bytes(best[1])
            logger.info("没有更大封面，保留 %spx: %s", best[0], dest_path.name)
            return True
        logger.warning("没有可用封面: %s", url)
        return False
    finally:
        if own_client:
            await http_client.close()


def is_local_placeholder(path) -> bool:
    """本程序生成的占位图固定是 300×450 的小 JPEG。真封面不使用这个尺寸。"""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= 0 or size > 20000:
        return False
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size == (300, 450)
    except Exception:
        return False


def create_placeholder(
    dest_path,
    text: str = "No Image",
    width: int = 300,
    height: int = 450,
):
    """用 Pillow 生成真正的 JPG 占位图（深色底 + 居中文字），一次写出。

    若 Pillow 不可用则退化为写一个极小的合法 JFIF 头文件。
    """
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image, ImageDraw

            img = Image.new("RGB", (width, height), (24, 38, 54))
            draw = ImageDraw.Draw(img)
            label = (text or "No Image")[:40]
            # 简单居中（按近似字宽）
            try:
                bbox = draw.textbbox((0, 0), label)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                tw, th = len(label) * 7, 12
            draw.text(
                ((width - tw) / 2, (height - th) / 2),
                label,
                fill=(150, 170, 185),
            )
            img.save(dest_path, format="JPEG", quality=85)
        except ImportError:
            # 无 Pillow：写最小合法 JFIF（1x1 灰点），保证扩展名与文件头一致
            jpeg_bytes = bytes.fromhex(
                "ffd8ffe000104a46494600010100000100010000ffdb0043000806060706050807"
                "07070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c"
                "231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101"
                "011100ffc400140001000000000000000000000000000000000008ffc400141001"
                "000000000000000000000000000000000000ffda0008010100003f00d2cf20ffd9"
            )
            dest_path.write_bytes(jpeg_bytes)

        logger.info("占位图已创建: %s", dest_path.name)
        return dest_path

    except Exception as e:  # noqa: BLE001
        logger.error("占位图创建失败: %s", e)
        return None
