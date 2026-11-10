from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from transformers import AutoTokenizer

base = Path(__file__).resolve().parents[1]
directory = base / 'outputs/pdf-contract-witness-final/transformation'
manifest = json.loads((directory/'manifest.json').read_bytes())
text = (directory/'canonical.txt').read_bytes()
tokenizer = AutoTokenizer.from_pretrained(str(base/'work/e5-model'),local_files_only=True)
counts = []
for fragment in manifest['fragments']:
    content = text[fragment['start']:fragment['end']].decode('utf-8')
    count = len(tokenizer.encode('passage: '+content,add_special_tokens=True,truncation=False))
    if count != fragment['tokens'] or count > 512:
        raise ValueError('Independent token recount disagrees')
    counts.append(count)
queries = [json.loads(line) for line in (base/'outputs/litsearch-300/queries.jsonl').read_text(encoding='utf-8').splitlines()]
development = [json.loads(line) for line in (base/'outputs/litsearch-300/development-queries.jsonl').read_text(encoding='utf-8').splitlines()]
if {q['id'] for q in queries} & {q['id'] for q in development}:
    raise ValueError('ID leakage')
test_docs = {str(doc) for q in queries for doc in q['corpusids']}
dev_docs = {str(doc) for q in development for doc in q['corpusids']}
if test_docs & dev_docs:
    raise ValueError('Positive-document leakage')
report = {'status':'verified','fragments':len(counts),'maximum_actual_e5_tokens':max(counts),
          'minimum_actual_e5_tokens':min(counts),
          'canonical_sha256':sha256(text).hexdigest(),
          'packages':{name:version(name) for name in ('transformers','tokenizers','defusedxml','pypdf')},
          'litsearch_test_cohorts':{str(key):value for key,value in Counter((q['query_set'],q['specificity']) for q in queries).items()},
          'development_ids_and_positive_documents_disjoint':True,
          'verifier_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
(base/'outputs/pdf-contract-witness-final/independent-token-check.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
