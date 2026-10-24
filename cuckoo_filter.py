"""
cuckoo_filter.py
================
A Cuckoo Filter written to be easy to read and explain.

WHAT IS IT?
    A cuckoo filter answers the question "Have I seen this item before?"
    using very little memory. It can answer:
        - "Definitely NOT present"   (always correct)
        - "Probably present"         (very rarely wrong -> a "false positive")
    Unlike a Bloom filter, it also supports DELETE.

HOW IT WORKS (4 ideas)
    1. Instead of storing the item, we store a tiny FINGERPRINT of it.
    2. The filter is an array of BUCKETS. Each bucket holds a few fingerprints.
    3. Every item has TWO possible buckets:
           bucket1 = hash(item)
           bucket2 = bucket1 XOR hash(fingerprint)
       XOR has a neat property: doing it twice gets you back where you
       started. So from bucket2 we can get back to bucket1 using only the
       fingerprint (we do NOT need the original item).
    4. INSERT: if one of the two buckets has room, put the fingerprint there.
       If both are full, kick out a fingerprint that is already there and move
       it to ITS other bucket (like a cuckoo bird pushing eggs out of a nest).
       Repeat until someone finds room or we give up.

TIME COMPLEXITY  (b = bucket_size, a small constant; N = items stored)
    contains ... O(b) = O(1)     look in 2 buckets
    delete ..... O(b) = O(1)
    insert ..... O(1) amortised  usually a free slot; otherwise a short chain of
                                 kicks, capped at max_kicks (a constant)
    memory ..... O(N): about (N / load_factor) * fingerprint_bits bits

IMPORTANT RULES
    * The number of buckets must be a power of 2 (8, 16, 1024 ...).
      The XOR trick only works properly for powers of 2.
      (The code rounds up for you.)
    * Only delete items you really inserted, otherwise you might remove the
      fingerprint of a different item (this would cause a false negative).
    * The same item can only be inserted about 2 * bucket_size times, because
      all its copies have to fit in its two buckets.
"""

import hashlib
import random

# Remember at most this many "fingerprint -> bucket offset" results.
# (A 16-bit filter has at most 65,535 different fingerprints.)
_OFFSET_CACHE_LIMIT = 65536


