# -*- coding: utf-8 -*-
"""P4/P5/P6 优化专项测试：页面缓存、JPG 占位、并发封面、进度限频。"""

import asyncio
import time
from pathlib import Path

import pytest

from core.page_cache import PageCache
from metadata.image import create_placeholder, is_local_placeholder


# ---------------- P5 页面缓存 ----------------

def test_cache_set_get(tmp_path):
    cache = PageCache(cache_dir=tmp_path / "c", ttl=100)
    assert cache.get("https://x/A") is None
    cache.set("https://x/A", "<html>hello</html>")
    assert cache.get("https://x/A") == "<html>hello</html>"


def test_cache_ttl_expiry(tmp_path):
    cache = PageCache(cache_dir=tmp_path / "c", ttl=100)
    cache.set("https://x/A", "body")
    p = cache._path("https://x/A")
    # 把 mtime 调到 200s 前 → 超 TTL
    old = time.time() - 200
    import os
    os.utime(p, (old, old))
    assert cache.get("https://x/A") is None


def test_cache_distinct_urls(tmp_path):
    cache = PageCache(cache_dir=tmp_path / "c", ttl=100)
    cache.set("https://x/A", "a")
    cache.set("https://x/B", "b")
    assert cache.get("https://x/A") == "a"
    assert cache.get("https://x/B") == "b"


def test_cache_disabled_when_ttl_zero(tmp_path):
    cache = PageCache(cache_dir=tmp_path / "c", ttl=0)
    cache.set("https://x/A", "x")
    assert cache.get("https://x/A") is None


def test_cache_lru_eviction(tmp_path):
    cache = PageCache(cache_dir=tmp_path / "c", ttl=1000, max_entries=3)
    # 写超过 50 次以触发容量检查（PageCache 每 50 次写淘汰一次）
    for i in range(50):
        cache.set(f"https://x/{i}", str(i))
    files = list((Path(tmp_path) / "c" / "pages").glob("*.html"))
    assert len(files) <= 3


# ---------------- P4 Pillow JPG 占位 ----------------

def test_placeholder_is_real_jpeg(tmp_path):
    p = tmp_path / "poster.jpg"
    create_placeholder(p, text="测试影片")
    assert p.exists()
    head = p.read_bytes()[:3]
    assert head[:2] == b"\xff\xd8"  # JPEG SOI 魔数


def test_generated_placeholder_is_recognized(tmp_path):
    p = tmp_path / "poster.jpg"
    create_placeholder(p, text="测试影片")
    assert is_local_placeholder(p)


def test_placeholder_creates_parent(tmp_path):
    p = tmp_path / "deep" / "nested" / "p.jpg"
    create_placeholder(p, text="x")
    assert p.exists()


# ---------------- P4 并发封面下载（mock client） ----------------

def _png(width: int, height: int = 2) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class _FakeClient:
    def __init__(self, fail_urls=(), width=500):
        self.fail_urls = set(fail_urls)
        self.calls = []
        self.width = width

    async def download_file(self, url, dest, headers=None):
        self.calls.append(url)
        if url in self.fail_urls:
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(_png(self.width))
        return True

    async def close(self):
        pass


def test_concurrent_poster_download(tmp_path):
    from metadata.image import download_image

    client = _FakeClient()

    async def main():
        jobs = [
            (tmp_path / f"{i}" / "p.jpg", f"https://x/img{i}", f"T{i}",
             "https://www.javbus.com/")
            for i in range(6)
        ]
        sem = asyncio.Semaphore(6)

        async def one(job):
            p, url, _t, ref = job
            async with sem:
                return await download_image(url, p, client, referer=ref)

        results = await asyncio.gather(*[one(j) for j in jobs])
        return results

    results = asyncio.run(main())
    assert all(results)
    assert len(client.calls) == 6
    # 所有目标文件都写出
    assert all((tmp_path / str(i) / "p.jpg").exists() for i in range(6))


def test_failed_download_falls_back_placeholder(tmp_path):
    from metadata.image import download_image

    client = _FakeClient(fail_urls={"https://x/bad"})

    p = tmp_path / "p.jpg"
    ok = asyncio.run(
        download_image("https://x/bad", p, client, referer="https://www.javbus.com/")
    )
    assert ok is False


def test_poster_download_prefers_wider_image(tmp_path):
    from metadata.image import _image_width, download_image

    class Client:
        async def download_file(self, url, dest, headers=None):
            Path(dest).write_bytes(_png(800 if url.endswith("pl.jpg") else 160))
            return True

    dest = tmp_path / "p.jpg"
    ok = asyncio.run(download_image(
        "https://pics.dmm.co.jp/digital/video/ssis00001/ssis00001ps.jpg",
        dest,
        Client(),
    ))
    assert ok is True
    assert _image_width(dest.read_bytes()) == 800


