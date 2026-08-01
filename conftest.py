"""Keep sync-tool duplicates out of the test run.

The file-sync path drops byte-identical copies next to the originals
("test_api 2.py"). Fifteen of them were being collected here, contributing 208
tests to a run that reported 1891 — a number that looked like coverage and was
really the same assertions counted twice.

Worse than the inflation: a duplicate is a FROZEN copy. Edit the original and
its stale twin keeps passing the old expectation, so a run stays green while
disagreeing with itself. .gitignore keeps them out of the repo; this keeps them
out of the results.
"""

collect_ignore_glob = ["* [0-9].py", "**/* [0-9].py"]
