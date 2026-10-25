
import hashlib
import random

_OFFSET_CACHE_LIMIT = 65536


class CuckooFilter:
    def __init__(self, num_buckets=1024, bucket_size=4,
                 fingerprint_bits=16, max_kicks=500, seed=None):
        if not isinstance(num_buckets, int) or num_buckets < 1:
            raise ValueError("num_buckets must be an integer >= 1")
        if not isinstance(bucket_size, int) or bucket_size < 1:
            raise ValueError("bucket_size must be an integer >= 1")
        if not isinstance(fingerprint_bits, int) or not 1 <= fingerprint_bits <= 32:
            raise ValueError("fingerprint_bits must be between 1 and 32")
        if not isinstance(max_kicks, int) or max_kicks < 0:
            raise ValueError("max_kicks must be an integer >= 0")

        size = 1
        while size < num_buckets:
            size = size * 2

        self.num_buckets = size
        self.bucket_size = bucket_size
        self.fingerprint_bits = fingerprint_bits
        self.max_kicks = max_kicks

        self._rng = random.Random(seed)
        self._offset_cache = {}

        self.buckets = [[] for _ in range(self.num_buckets)]
        self.count = 0   

    @staticmethod
    def _hash(item):
        if isinstance(item, str):
            data = item.encode("utf-8")
        elif isinstance(item, (bytes, bytearray)):
            data = bytes(item)
        else:
            raise TypeError("items must be str or bytes, not "
                            + type(item).__name__)
        digest = hashlib.sha256(data).hexdigest()
        return int(digest[:16], 16)          

    def _fingerprint_from_hash(self, h):
        max_value = (2 ** self.fingerprint_bits) - 1
        return (h >> 32) % max_value + 1

    def _fingerprint_offset(self, fingerprint):
        offset = self._offset_cache.get(fingerprint)
        if offset is None:
            offset = self._hash(str(fingerprint)) % self.num_buckets
            if len(self._offset_cache) < _OFFSET_CACHE_LIMIT:
                self._offset_cache[fingerprint] = offset
        return offset

    def _fingerprint(self, item):
        return self._fingerprint_from_hash(self._hash(item))

    def _first_bucket(self, item):
        return self._hash(item) % self.num_buckets

    def _other_bucket(self, bucket, fingerprint):
        return bucket ^ self._fingerprint_offset(fingerprint)

    def _two_buckets(self, item):
        h = self._hash(item)
        fp = self._fingerprint_from_hash(h)
        b1 = h % self.num_buckets
        b2 = self._other_bucket(b1, fp)
        return fp, b1, b2

    def insert(self, item):
        fp, b1, b2 = self._two_buckets(item)

        for b in (b1, b2):
            if len(self.buckets[b]) < self.bucket_size:
                self.buckets[b].append(fp)
                self.count += 1
                return True

        bucket = self._rng.choice([b1, b2])
        history = []                          

        for _ in range(self.max_kicks):
            slot = self._rng.randrange(len(self.buckets[bucket]))
            fp, self.buckets[bucket][slot] = self.buckets[bucket][slot], fp
            history.append((bucket, slot))

            bucket = self._other_bucket(bucket, fp)
            if len(self.buckets[bucket]) < self.bucket_size:
                self.buckets[bucket].append(fp)
                self.count += 1
                return True

        for bucket, slot in reversed(history):
            fp, self.buckets[bucket][slot] = self.buckets[bucket][slot], fp
        return False

    def contains(self, item):
        fp, b1, b2 = self._two_buckets(item)
        return fp in self.buckets[b1] or fp in self.buckets[b2]

    def delete(self, item):
        fp, b1, b2 = self._two_buckets(item)
        for b in (b1, b2):
            if fp in self.buckets[b]:
                self.buckets[b].remove(fp)
                self.count -= 1
                return True
        return False

    def capacity(self):
        return self.num_buckets * self.bucket_size

    def load_factor(self):
        return self.count / self.capacity()

    def memory_bits(self):
        return self.capacity() * self.fingerprint_bits

    def max_false_positive_rate(self):
        return 2 * self.bucket_size / (2 ** self.fingerprint_bits)

    def __len__(self):
        return self.count

    def __contains__(self, item):
        return self.contains(item)

    def __repr__(self):
        return (f"CuckooFilter(buckets={self.num_buckets}, "
                f"bucket_size={self.bucket_size}, "
                f"fingerprint_bits={self.fingerprint_bits}, "
                f"items={self.count})")
