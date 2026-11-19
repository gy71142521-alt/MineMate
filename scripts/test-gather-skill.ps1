param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')]
    [string]$Player,
    [string]$Resource = 'oak_log',
    [ValidateRange(1, 64)]
    [int]$Count = 4,
    [string]$BlockName,
    [string]$ToolName,
    [ValidateRange(1, 16)]
    [int]$SearchRadius = 16,
    [ValidateRange(1, 64)]
    [int]$MaxBlocks = 16,
    [string]$AgentUrl = 'http://127.0.0.1:8000'
)

# Count is the target inventory total, including existing items.
$ErrorActionPreference = 'Stop'
$skillBase = $AgentUrl.TrimEnd('/') + '/api/agent/skills'
$executionId = $null
$finished = $false
$skillArguments = @{ resource = $Resource; count = $Count; search_radius = $SearchRadius; max_blocks = $MaxBlocks }
if ($BlockName) { $skillArguments.block_name = $BlockName }
if ($ToolName) { $skillArguments.tool_name = $ToolName }
$payload = @{
    player = $Player; request_id = 'gather-' + [guid]::NewGuid().ToString('N')
    skill = 'gather_resource'; arguments = $skillArguments
    timeout_seconds = 300; max_tool_calls = 192
} | ConvertTo-Json -Depth 8

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
        $progress = '[Skill] status={0} stage={1} inventory={2}/{3} mined={4} calls={5}' -f $result.status, $result.data.stage, $result.data.current_count, $Count, $result.data.blocks_mined, $result.calls
        if ($progress -ne $lastProgress) { Write-Host $progress; $lastProgress = $progress }
        if ($result.status -notin @('pending', 'running')) {
            $finished = $true
            $result | ConvertTo-Json -Depth 20
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $finished) { throw 'Timed out waiting for Skill status.' }
} finally {
    if ($executionId -and -not $finished) {
        try {
            $cancelBody = @{ username = $Player } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri ($skillBase + '/executions/' + $executionId + '/cancel') -ContentType 'application/json' -Body $cancelBody -TimeoutSec 10 | Out-Null
            Write-Host '[Skill] cancellation requested.'
        } catch {
            Write-Warning 'Could not confirm cancellation. Use the in-game stop command or the cancellation API.'
        }
    }
}
