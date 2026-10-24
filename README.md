# File Deduplication and Verification Framework

Finds duplicate files and checks that stored files have not been damaged or changed.
It uses a **Cuckoo Filter** as a fast, memory-saving first check, and an exact index to confirm every answer.

## Files

| File | Purpose |
|---|---|
| `cuckoo_filter.py` | The cuckoo filter (insert, contains, delete) |
| `dedup.py` | Hashing, duplicate detection, verification, saving the index |
| `main.py` | Command-line program |

## How to run

Needs Python 3 only. Run the commands from the folder that holds the three files.

```
python main.py scan <folder>                        find duplicates and build the index
python main.py scan <folder> --delete-duplicates    preview which copies could be deleted (deletes nothing)
python main.py scan <folder> --delete-duplicates --yes    really delete byte-identical copies
python main.py check <file>       is this content already stored?
python main.py verify             report missing or changed files
python main.py remove <file>      forget a file (the file on disk is kept)
python main.py stats              show filter and index statistics
```

Run `scan` first: the other commands use the index (`index.json`) that it creates.

Options: `--index name.json` (use a different index file), `--seed N` (repeatable filter),
`--rehash` (re-read every file), `--accept-changes` (accept whitespace-only edits as the new baseline),
`--min-size N` (never delete files smaller than N bytes; default 1, so empty files are kept).

Exit codes: `0` ok, `1` problems or failed files, `2` damaged index (never overwritten), `3` index in use by another run.

## How it works

1. Each file is read in chunks and hashed with SHA-256. Two hashes are kept:
   - one that **ignores whitespace**, used to find duplicates (so `hello   world` and `hello world` match);
   - one of the **exact bytes**, used by `verify` to detect any change at all.
2. The cuckoo filter is asked: have we seen this content?
   - **"Definitely not"** (always correct): the file is new, so store it.
   - **"Probably yes"**: confirm with the exact index (a dictionary). If it is not there, it was a false alarm and the file is new.

So the filter only speeds things up. It can never cause a wrong result.

## Why a cuckoo filter?

| Structure | Lookup | Delete | Memory per item |
|---|---|---|---|
| List | O(N) | O(N) | 256 bits |
| Sorted list | O(log N) | O(N) | 256 bits |
| Hash set | O(1) | O(1) | 256+ bits |
| Bloom filter | O(1) | **not possible** | about 19 bits |
| **Cuckoo filter** | **O(1)** | **O(1)** | **about 20-40 bits** |

- It supports **delete**, which a Bloom filter cannot (needed for `remove`).
- A lookup checks only 2 small buckets, however many files are stored.
- It stores a 16-bit fingerprint instead of a 256-bit hash.
- Its false-positive rate is at most `2 x 4 / 2^16`, about **0.012%**.
- The bucket count is rounded up to a power of 2, so the real load is between 40% and 80%, which is 20 to 40 bits per stored content.

**Honest note on memory:** here the full index is also kept in RAM, and the filter is rebuilt from it at start-up, so the filter does not reduce total memory. It demonstrates the technique. In a real system the exact index would live on disk or in a database, and the small filter in RAM would let the program skip disk lookups for definitely-new files.

## Time complexity

`N` = files stored, `S` = size of one file, `F` = files scanned, `k` = save interval (2000).

**Cuckoo filter**

| Operation | Time |
|---|---|
| `contains` | O(1) (looks in 2 buckets of 4 slots) |
| `delete` | O(1) |
| `insert` | O(1) amortised (usually a free slot; otherwise a short chain of kicks, capped at 500) |

Why insert is O(1) amortised: when the filter fills up it is rebuilt at least **twice as big**, so the O(N) rebuild cost is spread over many inserts (like a growing dynamic array). The worst case for a single insert is O(max_kicks), a constant.

**Whole system**

| Operation | Time |
|---|---|
| Hash one file | O(S), reads in chunks, so memory stays constant |
| `add_file` / `check_file` | O(S) (hashing dominates; filter and dictionary work is O(1)) |
| Re-scan of an unchanged file | O(1) (size and modified time match, so it is not re-read) |
| `scan_folder` | O(total size of files + F log F + N + (F / k) x N) |
| `verify_all` | O(total size of files); a file whose size changed is flagged in O(1) |
| `remove_file` | O(1) (plus O(N) if the index is saved) |
| Start-up | O(N) (load index, rebuild filter) |

Reading the `scan_folder` row: each file is hashed once (the total size term), names are sorted per folder (the `F log F` term), and the index is saved once at the end (the `N` term). It is also saved every 2000 files so a crash or Ctrl-C loses little work. Each save is O(N), so a scan of a very large number of files spends noticeable time saving (the `(F / k) x N` term). For typical folders this is small.

## Space complexity

O(N) overall: about 20-40 bits per different content in the filter, plus the index (path, hashes, size per file). While hashing, only one chunk is in memory, so a file's size does not affect memory.

## Edge cases handled

| Situation | What happens |
|---|---|
| Whitespace-only differences (spaces, tabs, `\r\n`) | Files count as duplicates |
| Binary, empty or non-UTF-8 files | Binary files are compared byte for byte |
| Copy differs from original only in whitespace | Reported as duplicate, never deleted (byte-for-byte check) |
| Empty files (`__init__.py`, `.gitkeep`) | Reported as duplicates but never deleted (`--min-size`) |
| Hard link or symlink to the original | Never deleted (`samefile` check) |
| `--delete-duplicates` | Preview by default; `--yes` is required to delete |
| File edited after being indexed | Old entry replaced; `verify` reports changes since indexing |
| File edited only in whitespace | `verify` keeps reporting it until you scan with `--accept-changes` |
| Original deleted or moved outside the tool | It is dropped from the index; the next copy becomes the original |
| File name that is not valid Unicode (Linux) | Index is saved as ASCII-escaped JSON, so saving cannot crash |
| Ctrl-C or crash during a scan | Progress is saved (every 2000 files and on interrupt) |
| Crash while saving | The old index stays intact (write to a temp file, then swap) |
| Damaged index file | Run stops with exit code 2; the file is never overwritten |
| Old version-1 index | Loaded and upgraded; relative paths are made absolute |
| Two runs at the same time | Lock file; the second run exits with code 3 |
| Read-only folder, full disk, missing index folder | One clean error message, exit code 1 |
| Unreadable file, broken link, fifo, socket | Skipped and listed as `SKIPPED`; the scan continues |
| Cuckoo filter full | Rebuilt at least twice as big; a failed insert never loses data |
| Filter false positive | Caught by the exact index |

## Known limits

- `scan` skips a file whose size and modified time are unchanged, so an edit that keeps both is missed by `scan`. `verify` still catches it. Use `--rehash` to force a re-read.
- If an original file disappears and another copy of the same content is scanned later, the missing original is dropped from the index quietly, so `verify` will not list it as missing.
- If a run is killed hard (power cut), a leftover `index.json.lock` must be deleted by hand.
- A file that changes while it is being read can produce an inconsistent hash.
- On case-insensitive filesystems other than Windows (for example default macOS), two spellings of the same path count as two files. Deleting stays safe because of the `samefile` check.
