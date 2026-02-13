<#
.SYNOPSIS
    Run gas-benchmarks with Nethermind started natively (no Docker).
.EXAMPLE
    .\run-native.ps1 -Filter "keccak" -TestsPath "eest_tests" -Runs 1
.EXAMPLE
    .\run-native.ps1 -Filter "identity" -Runs 1 -ForceRebuild
.EXAMPLE
    .\run-native.ps1 -Mode branch-compare -BaselineBranch master -OptimizedBranch feature/my-opt -ResultsDir compare_identity -Filter "identity" -Runs 1
#>
param(
    [ValidateSet("single", "branch-compare")]
    [string]$Mode = "single",
    [string]$Filter = "",
    [string]$TestsPath = "eest_tests",
    [string]$Client = "nethermind",
    [int]$Runs = 1,
    [int]$WarmupCount = 1,
    [string]$WarmupTestsPath = "warmup-tests",
    [switch]$SkipForkchoice,
    [switch]$SkipPrepareTools,
    [switch]$ForceRebuild,
    [string]$NethermindRepo = "C:\Users\kamil\source\repos\nethermind",
    [string]$BaselineBranch = "",
    [string]$OptimizedBranch = "",
    [string]$ResultsDir = "results",
    [switch]$EnableDotnetTrace,
    [string]$DotnetTraceProfile = "cpu-sampling",
    [string]$DotnetTraceDuration = "00:05:00",
    [int]$DotnetTraceTopN = 40,
    [string]$DotnetTraceOutputDir = ""
)

$ErrorActionPreference = "Stop"
# Needed in PowerShell 7 shells where native stderr can honor ErrorActionPreference.
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$env:PYTHONUNBUFFERED = "1"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PidFile = Join-Path $ScriptDir ".nethermind_native.pid"
$NethProc = $null
$DotnetTraceProc = $null
$DotnetTraceState = $null
$DotnetTraceTool = "dotnet-trace"
$DotnetTraceResultsPath = $null
$ResolvedDotnetTraceProfile = $DotnetTraceProfile
$TraceReports = @()