def test_poster_download_keeps_small_when_large_missing(tmp_path):
    from metadata.image import _image_width, download_image

    class Client:
        async def download_file(self, url, dest, headers=None):
            if url.endswith("pl.jpg"):
                return False
            Path(dest).write_bytes(_png(200))
            return True

    dest = tmp_path / "p.jpg"
    ok = asyncio.run(download_image(
        "https://pics.dmm.co.jp/digital/video/ssis00001/ssis00001ps.jpg",
        dest,
        Client(),
    ))
    assert ok is True
    assert _image_width(dest.read_bytes()) == 200


def test_webp_poster_is_saved_as_jpeg(tmp_path):
    from io import BytesIO

    from PIL import Image

    from metadata.image import _image_width, download_image

    raw = BytesIO()
    Image.new("RGB", (500, 700), (12, 180, 130)).save(raw, format="WEBP")

    class Client:
        async def download_file(self, url, dest, headers=None):
            Path(dest).write_bytes(raw.getvalue())
            return True

    dest = tmp_path / "START-632-poster.jpg"
    ok = asyncio.run(download_image(
        "https://www.javdatabase.com/covers/full/1s/1start00632pl.webp",
        dest,
        Client(),
        referer="https://www.javdatabase.com/movies/start-632/",
    ))
    saved = dest.read_bytes()
    assert ok is True
    assert saved.startswith(b"\xff\xd8")
    assert _image_width(saved) == 500


def test_placeholder_poster_is_not_downloaded(tmp_path):
    from metadata.image import download_image

    client = _FakeClient()
    dest = tmp_path / "p.jpg"
    ok = asyncio.run(download_image("https://cdn.example/no_image.jpg", dest, client))
    assert ok is False
    assert client.calls == []
    assert not dest.exists()


# ---------------- P6 worker 进度限频 ----------------

def test_worker_progress_throttle():
    # 复刻 worker.on_progress 的 100ms 限频门控逻辑，验证中间进度合并、收尾必发
    emitted = []
    last = [0.0]

    def gate(current, total):
        now = time.monotonic()
        is_final = total and current >= total
        if is_final or now - last[0] >= 0.1:
            last[0] = now
            emitted.append(current)

    for i in range(1, 6):  # 极短时间连发 5 次非收尾
        gate(i, 10)
    assert len(emitted) == 1  # 只有第一次发射

    gate(10, 10)  # 收尾进度必发
    assert len(emitted) == 2


# ---------------- 反爬：浏览器 UA + 随机抖动 ----------------

def test_default_session_uses_browser_ua():
    from core.http_sessions import DEFAULT_USER_AGENT, DEFAULT_HEADERS
    assert "Mozilla/5.0" in DEFAULT_USER_AGENT
    assert "Chrome/" in DEFAULT_USER_AGENT
    assert DEFAULT_HEADERS["User-Agent"] == DEFAULT_USER_AGENT
    assert "Accept-Language" in DEFAULT_HEADERS


def test_session_header_ua_override():
    import asyncio
    from core.http_sessions import get_session_pool, close_all_sessions

    async def main():
        custom = "MyBot/2.0"
        s = await get_session_pool().get(timeout=10, user_agent=custom)
        try:
            assert s.headers["User-Agent"] == custom
        finally:
            await close_all_sessions()

    asyncio.run(main())


def test_jitter_delay_applied():
    import asyncio
    from core.http_sessions import _DomainRateLimiter

    limiter = _DomainRateLimiter()

    async def main():
        url = "https://same-domain.test/a"
        # 第一次请求记录时间
        await limiter.wait(url, min_interval=0.0, jitter=0.0)
        # 第二次：min_interval=0.2 + 抖动，实测应等待 ≥0.2s
        t = time.perf_counter()
        await limiter.wait(url, min_interval=0.2, jitter=0.05)
        dt = time.perf_counter() - t
        return dt

    dt = asyncio.run(main())
    assert dt >= 0.19  # 至少等了基础间隔


def test_jitter_random_range(monkeypatch):
    # 固定 random.uniform 返回值，验证抖动确实进入等待时长
    import asyncio
    from core.http_sessions import _DomainRateLimiter

    monkeypatch.setattr(
        "core.http_sessions.random.uniform", lambda a, b: 0.1
    )
    limiter = _DomainRateLimiter()

    async def main():
        url = "https://d2.test/x"
        await limiter.wait(url, 0.0, 0.0)
        t = time.perf_counter()
        # jitter 固定为 0.1
        await limiter.wait(url, min_interval=0.0, jitter=0.5)
        return time.perf_counter() - t

    dt = asyncio.run(main())
    assert 0.09 <= dt <= 0.2
