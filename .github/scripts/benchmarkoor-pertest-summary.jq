# Renders a per-test MGas/s markdown table from a benchmarkoor result.json.
# Used by the "Benchmarkoor - Nethermind" workflow's job summary step:
#   jq -r -f .github/scripts/benchmarkoor-pertest-summary.jq result.json
[ .tests | to_entries[]
  | .value.steps.test.aggregated as $a
  | select($a != null and ($a.time_total // 0) > 0)
  | { name: (.key | sub("\\.txt$"; "")),
      gasM: (($a.gas_used_total // 0) / 1e6),
      timeS: (($a.time_total // 0) / 1e9),
      fail: ($a.fail // 0) }
  | .mgass = (.gasM / .timeS) ] as $rows
| ($rows | map(.mgass) | sort) as $s
| "**\($rows | length) tests** · median \($s[(($s | length) / 2) | floor] | round) MGas/s · min \($s[0] | round) · max \($s[-1] | round)",
  "",
  "<details><summary>All tests (MGas/s)</summary>",
  "",
  "| Test | Gas (M) | Time (s) | MGas/s | |",
  "|---|---:|---:|---:|---|",
  ($rows | sort_by(.name)[]
    | "| `\(.name)` | \(.gasM | round) | \(.timeS * 100 | round / 100) | **\(.mgass * 10 | round / 10)** | \(if .fail == 0 then "✅" else "❌" end) |"),
  "",
  "</details>"
