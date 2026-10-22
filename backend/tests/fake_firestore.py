"""A tiny in-memory stand-in for google.cloud.firestore, enough for the ledger tests.
Install before importing app modules: `import fake_firestore; fake_firestore.install()`."""
import itertools
import sys
import types

_ids = itertools.count(1)


class Snap:
    def __init__(self, ref, data):
        self.reference, self.id, self._d = ref, ref.id, data

    @property
    def exists(self):
        return self._d is not None

    def to_dict(self):
        return None if self._d is None else dict(self._d)

    def get(self, k):
        return (self._d or {}).get(k)


class Doc:
    def __init__(self, store, path, id):
        self.store, self.path, self.id = store, path, id

    def get(self):
        return Snap(self, self.store.get((self.path, self.id)))

    def set(self, data, merge=False):
        cur = self.store.get((self.path, self.id)) if merge else None
        self.store[(self.path, self.id)] = {**(cur or {}), **data}

    def update(self, data):
        self.set(data, merge=True)

    def delete(self):
        self.store.pop((self.path, self.id), None)

    def collection(self, name):
        return Query(self.store, f"{self.path}/{self.id}/{name}")


OPS = {"==": lambda a, b: a == b, ">=": lambda a, b: a is not None and a >= b, "<=": lambda a, b: a is not None and a <= b,
       ">": lambda a, b: a is not None and a > b, "<": lambda a, b: a is not None and a < b}


class Query:
    def __init__(self, store, path, filters=(), order=None, lim=None):
        self.store, self.path, self.filters, self.order, self.lim = store, path, list(filters), order, lim

    def document(self, id=None):
        return Doc(self.store, self.path, id or f"auto{next(_ids)}")

    def where(self, field, op, value):
        return Query(self.store, self.path, self.filters + [(field, op, value)], self.order, self.lim)

    def order_by(self, field, direction=None):
        return Query(self.store, self.path, self.filters, (field, direction), self.lim)

    def limit(self, n):
        return Query(self.store, self.path, self.filters, self.order, n)

    def stream(self):
        rows = [Snap(Doc(self.store, p, i), d) for (p, i), d in list(self.store.items()) if p == self.path]
        rows = [r for r in rows if all(OPS[op](r._d.get(f), v) for f, op, v in self.filters)]
        if self.order:
            rows.sort(key=lambda r: r._d.get(self.order[0]), reverse=self.order[1] == "DESCENDING")
        return rows[: self.lim] if self.lim else rows


class Batch:
    def __init__(self):
        self.ops = []

    def set(self, ref, data, merge=False):
        self.ops.append(("set", ref, data, merge))

    def delete(self, ref):
        self.ops.append(("del", ref, None, None))

    def commit(self):
        for op, ref, data, merge in self.ops:
            ref.set(data, merge=merge) if op == "set" else ref.delete()
        self.ops = []


class Client:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return Query(self.store, name)

    def batch(self):
        return Batch()


def install():
    fs = types.ModuleType("google.cloud.firestore")
    fs.Client = Client
    fs.SERVER_TIMESTAMP = "SERVER_TIMESTAMP"
    fs.Query = types.SimpleNamespace(DESCENDING="DESCENDING", ASCENDING="ASCENDING")
    g = sys.modules.get("google") or types.ModuleType("google")
    gc = types.ModuleType("google.cloud")
    gc.firestore = fs
    g.cloud = gc
    sys.modules.update({"google": g, "google.cloud": gc, "google.cloud.firestore": fs})
    return fs
