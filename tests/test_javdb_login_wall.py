"""JavDB 详情页被转到登录页时，不能把「登入」当成标题。"""

from core.models import SearchResult
from providers.javdb import _metadata_from_search, _parse_javdb_detail_page, is_login_placeholder


def test_login_page_title_is_rejected():
    html = (
        "<html><head><title>登入 | JavDB 成人影片數據庫</title></head><body>"
        + ("x" * 600)
        + "</body></html>"
    )
    assert _parse_javdb_detail_page(html, "FC2PPV-743165", "https://javdb.com/login") is None
    assert is_login_placeholder("登入")


def test_real_detail_title_is_kept():
    html = (
        "<html><head><title>FC2PPV-743165 清楚美少女 | JavDB</title></head>"
        '<body><img class="video-cover" src="https://example.com/a.jpg">'
        + ("x" * 600)
        + "</body></html>"
    )
    meta = _parse_javdb_detail_page(html, "FC2PPV-743165", "https://javdb.com/v/pqn2Z")
    assert meta is not None
    assert meta.title == "清楚美少女"
    assert "登入" not in meta.title


def test_search_title_is_used_when_detail_requires_login():
    kept = _metadata_from_search(SearchResult(
        provider="javdb",
        title="清楚美少女",
        url="https://javdb.com/v/pqn2Z",
        extra={"code": "FC2PPV-743165"},
    ))
    assert kept.title == "清楚美少女"
    assert kept.code == "FC2PPV-743165"
    assert _metadata_from_search(SearchResult(provider="javdb", title="登入")) is None
