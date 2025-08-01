#!/usr/bin/env bash
#
# rename-rpcs.sh
#
# Loops through all .rpc.json files in the given directory (or cwd),
# strips off the “.rpc.json” suffix, removes underscores, uppercases
# the name, appends “_36M.txt”, and renames the file.
#

set -euo pipefail

DIR="${1:-.}"

shopt -s nullglob
for f in "$DIR"/*.rpc.json; do
  # strip directory and .rpc.json
  base="$(basename "$f" .rpc.json)"
  # remove all underscores
  no_underscores="${base//_/}"
  # uppercase
  upper="${no_underscores^^}"
  # build new name
  new="${upper}_36M.txt"
  # rename
  mv -- "$f" "$DIR/$new"
  echo "Renamed: '$f' → '$new'"
done
