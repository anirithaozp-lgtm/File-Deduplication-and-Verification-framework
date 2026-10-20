# File-Deduplication-and-Verification-framework
# File Deduplication and Verification Framework

A Python tool for scanning files and finding duplicates. It uses a cuckoo filter to detect duplicate files quickly.

## Usage

py main.py scan test_files
py main.py check test_files
py main.py verify
py main.py remove
py main.py stats

## Commands

- scan: scans a folder and builds the index
- check: checks files against the index
- verify: verifies the results
- remove: removes duplicates
- stats: shows statistics

## Files

- main.py: the main program that runs the commands
- dedup.py: the duplicate detection logic
- cuckoo_filter.py: the cuckoo filter data structure
- index.json: the saved index of scanned files
- test_files: sample files for testing