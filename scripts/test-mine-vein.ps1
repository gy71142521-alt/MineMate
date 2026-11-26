param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')][string]$Player,
    [string]$Resource = 'raw_iron',
    [ValidateRange(1, 256)][int]$Count = 3,
    [string]$BlockName,
    [string]$ToolName,
    [Nullable[int]]$SeedX, [Nullable[int]]$SeedY, [Nullable[int]]$SeedZ,
    [ValidateRange(0, 32)][int]$MaxClearBlocks = 16,
    [string]$AgentUrl = 'http://127.0.0.1:8000'
)
$ErrorActionPreference = 'Stop'
$skillBase = $AgentUrl.TrimEnd('/') + '/api/agent/skills'
$executionId = $null
$finished = $false
$arguments = @{ resource = $Resource; count = $Count; max_clear_blocks = $MaxClearBlocks }
if ($BlockName) { $arguments.block_name = $BlockName }
if ($ToolName) { $arguments.tool_name = $ToolName }
$seedValues = @($SeedX, $SeedY, $SeedZ) | Where-Object { $null -ne $_ }
if ($seedValues.Count -notin @(0, 3)) { throw 'Provide all of SeedX, SeedY and SeedZ, or omit all three.' }
if ($seedValues.Count -eq 3) { $arguments.seed_position = @{ x = $SeedX; y = $SeedY; z = $SeedZ } }
$payload = @{ player = $Player; request_id = 'vein-' + [guid]::NewGuid().ToString('N')
    skill = 'mine_vein'; arguments = $arguments; timeout_seconds = 300; max_tool_calls = 192 } | ConvertTo-Json -Depth 8
try {
    $submission = Invoke-RestMethod -Method Post -Uri ($skillBase + '/execute') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15
    if (-not $submission.success) { throw 'Skill submission failed.' }
    $executionId = $submission.data.execution_id
    Write-Host "[Skill] execution_id=$executionId target_total=$Count"
    $deadline = [DateTime]::UtcNow.AddSeconds(330)
    $lastProgress = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $response = Invoke-RestMethod -Method Get -Uri ($skillBase + '/executions/' + $executionId + '?player=' + $Player) -TimeoutSec 10
        if (-not $response.success) { throw 'Skill status request failed.' }
        $result = $response.data
        $progress = '[Skill] status={0} stage={1} inventory={2}/{3} mined={4} cleared={5} calls={6}' -f $result.status, $result.data.stage, $result.data.current_count, $Count, $result.data.blocks_mined, $result.data.cleared_blocks, $result.calls
        if ($progress -ne $lastProgress) { Write-Host $progress; $lastProgress = $progress }
        if ($result.status -notin @('pending', 'running')) { $finished = $true; $result | ConvertTo-Json -Depth 24; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $finished) { throw 'Timed out waiting for Skill status.' }
} finally {
    if ($executionId -and -not $finished) {
        try {
            $cancelBody = @{ username = $Player } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri ($skillBase + '/executions/' + $executionId + '/cancel') -ContentType 'application/json' -Body $cancelBody -TimeoutSec 10 | Out-Null
        } catch { Write-Warning 'Cancellation not confirmed; use the in-game stop command.' }
    }
}
