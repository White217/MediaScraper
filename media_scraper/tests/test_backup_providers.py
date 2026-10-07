"""离线测试：新增刮削源的注册、番号门控和页面解析。"""

import json

from core.models import ParsedFilename, VideoType
from providers.avsox import parse_avsox
from providers.caribbeancom import parse_caribbean
from providers.choices import apply_provider_selection, ensure_known_providers
from providers.fc2 import article_url, fc2_article_id, parse_fc2
from providers.heyzo import heyzo_id, parse_heyzo
from providers.jav321 import parse_jav321
from providers.javmenu import parse_javmenu
from providers.javtxt import parse_javtxt
from providers.maker_json import maker_movie_id, parse_maker_json
from providers.r18dev import dvd_id_of, parse_r18
from providers.registry import create_provider, list_provider_names


NEW_NAMES = [
    "r18dev", "jav321", "libredmm", "avsox", "javmenu", "javtxt", "javstore",
    "fc2", "heyzo", "caribbeancom", "tokyohot", "tenmusume", "onepondo",
    "pacopacomama", "faleno", "aventertainments",
]


def _coded(code="SSIS-001", name=None):
    return ParsedFilename(
        original_path=f"/v/{name or code}.mp4",
        original_name=f"{name or code}.mp4",
        video_type=VideoType.CODED,
        code=code,
    )


def test_new_providers_registered():
    names = list_provider_names()
    for name in NEW_NAMES:
        assert name in names
        provider = create_provider(name)
        assert provider is not None
        assert provider.name == name


def test_studio_sources_ignore_regular_codes():
    sample = _coded("SSIS-001")
    assert create_provider("heyzo").can_handle(sample) is False
    assert create_provider("fc2").can_handle(sample) is False
    assert create_provider("caribbeancom").can_handle(sample) is False
    assert create_provider("tokyohot").can_handle(sample) is False
    assert create_provider("tenmusume").can_handle(sample) is False
    assert create_provider("r18dev").can_handle(sample) is True
    assert create_provider("jav321").can_handle(sample) is True


def test_special_codes():
    assert heyzo_id(_coded("HEYZO-0401")) == "0401"
    assert fc2_article_id(_coded("FC2-PPV-1403076")) == "1403076"
    assert maker_movie_id(_coded("010120_01", name="010120_01.mp4")) == "010120_01"
    assert dvd_id_of("SSIS-001") == "ssis00001"


def test_parse_jav321_fields():
    html = """
    <div class="panel-heading"><h3>示例标题</h3></div>
    <img class="img-responsive" src="https://pics.dmm.co.jp/x/ssis00001ps.jpg">
    <b>出演者</b>: <a href="/star/1">演员甲</a><br>
    <b>メーカー</b>: <a href="/m">片商</a><br>
    <b>品番</b>: ssis-001<br>
    <b>配信開始日</b>: 2021-02-19<br>
    <b>収録時間</b>: 150<br>
    """
    meta = parse_jav321(html, "https://www.jav321.com/video/ssis00001")
    assert meta is not None
    assert meta.code == "SSIS-001"
    assert meta.title == "示例标题"
    assert meta.year == 2021
    assert meta.runtime == 150
    assert meta.actors[0].name == "演员甲"
    assert meta.studio == "片商"
    assert "ssis00001pl.jpg" in meta.poster_url


def test_parse_caribbean_relative_poster_is_absolute():
    html = (
        "<h1>示例标题</h1>"
        '<img src="/moviepages/091926-001/images/l_l.jpg">'
    )
    meta = parse_caribbean(
        html,
        "091926-001",
        "https://www.caribbeancom.com/moviepages/091926-001/index.html",
    )
    assert meta is not None
    assert meta.poster_url == (
        "https://www.caribbeancom.com/moviepages/091926-001/images/l_l.jpg"
    )