function Get-NowStamp {
    return (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
}

function Format-Elapsed([TimeSpan]$Elapsed) {
    return [string]::Format("{0:hh\:mm\:ss\.fff}", $Elapsed)
}

function Write-Phase([string]$Message) {
    Write-Host "[$(Get-NowStamp)] [run-native] $Message"
}

function Stop-DotnetTraceCollector([int]$TimeoutMs = 180000) {
    if (!$EnableDotnetTrace -or $null -eq $script:DotnetTraceProc) {
        return
    }

    if (!$script:DotnetTraceProc.HasExited) {
        $script:DotnetTraceProc.WaitForExit($TimeoutMs) | Out-Null
    }

    if (!$script:DotnetTraceProc.HasExited) {
        Write-Host "[run-native] dotnet-trace collector did not exit in time. Terminating."
        Stop-Process -Id $script:DotnetTraceProc.Id -Force -ErrorAction SilentlyContinue
        $script:DotnetTraceProc.WaitForExit(10000) | Out-Null
    }
}

function Start-DotnetTraceCollector([int]$RunNumber) {
    if (!$EnableDotnetTrace) {
        return
    }
    if ($null -eq $script:NethProc -or $script:NethProc.HasExited) {
        return
    }

    $runTraceDir = Join-Path $DotnetTraceResultsPath ("run_{0:D2}" -f $RunNumber)
    New-Item -ItemType Directory -Path $runTraceDir -Force | Out-Null

    $safeProfile = ($ResolvedDotnetTraceProfile -replace "[^A-Za-z0-9._-]", "_")
    $traceFile = Join-Path $runTraceDir ("{0}.nettrace" -f $safeProfile)
    $collectorStdOut = Join-Path $runTraceDir "collector.stdout.log"
    $collectorStdErr = Join-Path $runTraceDir "collector.stderr.log"
    foreach ($path in @($traceFile, $collectorStdOut, $collectorStdErr)) {
        if (Test-Path $path) {
            Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
        }
    }

    $traceArgs = @(
        "collect"
        "-p", "$($script:NethProc.Id)"
        "--profile", $ResolvedDotnetTraceProfile
        "--format", "NetTrace"
        "-o", $traceFile
    )
    if (![string]::IsNullOrWhiteSpace($DotnetTraceDuration)) {
        $traceArgs += @("--duration", $DotnetTraceDuration)
    }

    Write-Host "[run-native] Starting dotnet-trace for run $RunNumber (PID $($script:NethProc.Id), profile '$ResolvedDotnetTraceProfile')"
    $script:DotnetTraceProc = Start-Process -FilePath $DotnetTraceTool -ArgumentList $traceArgs `
        -PassThru -NoNewWindow -RedirectStandardOutput $collectorStdOut -RedirectStandardError $collectorStdErr

    $script:DotnetTraceState = @{
        RunNumber = $RunNumber
        RunTraceDir = $runTraceDir
        TraceFile = $traceFile
        CollectorStdOut = $collectorStdOut
        CollectorStdErr = $collectorStdErr
        Profile = $ResolvedDotnetTraceProfile
    }
}

function Export-DotnetTraceReports {
    if (!$EnableDotnetTrace -or $null -eq $script:DotnetTraceState) {
        return
    }

    $runNumber = [int]$script:DotnetTraceState["RunNumber"]
    $runTraceDir = "$($script:DotnetTraceState["RunTraceDir"])"
    $traceFile = "$($script:DotnetTraceState["TraceFile"])"
    $collectorStdErr = "$($script:DotnetTraceState["CollectorStdErr"])"
    $collectorExitCode = $null

    if ($null -ne $script:DotnetTraceProc -and $script:DotnetTraceProc.HasExited) {
        $collectorExitCode = $script:DotnetTraceProc.ExitCode
    }

    if (!(Test-Path $traceFile)) {
        Write-Host "[run-native] dotnet-trace output was not created for run $runNumber."
        if (Test-Path $collectorStdErr) {
            Write-Host "[run-native] dotnet-trace stderr (tail):"
            Get-Content -Path $collectorStdErr -Tail 40
        }
    } else {
        $inclusiveReportPath = Join-Path $runTraceDir "top_inclusive.txt"
        $exclusiveReportPath = Join-Path $runTraceDir "top_exclusive.txt"
        $speedscopeBasePath = Join-Path $runTraceDir "trace"
        $speedscopePath = "$speedscopeBasePath.speedscope.json"
        $convertLogPath = Join-Path $runTraceDir "convert.log"
        $topNOk = $true
        $traceFileArg = '"' + $traceFile + '"'
        $inclusiveCmd = "$DotnetTraceTool report $traceFileArg topN -n $DotnetTraceTopN --inclusive -v 2>&1"
        $exclusiveCmd = "$DotnetTraceTool report $traceFileArg topN -n $DotnetTraceTopN -v 2>&1"
        $convertCmd = "$DotnetTraceTool convert $traceFileArg --format Speedscope -o `"$speedscopeBasePath`" 2>&1"

        Write-Host "[run-native] Generating dotnet-trace reports for run $runNumber"
        $inclusiveOutput = & cmd.exe /c $inclusiveCmd
        $inclusiveExitCode = $LASTEXITCODE
        $inclusiveOutput | Set-Content -Path $inclusiveReportPath
        if ($inclusiveExitCode -ne 0) {
            $topNOk = $false
            Write-Host "[run-native] dotnet-trace inclusive topN failed. See: $inclusiveReportPath"
        }

        $exclusiveOutput = & cmd.exe /c $exclusiveCmd
        $exclusiveExitCode = $LASTEXITCODE
        $exclusiveOutput | Set-Content -Path $exclusiveReportPath
        if ($exclusiveExitCode -ne 0) {
            $topNOk = $false
            Write-Host "[run-native] dotnet-trace exclusive topN failed. See: $exclusiveReportPath"
        }

        $convertOutput = & cmd.exe /c $convertCmd
        $convertExitCode = $LASTEXITCODE
        $convertOutput | Set-Content -Path $convertLogPath
        if ($convertExitCode -ne 0) {
            Write-Host "[run-native] dotnet-trace conversion to Speedscope failed. See: $convertLogPath"
        }

        $script:TraceReports += [pscustomobject]@{
            Run = $runNumber
            Profile = $script:DotnetTraceState["Profile"]
            TraceFile = $traceFile
            InclusiveTopN = $inclusiveReportPath
            ExclusiveTopN = $exclusiveReportPath
            Speedscope = $speedscopePath
            TopNOk = $topNOk
            ConvertLog = $convertLogPath
            CollectorExitCode = $collectorExitCode
        }
    }

    $script:DotnetTraceProc = $null
    $script:DotnetTraceState = $null
}

