# amaGama metrics

Snapshots of https://amagama-live.translatehouse.org/api/v1/stats/, kept by
the Metrics workflow (`devsupport/metrics.py` on main); see the API docs for
what each figure means.

- `rebuilds.csv`: a row per source language and rebuild. `built` is when its
  imports finished (to the second); `started`, `imported` and `swapped` when
  it started, finished importing and swapped in; `files`, `units`, `skipped`
  and `unreadable` what it imported; `copied_blocks`, `copied_files` and
  `copied_targets` what it copied unchanged from the live data; `left_out` and `left_out_<check>` the
  targets its checks left out; `note` says what else to know about a row.
  Rebuilds from before the statistics kept these were filled in from the
  update journal, and the first row is the 2019 database (Legacy OSS) every
  rebuild loads first.
- `use.csv`: lookups per day, by kind.
- `builds/`: each rebuild's figures, as first seen.
