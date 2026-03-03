# monitor_pipeline.ps1 - Live pipeline monitoring dashboard
#
# Usage: .\infra\monitor_pipeline.ps1 [-Minutes 5] [-Loop] [-IntervalSec 60]
#
# Shows: Router, Standard Parser, Big Bill (Large/Chunk), Rework, Enricher
# Metrics: throughput, avg time, failures, queue depths

param(
    [int]$Minutes = 5,
    [switch]$Loop,
    [int]$IntervalSec = 60
)

$PROFILE = "jrk-analytics-admin"
$REGION  = "us-east-1"
$BUCKET  = "jrk-analytics-billing"

function Get-QueueDepths {
    $queues = @(
        @{ Name = "Pending";   Prefix = "Bill_Parser_1_Pending_Parsing/" }
        @{ Name = "Standard";  Prefix = "Bill_Parser_1_Standard/" }
        @{ Name = "LargeFile"; Prefix = "Bill_Parser_1_LargeFile/" }
        @{ Name = "Chunks";    Prefix = "Bill_Parser_1_LargeFile_Chunks/" }
        @{ Name = "Rework";    Prefix = "Bill_Parser_Rework_Input/" }
        @{ Name = "Failed";    Prefix = "Bill_Parser_Failed_Jobs/" }
    )

    $results = @{}
    foreach ($q in $queues) {
        $count = 0
        $raw = aws s3api list-objects-v2 `
            --bucket $BUCKET `
            --prefix $q.Prefix `
            --query "Contents[?ends_with(Key, '.pdf')].Key" `
            --output json `
            --region $REGION --profile $PROFILE 2>$null | ConvertFrom-Json
        if ($raw) { $count = $raw.Count }
        $results[$q.Name] = $count
    }
    return $results
}

function Get-LambdaMetrics {
    param(
        [string]$LogGroup,
        [string]$Label,
        [int]$LookbackMs
    )

    $startTime = [long]([DateTimeOffset]::UtcNow.AddMilliseconds(-$LookbackMs).ToUnixTimeMilliseconds())

    # Get _metric lines
    $raw = aws logs filter-log-events `
        --log-group-name $LogGroup `
        --start-time $startTime `
        --filter-pattern "_metric" `
        --region $REGION --profile $PROFILE `
        --query "events[].message" `
        --output json 2>$null | ConvertFrom-Json

    $total = 0; $successes = 0; $failures = 0
    $totalMs = @(); $geminiMs = @(); $enrichMs = @()
    $totalPages = 0; $totalLines = 0

    if ($raw) {
        foreach ($msg in $raw) {
            try {
                $d = $msg | ConvertFrom-Json
                $total++
                if ($d.success) { $successes++ } else { $failures++ }
                if ($d.totalMs)   { $totalMs   += $d.totalMs }
                if ($d.geminiMs)  { $geminiMs  += $d.geminiMs }
                if ($d.enrichMs)  { $enrichMs  += $d.enrichMs }
                if ($d.pageCount) { $totalPages += $d.pageCount }
                if ($d.lineCount) { $totalLines += $d.lineCount }
            } catch {}
        }
    }

    $avgTotal  = if ($totalMs.Count -gt 0)  { [math]::Round(($totalMs  | Measure-Object -Average).Average / 1000, 1) } else { 0 }
    $avgGemini = if ($geminiMs.Count -gt 0) { [math]::Round(($geminiMs | Measure-Object -Average).Average / 1000, 1) } else { 0 }
    $avgEnrich = if ($enrichMs.Count -gt 0) { [math]::Round(($enrichMs | Measure-Object -Average).Average / 1000, 1) } else { 0 }

    return @{
        Label      = $Label
        Total      = $total
        Successes  = $successes
        Failures   = $failures
        AvgTotalS  = $avgTotal
        AvgGeminiS = $avgGemini
        AvgEnrichS = $avgEnrich
        Pages      = $totalPages
        Lines      = $totalLines
    }
}

function Get-RouterMetrics {
    param([int]$LookbackMs)

    $startTime = [long]([DateTimeOffset]::UtcNow.AddMilliseconds(-$LookbackMs).ToUnixTimeMilliseconds())

    $raw = aws logs filter-log-events `
        --log-group-name "/aws/lambda/jrk-bill-router" `
        --start-time $startTime `
        --filter-pattern "routed successfully" `
        --region $REGION --profile $PROFILE `
        --query "events[].message" `
        --output json 2>$null | ConvertFrom-Json

    $standard = 0; $large = 0; $total = 0
    if ($raw) {
        foreach ($msg in $raw) {
            try {
                $d = $msg | ConvertFrom-Json
                $total++
                if ($d.route -eq "standard") { $standard++ }
                elseif ($d.route -eq "largefile") { $large++ }
            } catch {}
        }
    }

    # Check for errors
    $errRaw = aws logs filter-log-events `
        --log-group-name "/aws/lambda/jrk-bill-router" `
        --start-time $startTime `
        --filter-pattern "routing_failed" `
        --region $REGION --profile $PROFILE `
        --query "events[].message" `
        --output json 2>$null | ConvertFrom-Json
    $errors = if ($errRaw) { @($errRaw).Count } else { 0 }

    return @{
        Total    = $total
        Standard = $standard
        Large    = $large
        Errors   = $errors
    }
}

function Get-ReworkMetrics {
    param([int]$LookbackMs)

    $startTime = [long]([DateTimeOffset]::UtcNow.AddMilliseconds(-$LookbackMs).ToUnixTimeMilliseconds())

    $raw = aws logs filter-log-events `
        --log-group-name "/aws/lambda/jrk-bill-parser-rework" `
        --start-time $startTime `
        --region $REGION --profile $PROFILE `
        --query "events[].message" `
        --output json 2>$null | ConvertFrom-Json

    $forwarded = 0; $errors = 0
    if ($raw) {
        foreach ($msg in $raw) {
            $s = [string]$msg
            if ($s -match "forwarded_to") { $forwarded++ }
            if ($s -match "error|Error|warning") { $errors++ }
        }
    }

    return @{
        Forwarded = $forwarded
        Errors    = $errors
    }
}

function Show-Dashboard {
    $lookbackMs = $Minutes * 60 * 1000
    $now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")

    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "  BILL PARSER PIPELINE MONITOR  |  $now UTC  |  Last ${Minutes}m" -ForegroundColor Cyan
    Write-Host "================================================================" -ForegroundColor Cyan

    # --- Queue Depths ---
    Write-Host "`n  QUEUE DEPTHS" -ForegroundColor Yellow
    Write-Host "  -----------" -ForegroundColor Yellow
    $queues = Get-QueueDepths
    $qOrder = @("Pending", "Standard", "LargeFile", "Chunks", "Rework", "Failed")
    foreach ($q in $qOrder) {
        $count = $queues[$q]
        $color = if ($count -eq 0) { "Green" }
                 elseif ($q -eq "Failed" -or $count -gt 100) { "Red" }
                 else { "White" }
        $bar = "#" * [math]::Min($count, 50)
        Write-Host ("  {0,-12} {1,5}  {2}" -f $q, $count, $bar) -ForegroundColor $color
    }

    # --- Router ---
    Write-Host "`n  ROUTER (jrk-bill-router)" -ForegroundColor Yellow
    Write-Host "  ------------------------" -ForegroundColor Yellow
    $router = Get-RouterMetrics -LookbackMs $lookbackMs
    Write-Host ("  Routed: {0}  (Standard: {1}, Large: {2})" -f $router.Total, $router.Standard, $router.Large)
    if ($router.Errors -gt 0) {
        Write-Host ("  Routing Errors: {0}" -f $router.Errors) -ForegroundColor Red
    } else {
        Write-Host "  Errors: 0" -ForegroundColor Green
    }

    # --- Standard Parser ---
    Write-Host "`n  STANDARD PARSER (jrk-bill-parser)" -ForegroundColor Yellow
    Write-Host "  ----------------------------------" -ForegroundColor Yellow
    $parser = Get-LambdaMetrics -LogGroup "/aws/lambda/jrk-bill-parser" -Label "Parser" -LookbackMs $lookbackMs
    $rate = if ($parser.Total -gt 0) { [math]::Round($parser.Total / $Minutes * 60, 0) } else { 0 }
    $successPct = if ($parser.Total -gt 0) { [math]::Round(100 * $parser.Successes / $parser.Total, 0) } else { 0 }
    Write-Host ("  Parsed: {0}  |  Success: {1}%  |  Rate: ~{2}/hr" -f $parser.Total, $successPct, $rate)
    Write-Host ("  Pages: {0}  |  Lines: {1}" -f $parser.Pages, $parser.Lines)
    Write-Host ("  Avg Time: {0}s  |  Avg Gemini: {1}s" -f $parser.AvgTotalS, $parser.AvgGeminiS)
    if ($parser.Failures -gt 0) {
        Write-Host ("  FAILURES: {0}" -f $parser.Failures) -ForegroundColor Red
    }

    # --- Big Bill (Large + Chunk) ---
    Write-Host "`n  BIG BILL (jrk-bill-large-parser + jrk-bill-chunk-processor)" -ForegroundColor Yellow
    Write-Host "  ------------------------------------------------------------" -ForegroundColor Yellow

    # Large parser (splitter)
    $splitEvents = aws logs filter-log-events `
        --log-group-name "/aws/lambda/jrk-bill-large-parser" `
        --start-time ([long]([DateTimeOffset]::UtcNow.AddMilliseconds(-$lookbackMs).ToUnixTimeMilliseconds())) `
        --filter-pattern "Splitting" `
        --region $REGION --profile $PROFILE `
        --query "events[].message" `
        --output json 2>$null | ConvertFrom-Json
    $splits = if ($splitEvents) { @($splitEvents).Count } else { 0 }

    # Chunk processor
    $chunk = Get-LambdaMetrics -LogGroup "/aws/lambda/jrk-bill-chunk-processor" -Label "Chunk" -LookbackMs $lookbackMs
    Write-Host ("  Splits: {0}  |  Chunks Parsed: {1}" -f $splits, $chunk.Total)
    if ($chunk.Total -gt 0) {
        Write-Host ("  Chunk Avg Time: {0}s  |  Avg Gemini: {1}s" -f $chunk.AvgTotalS, $chunk.AvgGeminiS)
        Write-Host ("  Lines: {0}  |  Success: {1}/{2}" -f $chunk.Lines, $chunk.Successes, $chunk.Total)
    }
    if ($chunk.Failures -gt 0) {
        Write-Host ("  CHUNK FAILURES: {0}" -f $chunk.Failures) -ForegroundColor Red
    }

    # --- Rework ---
    Write-Host "`n  REWORK (jrk-bill-parser-rework)" -ForegroundColor Yellow
    Write-Host "  --------------------------------" -ForegroundColor Yellow
    $rework = Get-ReworkMetrics -LookbackMs $lookbackMs
    Write-Host ("  Forwarded: {0}  |  Errors: {1}" -f $rework.Forwarded, $rework.Errors)
    if ($rework.Errors -gt 0) {
        Write-Host "  ^ Check CloudWatch for details" -ForegroundColor Red
    }
    if ($rework.Forwarded -eq 0 -and $queues["Rework"] -eq 0) {
        Write-Host "  Queue clear, handler idle" -ForegroundColor Green
    }

    # --- Enricher ---
    Write-Host "`n  ENRICHER (jrk-bill-enricher)" -ForegroundColor Yellow
    Write-Host "  ----------------------------" -ForegroundColor Yellow
    $enricher = Get-LambdaMetrics -LogGroup "/aws/lambda/jrk-bill-enricher" -Label "Enricher" -LookbackMs $lookbackMs
    $eRate = if ($enricher.Total -gt 0) { [math]::Round($enricher.Total / $Minutes * 60, 0) } else { 0 }
    $eSuccessPct = if ($enricher.Total -gt 0) { [math]::Round(100 * $enricher.Successes / $enricher.Total, 0) } else { 0 }
    Write-Host ("  Enriched: {0}  |  Success: {1}%  |  Rate: ~{2}/hr" -f $enricher.Total, $eSuccessPct, $eRate)
    Write-Host ("  Lines: {0}  |  Avg Time: {1}s" -f $enricher.Lines, $enricher.AvgEnrichS)
    if ($enricher.Failures -gt 0) {
        Write-Host ("  FAILURES: {0}" -f $enricher.Failures) -ForegroundColor Red
    }

    # --- Summary ---
    Write-Host "`n  ----------------" -ForegroundColor Cyan
    $totalWaiting = $queues["Pending"] + $queues["Standard"] + $queues["LargeFile"] + $queues["Rework"]
    $allGood = ($parser.Failures -eq 0) -and ($chunk.Failures -eq 0) -and ($router.Errors -eq 0) -and ($enricher.Failures -eq 0)
    $statusColor = if ($allGood) { "Green" } else { "Red" }
    $statusText  = if ($allGood) { "ALL CLEAR" } else { "ERRORS DETECTED" }
    Write-Host ("  Status: {0}  |  Total Waiting: {1}" -f $statusText, $totalWaiting) -ForegroundColor $statusColor
    Write-Host "================================================================`n" -ForegroundColor Cyan
}

# --- Main ---
if ($Loop) {
    Write-Host "Pipeline monitor running (Ctrl+C to stop, refreshing every ${IntervalSec}s)..." -ForegroundColor Gray
    while ($true) {
        Clear-Host
        Show-Dashboard
        Start-Sleep -Seconds $IntervalSec
    }
} else {
    Show-Dashboard
}
