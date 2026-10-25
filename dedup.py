import codecs
import filecmp
import hashlib
import json
import os

from cuckoo_filter import CuckooFilter

CHUNK_SIZE = 1024 * 1024        
INDEX_VERSION = 2


class IndexCorruptError(Exception):
 
def clean_text(data):
    
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return " ".join(text.split()).encode("utf-8")


class _WhitespaceNormalizer:
    
    def __init__(self):
        self.hasher = hashlib.sha256()
        self.started = False      
        self.in_word = False      

    def feed(self, text):
        words = text.split()
        if not words:                        
            if text:
                self.in_word = False
            return
        continues = self.in_word and not text[0].isspace()
        for i, word in enumerate(words):
            if self.started and not (i == 0 and continues):
                self.hasher.update(b" ")     
            self.hasher.update(word.encode("utf-8"))
            self.started = True
        self.in_word = not text[-1].isspace()

    def finish(self):
        return self.hasher.hexdigest()


def hash_file(path, chunk_size=CHUNK_SIZE):
   
    raw = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    normalizer = _WhitespaceNormalizer()
    is_text = True

    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            raw.update(chunk)
            if is_text:
                try:
                    normalizer.feed(decoder.decode(chunk))
                except UnicodeDecodeError:
                    is_text = False      

    if is_text:
        try:
            normalizer.feed(decoder.decode(b"", final=True))
        except UnicodeDecodeError:        
            is_text = False

    raw_hash = raw.hexdigest()
    if not is_text:
        return raw_hash, raw_hash
    return normalizer.finish(), raw_hash