class CuckooFilter:
    def __init__(self, num_buckets=1024, bucket_size=4,
                 fingerprint_bits=16, max_kicks=500, seed=None):
        """
        num_buckets      : how many buckets (rounded up to a power of 2)
        bucket_size      : fingerprints each bucket can hold
        fingerprint_bits : size of each fingerprint (bigger = fewer mistakes)
        max_kicks        : how many times insert may kick before giving up
        seed             : optional number that makes the random kicking
                           repeatable (handy for tests and experiments)
        """
        if not isinstance(num_buckets, int) or num_buckets < 1:
            raise ValueError("num_buckets must be an integer >= 1")
        if not isinstance(bucket_size, int) or bucket_size < 1:
            raise ValueError("bucket_size must be an integer >= 1")
        if not isinstance(fingerprint_bits, int) or not 1 <= fingerprint_bits <= 32:
            raise ValueError("fingerprint_bits must be between 1 and 32")
        if not isinstance(max_kicks, int) or max_kicks < 0:
            raise ValueError("max_kicks must be an integer >= 0")

        # Round num_buckets UP to the next power of 2.
        size = 1
        while size < num_buckets:
            size = size * 2

        self.num_buckets = size
        self.bucket_size = bucket_size
        self.fingerprint_bits = fingerprint_bits
        self.max_kicks = max_kicks

        self._rng = random.Random(seed)
        self._offset_cache = {}

        # buckets[i] is a list of fingerprints, e.g. buckets[3] = [17, 902]
        self.buckets = [[] for _ in range(self.num_buckets)]
        self.count = 0   # how many fingerprints are stored right now

    # ------------------------------------------------------------------
    # Helper methods (they turn an item into a fingerprint and buckets)
    # ------------------------------------------------------------------
    @staticmethod
    def _hash(item):
        """Turn text/bytes into a big number. Same input -> same number."""
        if isinstance(item, str):
            data = item.encode("utf-8")
        elif isinstance(item, (bytes, bytearray)):
            data = bytes(item)
        else:
            raise TypeError("items must be str or bytes, not "
                            + type(item).__name__)
        digest = hashlib.sha256(data).hexdigest()
        return int(digest[:16], 16)          # use the first 64 bits

    def _fingerprint_from_hash(self, h):
        """A small number from 1 to (2^bits - 1). Zero is never used."""
        max_value = (2 ** self.fingerprint_bits) - 1
        return (h >> 32) % max_value + 1

    def _fingerprint_offset(self, fingerprint):
        """hash(fingerprint) mod buckets. Cached: it only depends on the
        fingerprint, and the same few fingerprints are used again and again."""
        offset = self._offset_cache.get(fingerprint)
        if offset is None:
            offset = self._hash(str(fingerprint)) % self.num_buckets
            if len(self._offset_cache) < _OFFSET_CACHE_LIMIT:
                self._offset_cache[fingerprint] = offset
        return offset

    def _fingerprint(self, item):
        return self._fingerprint_from_hash(self._hash(item))

    def _first_bucket(self, item):
        """The first bucket where this item may live."""
        return self._hash(item) % self.num_buckets

    def _other_bucket(self, bucket, fingerprint):
        """The item's second bucket (works in both directions)."""
        return bucket ^ self._fingerprint_offset(fingerprint)

    def _two_buckets(self, item):
        """Hash the item ONCE and derive fingerprint + both buckets from it."""
        h = self._hash(item)
        fp = self._fingerprint_from_hash(h)
        b1 = h % self.num_buckets
        b2 = self._other_bucket(b1, fp)
        return fp, b1, b2

    # ------------------------------------------------------------------
    # The three main operations
    # ------------------------------------------------------------------
    def insert(self, item):
        """
        Add an item. Returns True on success.
        Returns False if the filter is too full. In that case the filter is
        left exactly as it was, so nothing already stored is lost.
        """
        fp, b1, b2 = self._two_buckets(item)

        # Step 1: any free space in either bucket? Great, we are done.
        for b in (b1, b2):
            if len(self.buckets[b]) < self.bucket_size:
                self.buckets[b].append(fp)
                self.count += 1
                return True

        # Step 2: both buckets are full, so start kicking fingerprints out.
        bucket = self._rng.choice([b1, b2])
        history = []                          # remember each swap (to undo)

        for _ in range(self.max_kicks):
            # pick a random fingerprint in this bucket and swap with ours
            slot = self._rng.randrange(len(self.buckets[bucket]))
            fp, self.buckets[bucket][slot] = self.buckets[bucket][slot], fp
            history.append((bucket, slot))

            # the fingerprint we just kicked out must go to ITS other bucket
            bucket = self._other_bucket(bucket, fp)
            if len(self.buckets[bucket]) < self.bucket_size:
                self.buckets[bucket].append(fp)
                self.count += 1
                return True

        # Step 3: we gave up. Undo every swap, newest first, so the filter
        # goes back to how it was before this insert.
        for bucket, slot in reversed(history):
            fp, self.buckets[bucket][slot] = self.buckets[bucket][slot], fp
        return False

    def contains(self, item):
        """
        True  -> item is PROBABLY present (small chance of being wrong)
        False -> item is DEFINITELY not present
        """
        fp, b1, b2 = self._two_buckets(item)
        return fp in self.buckets[b1] or fp in self.buckets[b2]

    def delete(self, item):
        """
        Remove an item. Returns True if something was removed.
        Only delete items you really inserted, otherwise you might remove
        the fingerprint of a different item.
        """
        fp, b1, b2 = self._two_buckets(item)
        for b in (b1, b2):
            if fp in self.buckets[b]:
                self.buckets[b].remove(fp)
                self.count -= 1
                return True
        return False

    # ------------------------------------------------------------------
    # Statistics (useful for the report)
    # ------------------------------------------------------------------
    def capacity(self):
        """Total number of fingerprints the filter can hold."""
        return self.num_buckets * self.bucket_size

    def load_factor(self):
        """How full the filter is: 0.0 = empty, 1.0 = completely full."""
        return self.count / self.capacity()

    def memory_bits(self):
        """Bits needed to store all the slots."""
        return self.capacity() * self.fingerprint_bits

    def max_false_positive_rate(self):
        """
        Upper bound on the chance of a wrong "present" answer.
        Two buckets are checked, each with bucket_size slots, and each slot
        matches a random item with probability 1 / 2^bits.
        """
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
