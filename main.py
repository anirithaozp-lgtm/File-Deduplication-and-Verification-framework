

import argparse
import os
import sys

from dedup import DedupSystem, IndexCorruptError


class IndexLockedError(Exception):
   


class IndexLock:
  

    def __init__(self, index_file):
        self.path = os.path.abspath(index_file) + ".lock"
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise IndexLockedError(
                f"another run is using this index (lock file '{self.path}'). "
                f"If no other run is active, delete that file.")
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def build_parser():
    parser = argparse.ArgumentParser(description="Cuckoo filter file deduplication")
    parser.add_argument("command",
                        choices=["scan", "check", "verify", "remove", "stats"])
    parser.add_argument("target", nargs="?", help="folder or file path")
    parser.add_argument("--index", default="index.json",
                        help="where the saved index lives (default index.json)")
    parser.add_argument("--seed", type=int, default=None,
                        help="seed for the filter's random choices")
    parser.add_argument("--rehash", action="store_true",
                        help="scan: re-read files even if they look unchanged")
    parser.add_argument("--accept-changes", action="store_true",
                        help="scan: accept whitespace-only edits as the new baseline")
    parser.add_argument("--delete-duplicates", action="store_true",
                        help="scan: list byte-for-byte identical copies that can be deleted")
    parser.add_argument("--yes", action="store_true",
                        help="scan: with --delete-duplicates, really delete them")
    parser.add_argument("--min-size", type=int, default=1,
                        help="scan: never delete files smaller than this (default 1)")
    return parser


def run_scan(system, args):
    if not os.path.isdir(args.target):
        print("Not a folder:", args.target)
        return 1

    duplicates = system.scan_folder(args.target, force=args.rehash,
                                    rebaseline=args.accept_changes)
    for copy, original in duplicates:
        print(f"DUPLICATE: {copy}\n     copy of: {original}")

    if args.delete_duplicates and duplicates:
        print()
        results = system.delete_duplicates(duplicates, dry_run=not args.yes,
                                           min_size=args.min_size)
        for path, action in results:
            print(f"{action.upper()}: {path}")
        if not args.yes and any(a == "would delete" for _, a in results):
            print("\nNothing was deleted. Run again with --yes to delete.")

    for path, message in system.errors:
        print(f"SKIPPED: {path} ({message})")

    s = system.stats
    print("\n--- Scan finished ---")
    print(f"Files scanned     : {s['scanned']}")
    print(f"New files stored  : {s['new']}")
    print(f"Duplicates found  : {s['duplicates']}")
    print(f"Already indexed   : {s['already_indexed']}")
    print(f"Updated (changed) : {s['updated']}")
    print(f"Skipped (errors)  : {s['errors']}")
    print(f"Filter false alarms (caught by exact check): {s['false_positives']}")
    return 1 if system.errors else 0


def run_check(system, args):
    if not os.path.isfile(args.target):
        print("Not a file:", args.target)
        return 1
    try:
        known, where = system.check_file(args.target)
    except OSError as err:
        print(f"Cannot read {args.target}: {err.strerror or err}")
        return 1
    if known:
        print("KNOWN: same content is already stored at", where)
    else:
        print("NEW: this content is not in the index")
    return 0


def run_verify(system):
    missing, changed, errors = system.verify_all()
    for path in missing:
        print("MISSING:", path)
    for path in changed:
        print("CHANGED:", path)
    for path, message in errors:
        print(f"UNREADABLE: {path} ({message})")
    if missing or changed or errors:
        return 1
    print("All stored files verified OK.")
    return 0


def run_command(args):
   
    try:
        system = DedupSystem(index_file=args.index, seed=args.seed)
    except IndexCorruptError as err:
        print("ERROR:", err)
        return 2

    if args.command == "scan":
        return run_scan(system, args)
    if args.command == "check":
        return run_check(system, args)
    if args.command == "verify":
        return run_verify(system)
    if args.command == "remove":
        if system.remove_file(args.target):
            print("Removed from index:", os.path.abspath(args.target))
            return 0
        print("That file is not in the index.")
        return 1
    if args.command == "stats":
        for key, value in system.summary().items():
            print(f"{key}: {value}")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in ("scan", "check", "remove") and not args.target:
        parser.error(f"'{args.command}' needs a path")
    scan_only = (args.rehash or args.accept_changes or args.delete_duplicates
                 or args.yes or args.min_size != 1)
    if args.command != "scan" and scan_only:
        parser.error("--rehash, --accept-changes, --delete-duplicates, --yes "
                     "and --min-size only work with 'scan'")
    if args.yes and not args.delete_duplicates:
        parser.error("--yes needs --delete-duplicates")
    if args.min_size < 0:
        parser.error("--min-size cannot be negative")


    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    try:
        if args.command in ("scan", "remove"):      
            with IndexLock(args.index):
                return run_command(args)
        return run_command(args)
    except IndexLockedError as err:
        print("ERROR:", err)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted. Progress made so far was saved.")
        return 1
    except OSError as err:                           
        print(f"ERROR: {err.strerror or err}"
              + (f" ({err.filename})" if err.filename else ""))
        return 1


if __name__ == "__main__":
    sys.exit(main())