function Stop-Nethermind {
    # Finalize trace capture before shutting down the target process.
    Stop-DotnetTraceCollector

    if ($script:NethProc -and !$script:NethProc.HasExited) {
        Write-Host "[run-native] Stopping Nethermind (PID $($script:NethProc.Id))"
        try {
            Stop-Process -Id $script:NethProc.Id -ErrorAction Stop
        } catch {}

        # Wait for process to fully exit and release file locks (RocksDB LOCK files).
        if (!$script:NethProc.WaitForExit(10000)) {
            Stop-Process -Id $script:NethProc.Id -Force -ErrorAction SilentlyContinue
            $script:NethProc.WaitForExit(10000) | Out-Null
        }
        Start-Sleep -Seconds 2
    }
    if ($script:PidFile -and (Test-Path $script:PidFile)) {
        Remove-Item $script:PidFile -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-PathFromScriptRoot([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $ScriptDir
    }

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    return Join-Path $ScriptDir $PathValue
}

# --- Branch compare mode ---
if ($Mode -eq "branch-compare") {
    if ([string]::IsNullOrWhiteSpace($BaselineBranch)) {
        Write-Error "Baseline branch is required in branch-compare mode. Pass -BaselineBranch."
    }
    if ([string]::IsNullOrWhiteSpace($OptimizedBranch)) {
        Write-Error "Optimized branch is required in branch-compare mode. Pass -OptimizedBranch."
    }
    if (!(Test-Path (Join-Path $NethermindRepo ".git"))) {
        Write-Error "Nethermind repository is not a git checkout: $NethermindRepo"
    }

    $compareRoot = Resolve-PathFromScriptRoot $ResultsDir
    $worktreesRoot = Join-Path $ScriptDir ".branch-worktrees"
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $baselineWorktree = Join-Path $worktreesRoot "baseline_$timestamp"
    $optimizedWorktree = Join-Path $worktreesRoot "optimized_$timestamp"
    $baselineResultsPath = Join-Path $compareRoot "baseline"
    $optimizedResultsPath = Join-Path $compareRoot "optimized"
    $hostExe = (Get-Process -Id $PID).Path
    $isFirstRunForResultsDir = -not (Test-Path $baselineResultsPath)

    function Assert-GitRefExists([string]$repoPath, [string]$refName) {
        try {
            & git -C $repoPath rev-parse --verify "$refName^{commit}" *> $null
        } catch {}
        if ($LASTEXITCODE -ne 0) {
            throw "Branch or ref '$refName' was not found in '$repoPath'."
        }
    }

    function Invoke-SingleModeRun([string]$repoPath, [string]$outputPath, [string]$label) {
        Write-Host "[run-native] Running $label benchmarks using: $repoPath"
        Write-Host "[run-native] Output directory: $outputPath"

        $childArgs = @(
            "-NoProfile"
            "-ExecutionPolicy"
            "Bypass"
            "-File"
            $PSCommandPath
            "-Mode"
            "single"
            "-TestsPath"
            $TestsPath
            "-Client"
            $Client
            "-Runs"
            "$Runs"
            "-WarmupCount"
            "$WarmupCount"
            "-WarmupTestsPath"
            $WarmupTestsPath
            "-NethermindRepo"
            $repoPath
            "-ResultsDir"
            $outputPath
            "-Filter"
            $Filter
        )

        if ($SkipForkchoice) { $childArgs += "-SkipForkchoice" }
        if ($SkipPrepareTools) { $childArgs += "-SkipPrepareTools" }
        if ($ForceRebuild) { $childArgs += "-ForceRebuild" }

        & $hostExe @childArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Benchmark run failed for $label (exit code $LASTEXITCODE)."
        }
    }

    if ([string]::IsNullOrWhiteSpace($hostExe)) {
        $hostExe = "powershell"
    }

    New-Item -ItemType Directory -Path $compareRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $worktreesRoot -Force | Out-Null

    try {
        Assert-GitRefExists $NethermindRepo $OptimizedBranch

        if ($isFirstRunForResultsDir) {
            Assert-GitRefExists $NethermindRepo $BaselineBranch

            Write-Host "[run-native] First run detected for '$compareRoot'. Running baseline and optimized."
            Write-Host "[run-native] Creating baseline worktree for '$BaselineBranch'"
            try {
                & git -C $NethermindRepo worktree add --force --detach $baselineWorktree $BaselineBranch
            } catch {}
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create worktree for baseline branch '$BaselineBranch'."
            }

            Invoke-SingleModeRun -repoPath $baselineWorktree -outputPath $baselineResultsPath -label "baseline ($BaselineBranch)"
        } else {
            Write-Host "[run-native] Existing baseline results found at '$baselineResultsPath'. Skipping baseline run."
        }

        Write-Host "[run-native] Creating optimized worktree for '$OptimizedBranch'"
        try {
            & git -C $NethermindRepo worktree add --force --detach $optimizedWorktree $OptimizedBranch
        } catch {}
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create worktree for optimized branch '$OptimizedBranch'."
        }

        Invoke-SingleModeRun -repoPath $optimizedWorktree -outputPath $optimizedResultsPath -label "optimized ($OptimizedBranch)"
    }
    finally {
        foreach ($worktreePath in @($baselineWorktree, $optimizedWorktree)) {
            if (Test-Path $worktreePath) {
                try {
                    & git -C $NethermindRepo worktree remove --force $worktreePath *> $null
                } catch {}
            }
        }
    }

    Write-Host "[run-native] Branch comparison finished. Results root: $compareRoot"
    Write-Host "[run-native] Baseline results:  $baselineResultsPath"
    Write-Host "[run-native] Optimized results: $optimizedResultsPath"
    exit 0
}

