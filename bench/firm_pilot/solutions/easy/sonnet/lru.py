from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._d = OrderedDict()

    def get(self, key):
        if key not in self._d:
            return -1
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key, value):
        if self.capacity <= 0:
            return
        if key in self._d:
            self._d[key] = value
            self._d.move_to_end(key)
            return
        if len(self._d) >= self.capacity:
            self._d.popitem(last=False)
        self._d[key] = value
