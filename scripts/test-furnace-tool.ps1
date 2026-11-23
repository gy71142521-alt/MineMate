param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_]{3,16}$')]
    [string]$Player,
    [Parameter(Mandatory = $true)]
    [ValidateSet('add_input', 'add_fuel', 'take_output')]
    [string]$Action,
    [Parameter(Mandatory = $true)]
    [int[]]$Position,
    [string]$ItemName,
    [string]$FuelName,
    [ValidateRange(1, 64)]
    [int]$Count = 1,
    [string]$NodeUrl = 'http://127.0.0.1:3000'
)

# One tool call only. Never retry a write after a timeout or uncertain result.
$ErrorActionPreference = 'Stop'
if ($Position.Count -ne 3) { throw 'Position requires exactly three integers: X,Y,Z.' }
$toolName = 'furnace_' + $Action
$toolArguments = @{ position = @{ x = $Position[0]; y = $Position[1]; z = $Position[2] } }
if ($Action -eq 'add_fuel') {
    if ($ItemName) { throw 'Use FuelName for add_fuel, not ItemName.' }
    if ($FuelName) { $toolArguments.fuel_name = $FuelName }
    $toolArguments.count = $Count
} else {
    if (-not $ItemName) { throw 'ItemName is required for add_input and take_output.' }
    if ($FuelName) { throw 'FuelName is only used with add_fuel.' }
    $toolArguments.item_name = $ItemName
    if ($Action -eq 'add_input' -or $PSBoundParameters.ContainsKey('Count')) { $toolArguments.count = $Count }
}
$base = $NodeUrl.TrimEnd('/')
$catalog = Invoke-RestMethod -Method Get -Uri ($base + '/api/tools') -TimeoutSec 10
if (-not $catalog.success) { throw 'Could not read Node tool catalog.' }
if (-not ($catalog.data.tools | Where-Object name -eq $toolName)) { throw 'Tool not registered. Restart Node first.' }
$requestId = 'furnace-' + [guid]::NewGuid().ToString('N')
Write-Host ('[Tool] request_id={0} tool={1}' -f $requestId, $toolName)
$payload = @{
    username = $Player; requestId = $requestId; tool = $toolName; arguments = $toolArguments
    instanceId = $catalog.data.instanceId; generation = $catalog.data.generation
} | ConvertTo-Json -Depth 8
$result = Invoke-RestMethod -Method Post -Uri ($base + '/api/tools/execute') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload)) -TimeoutSec 30
$result | ConvertTo-Json -Depth 20
