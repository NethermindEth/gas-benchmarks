# Benchmarkoor - Nethermind

CI integration of [ethpandaops/benchmarkoor](https://github.com/ethpandaops/benchmarkoor)
into gas-benchmarks, exclusively for Nethermind. Triggered via the
**Benchmarkoor - Nethermind** workflow (`.github/workflows/benchmarkoor-nethermind.yml`)
on the `stateful-generator` self-hosted runner.

## Why

The ethpandaops stateful runs (e.g. the jochemnet repricing suites on
benchmarkoor.core.ethpandaops.io) roll back state between tests with
`rollback_strategy: container-recreate` — the client container is stopped,
removed and restarted for **every test**, which makes stateful suites very
slow and adds startup noise.

This workflow instead defaults to **`container-checkpoint-restore`**:

1. The Nethermind container starts on Podman and (optionally) gets one clean
   restart so the checkpoint captures a cold-cache, cleanly-shut-down process.
2. The pre-run steps (`gas-bump.txt`, `funding.txt`) are executed **before**
   the checkpoint, so their state is baked into it.
3. The datadir is snapshotted on ZFS and the whole process memory is
   checkpointed via CRIU (optionally held on tmpfs). The container stops.
4. Per test: ZFS rollback + CRIU restore — the client resumes mid-execution
   at the exact checkpointed state. No startup, no RPC polling, stable
   baseline for every test.

Requirements (handled automatically by the workflow / benchmarkoor action):

- Podman in rootful mode with `podman.socket` active
- CRIU + a CRIU-enabled `crun` (the action installs both; note it replaces
  `/usr/bin/crun` with the upstream 1.26 release binary)
- the Nethermind datadir on **ZFS** (see below)

## Files

| File | Purpose |
|------|---------|
| `global.yaml` | Base runner settings: `container_runtime: podman`, cache drops, cleanup |
| `tests-archive.yaml` | Test source: `generated-tests-<type>-<network>.tar.gz` from a gas-benchmarks release (layout `repricings_<type>/<network>/{gas-bump,funding,setup/*,testing/*}`) |
| `opcodes.yaml` | Optional opcode metadata (`opcodes_tracing-<type>-<network>.json`) |
| `datadir-zfs.yaml` | Nethermind datadir with `method: zfs` |
| `rollback/*.yaml` | One file per rollback strategy; the workflow picks one |

Configs are fetched by the benchmarkoor GitHub action as raw URLs pinned to the
triggering commit and merged in order (later files win). The Nethermind
instance itself (image, chainspec, extra flags) is composed inline by the
workflow from its inputs, and `extra_run_config` is deep-merged last, so any
setting can be overridden ad hoc without editing files.

`${GB_*}` placeholders are environment variables exported by the workflow;
benchmarkoor resolves them natively when loading the config.

## Runner prerequisites

The `stateful-generator` runner must have the network snapshot at
`/mnt/sda/<network>/nethermind` (the same layout the Repricing - Nethermind
workflow uses; the jochemnet snapshot can be downloaded with
`run-on-stateful-generator.yml`).

**ZFS bootstrap:** `container-checkpoint-restore` only supports ZFS datadirs.
If the snapshot directory is not already on ZFS, the workflow creates a
file-backed zpool (default: sparse 500G image at
`/mnt/sda/benchmarkoor-zpool.img`, pool `benchmarkoor`) and seeds a dataset
from the snapshot with a **one-time** rsync copy (~snapshot size of extra disk
usage; make sure `/mnt/sda` has headroom). Subsequent runs reuse the dataset.
Pass `zpool: {"reseed": true, ...}` to refresh it after updating the snapshot.

## Defaults

- network `jochemnet`, test type `stateful`
- tests + opcodes from release `amsterdam-repricings-v5.2.0`
- chainspec `scripts/genesisfiles/nethermind/generator-amsterdam-<network>.json`
  (jochemnet variant activates the amsterdam EIP set at `0x697ddeff`, matching
  the ethpandaops amsterdam-devnet-7 context)
- image `nethermindeth/nethermind:bal-devnet-7` with the same flags
  ethpandaops use for `nethermind-bal-full`
- rollback `auto`: `container-checkpoint-restore` for stateful, `none` for
  compute; `container-recreate` remains selectable for A/B comparison

Results are uploaded as the `benchmarkoor-<run_id>` workflow artifact and a
per-test summary is rendered on the job summary page.

## Notes

- If `tests_archive_url` points at a GitHub Actions artifact (flat layout:
  `gas-bump.txt`, `setup/…` at the archive root), override the step globs via
  `extra_run_config`, since `tests-archive.yaml` assumes the release layout.
- `snapshot_dir: none` runs without a pre-populated datadir
  (checkpoint-restore then uses its copy-based rollback) — only meaningful for
  suites that build all state from genesis.
- Compute suites default to `rollback_strategy: none`, mirroring the
  ethpandaops compute contexts.
