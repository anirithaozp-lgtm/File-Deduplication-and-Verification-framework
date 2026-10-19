

import hashlib
import random


class CuckooFilter:
    def __init__(self, num_buckets=1024, bucket_size=4,
                 fingerprint_bits=16, max_kicks=500):
        """
        num_buckets      : how many buckets (rounded up to a power of 2)
        bucket_size      : fingerprints each bucket can hold
        fingerprint_bits : size of each fingerprint (bigger = fewer mistakes)
        max_kicks        : how many times insert may kick before giving up
        """
        if num_buckets < 1 or bucket_size < 1:
            raise ValueError("num_buckets and bucket_size must be at least 1")
        if not 1 <= fingerprint_bits <= 32:
            raise ValueError("fingerprint_bits must be between 1 and 32")

        # Round num_buckets UP to the next power of 2.
        size = 1
        while size < num_buckets:
            size = size * 2

        self.num_buckets = size
        self.bucket_size = bucket_size
        self.fingerprint_bits = fingerprint_bits
        self.max_kicks = max_kicks

        # buckets[i] is a list of fingerprints, e.g. buckets[3] = [17, 902]
        self.buckets = [[] for _ in range(self.num_buckets)]
        self.count = 0   # how many fingerprints are stored right now

    # ------------------------------------------------------------------
    # Helper methods (they turn an item into a fingerprint and buckets)
    # ------------------------------------------------------------------
    def _hash(self, text):
        """Turn text into a big number. Same text always gives same number."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return int(digest[:16], 16)          # use the first 64 bits

    def _fingerprint(self, item):
        """A small number from 1 to (2^bits - 1). Zero is never used."""
        h = self._hash(item)
        max_value = (2 ** self.fingerprint_bits) - 1
        return (h >> 32) % max_value + 1

    def _first_bucket(self, item):
        """The first bucket where this item may live."""
        return self._hash(item) % self.num_buckets

    def _other_bucket(self, bucket, fingerprint):
        """The item's second bucket (works in both directions)."""
        return bucket ^ (self._hash(str(fingerprint)) % self.num_buckets)

    def _two_buckets(self, item):
        fp = self._fingerprint(item)
        b1 = self._first_bucket(item)
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
        bucket = random.choice([b1, b2])
        history = []                          # remember each swap (to undo)

        for _ in range(self.max_kicks):
            # pick a random fingerprint in this bucket and swap with ours
            slot = random.randrange(len(self.buckets[bucket]))
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