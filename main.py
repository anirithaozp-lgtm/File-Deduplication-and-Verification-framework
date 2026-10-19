"""
main.py  -  command line program

Usage:
    python main.py scan   <folder>     find duplicate files in a folder
    python main.py check  <file>       is this file's content already stored?
    python main.py verify              check stored files for missing/changed
    python main.py remove <file>       forget a stored file
    python main.py stats               show filter and index statistics

Add  --index my_index.json  to use a different saved index file.
"""

import argparse
import os
import sys

from dedup import DedupSystem


def main():
    parser = argparse.ArgumentParser(description="Cuckoo filter file deduplication")
    parser.add_argument("command",
                        choices=["scan", "check", "verify", "remove", "stats"])
    parser.add_argument("target", nargs="?", help="folder or file path")
    parser.add_argument("--index", default="index.json",
                        help="where the saved index lives (default index.json)")
    args = parser.parse_args()

    system = DedupSystem(index_file=args.index)

    if args.command in ("scan", "check", "remove") and not args.target:
        parser.error(f"'{args.command}' needs a path")

    if args.command == "scan":
        if not os.path.isdir(args.target):
            print("Not a folder:", args.target)
            return 1
        duplicates = system.scan_folder(args.target)
        for copy, original in duplicates:
            print(f"DUPLICATE: {copy}\n     copy of: {original}")
        s = system.stats
        print("\n--- Scan finished ---")
        print(f"Files scanned     : {s['scanned']}")
        print(f"New files stored  : {s['new']}")
        print(f"Duplicates found  : {s['duplicates']}")
        print(f"Already indexed   : {s['already_indexed']}")
        print(f"Filter false alarms (caught by exact check): {s['false_positives']}")

    elif args.command == "check":
        if not os.path.isfile(args.target):
            print("Not a file:", args.target)
            return 1
        known, where = system.check_file(args.target)
        if known:
            print("KNOWN: same content is already stored at", where)
        else:
            print("NEW: this content is not in the index")

    elif args.command == "verify":
        missing, changed = system.verify_all()
        for path in missing:
            print("MISSING:", path)
        for path in changed:
            print("CHANGED:", path)
        if not missing and not changed:
            print("All stored files verified OK.")
        else:
            return 1

    elif args.command == "remove":
        if system.remove_file(args.target):
            print("Removed from index:", os.path.abspath(args.target))
        else:
            print("That file is not in the index.")

    elif args.command == "stats":
        for key, value in system.summary().items():
            print(f"{key}: {value}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

# Testing Git changes