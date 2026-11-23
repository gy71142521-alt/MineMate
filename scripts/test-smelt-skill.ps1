param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')]
    [string]$Player,
    [string]$ItemName = 'raw_iron',
    [ValidateRange(1, 64)]
    [int]$Count = 3,
    [string]$FuelName,
    [ValidateRange(1, 16)]
    [int]$SearchRadius = 16,
    [int[]]$FurnacePosition,
    [switch]$UseExisting,
    [ValidateRange(1, 1500)]
    [int]$WaitTimeout = 900,
    [ValidateRange(1, 1800)]
    [int]$TimeoutSeconds = 1200,
    [string]$AgentUrl = 'http://127.0.0.1:8000'
)

# Loading only: no processing wait or product collection. WaitTimeout is accepted for old callers and ignored.
$ErrorActionPreference = 'Stop'
$skillArguments = @{ item_name = $ItemName; count = $Count; search_radius = $SearchRadius; use_existing = [bool]$UseExisting }
if ($FuelName) { $skillArguments.fuel_name = $FuelName }
if ($PSBoundParameters.ContainsKey('FurnacePosition')) {
    if ($FurnacePosition.Count -ne 3) { throw 'FurnacePosition requires exactly three integers: X,Y,Z.' }
    $skillArguments.furnace_position = @{ x = $FurnacePosition[0]; y = $FurnacePosition[1]; z = $FurnacePosition[2] }
}
$skillBase = $AgentUrl.TrimEnd('/') + '/api/agent/skills'
$executionId = $null
$finished = $false
$payload = @{
    player = $Player; request_id = 'smelt-' + [guid]::NewGuid().ToString('N')
    skill = 'smelt_item'; arguments = $skillArguments
    timeout_seconds = $TimeoutSeconds; max_tool_calls = 192
} | ConvertTo-Json -Depth 8

try {
    $submission = Invoke-RestMethod -Method Post -Uri ($skillBase + '/execute') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 15
    if (-not $submission.success) { throw 'Skill submission failed.' }
    $executionId = $submission.data.execution_id
    Write-Host "[Skill] execution_id=$executionId input_count=$Count"
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds + 30)
    $lastProgress = ''
    while ([DateTime]::UtcNow -lt $deadline) {
        $response = Invoke-RestMethod -Method Get -Uri ($skillBase + '/executions/' + $executionId + '?player=' + $Player) -TimeoutSec 10
        if (-not $response.success) { throw 'Skill status request failed.' }
        $result = $response.data
        $progress = '[Skill] status={0} stage={1} scope={2} input_submitted={3} input_remaining={4} fuel_added={5} fuel_pending={6} fuel_followup={7} calls={8}' -f $result.status, $result.data.stage, $result.data.completion_scope, $result.data.input_submitted_count, $result.data.remaining_input, $result.data.fuel_added, $result.data.fuel_pending_count, $result.data.requires_fuel_followup, $result.calls
        if ($progress -ne $lastProgress) { Write-Host $progress; $lastProgress = $progress }
        if ($result.status -notin @('pending', 'running')) {
            $finished = $true
            if ($result.success) {
                Write-Host '[Skill] Furnace loading confirmed. Processing continues in Minecraft; products were not collected.'
            }
            if (-not $result.success) {
                foreach ($attempt in $result.data.furnace_attempts) {
                    Write-Host ('[Skill] furnace=({0},{1},{2}) reason={3} existing_input={4} requested_input={5} total_input={6} queue_limit={7}' -f $attempt.position.x, $attempt.position.y, $attempt.position.z, $attempt.reason, $attempt.existing_input, $attempt.requested_input, $attempt.total_input, $attempt.queue_limit)
                }
            }
            if (-not $result.success -and $result.data.furnace_check) {
                $check = $result.data.furnace_check
                Write-Host ('[Skill] last_furnace_check reason={0} distance={1} block={2} visible={3}' -f $check.reason, $check.interaction_distance, $check.actual_name, $check.visible)
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
            Write-Host '[Skill] cancellation requested. Deposited materials remain in the furnace.'
        } catch {
            Write-Warning 'Could not confirm cancellation. Use the in-game stop command or cancellation API.'
        }
    }
}
