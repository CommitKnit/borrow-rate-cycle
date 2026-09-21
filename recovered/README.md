# Recovered originals

The original research scripts, preserved **unmodified**.

None of these were ever committed to any branch of the source repository. They
survived only as dangling git blobs — objects unreachable from every branch,
tag and stash, which a single `git gc` would have deleted permanently. They
were recovered by content hash with `git cat-file`, at byte counts matching
their originals exactly.

They are kept here as the historical record and are **not** part of the
package: they still import the original repo's ArcticDB layer and will not run
from this clone. The working code is the port in `borrowcycle/`, which fixes
three defects present in these files.

SHA table and full recovery notes:
[`../docs/07_provenance.md`](../docs/07_provenance.md).

Two CSVs are also preserved here — `research_iv_pairs.csv` (1,502 rows) and
`research_collapse_signals.csv` (14,732 rows) — per-bar research exports
covering a 6-ticker universe.