$ResultsPath = Resolve-PathFromScriptRoot $ResultsDir
if ($ResultsDir -eq "results") {
    $PrepResultsPath = Join-Path $ScriptDir "prepresults"
    $WarmupResultsPath = Join-Path $ScriptDir "warmupresults"
    $DotnetTraceResultsPath = Join-Path $ScriptDir "results_trace"
} else {
    $normalizedResultsPath = $ResultsPath.TrimEnd('\', '/')
    $resultsParent = Split-Path -Parent $normalizedResultsPath
    $resultsLeaf = Split-Path -Leaf $normalizedResultsPath
    if ([string]::IsNullOrWhiteSpace($resultsParent)) {
        $resultsParent = $ScriptDir
    }
    if ([string]::IsNullOrWhiteSpace($resultsLeaf)) {
        $resultsLeaf = "results"
    }
    $PrepResultsPath = Join-Path $resultsParent "$resultsLeaf`_prep"
    $WarmupResultsPath = Join-Path $resultsParent "$resultsLeaf`_warmup"
    $DotnetTraceResultsPath = Join-Path $resultsParent "$resultsLeaf`_trace"
}

if (![string]::IsNullOrWhiteSpace($DotnetTraceOutputDir)) {
    $DotnetTraceResultsPath = Resolve-PathFromScriptRoot $DotnetTraceOutputDir
}
# --- Paths ---
$RunnerProject = Join-Path $NethermindRepo "src\Nethermind\Nethermind.Runner"
$RunnerBin = Join-Path $NethermindRepo "src\Nethermind\artifacts\bin\Nethermind.Runner\release\nethermind.exe"
$DataDir = Join-Path $ScriptDir "scripts\nethermind\execution-data"
$JwtSecret = Join-Path $ScriptDir "scripts\nethermind\jwtsecret"
$Chainspec = Join-Path $ScriptDir "scripts\genesisfiles\nethermind\zkevmgenesis.json"
$LogsDir = Join-Path $ScriptDir "logs"
$ScriptLog = Join-Path $LogsDir "run-script.log"
$KuteBin = Join-Path $ScriptDir "nethermind\tools\artifacts\bin\Nethermind.Tools.Kute\release\Nethermind.Tools.Kute.exe"

# --- Start transcript (logs all script output to run-script.log) ---
New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
$TranscriptStarted = $false
try {
    Start-Transcript -Path $ScriptLog -Force | Out-Null
    $TranscriptStarted = $true
} catch {
    Write-Host "[run-native] Transcript disabled: $($_.Exception.Message)"
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
if ($EnableDotnetTrace) {
    $dotnetTraceCommand = Get-Command $DotnetTraceTool -ErrorAction SilentlyContinue
    if ($null -eq $dotnetTraceCommand) {
        throw "dotnet-trace is not available in PATH. Install dotnet-trace and retry."
    }
    $availableProfiles = @()
    $profilesOutput = & cmd.exe /c "$DotnetTraceTool list-profiles 2>&1"
    foreach ($line in $profilesOutput) {
        if ($line -match '^\s*([A-Za-z0-9\-]+)\s+') {
            $availableProfiles += $Matches[1]
        }
    }
    $availableProfiles = @($availableProfiles | Select-Object -Unique)

    $isLinux = [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Linux)
    if ($DotnetTraceProfile -eq "cpu-sampling" -and !$isLinux -and ($availableProfiles -contains "dotnet-sampled-thread-time")) {
        $ResolvedDotnetTraceProfile = "dotnet-sampled-thread-time"
        Write-Host "[run-native] dotnet-trace profile '$DotnetTraceProfile' is Linux-only on this version. Using '$ResolvedDotnetTraceProfile'."
    }

    if ($availableProfiles.Count -gt 0 -and !($availableProfiles -contains $ResolvedDotnetTraceProfile)) {
        if ($ResolvedDotnetTraceProfile -eq "cpu-sampling" -and ($availableProfiles -contains "dotnet-sampled-thread-time")) {
            $ResolvedDotnetTraceProfile = "dotnet-sampled-thread-time"
            Write-Host "[run-native] dotnet-trace profile '$DotnetTraceProfile' is unavailable. Using '$ResolvedDotnetTraceProfile'."
        } else {
            throw "dotnet-trace profile '$DotnetTraceProfile' is unavailable. Available profiles: $([string]::Join(', ', $availableProfiles))"
        }
    }

    Write-Host "[run-native] dotnet-trace enabled (profile '$ResolvedDotnetTraceProfile')"
    if (![string]::IsNullOrWhiteSpace($DotnetTraceDuration)) {
        Write-Host "[run-native] dotnet-trace duration: $DotnetTraceDuration"
    }
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

# --- Build Nethermind ---
if ($ForceRebuild -or !(Test-Path $RunnerBin)) {
    $buildStart = Get-Date
    Write-Phase "Nethermind build started (project: $RunnerProject)"
    dotnet build $RunnerProject -c Release --property WarningLevel=0
    if (!(Test-Path $RunnerBin)) {
        Write-Error "Built runner not found: $RunnerBin"
    }
    Write-Phase "Nethermind build completed in $(Format-Elapsed ((Get-Date) - $buildStart))"
} else {
    Write-Phase "Nethermind build skipped (existing binary found: $RunnerBin)"
}

# --- Build Kute if needed ---
if (!$SkipPrepareTools) {
    $kuteProject = Join-Path $ScriptDir "nethermind\tools\Nethermind.Tools.Kute"
    if (!(Test-Path $KuteBin)) {
        $kuteBuildStart = Get-Date
        Write-Phase "Kute build started (project: $kuteProject)"
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
        Write-Phase "Kute build completed in $(Format-Elapsed ((Get-Date) - $kuteBuildStart))"
    } else {
        Write-Phase "Kute build skipped (existing binary found: $KuteBin)"
    }
}

# --- Prepare directories ---
$outputDirectories = @(
    @{ Path = $ResultsPath; Recreate = $true }
    @{ Path = $PrepResultsPath; Recreate = $false }
    @{ Path = $WarmupResultsPath; Recreate = $false }
)
if ($EnableDotnetTrace) {
    $outputDirectories += @{ Path = $DotnetTraceResultsPath; Recreate = $true }
}
foreach ($outputDir in $outputDirectories) {
    $p = $outputDir.Path
    if ($outputDir.Recreate -and (Test-Path $p)) { Remove-Item $p -Recurse -Force }
    New-Item -ItemType Directory -Path $p -Force | Out-Null
}

# --- Install Python deps ---
pip install -r (Join-Path $ScriptDir "requirements.txt") --quiet

# --- Nethermind arguments (reused each run) ---
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
    "--Blocks.CachePrecompilesOnBlockProcessing=false"
    "--Init.ChainSpecPath=$Chainspec"
)

function Start-FreshNethermind([int]$runNumber) {
    # Clean execution-data for fresh chain state (retry in case of lingering file locks)
    if (Test-Path $DataDir) {
        Write-Host "[run-native] Cleaning execution-data for fresh state"
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            try { Remove-Item $DataDir -Recurse -Force -ErrorAction Stop; break }
            catch { Write-Host "[run-native] Cleanup attempt $attempt failed, retrying..."; Start-Sleep -Seconds 2 }
        }
    }
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

    # Per-run log files (append run number for multi-run)
    $runNethLog = Join-Path $LogsDir "nethermind-exe.run${runNumber}.log"
    $runNethErr = Join-Path $LogsDir "nethermind-exe.run${runNumber}.err.log"

    Write-Host "[run-native] Starting Nethermind from $RunnerBin (run $runNumber)"
    $script:NethProc = Start-Process -FilePath $RunnerBin -ArgumentList $nethArgs `
        -RedirectStandardOutput $runNethLog -RedirectStandardError $runNethErr `
        -PassThru -NoNewWindow
    $script:NethProc.Id | Set-Content $PidFile
    Write-Host "[run-native] Nethermind PID: $($script:NethProc.Id)"

    # Wait for RPC
    Write-Host "[run-native] Waiting for RPC at http://127.0.0.1:8545"
    $rpcBody = '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
    $maxAttempts = 300
    $rpcReady = $false
    for ($i = 1; $i -le $maxAttempts; $i++) {
        if ($script:NethProc.HasExited) {
            Write-Host "[run-native] Nethermind exited unexpectedly (exit code $($script:NethProc.ExitCode)). Last log lines:"
            Get-Content $runNethLog -Tail 30 -ErrorAction SilentlyContinue
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
        Get-Content $runNethLog -Tail 50 -ErrorAction SilentlyContinue
        Stop-Nethermind
        exit 1
    }

    Start-DotnetTraceCollector -RunNumber $runNumber
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
python -c "from utils import print_computer_specs; print(print_computer_specs())" | Set-Content (Join-Path $ResultsPath "computer_specs.txt")

# --- Common run_kute args ---
# Use --kutePath so run_kute.py doesn't try to run "./nethermind/..." via cmd.exe
# Use 127.0.0.1 to avoid DNS/IPv6 resolution overhead on Windows
$kutePathArg = "--kutePath `"$KuteBin`""
$ecUrlArg = "--ecURL http://127.0.0.1:8551"
$skipFcOpt = ""
if ($SkipForkchoice) { $skipFcOpt = " --skipForkchoice" }

# Helper: convert Windows path to forward slashes for run_kute.py compatibility
function To-ForwardSlash([string]$p) { $p.Replace('\', '/') }
$resultsOutput = To-ForwardSlash $ResultsPath
$prepResultsOutput = To-ForwardSlash $PrepResultsPath
$warmupResultsOutput = To-ForwardSlash $WarmupResultsPath

# --- Main execution loop ---
for ($run = 1; $run -le $Runs; $run++) {
    Write-Host "`n=== Run $run of $Runs ==="
    Write-Phase "Run $run/$Runs started"

    # Fresh Nethermind instance with clean state for each run
    Start-FreshNethermind $run
    $warmupDone = @{}

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
            Write-Phase "Preparation step start (run $run/$Runs): $filename"
            $cmd = "python run_kute.py --output `"$prepResultsOutput`" --testsPath `"$tf`" --jwtPath `"$jp`" --client $Client --rerunSyncing --run $run $kutePathArg $ecUrlArg$skipFcOpt"
            Write-Host "[INFO] $cmd"
            $prepStart = Get-Date
            Invoke-Expression $cmd
            Write-Phase "Preparation step done (run $run/$Runs): $filename in $(Format-Elapsed ((Get-Date) - $prepStart))"
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
                    $cmd = "python run_kute.py --output `"$warmupResultsOutput`" --testsPath `"$wf`" --jwtPath `"$jp`" --client $Client --run $run --kuteArguments '-f engine_newPayload' $kutePathArg $ecUrlArg$skipFcOpt"
                    Write-Host "[INFO] Warmup $w/$WarmupCount : $cmd"
                    $warmupStart = Get-Date
                    Invoke-Expression $cmd
                    Write-Phase "Warmup done (run $run/$Runs, warmup $w/$WarmupCount): $filename in $(Format-Elapsed ((Get-Date) - $warmupStart))"
                }
                $warmupDone[$testFile] = $true
            } else {
                Write-Host "[WARN] No warmup file found for $filename"
            }
        }

        # Measured run
        $tf = To-ForwardSlash $testFile
        $jp = To-ForwardSlash $JwtSecret
        $cmd = "python run_kute.py --output `"$resultsOutput`" --testsPath `"$tf`" --jwtPath `"$jp`" --client $Client --run $run $kutePathArg $ecUrlArg$skipFcOpt"
        Write-Phase "Scenario start (run $run/$Runs): $filename"
        Write-Host "[INFO] Measured: $cmd"
        $scenarioStart = Get-Date
        Invoke-Expression $cmd
        Write-Phase "Scenario done (run $run/$Runs): $filename in $(Format-Elapsed ((Get-Date) - $scenarioStart))"
        Write-Host ""
    }

    # Stop Nethermind after each run (next run starts fresh)
    Stop-Nethermind
    Export-DotnetTraceReports
    Write-Phase "Run $run/$Runs completed"
}