class DedupSystem:
    def __init__(self, index_file="index.json", expected_files=1000, seed=None):
        self.index_file = index_file
        self.expected_files = expected_files
        self.seed = seed

        self.files = {}
        self.by_digest = {}
        self.filter = None

        self.errors = []

        self.stats = {"scanned": 0, "new": 0, "duplicates": 0,
                      "already_indexed": 0, "updated": 0, "filter_hits": 0,
                      "false_positives": 0, "errors": 0}

        self._load()

    @staticmethod
    def _key(path):
       
        absolute = os.path.abspath(path)
        return os.path.normcase(absolute), absolute

    def _error(self, path, message):
        self.errors.append((path, message))
        self.stats["errors"] += 1

    def _original_key(self, digest):
        return next(iter(self.by_digest[digest]))

    def _original_path(self, digest):
        return self.files[self._original_key(digest)]["path"]

    def _link(self, key, info):
       
        self.files[key] = info
        self.by_digest.setdefault(info["digest"], {})[key] = None

    def _unlink(self, key):
       
        info = self.files.pop(key)
        keys = self.by_digest[info["digest"]]
        del keys[key]
        if not keys:
            del self.by_digest[info["digest"]]
            self.filter.delete(info["digest"])
        return info

    def _drop_missing_originals(self, digest):
       
        keys = self.by_digest.get(digest)
        while keys:
            first = next(iter(keys))
            if os.path.exists(self.files[first]["path"]):
                return
            self._unlink(first)
            keys = self.by_digest.get(digest)

    def _load(self):
       
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise IndexCorruptError(
                    f"index file '{self.index_file}' is not valid JSON "
                    f"({err}). It was NOT changed; fix or delete it.")
            self._ingest(data)
        self._build_filter()

    def _ingest(self, data):
       
        try:
            if not isinstance(data, dict):
                raise TypeError("top level must be an object")

            if data.get("version") == INDEX_VERSION:
                for key, info in data["files"].items():
                    self._link(key, {
                        "path": info["path"],
                        "digest": info["digest"],
                        "raw": info.get("raw"),
                        "size": info["size"],
                        "mtime_ns": info.get("mtime_ns"),
                    })
            elif "version" in data:
                raise ValueError(f"unsupported index version {data['version']!r}")
            else:
                # Version 1 (old): {digest: {"path": ..., "size": ...}}
                # Paths are made absolute so they match the keys used later.
                for digest, info in data.items():
                    key, absolute = self._key(info["path"])
                    if key in self.files:             # same path twice: keep newest
                        self._unlink_loading(key)
                    self._link(key, {
                        "path": absolute,
                        "digest": digest,
                        "raw": None,
                        "size": info["size"],
                        "mtime_ns": None,
                    })
        except (KeyError, TypeError, AttributeError, ValueError) as err:
            self.files.clear()
            self.by_digest.clear()
            raise IndexCorruptError(
                f"index file '{self.index_file}' has an unexpected layout "
                f"({err}). It was NOT changed; fix or delete it.")

    def _unlink_loading(self, key):
       
        info = self.files.pop(key)
        keys = self.by_digest[info["digest"]]
        del keys[key]
        if not keys:
            del self.by_digest[info["digest"]]

    def save(self):
        
        temp = self.index_file + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump({"version": INDEX_VERSION, "files": self.files},
                          f, indent=2, ensure_ascii=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.index_file)
        except BaseException:
            if os.path.exists(temp):
                os.remove(temp)
            raise

    def _build_filter(self, grow=False):
       
        wanted = max(self.expected_files, len(self.by_digest))
        num_buckets = max(8, int(wanted / (4 * 0.80)))   # aim for 80% full
        if grow and self.filter is not None:
            num_buckets = max(num_buckets, self.filter.num_buckets * 2)
        for _ in range(32):                              # 32 doublings is plenty
            cf = CuckooFilter(num_buckets=num_buckets, seed=self.seed)
            if all(cf.insert(digest) for digest in self.by_digest):
                self.filter = cf
                return
            num_buckets = cf.num_buckets * 2
        raise RuntimeError("could not build a cuckoo filter for this index")

    def filter_consistent(self):
       
        return (len(self.filter) == len(self.by_digest) and
                all(self.filter.contains(d) for d in self.by_digest))

    def _report_known(self, key):
      
        digest = self.files[key]["digest"]
        original_key = self._original_key(digest)
        if original_key == key:
            self.stats["already_indexed"] += 1
            return "already_indexed", self.files[key]["path"]
        self.stats["duplicates"] += 1
        return "duplicate", self.files[original_key]["path"]

    def add_file(self, path, force=False, rebaseline=False):
        
        key, absolute = self._key(path)
        st = os.stat(absolute)
        known = self.files.get(key)

      
        if (known and not force and known["mtime_ns"] is not None
                and known["size"] == st.st_size
                and known["mtime_ns"] == st.st_mtime_ns):
            self.stats["scanned"] += 1
            self._drop_missing_originals(known["digest"])
            return self._report_known(key)

        digest, raw = hash_file(absolute)
        self.stats["scanned"] += 1

        if known:
            if known["digest"] == digest:
               
                if rebaseline or known["raw"] is None or known["raw"] == raw:
                    known.update(raw=raw, size=st.st_size,
                                 mtime_ns=st.st_mtime_ns)
                self._drop_missing_originals(digest)
                return self._report_known(key)
           
            self._unlink(key)
            self.stats["updated"] += 1

        info = {"path": absolute, "digest": digest, "raw": raw,
                "size": st.st_size, "mtime_ns": st.st_mtime_ns}

        self._drop_missing_originals(digest)
        if self.filter.contains(digest):
            self.stats["filter_hits"] += 1
            if digest in self.by_digest:               
                original = self._original_path(digest)
                self._link(key, info)
                self.stats["duplicates"] += 1
                return "duplicate", original
            self.stats["false_positives"] += 1        

        
        self._link(key, info)
        if not self.filter.insert(digest):
            self._build_filter(grow=True)    
        self.stats["new"] += 1
        return "new", absolute

    def scan_folder(self, folder, force=False, rebaseline=False,
                    save_every=2000):
       
        if not os.path.isdir(folder):
            raise NotADirectoryError(folder)

        self.errors = []
        skip = {self._key(self.index_file)[0],             
                self._key(self.index_file + ".tmp")[0],
                self._key(self.index_file + ".lock")[0]}
        duplicates = []
        since_save = 0

        def on_walk_error(err):
            self._error(err.filename, err.strerror or str(err))

        try:
            for dirpath, dirnames, filenames in os.walk(folder, onerror=on_walk_error):
                dirnames.sort()                            # same order every run
                for name in sorted(filenames):
                    path = os.path.join(dirpath, name)
                    if self._key(path)[0] in skip:
                        continue
                    if not os.path.isfile(path):           # fifo, socket, broken link
                        self._error(path, "not a regular file (or a broken link)")
                        continue
                    try:
                        status, original = self.add_file(
                            path, force=force, rebaseline=rebaseline)
                    except OSError as err:
                        self._error(path, err.strerror or str(err))
                        continue
                    if status == "duplicate":
                        duplicates.append((os.path.abspath(path), original))
                    since_save += 1
                    if save_every and since_save >= save_every:
                        self.save()
                        since_save = 0
        finally:
            self.save()
        return duplicates

    def delete_duplicates(self, duplicates, dry_run=True, min_size=1):
        """
        Delete duplicate copies found by scan_folder.

        Safety rules, because deleting cannot be undone:
          * the original must still exist,
          * both must be different files (a hard link is not a copy),
          * the copy must be at least `min_size` bytes (default 1, so empty
            files such as __init__.py or .gitkeep are never deleted),
          * they must be BYTE-FOR-BYTE identical (checked right now).
            A file that only differs in whitespace is never deleted.
          * dry_run=True (the default) only reports.

        Returns a list of (path, action).
        """
        results = []
        deleted_any = False
        for dup, original in duplicates:
            dup_key = self._key(dup)[0]
            try:
                if dup_key not in self.files:
                    results.append((dup, "kept (not in index)"))
                elif not os.path.isfile(original):
                    results.append((dup, "kept (original is missing)"))
                elif not os.path.isfile(dup):
                    results.append((dup, "kept (file is missing)"))
                elif os.path.getsize(dup) < min_size:
                    results.append((dup, f"kept (smaller than {min_size} bytes)"))
                elif os.path.samefile(dup, original):
                    results.append((dup, "kept (same file as original)"))
                elif not filecmp.cmp(dup, original, shallow=False):
                    results.append((dup, "kept (differs from original, e.g. whitespace)"))
                elif dry_run:
                    results.append((dup, "would delete"))
                else:
                    os.remove(dup)
                    self._unlink(dup_key)
                    deleted_any = True
                    results.append((dup, "deleted"))
            except OSError as err:           # permissions, file vanished, ...
                results.append((dup, f"kept (error: {err.strerror or err})"))
        if deleted_any:
            self.save()
        return results

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def check_file(self, path):
        """
        Is a file with this content already stored?
        Returns (True, original_path) or (False, None).
        Raises OSError if the file cannot be read.
        Time: O(S) to hash, then O(1).
        """
        digest, _ = hash_file(path)
        self._drop_missing_originals(digest)
        if not self.filter.contains(digest):    # fast "definitely not"
            return False, None
        if digest in self.by_digest:            # confirm with exact index
            return True, self._original_path(digest)
        return False, None

    def verify_all(self):
        """
        Check every stored file against what we saved.
        Returns three lists:
            missing : files that no longer exist
            changed : files whose bytes are different now (even by one space)
            errors  : [(path, message)] files that could not be read
        Files indexed by the old version have no raw hash, so for them only
        the whitespace-insensitive digest can be compared.
        Time: O(sum of file sizes); a file whose size changed is flagged in
        O(1) without reading it.
        """
        missing, changed, errors = [], [], []
        for info in self.files.values():
            path = info["path"]
            if not os.path.isfile(path):
                missing.append(path)
                continue
            try:
                if info["raw"] is not None and os.path.getsize(path) != info["size"]:
                    changed.append(path)          # size differs: no need to hash
                    continue
                digest, raw = hash_file(path)
            except OSError as err:
                errors.append((path, err.strerror or str(err)))
                continue
            expected = info["raw"] if info["raw"] is not None else info["digest"]
            actual = raw if info["raw"] is not None else digest
            if actual != expected:
                changed.append(path)
        return missing, changed, errors

    # ------------------------------------------------------------------
    # Removing a file from the system (uses the filter's DELETE)
    # ------------------------------------------------------------------
    def remove_file(self, path, save=True):
        """
        Forget a stored file (the file on disk is not touched).
        Returns True if it was found.
        If it was the original of some copies, the next copy becomes the
        original. The filter forgets the content only when no copy is left.
        Time: O(1) to forget, plus O(N) to save.
        """
        key = self._key(path)[0]
        if key not in self.files:
            return False
        self._unlink(key)
        if save:
            self.save()
        return True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self):
        cf = self.filter
        return {
            "files_stored": len(self.files),
            "unique_contents": len(self.by_digest),
            "duplicate_files": len(self.files) - len(self.by_digest),
            "filter_buckets": cf.num_buckets,
            "filter_load_factor": round(cf.load_factor(), 3),
            "filter_memory_bytes": cf.memory_bits() // 8,
            "max_false_positive_rate": cf.max_false_positive_rate(),
            "filter_consistent": self.filter_consistent(),
        }