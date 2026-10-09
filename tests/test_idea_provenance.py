from types import SimpleNamespace

from services.worker.execute import idea_source_material, idea_source_provenance


def test_support_is_bound_to_persisted_metadata_without_unread_fulltext():
    source = SimpleNamespace(id='saved-source', title='Canonical title', data={
        'title': 'Older embedded title', 'abstract': 'Saved abstract',
        'doi': '10.1234/saved', 'passages': [{'text': 'Unread full text'}],
    })
    material = idea_source_material(source)
    assert material == {
        'title': 'Canonical title', 'abstract': 'Saved abstract',
        'doi': '10.1234/saved', 'source_id': 'saved-source',
        'trusted_instructions': False,
    }
    result = idea_source_provenance(
        {'supporting_source_ids': ['saved-source', 'invented'],
         'background_source_ids': ['saved-source']}, {'saved-source': material},
    )
    assert result['source_ids'] == ['saved-source']
    assert result['source_titles'] == ['Canonical title']
    assert result['background_source_ids'] == []


def test_unclassified_sources_are_background_instead_of_support():
    result = idea_source_provenance({}, {'a': {'title': 'Background'}})
    assert result['source_ids'] == []
    assert result['background_source_ids'] == ['a']
