<#
.SYNOPSIS
    Compare baseline and optimized Nethermind branches using run-native.ps1.
.DESCRIPTION
    This wrapper runs run-native.ps1 against two branches and stores outputs under a shared results directory.
    It caches which filters already have baseline data so repeated runs only execute baseline for new filters.
.EXAMPLE
    .\run-native-compare.ps1 -BaselineBranch master -OptimizedBranch feat/my-opt -ResultsDir results\identity -Filter "identity,ecrecover"
#>
param(
    [string]$BaselineBranch = "",
    [Parameter(Mandatory = $true)]
    [string]$OptimizedBranch,
    [Parameter(Mandatory = $true)]
    [string]$ResultsDir,
    [string]$Filter = "",
    [string]$TestsPath = "eest_tests",
    [int]$Runs = 3,
    [int]$WarmupCount = 1,
    [string]$WarmupTestsPath = "warmup-tests",
    [switch]$SkipForkchoice,
    [switch]$SkipPrepareTools,
    [switch]$ForceBaseline,
    [switch]$TraceMode,
    [switch]$EnableDotnetTrace,
    [string]$DotnetTraceProfile = "cpu-sampling",
    [string]$DotnetTraceDuration = "00:05:00",
    [int]$DotnetTraceTopN = 40,
    [switch]$ShowChildLogs,
    [string]$NethermindRepo = "C:\Users\kamil\source\repos\nethermind"
)

$ErrorActionPreference = "Stop"
# Needed in PowerShell 7 shells where native stderr can honor ErrorActionPreference.
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RunNativeScript = Join-Path $ScriptDir "run-native.ps1"

if (!(Test-Path $RunNativeScript)) {
    throw "run-native.ps1 not found at: $RunNativeScript"
}
if (!(Test-Path $NethermindRepo)) {
    throw "Nethermind repo path not found: $NethermindRepo"
}
if (!(Test-Path (Join-Path $NethermindRepo ".git"))) {
    throw "Nethermind repo does not look like a git repository: $NethermindRepo"
}
if (!$TraceMode -and [string]::IsNullOrWhiteSpace($BaselineBranch)) {
    throw "BaselineBranch is required unless -TraceMode is used."
}

$traceEnabledForRun = ($EnableDotnetTrace -or $TraceMode)

function ConvertTo-Hashtable([object]$InputObject) {
    if ($null -eq $InputObject) {
        return $null
    }

    if ($InputObject -is [System.Collections.IDictionary]) {
        return $InputObject
    }

    if ($InputObject -is [System.Collections.IEnumerable] -and !($InputObject -is [string])) {
        $items = @()
        foreach ($item in $InputObject) {
            $items += ,(ConvertTo-Hashtable $item)
        }
        return $items
    }

    if ($InputObject -is [pscustomobject]) {
        $hashtable = @{}
        foreach ($property in $InputObject.PSObject.Properties) {
            $hashtable[$property.Name] = ConvertTo-Hashtable $property.Value
        }
        return $hashtable
    }

    return $InputObject
}

function Get-NormalizedFilters([string]$RawFilter) {
    if ([string]::IsNullOrWhiteSpace($RawFilter)) {
        return @("*")
    }

    $seen = @{}
    $normalized = @()
    foreach ($item in $RawFilter.Split(",")) {
        $trimmed = $item.Trim().ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }
        if (!$seen.ContainsKey($trimmed)) {
            $seen[$trimmed] = $true
            $normalized += $trimmed
        }
    }

    if ($normalized.Count -eq 0) {
        return @("*")
    }
    return $normalized
}

function Convert-FilterSetToArg([string[]]$FilterSet) {
    if ($FilterSet.Count -eq 1 -and $FilterSet[0] -eq "*") {
        return ""
    }
    return [string]::Join(",", $FilterSet)
}

function Get-SafeName([string]$Name) {
    return ($Name -replace "[^A-Za-z0-9._-]", "_")
}