def test_parse_r18_json():
    payload = {
        "title": "示例",
        "content_id": "ssis00001",
        "dvd_id": "SSIS-001",
        "release_date": "2021-02-19",
        "runtime_minutes": 150,
        "actresses": [{"name": "演员甲"}],
        "categories": [{"name": "类型"}],
        "maker": {"name": "片商"},
        "images": {"jacket_image": {"large": "https://img/cover.jpg"}},
    }
    meta = parse_r18(payload, "https://r18.dev/x")
    assert meta.title == "示例"
    assert meta.code == "SSIS-001"
    assert meta.year == 2021
    assert meta.poster_url.endswith("cover.jpg")
    assert meta.actors[0].name == "演员甲"


def test_parse_javmenu_and_heyzo_and_fc2():
    menu = parse_javmenu(
        '<meta property="og:title" content="SSIS-001 中文标题">'
        '<meta property="og:image" content="https://img/p.jpg">',
        "SSIS-001",
        "https://javmenu.com/zh/SSIS-001",
    )
    assert menu.title == "中文标题"
    assert menu.poster_url == "https://img/p.jpg"


def test_parse_javmenu_rejects_guess_you_like_soft_404():
    """番号不存在时 JavMenu 把推荐区「猜你喜欢」写成 og:title，应视为无结果。"""
    soft_404 = """
    <title>猜你喜欢 | 世界上最齐全的日本AV资料库</title>
    <meta property="og:title" content="猜你喜欢" />
    <meta property="og:image" content="https://v5.javmenu.com/storage/6/logo_black.png" />
    <meta property="og:description" content="世界上最齐全的日本AV资料库" />
    """
    assert parse_javmenu(soft_404, "FC2-4521262", "https://javmenu.com/zh/FC2-4521262") is None
    assert parse_javmenu(
        '<meta property="og:title" content="猜你喜欢">',
        "FC2-PPV-3169629",
        "https://javmenu.com/zh/FC2-PPV-3169629",
    ) is None
    # 正常详情页仍可解析
    ok = parse_javmenu(
        '<title>真实作品标题 | JAV目录大全</title>'
        '<meta property="og:title" content="FC2-PPV-1085593 真实作品标题">'
        '<meta property="og:image" content="https://img/cover.jpg">',
        "FC2-PPV-1085593",
        "https://javmenu.com/zh/FC2-PPV-1085593",
    )
    assert ok is not None
    assert ok.title == "真实作品标题"
    assert ok.poster_url == "https://img/cover.jpg"

    hey = parse_heyzo(
        '<meta property="og:title" content="HEYZO 标题">'
        '<meta property="og:image" content="https://img/h.jpg">'
        '<tr class="table-actor"><td>出演</td><td><a>演员乙</a></td></tr>',
        "0401",
        "https://www.heyzo.com/moviepages/0401/index.html",
    )
    assert hey.code == "HEYZO-0401"
    assert hey.actors[0].name == "演员乙"

    assert parse_fc2('<div class="items_notfound_header">', "1", "u") is None
    assert parse_fc2(
        '<meta property="og:title" content="登入">',
        "1085593",
        article_url("1085593"),
    ) is None
    assert parse_fc2(
        '<meta property="og:title" content="真实标题">',
        "1085593",
        "https://id.fc2.com/login.php",
    ) is None
    assert fc2_article_id(_coded(
        "FC2-PPV-1085593", name="FC2-PPV-1085593 登入.mp4",
    )) == "1085593"
    found = parse_fc2(
        '<meta property="og:title" content="FC2 标题">',
        "1403076",
        "https://adult.contents.fc2.com/article/1403076/",
    )
    assert found.code == "FC2-PPV-1403076"
    assert found.poster_url is None


def test_fc2_poster_skips_placeholder_and_upgrades_thumb():
    html = """
    <meta property="og:title" content="FC2 标题">
    <meta property="og:image" content="https://adult.contents.fc2.com/images/no_image.png">
    <div class="items_article_MainitemThumb">
      <img src="https://contents-thumbnail2.fc2.com/w240/upload/a.jpg">
    </div>
    """
    found = parse_fc2(html, "1403076", article_url("1403076"))
    assert found.poster_url == "https://contents-thumbnail2.fc2.com/w1280/upload/a.jpg"


