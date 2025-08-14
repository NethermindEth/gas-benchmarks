#!/usr/bin/env bash
#
# rename-rpcs.sh
#
# Renames all *.rpc.json files in DIR (or cwd) to:
#   <BASENAME_WITHOUT_GAS_LIMIT_UPPERCASED_AND_WITHOUT_UNDERSCORES>_<GASLIMIT>.txt
#
# Examples:
#   foo_bar_gas-limit_100M.rpc.json  ->  FOOBAR_100M.txt
#   test-gas_limit-36m.rpc.json      ->  TEST-36M.txt
#   sample.rpc.json                  ->  SAMPLE.txt   (no gas-limit found)
#
# Notes:
# - Removes the literal "gas-limit" (or "gas_limit" / "gaslimit") from the final name.
# - Extracts the value after it (e.g., 100M, 36m, 500K) and appends as "_<VALUE>.txt".
# - If no gas-limit is found, just ".txt" is appended to the transformed base name.
#

set -euo pipefail

DIR="${1:-.}"

shopt -s nullglob
for f in "$DIR"/*.rpc.json; do
  base="$(basename "$f" .rpc.json)"

  # Try to capture a gas-limit segment:
  # matches: gas-limit_100M, gas_limit-36m, gaslimit500k, etc.
  #   group 1: prefix before gas-limit
  #   group 2: value (e.g., 100M, 36m, 500k)
  #   group 3: suffix after the value
  if [[ "$base" =~ ^(.*?)[_-]?gas[-_]?limit[_-]?([0-9]+[kKmMgG]?)($|[_-].*) ]]; then
    prefix="${BASH_REMATCH[1]}"
    gasval="${BASH_REMATCH[2]}"
    suffix="${BASH_REMATCH[3]}"

    # Rebuild the base WITHOUT the 'gas-limit' token
    newbase="${prefix}${suffix}"

    # Trim leading/trailing separators created by removal
    newbase="${newbase#[-_]}"
    newbase="${newbase%[-_]}"

    # Preserve your current behavior: remove underscores and uppercase the base
    no_underscores="${newbase//_/}"
    upper="${no_underscores^^}"

    # Uppercase the gas-limit value (e.g., 36m -> 36M)
    gasval="${gasval^^}"

    new="${upper}_${gasval}.txt"
  else
    # No gas-limit found: keep original behavior (no '_36M'), just make it UPPER and drop underscores
    no_underscores="${base//_/}"
    upper="${no_underscores^^}"
    new="${upper}.txt"
  fi

  mv -- "$f" "$DIR/$new"
  echo "Renamed: '$f' → '$new'"
done