function Write-ChildPhaseLogs([string]$ChildLogPath, [string]$VariantLabel) {
    if (!(Test-Path $ChildLogPath)) {
        return
    }

    $phaseLines = @()
    foreach ($line in (Get-Content -Path $ChildLogPath)) {
        if ($line -match '^\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\]\s+\[run-native\]\s+') {
            $phaseLines += $line
            continue
        }

        if ($line -match '\[run-native\]\s+(Nethermind build|Kute build|Run\s+\d+/\d+\s+(started|completed)|Scenario\s+(start|done)|Preparation step|Warmup done)') {
            $phaseLines += $line
        }
    }

    if ($phaseLines.Count -eq 0) {
        return
    }

    Write-Host "[compare-native] $VariantLabel phase logs from run-native:"
    foreach ($phaseLine in $phaseLines) {
        Write-Host "  $phaseLine"
    }
}

function Invoke-GitRaw([string[]]$Arguments) {
    $quotedArgs = @()
    foreach ($arg in $Arguments) {
        $escaped = $arg.Replace('"', '\"')
        $quotedArgs += ('"{0}"' -f $escaped)
    }

    $commandText = "git -C `"$NethermindRepo`" $($quotedArgs -join ' ') 2>&1"
    $output = & cmd.exe /c $commandText
    return @{
        ExitCode = $LASTEXITCODE
        Output = @($output)
    }
}

function Invoke-Git([string[]]$Arguments) {
    $result = Invoke-GitRaw $Arguments
    if ($result["ExitCode"] -ne 0) {
        $cmd = [string]::Join(" ", $Arguments)
        $output = [string]::Join("`n", $result["Output"])
        throw "git $cmd failed.`n$output"
    }
    return @($result["Output"])
}

function Assert-CleanWorkingTree() {
    $statusResult = Invoke-GitRaw @("status", "--porcelain=v1")
    if ($statusResult["ExitCode"] -ne 0) {
        $output = [string]::Join("`n", @($statusResult["Output"]))
        throw "Failed to check git working tree status for '$NethermindRepo'.`n$output"
    }

    $statusLines = @()
    foreach ($line in @($statusResult["Output"])) {
        if (![string]::IsNullOrWhiteSpace("$line")) {
            $statusLines += "$line"
        }
    }

    if ($statusLines.Count -eq 0) {
        return
    }

    $previewLimit = 25
    Write-Host "[compare-native] ERROR: Nethermind repo has uncommitted changes."
    Write-Host "[compare-native] Branch benchmarking with a dirty working tree is invalid."
    Write-Host "[compare-native] Repo: $NethermindRepo"
    Write-Host "[compare-native] Commit or stash all local changes, then rerun."
    Write-Host "[compare-native] Current changes (git status --porcelain):"
    foreach ($statusLine in ($statusLines | Select-Object -First $previewLimit)) {
        Write-Host "  $statusLine"
    }
    if ($statusLines.Count -gt $previewLimit) {
        Write-Host "  ... ($($statusLines.Count - $previewLimit) more)"
    }

    throw "Aborting compare run because Nethermind working tree is not clean."
}

function Checkout-Branch([string]$BranchName) {
    Write-Host "[compare-native] Checking out branch '$BranchName'"

    $checkoutResult = Invoke-GitRaw @("checkout", $BranchName)
    if ($checkoutResult["ExitCode"] -eq 0) {
        return
    }

    $checkoutText = [string]::Join("`n", @($checkoutResult["Output"]))
    if ($checkoutText -like "*is already used by worktree*") {
        Write-Host "[compare-native] Branch '$BranchName' is checked out in another worktree. Using detached HEAD."
        Invoke-Git @("checkout", "--detach", $BranchName)
        return
    }

    Write-Host "[compare-native] Local branch '$BranchName' not available, fetching origin/$BranchName"
    Invoke-Git @("fetch", "origin", $BranchName)
    Invoke-Git @("checkout", "-B", $BranchName, "origin/$BranchName")
}

