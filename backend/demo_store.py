"""An in-memory stand-in for the parts of the Firestore client the app uses, for the demo profile.

Each demo visitor gets their own copy (see demo.py), so the demo never reads or writes real Firestore.
Supports: collection/document paths, get/set(merge)/update/delete, where (==, !=, <, <=, >, >=, in,
array_contains), order_by, limit, stream, get on queries, and batches. SERVER_TIMESTAMP becomes the current time.
"""
import copy
import itertools
import os
from datetime import datetime, timezone

_ids = itertools.count(1)
SENTINEL = object()   # demo.py sets this to firestore.SERVER_TIMESTAMP


def _resolve(v):
    if v is SENTINEL or (isinstance(v, str) and isinstance(SENTINEL, str) and v == SENTINEL):
        return datetime.now(timezone.utc)
    if isinstance(v, dict):
        return {k: _resolve(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x) for x in v]
    return v


class Snap:
    def __init__(self, ref, data):
        self.reference, self.id, self._d = ref, ref.id, data

    @property
    def exists(self):
        return self._d is not None

    def to_dict(self):
        return None if self._d is None else copy.deepcopy(self._d)

    def get(self, k):
        return copy.deepcopy((self._d or {}).get(k))


class Doc:
    def __init__(self, store, parent, id):
        self.store, self.parent, self.id = store, parent, id
        self.path = f"{parent}/{id}"

    def get(self, *a, **k):
        return Snap(self, self.store.docs.get(self.path))

    def set(self, data, merge=False):
        data = _resolve(copy.deepcopy(data))
        cur = self.store.docs.get(self.path) if merge else None
        if cur and merge:
            data = {**cur, **data}
        self.store.docs[self.path] = data

    def update(self, data):
        if self.path not in self.store.docs:
            raise KeyError(f"no document to update: {self.path}")
        self.set(data, merge=True)

    def delete(self):
        self.store.docs.pop(self.path, None)

    def collection(self, name):
        return Query(self.store, f"{self.path}/{name}")


def _cmp(op):
    def safe(f):
        def run(a, b):
            try:
                return a is not None and f(a, b)
            except TypeError:
                return False
        return run
    return {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
            "<": safe(lambda a, b: a < b), "<=": safe(lambda a, b: a <= b),
            ">": safe(lambda a, b: a > b), ">=": safe(lambda a, b: a >= b),
            "in": lambda a, b: a in b, "not-in": lambda a, b: a not in b,
            "array_contains": lambda a, b: isinstance(a, list) and b in a}[op]


class Query:
    def __init__(self, store, path, filters=(), order=(), lim=None):
        self.store, self.path, self.filters, self.order, self.lim = store, path, tuple(filters), tuple(order), lim
        self.id = path.rsplit("/", 1)[-1]

    def document(self, id=None):
        return Doc(self.store, self.path, id or f"demo{next(_ids):06d}{os.urandom(3).hex()}")

    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:   # FieldFilter(field_path, op_string, value)
            field, op, value = filter.field_path, filter.op_string, filter.value
        return Query(self.store, self.path, self.filters + ((field, op, value),), self.order, self.lim)

    def order_by(self, field, direction=None):
        return Query(self.store, self.path, self.filters, self.order + ((field, str(direction or "ASCENDING")),), self.lim)

    def limit(self, n):
        return Query(self.store, self.path, self.filters, self.order, n)

    def stream(self, *a, **k):
        prefix = self.path + "/"
        rows = [Snap(Doc(self.store, self.path, p[len(prefix):]), d) for p, d in list(self.store.docs.items())
                if p.startswith(prefix) and "/" not in p[len(prefix):]]
        rows = [r for r in rows if all(_cmp(op)(r._d.get(f), v) for f, op, v in self.filters)]
        for field, direction in reversed(self.order):
            rows.sort(key=lambda r: (r._d.get(field) is None, r._d.get(field) if r._d.get(field) is not None else 0),
                      reverse="DESC" in direction.upper())
        return rows[: self.lim] if self.lim else rows

    def get(self, *a, **k):
        return self.stream()

    def list_documents(self):
        return [s.reference for s in self.stream()]


class Batch:
    def __init__(self):
        self.ops = []

    def set(self, ref, data, merge=False):
        self.ops.append(("set", ref, data, merge))

    def update(self, ref, data):
        self.ops.append(("update", ref, data, None))

    def delete(self, ref):
        self.ops.append(("delete", ref, None, None))

    def commit(self):
        for op, ref, data, merge in self.ops:
            if op == "set":
                ref.set(data, merge=merge)
            elif op == "update":
                ref.update(data)
            else:
                ref.delete()
        self.ops = []


class Client:
    def __init__(self, docs=None):
        self.docs = docs if docs is not None else {}

    def collection(self, name):
        return Query(self, name)

    def document(self, path):
        parent, _, id = path.rpartition("/")
        return Doc(self, parent, id)

    def batch(self):
        return Batch()

    def copy(self):
        return Client(copy.deepcopy(self.docs))

    def size(self):
        return len(self.docs)
