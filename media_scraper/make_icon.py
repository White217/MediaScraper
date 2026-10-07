"""从源图生成多尺寸 MediaScraper 应用图标 assets/app.ico。

图标样式（与 README 一致，禁止改成别的吉祥物或扁平色块）：
- 圆角方块（squircle）青绿渐变底：左上偏亮青绿 → 右下更深青绿
- 白色扁平「播放三角 + 放大镜」合体符号居中
- 输出多尺寸 ICO：16 / 32 / 48 / 64 / 128 / 256

权威源图：assets/app_icon_source.png（由现网 app.ico 导出，视觉必须一致）。
再生时只做缩放与 ICO 封装，不要重画符号。

用法（在 media_scraper 目录）::

    python make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "assets" / "app_icon_source.png"
OUT_ICO = ROOT / "assets" / "app.ico"
OUT_PREVIEW = ROOT / "assets" / "app_icon_preview.png"
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(
            f"缺少源图 {SOURCE}。请先放入 256×256 的 PNG（播放+放大镜青绿圆角图标）。"
        )
    base = Image.open(SOURCE).convert("RGBA")
    if base.size[0] != base.size[1]:
        # 居中裁成正方形
        side = min(base.size)
        left = (base.size[0] - side) // 2
        top = (base.size[1] - side) // 2
        base = base.crop((left, top, left + side, top + side))
    if base.size != (256, 256):
        base = base.resize((256, 256), Image.Resampling.LANCZOS)

    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT_PREVIEW)
    # 踩坑：手动 append_images 只会存 1 帧；必须单图 + sizes=
    base.save(OUT_ICO, format="ICO", sizes=SIZES)
    print(f"wrote {OUT_ICO} ({OUT_ICO.stat().st_size} bytes) and {OUT_PREVIEW}")


if __name__ == "__main__":
    main()
