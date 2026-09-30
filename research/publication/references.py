"""Verified framework-design references; new topics still require relevant peers."""
import json
from pathlib import Path


def reference_context():
    corpus=json.loads(Path(__file__).with_name('accepted_reference_corpus.json').read_text())
    return {'scope':corpus['scope'],'review_rubric':corpus['review_rubric'],
        'papers':[{'id':p['id'],'title':p['title'],'venue':p['venue'],'year':p['year'],
            'official_url':p['official_url'],'experiments':p['experiments'],
            'layout':p['layout'],'transferable_lesson':p['transferable_lesson']} for p in corpus['papers']],
        'usage':'These accepted agent/ML research papers inform framework design. Retrieve topic-matched accepted full texts for each new scientific project; do not count unrelated examples toward its peer coverage.'}