def test_fc2_poster_upgrades_w276_thumb():
    html = """
    <meta property="og:title" content="FC2 标题">
    <div class="items_article_MainitemThumb">
      <img src="https://contents-thumbnail2.fc2.com/w276/storage/a.png">
    </div>
    """
    found = parse_fc2(html, "1085593", article_url("1085593"))
    assert found.poster_url == "https://contents-thumbnail2.fc2.com/w1280/storage/a.png"


def test_fc2_poster_uses_sample_when_main_is_placeholder():
    html = """
    <meta property="og:title" content="FC2 标题">
    <meta property="og:image" content="https://adult.contents.fc2.com/images/now_printing.jpg">
    <div class="items_article_MainitemThumb">
      <img src="https://adult.contents.fc2.com/images/noimage.jpg">
    </div>
    <ul class="items_article_SampleImages">
      <li><a href="https://storage.contents.fc2.com/file/1.jpg">
        <img src="https://contents-thumbnail2.fc2.com/w240/s.jpg">
      </a></li>
    </ul>
    """
    found = parse_fc2(html, "1403076", article_url("1403076"))
    assert found.poster_url == "https://storage.contents.fc2.com/file/1.jpg"


def test_javbus_poster_uses_big_image_href():
    from providers.javbus import _parse_javbus_movie_page

    html = """
    <html><head><title>示例标题 - JavBus</title></head><body>
    <h3>SSIS-001 示例标题</h3>
    <a class="bigImage" href="https://pics.dmm.co.jp/x/ssis00001ps.jpg">
      <img src="https://pics.dmm.co.jp/x/ssis00001pt.jpg">
    </a>
    """ + (" " * 500) + "</body></html>"
    meta = _parse_javbus_movie_page(html, "SSIS-001")
    assert meta is not None
    assert meta.poster_url.endswith("ssis00001pl.jpg")


def test_parse_avsox_detail_and_javtxt():
    html = """
    <h3>AVSOX 标题</h3>
    <a class="bigImage" href="https://img/big.jpg"></a>
    <span class="header">識別碼:</span> SSIS-001
    <span class="header">發行日期:</span> 2021-02-19
    <span class="star-name"><a>演员丙</a></span>
    """
    meta = parse_avsox(html, "SSIS-001", "https://avsox.click/cn/SSIS-001")
    assert meta.title == "AVSOX 标题"
    assert meta.year == 2021
    assert meta.actors[0].name == "演员丙"
    assert meta.poster_url == "https://img/big.jpg"
    assert parse_avsox("<html>shell</html>", "SSIS-001") is None

    listing = '<div class="work-id">SSIS-001</div><div class="work-actress">演员丁</div>'
    listed = parse_javtxt(listing, "SSIS-001", "https://javtxt.com/?s=SSIS-001")
    assert listed.actors[0].name == "演员丁"


def test_parse_maker_json():
    payload = {
        "Title": "厂牌标题",
        "MovieID": "010120_01",
        "Release": "2020-01-01",
        "ActressesJa": ["演员戊"],
        "Duration": 60,
        "ThumbUltra": "https://img/ultra.jpg",
        "ThumbHigh": "https://img/t.jpg",
        "UCNAME": "厂牌",
    }
    meta = parse_maker_json(payload, "010120_01", "tenmusume", "https://example/json")
    assert meta.title == "厂牌标题"
    assert meta.year == 2020
    assert meta.studio == "厂牌"
    assert meta.poster_url == "https://img/ultra.jpg"
    assert json.dumps(meta.actors[0].name)


def test_selection_updates_enabled_without_dropping_keys():
    config = {
        "providers": [
            {"name": "javdb", "enabled": True},
            {"name": "tmdb", "enabled": True, "api_key": "keep"},
        ]
    }
    ensure_known_providers(config)
    names = [item["name"] for item in config["providers"]]
    assert names.index("r18dev") < names.index("tmdb")
    apply_provider_selection(config, {"jav321", "tmdb"})
    by_name = {item["name"]: item for item in config["providers"]}
    assert by_name["javdb"]["enabled"] is False
    assert by_name["jav321"]["enabled"] is True
    assert by_name["tmdb"]["enabled"] is True
    assert by_name["tmdb"]["api_key"] == "keep"
