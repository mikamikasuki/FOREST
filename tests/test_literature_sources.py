from research.literature import sources


def test_arxiv_multiword_search_prioritizes_title_phrase_and_deduplicates(monkeypatch):
    phrase_match = {"arxiv_id": "1706.03762v7", "title": "Attention Is All You Need"}
    keyword_match = {"arxiv_id": "2104.04692v3", "title": "Not All Attention Is All You Need"}
    older_duplicate = {"arxiv_id": "1706.03762v6", "title": "Attention Is All You Need"}
    calls = []

    def fake_arxiv(params):
        calls.append(params)
        return [phrase_match] if len(calls) == 1 else [older_duplicate, keyword_match]

    monkeypatch.setattr(sources, "_arxiv", fake_arxiv)

    results = sources.search('"Attention Is All You Need"', source="arxiv", limit=2)

    assert [record["arxiv_id"] for record in results] == ["1706.03762v7", "2104.04692v3"]
    assert [call["search_query"] for call in calls] == [
        'ti:"Attention Is All You Need"',
        "all:Attention AND all:Is AND all:All AND all:You AND all:Need",
    ]
    assert all(call["max_results"] == 2 for call in calls)


def test_arxiv_single_word_search_keeps_existing_query(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "_arxiv", lambda params: calls.append(params) or [])

    assert sources.search("transformer", source="arxiv", limit=4) == []
    assert len(calls) == 1
    assert calls[0]["search_query"] == "all:transformer"


def test_arxiv_title_phrase_skips_fallback_when_it_fills_page(monkeypatch):
    calls = []
    matches = [
        {"arxiv_id": "1706.03762v7", "title": "Attention Is All You Need"},
        {"arxiv_id": "2104.04692v3", "title": "Not All Attention Is All You Need"},
    ]
    monkeypatch.setattr(sources, "_arxiv", lambda params: calls.append(params) or matches)

    results = sources.search("Attention Is All You Need", source="arxiv", limit=2)

    assert results == matches
    assert len(calls) == 1
    assert calls[0]["search_query"] == 'ti:"Attention Is All You Need"'