# --- Reports ---
Write-Host "`n=== Generating reports ==="
$reportBaseArgs = @("--resultsPath", $ResultsPath, "--clients", $Client, "--testsPath", $TestsPath, "--runs", "$Runs", "--skipEmpty")
if ($Filter) { $reportBaseArgs += @("--filter", $Filter) }

Write-Host "[run-native] Running: python report_tables.py $($reportBaseArgs -join ' ')"
& python report_tables.py @reportBaseArgs

# --- Cleanup ---
Write-Host "`n=== Done ==="
Write-Host "[run-native] Logs: $LogsDir"
Write-Host "[run-native] Results: $ResultsPath"
if ($EnableDotnetTrace) {
    Write-Host "[run-native] Trace output: $DotnetTraceResultsPath"
    foreach ($traceReport in $script:TraceReports) {
        Write-Host "[run-native] Trace run $($traceReport.Run): $($traceReport.TraceFile)"
        Write-Host "[run-native]   top inclusive: $($traceReport.InclusiveTopN)"
        Write-Host "[run-native]   top exclusive: $($traceReport.ExclusiveTopN)"
        Write-Host "[run-native]   topN status: $(if ($traceReport.TopNOk) { "ok" } else { "failed" })"
        Write-Host "[run-native]   convert log: $($traceReport.ConvertLog)"
    }
}
if ($TranscriptStarted) {
    Stop-Transcript | Out-Null
}

