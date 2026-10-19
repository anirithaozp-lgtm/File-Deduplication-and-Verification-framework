

import hashlib
import json
import os

from cuckoo_filter import CuckooFilter


def clean_text(data):
    """
    Remove whitespace differences so these all count as the SAME content:
        "hello"   " hello"   "hello "   "hello\\n"   "hello\\r\\n"
    How: split the text into words and join them with ONE space.
    That removes leading/trailing spaces and shrinks any run of
    spaces, tabs and new lines into a single space.

    Only done for text files. If the file is not readable text (a photo,
    a zip ...) we return it unchanged, so binary files are compared exactly.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data                       # binary file: leave as it is
    return " ".join(text.split()).encode("utf-8")


def hash_file(path):
    """
    SHA-256 of a file's content, ignoring whitespace differences.
    (Whole file is read into memory, which is fine for a course project.)
    """
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha256(clean_text(data)).hexdigest()


class DedupSystem:
    def __init__(self, index_file="index.json", expected_files=1000):
        self.index_file = index_file
        self.expected_files = expected_files

        # exact record:  hash -> {"path": ..., "size": ...}
        self.records = {}
        self.filter = None

        # counters used in the report
        self.stats = {"scanned": 0, "new": 0, "duplicates": 0,
                      "already_indexed": 0, "filter_hits": 0,
                      "false_positives": 0}

        self._load()

    # ------------------------------------------------------------------
    # Saving, loading and building the filter
    # ------------------------------------------------------------------
    def _load(self):
        """Read the saved record (if any) and build the filter from it."""
        if os.path.exists(self.index_file):
            with open(self.index_file, "r") as f:
                self.records = json.load(f)
        self._build_filter()

    def save(self):
        with open(self.index_file, "w") as f:
            json.dump(self.records, f, indent=2)

    def _build_filter(self):
        """
        Make a filter big enough for all stored files.
        If it still turns out too small, double the size and try again.
        """
        wanted = max(self.expected_files, len(self.records))
        num_buckets = max(8, int(wanted / (4 * 0.80)))   # aim for 80% full
        while True:
            cf = CuckooFilter(num_buckets=num_buckets)
            if all(cf.insert(digest) for digest in self.records):
                self.filter = cf
                return
            num_buckets = cf.num_buckets * 2

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------
    def add_file(self, path):
        """
        Process one file.
        Returns (status, original_path) where status is one of:
            "new"              first time we see this content
            "duplicate"        same content as another stored file
            "already_indexed"  this exact file was stored before
        """
        path = os.path.abspath(path)
        digest = hash_file(path)
        self.stats["scanned"] += 1

        if self.filter.contains(digest):
            self.stats["filter_hits"] += 1
            if digest in self.records:                  # confirmed
                original = self.records[digest]["path"]
                if original == path:
                    self.stats["already_indexed"] += 1
                    return "already_indexed", original
                self.stats["duplicates"] += 1
                return "duplicate", original
            self.stats["false_positives"] += 1          # filter was wrong

        # It is a new file: remember it.
        self.records[digest] = {"path": path, "size": os.path.getsize(path)}
        if not self.filter.insert(digest):
            self._build_filter()          # filter full -> rebuild bigger
        self.stats["new"] += 1
        return "new", path

    def scan_folder(self, folder):
        """
        Process every file inside a folder (and its sub-folders).
        Returns a list of (duplicate_path, original_path).
        """
        duplicates = []
        for dirpath, _, filenames in os.walk(folder):
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                status, original = self.add_file(path)
                if status == "duplicate":
                    duplicates.append((os.path.abspath(path), original))
        self.save()
        return duplicates

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def check_file(self, path):
        """
        Is a file with this content already stored?
        Returns (True, stored_path) or (False, None).
        """
        digest = hash_file(path)
        if not self.filter.contains(digest):    # fast "definitely not"
            return False, None
        if digest in self.records:              # confirm with exact record
            return True, self.records[digest]["path"]
        return False, None

    def verify_all(self):
        """
        Re-hash every stored file and compare with the hash we saved.
        Returns two lists: missing files and changed (corrupted) files.
        """
        missing = []
        changed = []
        for digest, info in self.records.items():
            path = info["path"]
            if not os.path.isfile(path):
                missing.append(path)
            elif hash_file(path) != digest:
                changed.append(path)
        return missing, changed

    # ------------------------------------------------------------------
    # Removing a file from the system (uses the filter's DELETE)
    # ------------------------------------------------------------------
    def remove_file(self, path):
        """Forget a stored file. Returns True if it was found."""
        path = os.path.abspath(path)
        for digest, info in self.records.items():
            if info["path"] == path:
                self.filter.delete(digest)
                del self.records[digest]
                self.save()
                return True
        return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self):
        cf = self.filter
        return {
            "files_stored": len(self.records),
            "filter_buckets": cf.num_buckets,
            "filter_load_factor": round(cf.load_factor(), 3),
            "filter_memory_bytes": cf.memory_bits() // 8,
            "max_false_positive_rate": cf.max_false_positive_rate(),
        }
