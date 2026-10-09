from contextlib import contextmanager
from pathlib import Path

import httpx

from research.literature import browser


def test_read_page_uses_declared_response_charset(tmp_path, monkeypatch):
    html = '<html><head><title>Пример страницы</title></head><body><p>Привет, мир!</p></body></html>'
    request = httpx.Request('GET', 'https://example.org/page')
    response = httpx.Response(
        200,
        headers={'Content-Type': 'text/html; charset=windows-1251'},
        content=html.encode('windows-1251'),
        request=request,
    )
    @contextmanager
    def stream(*args, **kwargs):
        yield response

    monkeypatch.setattr(browser.httpx, 'stream', stream)
    monkeypatch.setattr(browser, '_public_url', lambda url: None)

    result = browser.read_page('https://example.org/page', tmp_path)

    assert result['title'] == 'Пример страницы'
    assert result['text'] == 'Пример страницы\nПривет, мир!'
    assert Path(result['text_path']).read_text() == 'Пример страницы\nПривет, мир!'
