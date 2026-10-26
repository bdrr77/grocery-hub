# Offline connector package evaluation

These checks test the actual installed third-party clients with synthetic HTTP
responses. They never use account credentials or change a real cart. They are separate
from the application's regression tests and do not select or deploy a backend.

Python (requires Python >=3.13):

```bash
python -m pip install python-picnic-api2==2.0.1
python evaluations/test_picnic_python.py
```

Node (use the Node 24 runtime used by the Docker images):

```bash
npm install --prefix apps/api/picnic-search --ignore-scripts
node --test evaluations/test_picnic_node.cjs apps/api/picnic-search/search.test.mjs
```

The checks intentionally confirm known limitations as well as working behavior:
Python's lack of RSC parsing, missing HTTP error guards and the Node normalizer's
zero default for absent totals. See `docs/integration-evaluation.md` for the decision
and the separate live-account acceptance checks.
