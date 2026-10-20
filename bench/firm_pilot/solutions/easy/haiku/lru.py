from collections import OrderedDict


class LRUCache:
    """Least-recently-used cache with fixed capacity.

    get(key) returns the stored value, or -1 if the key is absent.
    put(key, value) inserts or updates a key. Both a successful get and a
    put count as use. When a new key is added to a full cache, the least
    recently used key is evicted. A capacity of 0 stores nothing.
    """

    def __init__(self, capacity: int):
        if capacity < 0:
            raise ValueError("capacity must be non-negative")
        self.capacity = capacity
        self._data = OrderedDict()  # least recently used first

    def get(self, key):
        if key not in self._data:
            return -1
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key, value):
        if self.capacity == 0:
            return
        if key in self._data:
            self._data[key] = value
            self._data.move_to_end(key)
            return
        if len(self._data) >= self.capacity:
            self._data.popitem(last=False)
        self._data[key] = value
