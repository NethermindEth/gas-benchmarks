<#
.SYNOPSIS
    Run gas-benchmarks with Nethermind started natively (no Docker).
.EXAMPLE
    .\run-native.ps1 -Filter "keccak" -TestsPath "eest_tests" -Runs 1
#>
param(
    [string]$Filter = "",
    [string]$TestsPath = "eest_tests",
    [string]$Client = "nethermind",
    [int]$Runs = 1,
    [int]$WarmupCount = 1,
    [string]$WarmupTestsPath = "warmup-tests",
    [switch]$SkipForkchoice,
    [switch]$SkipPrepareTools,
    [string]$NethermindRepo = "C:\Users\kamil\source\repos\nethermind"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUNBUFFERED = "1"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# --- Paths ---
$RunnerProject = Join-Path $NethermindRepo "src\Nethermind\Nethermind.Runner"
$RunnerBin = Join-Path $NethermindRepo "src\Nethermind\artifacts\bin\Nethermind.Runner\release\nethermind.exe"
$DataDir = Join-Path $ScriptDir "scripts\nethermind\execution-data"
$JwtSecret = Join-Path $ScriptDir "scripts\nethermind\jwtsecret"
$Chainspec = Join-Path $ScriptDir "scripts\genesisfiles\nethermind\zkevmgenesis.json"
$LogFile = Join-Path $ScriptDir "scripts\nethermind\nethermind_native.log"
$PidFile = Join-Path $ScriptDir ".nethermind_native.pid"
$KuteBin = Join-Path $ScriptDir "nethermind\tools\artifacts\bin\Nethermind.Tools.Kute\release\Nethermind.Tools.Kute.exe"

$NethProc = $null

function Stop-Nethermind {
    if ($script:NethProc -and !$script:NethProc.HasExited) {
        Write-Host "[run-native] Stopping Nethermind (PID $($script:NethProc.Id))"
        Stop-Process -Id $script:NethProc.Id -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
}

trap { Stop-Nethermind }

# --- Validate ---
if (!(Test-Path $RunnerProject)) {
    Write-Error "Nethermind Runner project not found: $RunnerProject"
}
if (!(Test-Path $JwtSecret)) {
    Write-Error "JWT secret not found: $JwtSecret"
}
if (!(Test-Path $Chainspec)) {
    Write-Error "Chainspec not found: $Chainspec"
}

# --- Kill stale Nethermind ---
if (Test-Path $PidFile) {
    $stalePid = (Get-Content $PidFile -ErrorAction SilentlyContinue).Trim()
    if ($stalePid) {
        Write-Host "[run-native] Stopping stale Nethermind PID $stalePid"
        Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $PidFile -Force
}

# --- Build Nethermind if needed ---
if (!(Test-Path $RunnerBin)) {
    Write-Host "[run-native] Building Nethermind.Runner..."
    dotnet build $RunnerProject -c Release --property WarningLevel=0
    if (!(Test-Path $RunnerBin)) {
        Write-Error "Built runner not found: $RunnerBin"
    }
}

# --- Build Kute if needed ---
if (!$SkipPrepareTools) {
    $kuteProject = Join-Path $ScriptDir "nethermind\tools\Nethermind.Tools.Kute"
    if (!(Test-Path $KuteBin)) {
        if (!(Test-Path (Join-Path $ScriptDir "nethermind\.git"))) {
            git clone https://github.com/NethermindEth/nethermind (Join-Path $ScriptDir "nethermind")
        }
        Push-Location (Join-Path $ScriptDir "nethermind")
        git fetch --all --prune
        git checkout e1857d7ca6613ccdc40973899290f565f367e235
        git lfs pull
        Pop-Location
        dotnet build $kuteProject -c Release --property WarningLevel=0
        if (!(Test-Path $KuteBin)) {
            Write-Error "Kute binary not found at $KuteBin after build"
        }
    }
}

# --- Prepare directories ---
foreach ($dir in "results", "prepresults", "warmupresults", "logs") {
    $p = Join-Path $ScriptDir $dir
    if ($dir -eq "results" -and (Test-Path $p)) { Remove-Item $p -Recurse -Force }
    New-Item -ItemType Directory -Path $p -Force | Out-Null
}
if (Test-Path $DataDir) {
    Write-Host "[run-native] Cleaning execution-data for fresh state"
    Remove-Item $DataDir -Recurse -Force
}
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

# --- Install Python deps ---
pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet

# --- Start Nethermind ---
Write-Host "[run-native] Starting Nethermind from $RunnerBin"
$nethArgs = @(
    "--config=none"
    "--datadir=$DataDir"
    "--JsonRpc.Enabled=true"
    "--JsonRpc.Host=0.0.0.0"
    "--JsonRpc.Port=8545"
    "--JsonRpc.JwtSecretFile=$JwtSecret"
    "--JsonRpc.EngineHost=0.0.0.0"
    "--JsonRpc.EnginePort=8551"
    '--JsonRpc.EnabledModules=[Debug,Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Testing]'
    "--Network.DiscoveryPort=0"
    "--Network.MaxActivePeers=0"
    "--Init.DiscoveryEnabled=false"
    "--HealthChecks.Enabled=true"
    "--Metrics.Enabled=true"
    "--Metrics.ExposePort=8008"
    "--Sync.MaxAttemptsToUpdatePivot=0"
    "--Init.AutoDump=None"
    "--Merge.NewPayloadBlockProcessingTimeout=70000"
    "--Merge.TerminalTotalDifficulty=0"
    "--Init.LogRules=Consensus.Processing.ProcessingStats:Debug"
    "--Init.ChainSpecPath=$Chainspec"
)

$NethProc = Start-Process -FilePath $RunnerBin -ArgumentList $nethArgs `
    -RedirectStandardOutput $LogFile -RedirectStandardError "$LogFile.err" `
    -PassThru -NoNewWindow
$NethProc.Id | Set-Content $PidFile
Write-Host "[run-native] Nethermind PID: $($NethProc.Id)"

# --- Wait for RPC ---
Write-Host "[run-native] Waiting for RPC at http://127.0.0.1:8545"
$rpcBody = '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
$maxAttempts = 300
$rpcReady = $false
for ($i = 1; $i -le $maxAttempts; $i++) {
    if ($NethProc.HasExited) {
        Write-Host "[run-native] Nethermind exited unexpectedly (exit code $($NethProc.ExitCode)). Last log lines:"
        Get-Content $LogFile -Tail 30 -ErrorAction SilentlyContinue
        Stop-Nethermind
        exit 1
    }
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8545" -Method POST `
            -Body $rpcBody -ContentType "application/json" -TimeoutSec 2 -UseBasicParsing
        if ($resp.Content -match '"result"') {
            Write-Host "[run-native] RPC ready (attempt $i/$maxAttempts)"
            $rpcReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}
if (!$rpcReady) {
    Write-Host "[run-native] RPC failed to start. Last log lines:"
    Get-Content $LogFile -Tail 50 -ErrorAction SilentlyContinue
    Stop-Nethermind
    exit 1
}

# --- Discover test files ---
# Uses Python to walk the test directory with the same ordering logic as run.sh
$discoverPy = @"
import sys, os
from pathlib import Path

root = Path(sys.argv[1])
if not root.exists():
    sys.exit(0)

def try_append(path, bucket):
    if path.is_file() and path.suffix == '.txt':
        bucket.append(str(path))

ordered = []
for name in ('gas-bump.txt', 'funding.txt', 'setup-global-test.txt'):
    try_append(root / name, ordered)

phase_to_files = {}
for phase in ('setup', 'testing', 'cleanup'):
    phase_dir = root / phase
    per_name = {}
    if phase_dir.is_dir():
        for file in sorted(phase_dir.rglob('*.txt')):
            existing = per_name.get(file.stem)
            if existing is None or str(file) < str(existing):
                per_name[file.stem] = file
    phase_to_files[phase] = per_name

scenario_names = sorted(
    set(phase_to_files['setup'].keys())
    | set(phase_to_files['testing'].keys())
    | set(phase_to_files['cleanup'].keys())
)

for name in scenario_names:
    for phase in ('setup', 'testing', 'cleanup'):
        path = phase_to_files[phase].get(name)
        if path is not None:
            ordered.append(str(path))

for name in ('teardown-global-test.txt', 'current-last-global-test.txt'):
    try_append(root / name, ordered)

extra_root = [str(f) for f in sorted(root.glob('*.txt'))]
seen = set()
final = []
for p in ordered + extra_root:
    if p not in seen:
        seen.add(p)
        final.append(p)

for p in final:
    print(p)
"@

$testsPathFull = Join-Path $ScriptDir $TestsPath
$testFiles = @()

# Check if it's a stateful directory (has testing/ subdir) or flat
if (Test-Path (Join-Path $testsPathFull "testing")) {
    $testFiles = (python -c $discoverPy $testsPathFull) | Where-Object { $_.Trim() }
} elseif (Test-Path $testsPathFull -PathType Container) {
    # Flat directory: walk subdirectories, each may be stateful
    $subdirs = Get-ChildItem $testsPathFull -Directory | Sort-Object Name
    foreach ($sub in $subdirs) {
        if (Test-Path (Join-Path $sub.FullName "testing")) {
            $subFiles = (python -c $discoverPy $sub.FullName) | Where-Object { $_.Trim() }
            $testFiles += $subFiles
        } else {
            $testFiles += (Get-ChildItem $sub.FullName -Filter "*.txt" -Recurse | Sort-Object FullName | ForEach-Object { $_.FullName })
        }
    }
} else {
    # Single file
    $testFiles = @($testsPathFull)
}

if ($testFiles.Count -eq 0) {
    Write-Host "[run-native] No test files found in $testsPathFull"
    Stop-Nethermind
    exit 1
}
Write-Host "[run-native] Found $($testFiles.Count) test files"

# --- Classification helpers ---
$globalFiles = @("gas-bump.txt", "funding.txt", "setup-global-test.txt", "teardown-global-test.txt")

function Test-MeasuredFile([string]$filePath) {
    $filename = Split-Path -Leaf $filePath
    if ($globalFiles -contains $filename) { return $false }
    $norm = $filePath.Replace('\', '/')
    if ($norm -match '/setup/' -or $norm -match '/cleanup/') { return $false }
    return $true
}

function Test-FilterMatch([string]$filePath, [string[]]$filters) {
    if ($filters.Count -eq 0) { return $true }
    $filename = (Split-Path -Leaf $filePath).ToLower()
    foreach ($pat in $filters) {
        if ($filename -like "*$($pat.ToLower())*") { return $true }
    }
    return $false
}

# Parse filters
$filterPatterns = @()
if ($Filter) {
    $filterPatterns = $Filter.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

# --- Computer specs ---
python -c "from utils import print_computer_specs; print(print_computer_specs())" | Set-Content (Join-Path $ScriptDir "results\computer_specs.txt")

# --- Common run_kute args ---
# Use --kutePath so run_kute.py doesn't try to run "./nethermind/..." via cmd.exe
$kutePathArg = "--kutePath `"$KuteBin`""
$skipFcOpt = ""
if ($SkipForkchoice) { $skipFcOpt = " --skipForkchoice" }

# Helper: convert Windows path to forward slashes for run_kute.py compatibility
function To-ForwardSlash([string]$p) { $p.Replace('\', '/') }

# --- Main execution loop ---
$warmupDone = @{}

for ($run = 1; $run -le $Runs; $run++) {
    Write-Host "`n=== Run $run of $Runs ==="

    foreach ($testFile in $testFiles) {
        $filename = Split-Path -Leaf $testFile
        $measured = Test-MeasuredFile $testFile
        $norm = $testFile.Replace('\', '/')

        # Apply filter to measured files and setup/cleanup
        if ($filterPatterns.Count -gt 0) {
            if ($measured -or $norm -match '/setup/' -or $norm -match '/cleanup/') {
                if (!(Test-FilterMatch $testFile $filterPatterns)) {
                    continue
                }
            }
        }

        if (!$measured) {
            $tf = To-ForwardSlash $testFile
            $jp = To-ForwardSlash $JwtSecret
            Write-Host "Executing preparation script (not measured): $filename"
            $cmd = "python run_kute.py --output prepresults --testsPath `"$tf`" --jwtPath `"$jp`" --client $Client --rerunSyncing --run $run $kutePathArg$skipFcOpt"
            Write-Host "[INFO] $cmd"
            Invoke-Expression $cmd
            continue
        }

        # Warmup
        if ($WarmupCount -gt 0 -and !$warmupDone.ContainsKey($testFile)) {
            # Find warmup file
            $warmupFile = $null
            $relPath = $testFile
            if ($relPath.StartsWith($ScriptDir)) {
                $relPath = $relPath.Substring($ScriptDir.Length).TrimStart('\', '/')
            }
            $warmupCandidate = Join-Path $ScriptDir (Join-Path $WarmupTestsPath $relPath)
            if (Test-Path $warmupCandidate) {
                $warmupFile = $warmupCandidate
            } else {
                # Search by filename under warmup dir
                $warmupRoot = Join-Path $ScriptDir $WarmupTestsPath
                if (Test-Path $warmupRoot) {
                    $found = Get-ChildItem $warmupRoot -Filter $filename -Recurse -File | Select-Object -First 1
                    if ($found) { $warmupFile = $found.FullName }
                }
            }

            if ($warmupFile) {
                $wf = To-ForwardSlash $warmupFile
                $jp = To-ForwardSlash $JwtSecret
                for ($w = 1; $w -le $WarmupCount; $w++) {
                    $cmd = "python run_kute.py --output warmupresults --testsPath `"$wf`" --jwtPath `"$jp`" --client $Client --run $run --kuteArguments '-f engine_newPayload' $kutePathArg$skipFcOpt"
                    Write-Host "[INFO] Warmup $w/$WarmupCount : $cmd"
                    Invoke-Expression $cmd
                }
                $warmupDone[$testFile] = $true
            } else {
                Write-Host "[WARN] No warmup file found for $filename"
            }
        }

        # Measured run
        $tf = To-ForwardSlash $testFile
        $jp = To-ForwardSlash $JwtSecret
        $cmd = "python run_kute.py --output results --testsPath `"$tf`" --jwtPath `"$jp`" --client $Client --run $run $kutePathArg$skipFcOpt"
        Write-Host "[INFO] Measured: $cmd"
        Invoke-Expression $cmd
        Write-Host ""
    }
}

# --- Reports ---
Write-Host "`n=== Generating reports ==="
$reportBaseArgs = @("--resultsPath", "results", "--clients", $Client, "--testsPath", $TestsPath, "--runs", "$Runs", "--skipEmpty")
if ($Filter) { $reportBaseArgs += @("--filter", $Filter) }

Write-Host "[run-native] Running: python report_tables.py $($reportBaseArgs -join ' ')"
& python report_tables.py @reportBaseArgs

# --- Cleanup ---
Write-Host "`n=== Done ==="
Stop-Nethermind
