param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')]
    [string]$Player,
    [string]$Target = 'oak_log',
    [ValidateSet('block', 'entity')]
    [string]$TargetKind = 'block',
    [ValidateRange(1, 64)]
    [int]$SearchRadius = 16,
    [ValidateRange(1, 8)]
    [int]$MaxWaypoints = 4,
    [switch]$Approach,
    [switch]$Explore,
    [string]$AgentUrl = 'http://127.0.0.1:8000'
)

# 仅供测试：提交真实 Skill、显示后台进度，Ctrl+C 时尽力取消自己的执行。
$ErrorActionPreference = 'Stop'
$skillBase = $AgentUrl.TrimEnd('/') + '/api/agent/skills'
$executionId = $null
$finished = $false
$payload = @{
    player = $Player
    request_id = 'search-' + [guid]::NewGuid().ToString('N')
    skill = 'explore_and_find'
    arguments = @{
        target_kind = $TargetKind; target = $Target; search_radius = $SearchRadius
        approach = [bool]$Approach; explore = [bool]$Explore; max_waypoints = $MaxWaypoints
    }
    timeout_seconds = 300; max_tool_calls = 192
} | ConvertTo-Json -Depth 8

try {
    $submission = Invoke-RestMethod -Method Post -Uri ($skillBase + '/execute') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15
    if (-not $submission.success) { throw 'Skill submission failed.' }
    $executionId = $submission.data.execution_id
    Write-Host "[Skill] execution_id=$executionId"
    Write-Host "[Skill] cancel: POST $skillBase/executions/$executionId/cancel with username=$Player"
    $deadline = [DateTime]::UtcNow.AddSeconds(330)
    $lastProgress = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $response = Invoke-RestMethod -Method Get -Uri ($skillBase + '/executions/' + $executionId + '?player=' + $Player) -TimeoutSec 10
        if (-not $response.success) { throw 'Skill status request failed.' }
        $result = $response.data
        $scanCount = 0
        $waypointCount = 0
        if ($null -ne $result.data) {
            $scanCount = @($result.data.scans).Count
            $waypointCount = @($result.data.waypoints).Count
        }
        $progress = '[Skill] status={0} stage={1} scans={2} waypoints={3} calls={4}' -f $result.status, $result.data.stage, $scanCount, $waypointCount, $result.calls
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