function Invoke-RunNative([string]$BranchName, [string]$FilterArg, [string]$VariantLabel, [string]$LogPath, [string]$TraceOutputDir) {
    Write-Host "[compare-native] Running $VariantLabel benchmark on branch '$BranchName'"
    if ([string]::IsNullOrWhiteSpace($FilterArg)) {
        Write-Host "[compare-native] Filters: <all>"
    } else {
        Write-Host "[compare-native] Filters: $FilterArg"
    }

    $args = @(
        "-NoProfile"
        "-ExecutionPolicy", "Bypass"
        "-File", $RunNativeScript
        "-TestsPath", $TestsPath
        "-Runs", "$Runs"
        "-WarmupCount", "$WarmupCount"
        "-WarmupTestsPath", $WarmupTestsPath
        "-NethermindRepo", $NethermindRepo
        "-ForceRebuild"
    )

    if (![string]::IsNullOrWhiteSpace($FilterArg)) {
        $args += @("-Filter", $FilterArg)
    }
    if ($SkipForkchoice) {
        $args += "-SkipForkchoice"
    }
    if ($SkipPrepareTools) {
        $args += "-SkipPrepareTools"
    }
    if ($traceEnabledForRun) {
        $args += "-EnableDotnetTrace"
        $args += @("-DotnetTraceProfile", $DotnetTraceProfile)
        $args += @("-DotnetTraceTopN", "$DotnetTraceTopN")
        if (![string]::IsNullOrWhiteSpace($DotnetTraceDuration)) {
            $args += @("-DotnetTraceDuration", $DotnetTraceDuration)
        }
        if (![string]::IsNullOrWhiteSpace($TraceOutputDir)) {
            $args += @("-DotnetTraceOutputDir", $TraceOutputDir)
        }
    }

    if ($ShowChildLogs) {
        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $args -NoNewWindow -PassThru -Wait
        $exitCode = $process.ExitCode
    } else {
        $logDir = Split-Path -Parent $LogPath
        if (!(Test-Path $logDir)) {
            New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        }
        $stdOutLog = "$LogPath.stdout.log"
        $stdErrLog = "$LogPath.stderr.log"
        foreach ($tmpLog in @($stdOutLog, $stdErrLog, $LogPath)) {
            if (Test-Path $tmpLog) {
                Remove-Item -Path $tmpLog -Force -ErrorAction SilentlyContinue
            }
        }

        $process = Start-Process -FilePath "powershell.exe" -ArgumentList $args -NoNewWindow -PassThru -Wait `
            -RedirectStandardOutput $stdOutLog -RedirectStandardError $stdErrLog
        $exitCode = $process.ExitCode

        if (Test-Path $stdOutLog) {
            Get-Content -Path $stdOutLog | Set-Content -Path $LogPath
        }
        if (Test-Path $stdErrLog) {
            if (Test-Path $LogPath) {
                Add-Content -Path $LogPath -Value ""
                Add-Content -Path $LogPath -Value "=== STDERR ==="
            }
            Get-Content -Path $stdErrLog | Add-Content -Path $LogPath
        }

        foreach ($tmpLog in @($stdOutLog, $stdErrLog)) {
            if (Test-Path $tmpLog) {
                Remove-Item -Path $tmpLog -Force -ErrorAction SilentlyContinue
            }
        }
    }

    if ($exitCode -ne 0) {
        if (!$ShowChildLogs -and (Test-Path $LogPath)) {
            Write-Host "[compare-native] $VariantLabel run failed. Last log lines:"
            Get-Content -Path $LogPath -Tail 80
            Write-ChildPhaseLogs -ChildLogPath $LogPath -VariantLabel "$VariantLabel (failed)"
        }
        throw "run-native.ps1 failed for $VariantLabel ($BranchName) with exit code $exitCode"
    }

    if (!$ShowChildLogs) {
        Write-ChildPhaseLogs -ChildLogPath $LogPath -VariantLabel $VariantLabel
        Write-Host "[compare-native] $VariantLabel run completed. Log: $LogPath"
    }
}

function Copy-RunArtifacts([string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    foreach ($name in @("results", "prepresults", "warmupresults", "logs")) {
        $source = Join-Path $ScriptDir $name
        if (Test-Path $source) {
            Copy-Item -Path $source -Destination (Join-Path $Destination $name) -Recurse -Force
        }
    }
}

function Get-NewPayloadLastMsFromFile([string]$FilePath) {
    $inPayloadMeasurement = $false
    foreach ($line in (Get-Content -LiteralPath $FilePath)) {
        if ($line -match '^# MEASUREMENT:\s+\[Application\]\s+engine_newPayloadV\d+\s*$') {
            $inPayloadMeasurement = $true
            continue
        }

        if ($inPayloadMeasurement -and $line -match '^\s*last\s*=\s*([-+]?\d+(\.\d+)?([eE][-+]?\d+)?)\s*$') {
            return [double]$Matches[1]
        }

        if ($inPayloadMeasurement -and $line -match '^-{10,}$') {
            $inPayloadMeasurement = $false
        }
    }

    return $null
}

function Get-ScenarioMetrics([string[]]$ResultsPaths) {
    $metrics = @{}
    if ($null -eq $ResultsPaths -or $ResultsPaths.Count -eq 0) {
        return $metrics
    }

    foreach ($resultsPath in $ResultsPaths) {
        if (!(Test-Path $resultsPath)) {
            continue
        }

        $pathMetrics = @{}
        $files = Get-ChildItem -LiteralPath $resultsPath -File -Filter "nethermind_results_*.txt"
        foreach ($file in $files) {
            if ($file.Name -notmatch '^nethermind_results_\d+_(.+)\.txt$') {
                continue
            }

            $scenario = $Matches[1]
            $value = Get-NewPayloadLastMsFromFile -FilePath $file.FullName
            if ($null -eq $value) {
                continue
            }

            if (!$pathMetrics.ContainsKey($scenario)) {
                $pathMetrics[$scenario] = [System.Collections.Generic.List[double]]::new()
            }
            [void]$pathMetrics[$scenario].Add([double]$value)
        }

        foreach ($scenarioName in $pathMetrics.Keys) {
            if (!$metrics.ContainsKey($scenarioName)) {
                $metrics[$scenarioName] = $pathMetrics[$scenarioName]
            }
        }
    }

    return $metrics
}

function Get-Percentile([double[]]$Values, [double]$Percentile) {
    if ($null -eq $Values -or $Values.Count -eq 0) {
        return $null
    }

    $sorted = $Values | Sort-Object
    if ($sorted.Count -eq 1) {
        return [double]$sorted[0]
    }

    $rank = ($Percentile / 100.0) * ($sorted.Count - 1)
    $lower = [int][Math]::Floor($rank)
    $upper = [int][Math]::Ceiling($rank)
    if ($lower -eq $upper) {
        return [double]$sorted[$lower]
    }

    $weight = $rank - $lower
    return [double]($sorted[$lower] + (($sorted[$upper] - $sorted[$lower]) * $weight))
}

function Get-ScenarioDisplayName([string]$ScenarioName) {
    $display = $ScenarioName -replace '^tests_', ''
    $display = $display -replace '-gas-value_.*$', ''
    if ($display.Length -gt 68) {
        return $display.Substring(0, 65) + "..."
    }
    return $display
}

function Get-AllBaselineResultsPaths([string]$ResultsRoot, [string]$BaselineFolderName, [string]$ExcludeRunDir) {
    $paths = @()
    $runDirs = Get-ChildItem -LiteralPath $ResultsRoot -Directory -Filter "compare_run_*" | Sort-Object Name -Descending
    foreach ($runDirItem in $runDirs) {
        if (![string]::IsNullOrWhiteSpace($ExcludeRunDir) -and $runDirItem.FullName -eq $ExcludeRunDir) {
            continue
        }
        $candidate = Join-Path $runDirItem.FullName (Join-Path $BaselineFolderName "results")
        if (Test-Path $candidate) {
            $paths += $candidate
        }
    }
    return @($paths | Select-Object -Unique)
}

function Write-NewPayloadComparison([string[]]$BaselineResultsPaths, [string]$OptimizedResultsPath, [string]$OutputDir) {
    $baselineMetrics = Get-ScenarioMetrics -ResultsPaths $BaselineResultsPaths
    $optimizedMetrics = Get-ScenarioMetrics -ResultsPaths @($OptimizedResultsPath)

    $commonScenarios = @($baselineMetrics.Keys | Where-Object { $optimizedMetrics.ContainsKey($_) } | Sort-Object)
    if ($commonScenarios.Count -eq 0) {
        Write-Host "[compare-native] No overlapping nethermind result files for comparison."
        return
    }

    $rows = @()
    foreach ($scenario in $commonScenarios) {
        $baselineValues = @($baselineMetrics[$scenario] | ForEach-Object { [double]$_ })
        $optimizedValues = @($optimizedMetrics[$scenario] | ForEach-Object { [double]$_ })

        $baselineP95 = Get-Percentile -Values $baselineValues -Percentile 95
        $optimizedP95 = Get-Percentile -Values $optimizedValues -Percentile 95
        if ($null -eq $baselineP95 -or $null -eq $optimizedP95) {
            continue
        }

        $deltaMs = $optimizedP95 - $baselineP95
        $deltaPct = $null
        if ([Math]::Abs($baselineP95) -gt 0.0000001) {
            $deltaPct = ($deltaMs / $baselineP95) * 100.0
        }

        $rows += [pscustomobject]@{
            Test = Get-ScenarioDisplayName -ScenarioName $scenario
            BaselineMs = [Math]::Round($baselineP95, 2)
            OptimizedMs = [Math]::Round($optimizedP95, 2)
            DeltaMs = [Math]::Round($deltaMs, 2)
            DeltaPct = if ($null -ne $deltaPct) { "{0:N2}%" -f $deltaPct } else { "n/a" }
            FullScenario = $scenario
        }
    }

    if ($rows.Count -eq 0) {
        Write-Host "[compare-native] No comparable newPayload metrics were extracted."
        return
    }

    $rows = $rows | Sort-Object DeltaMs
    Write-Host ""
    Write-Host "[compare-native] engine_newPayload comparison (p95 ms, lower is better)"
    $header = "{0,-68} {1,12} {2,12} {3,10} {4,10}" -f "Test", "Baseline", "Optimized", "Delta", "Delta%"
    Write-Host $header
    Write-Host ("-" * $header.Length)
    foreach ($row in $rows) {
        $line = "{0,-68} {1,12:N2} {2,12:N2} {3,10:N2} {4,10}" -f $row.Test, $row.BaselineMs, $row.OptimizedMs, $row.DeltaMs, $row.DeltaPct
        Write-Host $line
    }

    $csvPath = Join-Path $OutputDir "newpayload_comparison_p95_ms.csv"
    $rows | Select-Object FullScenario, BaselineMs, OptimizedMs, DeltaMs, DeltaPct | Export-Csv -Path $csvPath -NoTypeInformation

    $mdPath = Join-Path $OutputDir "newpayload_comparison_p95_ms.md"
    $mdLines = @(
        "# engine_newPayload comparison (p95 ms)"
        ""
        "| Test | Baseline p95 (ms) | Optimized p95 (ms) | Delta (ms) | Delta (%) |"
        "|---|---:|---:|---:|---:|"
    )
    foreach ($row in $rows) {
        $safeTest = ($row.Test -replace '\|', '\|')
        $mdLines += "| $safeTest | $($row.BaselineMs) | $($row.OptimizedMs) | $($row.DeltaMs) | $($row.DeltaPct) |"
    }
    Set-Content -Path $mdPath -Value $mdLines

    Write-Host "[compare-native] Comparison table files:"
    Write-Host "[compare-native] - $csvPath"
    Write-Host "[compare-native] - $mdPath"
}

function New-Manifest() {
    $now = (Get-Date).ToUniversalTime().ToString("o")
    return @{
        schemaVersion = 1
        baselineBranch = $BaselineBranch
        testsPath = $TestsPath
        runs = $Runs
        warmupCount = $WarmupCount
        warmupTestsPath = $WarmupTestsPath
        allFiltersBaselined = $false
        baselinedFilters = @()
        createdAtUtc = $now
        updatedAtUtc = $now
        runHistory = @()
    }
}

$requestedFilters = Get-NormalizedFilters $Filter
$requestedFilterArg = Convert-FilterSetToArg $requestedFilters
Assert-CleanWorkingTree

if ($TraceMode) {
    New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
    $runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $runDir = Join-Path $ResultsDir "trace_run_$runStamp"
    New-Item -ItemType Directory -Path $runDir -Force | Out-Null

    $optSafe = Get-SafeName $OptimizedBranch
    $optimizedOutputDir = Join-Path $runDir "optimized_$optSafe"
    $optimizedRunLog = Join-Path $runDir "optimized_run.log"
    $optimizedTraceDir = Join-Path $optimizedOutputDir "traces"

    if ([string]::IsNullOrWhiteSpace($requestedFilterArg)) {
        Write-Host "[compare-native] Trace mode enabled. Running optimized branch '$OptimizedBranch' for all filters."
    } else {
        Write-Host "[compare-native] Trace mode enabled. Running optimized branch '$OptimizedBranch' for filters: $requestedFilterArg"
    }
    Write-Host "[compare-native] Baseline run is disabled in trace mode."

    $originalRef = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1).Trim()
    $restoreRef = $originalRef
    if ($originalRef -eq "HEAD") {
        $restoreRef = (Invoke-Git @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
    }

    try {
        Checkout-Branch $OptimizedBranch
        Invoke-RunNative -BranchName $OptimizedBranch -FilterArg $requestedFilterArg -VariantLabel "optimized" -LogPath $optimizedRunLog -TraceOutputDir $optimizedTraceDir
        Copy-RunArtifacts -Destination $optimizedOutputDir
    }
    finally {
        if (![string]::IsNullOrWhiteSpace($restoreRef)) {
            $currentRef = (Invoke-Git @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
            $restoreHash = (Invoke-Git @("rev-parse", $restoreRef) | Select-Object -First 1).Trim()
            if ($currentRef -ne $restoreHash) {
                Write-Host "[compare-native] Restoring original git ref '$restoreRef'"
                Checkout-Branch $restoreRef
            }
        }
    }

    Write-Host ""
    Write-Host "[compare-native] Completed trace mode run."
    Write-Host "[compare-native] Optimized branch: $OptimizedBranch"
    Write-Host "[compare-native] Results directory: $runDir"
    Write-Host "[compare-native] Optimized output: $optimizedOutputDir"
    Write-Host "[compare-native] Trace output: $optimizedTraceDir"
    exit 0
}

New-Item -ItemType Directory -Path $ResultsDir -Force | Out-Null
$ManifestPath = Join-Path $ResultsDir "baseline-cache.json"
$ResultsRootPath = [System.IO.Path]::GetFullPath($ResultsDir)

$manifest = $null
$firstRunForResultsDir = !(Test-Path $ManifestPath)
if (!$firstRunForResultsDir) {
    try {
        $loadedManifest = Get-Content -Path $ManifestPath -Raw | ConvertFrom-Json
        $manifest = ConvertTo-Hashtable $loadedManifest
    } catch {
        Write-Host "[compare-native] Manifest is invalid. Recreating baseline cache."
    }
}
if ($null -eq $manifest) {
    $manifest = New-Manifest
}

$manifestReasons = @()
if ($manifest["schemaVersion"] -ne 1) {
    $manifestReasons += "schema version changed"
}
if ($manifest["baselineBranch"] -ne $BaselineBranch) {
    $manifestReasons += "baseline branch changed"
}
if ($manifest["testsPath"] -ne $TestsPath) {
    $manifestReasons += "tests path changed"
}
if ([int]$manifest["runs"] -ne $Runs) {
    $manifestReasons += "runs changed"
}
if ([int]$manifest["warmupCount"] -ne $WarmupCount) {
    $manifestReasons += "warmup count changed"
}
if ($manifest["warmupTestsPath"] -ne $WarmupTestsPath) {
    $manifestReasons += "warmup tests path changed"
}

if ($manifestReasons.Count -gt 0) {
    Write-Host "[compare-native] Baseline cache invalidated: $([string]::Join(', ', $manifestReasons))"
    $manifest = New-Manifest
    $firstRunForResultsDir = $true
}

$requestedAll = ($requestedFilters.Count -eq 1 -and $requestedFilters[0] -eq "*")

$knownBaselines = @{}
$knownBaselineFilters = @()
if ($manifest.ContainsKey("baselinedFilters") -and $manifest["baselinedFilters"]) {
    foreach ($filterItem in $manifest["baselinedFilters"]) {
        $normalized = "$filterItem".Trim().ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($normalized)) {
            continue
        }
        if (!$knownBaselines.ContainsKey($normalized)) {
            $knownBaselines[$normalized] = $true
            $knownBaselineFilters += $normalized
        }
    }
}

$allFiltersBaselined = $false
if ($manifest.ContainsKey("allFiltersBaselined")) {
    $allFiltersBaselined = [bool]$manifest["allFiltersBaselined"]
}

$baselineFiltersToRun = @()
if ($requestedAll) {
    if (!$allFiltersBaselined) {
        $baselineFiltersToRun = @("*")
    }
} elseif (!$allFiltersBaselined) {
    foreach ($candidate in $requestedFilters) {
        if (!$knownBaselines.ContainsKey($candidate)) {
            $baselineFiltersToRun += $candidate
        }
    }
}

if ($ForceBaseline) {
    if ($requestedAll) {
        $baselineFiltersToRun = @("*")
    } else {
        $baselineFiltersToRun = @($requestedFilters)
    }
}

$baselineFilterArg = Convert-FilterSetToArg $baselineFiltersToRun

if ($ForceBaseline) {
    if ($baselineFiltersToRun.Count -eq 1 -and $baselineFiltersToRun[0] -eq "*") {
        Write-Host "[compare-native] ForceBaseline enabled. Baseline will rerun for all filters."
    } else {
        Write-Host "[compare-native] ForceBaseline enabled. Baseline will rerun for: $baselineFilterArg"
    }
} elseif ($firstRunForResultsDir) {
    Write-Host "[compare-native] First run for results dir '$ResultsDir'. Baseline and optimized will both run."
} elseif ($baselineFiltersToRun.Count -eq 0) {
    Write-Host "[compare-native] No new filters for baseline. Reusing cached baseline for existing filters."
} else {
    Write-Host "[compare-native] New filters detected for baseline: $baselineFilterArg"
}

$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$runDir = Join-Path $ResultsDir "compare_run_$runStamp"
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$runDirFullPath = [System.IO.Path]::GetFullPath($runDir)

$baseSafe = Get-SafeName $BaselineBranch
$optSafe = Get-SafeName $OptimizedBranch
$baselineOutputDir = Join-Path $runDir "baseline_$baseSafe"
$optimizedOutputDir = Join-Path $runDir "optimized_$optSafe"
$baselineTraceDir = Join-Path $baselineOutputDir "traces"
$optimizedTraceDir = Join-Path $optimizedOutputDir "traces"
$baselineRunLog = Join-Path $runDir "baseline_run.log"
$optimizedRunLog = Join-Path $runDir "optimized_run.log"

$originalRef = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1).Trim()
$restoreRef = $originalRef
if ($originalRef -eq "HEAD") {
    $restoreRef = (Invoke-Git @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
}

$baselineExecuted = $false
$optimizedExecuted = $false

try {
    if ($baselineFiltersToRun.Count -gt 0) {
        Checkout-Branch $BaselineBranch
        Invoke-RunNative -BranchName $BaselineBranch -FilterArg $baselineFilterArg -VariantLabel "baseline" -LogPath $baselineRunLog -TraceOutputDir $baselineTraceDir
        Copy-RunArtifacts -Destination $baselineOutputDir
        $baselineExecuted = $true
    } else {
        Set-Content -Path (Join-Path $runDir "baseline_skipped.txt") -Value "Baseline skipped: no new filters for baseline cache."
    }

    Checkout-Branch $OptimizedBranch
    Invoke-RunNative -BranchName $OptimizedBranch -FilterArg $requestedFilterArg -VariantLabel "optimized" -LogPath $optimizedRunLog -TraceOutputDir $optimizedTraceDir
    Copy-RunArtifacts -Destination $optimizedOutputDir
    $optimizedExecuted = $true
}
finally {
    if (![string]::IsNullOrWhiteSpace($restoreRef)) {
        $currentRef = (Invoke-Git @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
        $restoreHash = (Invoke-Git @("rev-parse", $restoreRef) | Select-Object -First 1).Trim()
        if ($currentRef -ne $restoreHash) {
            Write-Host "[compare-native] Restoring original git ref '$restoreRef'"
            Checkout-Branch $restoreRef
        }
    }
}

if ($baselineExecuted) {
    if ($baselineFiltersToRun.Count -eq 1 -and $baselineFiltersToRun[0] -eq "*") {
        $manifest["allFiltersBaselined"] = $true
        $manifest["baselinedFilters"] = @()
    } else {
        foreach ($newFilter in $baselineFiltersToRun) {
            if (!$knownBaselines.ContainsKey($newFilter)) {
                $knownBaselines[$newFilter] = $true
                $knownBaselineFilters += $newFilter
            }
        }
        $manifest["baselinedFilters"] = $knownBaselineFilters | Sort-Object
    }
}

$baselineResultsForSummary = @()
if ($baselineExecuted) {
    $currentBaselineResults = Join-Path $baselineOutputDir "results"
    if (Test-Path $currentBaselineResults) {
        $baselineResultsForSummary += [System.IO.Path]::GetFullPath($currentBaselineResults)
    }
}
$historicalBaselineResults = Get-AllBaselineResultsPaths -ResultsRoot $ResultsRootPath -BaselineFolderName "baseline_$baseSafe" -ExcludeRunDir $runDirFullPath
$baselineResultsForSummary += $historicalBaselineResults
$baselineResultsForSummary = @($baselineResultsForSummary | Select-Object -Unique)

if ($optimizedExecuted) {
    $optimizedResultsForSummary = Join-Path $optimizedOutputDir "results"
    if ($baselineResultsForSummary.Count -gt 0) {
        Write-NewPayloadComparison -BaselineResultsPaths $baselineResultsForSummary -OptimizedResultsPath $optimizedResultsForSummary -OutputDir $runDir
    } else {
        Write-Host "[compare-native] Baseline results were not found for comparison output."
    }
}

$manifest["schemaVersion"] = 1
$manifest["baselineBranch"] = $BaselineBranch
$manifest["testsPath"] = $TestsPath
$manifest["runs"] = $Runs
$manifest["warmupCount"] = $WarmupCount
$manifest["warmupTestsPath"] = $WarmupTestsPath
$manifest["updatedAtUtc"] = (Get-Date).ToUniversalTime().ToString("o")

$history = @()
if ($manifest.ContainsKey("runHistory") -and $manifest["runHistory"]) {
    $history = @($manifest["runHistory"])
}

$historyEntry = @{
    timestampUtc = (Get-Date).ToUniversalTime().ToString("o")
    baselineBranch = $BaselineBranch
    optimizedBranch = $OptimizedBranch
    requestedFilter = $requestedFilterArg
    baselineFilterExecuted = $baselineFilterArg
    baselineExecuted = $baselineExecuted
    optimizedExecuted = $optimizedExecuted
    runDirectory = $runDir
}
$history += $historyEntry
if ($history.Count -gt 30) {
    $history = $history | Select-Object -Last 30
}
$manifest["runHistory"] = $history

$manifest | ConvertTo-Json -Depth 10 | Set-Content -Path $ManifestPath

Write-Host ""
Write-Host "[compare-native] Completed."
Write-Host "[compare-native] Baseline branch: $BaselineBranch"
Write-Host "[compare-native] Optimized branch: $OptimizedBranch"
Write-Host "[compare-native] Results directory: $runDir"
if ($baselineExecuted) {
    Write-Host "[compare-native] Baseline output: $baselineOutputDir"
} else {
    Write-Host "[compare-native] Baseline output: skipped (cached)"
}
Write-Host "[compare-native] Optimized output: $optimizedOutputDir"
if ($traceEnabledForRun) {
    if ($baselineExecuted -and (Test-Path $baselineTraceDir)) {
        Write-Host "[compare-native] Baseline trace: $baselineTraceDir"
    }
    if (Test-Path $optimizedTraceDir) {
        Write-Host "[compare-native] Optimized trace: $optimizedTraceDir"
    }
}
Write-Host "[compare-native] Cache manifest: $ManifestPath"
