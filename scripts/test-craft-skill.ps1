param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')]
    [string]$Player,
    [string]$ItemName = 'crafting_table',
    [ValidateRange(1, 16)]
    [int]$Count = 1,
    [ValidateRange(1, 16)]
    [int]$SearchRadius = 16,
    [int[]]$TablePosition,
    [string]$AgentUrl = 'http://127.0.0.1:8000'
)

# Count is the requested inventory increase, not the target inventory total.
$ErrorActionPreference = 'Stop'
$skillArguments = @{ item_name = $ItemName; count = $Count; search_radius = $SearchRadius }
if ($PSBoundParameters.ContainsKey('TablePosition')) {
    if ($TablePosition.Count -ne 3) { throw 'TablePosition requires exactly three integers: X,Y,Z.' }
    $skillArguments.table_position = @{ x = $TablePosition[0]; y = $TablePosition[1]; z = $TablePosition[2] }
}
$skillBase = $AgentUrl.TrimEnd('/') + '/api/agent/skills'
$executionId = $null
$finished = $false
$payload = @{
    player = $Player; request_id = 'craft-' + [guid]::NewGuid().ToString('N')
    skill = 'craft_item'; arguments = $skillArguments
    timeout_seconds = 300; max_tool_calls = 96
} | ConvertTo-Json -Depth 8

try {
    $submission = Invoke-RestMethod -Method Post -Uri ($skillBase + '/execute') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15
    if (-not $submission.success) { throw 'Skill submission failed.' }
    $executionId = $submission.data.execution_id
    Write-Host "[Skill] execution_id=$executionId requested_increase=$Count"
    $deadline = [DateTime]::UtcNow.AddSeconds(330)
    $lastProgress = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $response = Invoke-RestMethod -Method Get -Uri ($skillBase + '/executions/' + $executionId + '?player=' + $Player) -TimeoutSec 10
        if (-not $response.success) { throw 'Skill status request failed.' }
        $result = $response.data
        $progress = '[Skill] status={0} stage={1} inventory={2} increase={3}/{4} calls={5}' -f $result.status, $result.data.stage, $result.data.current_count, $result.data.actual_increase, $Count, $result.calls
        if ($progress -ne $lastProgress) { Write-Host $progress; $lastProgress = $progress }
        if ($result.status -notin @('pending', 'running')) {
            $finished = $true
            if (-not $result.success -and $result.data.workbench_check) {
                $check = $result.data.workbench_check
                Write-Host ('[Skill] last_workbench_check reason={0} distance={1} block={2} visible={3}' -f $check.reason, $check.interaction_distance, $check.actual_name, $check.visible)
            }
            $result | ConvertTo-Json -Depth 30
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
