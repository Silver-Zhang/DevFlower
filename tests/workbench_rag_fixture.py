"""Test-only lexical retrieval and a fixed, synthetic DevFlower QA corpus."""
from collections import Counter
import math
import re

DOCUMENTS = {
    'ember': 'Ember settlement permits a 42-minute delivery window.\n\nEmber receipts are signed with quartz seals.',
    'ember-draft': 'An obsolete Ember draft proposed a 24-minute delivery window.\n\nThe draft never took effect.',
    'harbor': 'Harbor batches are held for 9 hours before release.\n\nHarbor uses the blue review queue.',
    'ridge': 'Ridge inventory reconciliation runs at 06:40 UTC.\n\nRidge confirmation slips are printed on silver paper.',
    'atlas': 'Atlas retention lasts 17 days.\n\nAtlas escalations go to the on-call specialist.',
    'meadow': 'Meadow uses an orange label for fragile cargo.\n\nMeadow packaging forbids lead liners.',
    'north': 'North inspection requires two independent signatures.\n\nNorth inspections use the checklist called Cedar.',
    'south': 'South inspection requires three independent signatures.\n\nSouth inspections use the checklist called Hazel.',
    'grove': 'Grove registers returns under queue Cypress.\n\nGrove disposes of expired labels every Tuesday.',
}
QUESTIONS = (
    ('01', 'What is the effective Ember delivery window in minutes?', '42 minutes', ('ember',)),
    ('02', 'What color is the Harbor review queue?', 'blue', ('harbor',)),
    ('03', 'What UTC time does Ridge inventory reconciliation run?', '06:40 UTC', ('ridge',)),
    ('04', 'How many days does Atlas retention last?', '17 days', ('atlas',)),
    ('05', 'Which queue does Grove use to register returns?', 'Cypress', ('grove',)),
    ('06', 'Which color labels fragile Meadow cargo and which paper is used for Ridge confirmation slips?', 'orange, silver', ('meadow', 'ridge')),
    ('07', 'How many North and South inspection signatures are required, in that order?', '2, 3', ('north', 'south')),
    ('08', 'What are the Ember receipt seal material and Harbor batch holding time?', 'quartz, 9 hours', ('ember', 'harbor')),
    ('09', 'Give both the North and South inspection checklist names, in that order.', 'Cedar, Hazel', ('north', 'south')),
    ('10', 'What is the private encryption key for Atlas?', None, ()),
    ('11', 'What is the price of a Meadow fragile shipment?', None, ()),
    ('12', 'Which supplier manufactures the quartz seals for Ember?', None, ()),
)
STOP = set('a an the in for of is are what which how does do it used to and both with give that before after called use uses'.split())


def terms(text):
    return Counter(term for term in re.findall(r'[a-z0-9]+', text.casefold()) if term not in STOP)


def build_and_rank(workspace, k=3):
    corpus = workspace / 'knowledge'
    corpus.mkdir()
    for name, body in DOCUMENTS.items():
        (corpus / (name + '.txt')).write_text(body + '\n', encoding='utf-8')
    chunks = []
    for path in sorted(corpus.glob('*.txt')):
        for index, passage in enumerate(path.read_text(encoding='utf-8').strip().split('\n\n'), start=1):
            chunks.append((path.stem, index, passage, terms(passage)))
    frequency = Counter(term for _, _, _, tokens in chunks for term in tokens)
    n = len(chunks)
    retrieved = []
    for ident, question, _, _ in QUESTIONS:
        query = terms(question)
        scores = {}
        for doc, index, passage, tokens in chunks:
            score = sum((1 + math.log(count)) * math.log(1 + (n - frequency[term] + .5) / (frequency[term] + .5))
                        for term, count in tokens.items() if term in query)
            if score > 0:
                scores.setdefault(doc, []).append((score, index, passage))
        order = sorted(scores, key=lambda key: (-max(scores[key])[0], key))[:k]
        retrieved.append(dict(id=ident, question=question, excerpts=[
            dict(source=doc, chunks=[dict(index=index, text=passage) for _, index, passage in
                 sorted(scores[doc], key=lambda item: item[1])]) for doc in order]))
    return retrieved


def score_retrieval(ranked, k=3):
    facts = {ident: set(source) for ident, _, _, source in QUESTIONS}
    recall, precision, reciprocal = [], [], []
    for item in ranked:
        expected = facts[item['id']]
        if not expected:
            continue
        returned = [row['source'] for row in item['excerpts']]
        hits = expected.intersection(returned)
        recall.append(len(hits) / len(expected))
        precision.append(len(hits) / k)
        reciprocal.append(next((1 / i for i, doc in enumerate(returned, 1) if doc in expected), 0))
    return {'k': k, 'answerable_queries': len(recall), 'recall_at_k': sum(recall)/len(recall),
            'precision_at_k': sum(precision)/len(precision), 'mrr_at_k': sum(reciprocal)/len(reciprocal)}


def score_answers(ranked, answers):
    def normalize(value):
        return re.sub(r'[^a-z0-9]', '', str(value).casefold())

    available = {item['id']: {row['source'] for row in item['excerpts']} for item in ranked}
    facts, cites, citation_total, unsupported = 0, 0, 0, 0
    rows = []
    for ident, _, expected, sources in QUESTIONS:
        item = answers.get(ident)
        present = isinstance(item, dict) and 'answer' in item and isinstance(item.get('sources'), list)
        item = item if present else {}
        answer, citations = item.get('answer'), item.get('sources', [])
        citations = {value for value in citations if isinstance(value, str)}
        accurate = present and (answer is None if expected is None else normalize(answer) == normalize(expected))
        correct = citations.intersection(sources).intersection(available[ident])
        facts += accurate
        cites += len(correct)
        citation_total += len(citations)
        ungrounded = answer is not None and (not accurate or correct != set(sources))
        unsupported += ungrounded
        rows.append(dict(id=ident, answer=answer, sources=sorted(citations), accurate=accurate,
                         fully_supported=present and (not ungrounded and (expected is not None or not citations))))
    return dict(answer_accuracy=facts/len(QUESTIONS),
                citation_precision=cites/citation_total if citation_total else None,
                unsupported_answer_rate=unsupported/len(QUESTIONS), rows=rows)
