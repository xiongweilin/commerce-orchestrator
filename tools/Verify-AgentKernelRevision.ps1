[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),

    [Parameter()]
    [string]$AgentKernelPath = $(
        if ($env:AGENT_KERNEL_CONTEXT) {
            $env:AGENT_KERNEL_CONTEXT
        }
        else {
            'D:\agent\agent-kernel'
        }
    )
)

$ErrorActionPreference = 'Stop'

$manifestPath = Join-Path $RepositoryRoot 'docs/contracts/responsibility-compatibility.toml'
$dockerfilePath = Join-Path $RepositoryRoot 'backend/Dockerfile'

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Compatibility manifest not found: $manifestPath"
}
if (-not (Test-Path -LiteralPath $dockerfilePath -PathType Leaf)) {
    throw "Backend Dockerfile not found: $dockerfilePath"
}
if (-not (Test-Path -LiteralPath $AgentKernelPath -PathType Container)) {
    throw "agent-kernel context not found: $AgentKernelPath"
}

$manifestMatch = Select-String -LiteralPath $manifestPath -Pattern '^agent_kernel_revision\s*=\s*"([0-9a-f]{40})"\s*$'
if (-not $manifestMatch) {
    throw "agent_kernel_revision is missing or invalid in $manifestPath"
}
$expectedRevision = $manifestMatch.Matches[0].Groups[1].Value

$dockerfileText = Get-Content -LiteralPath $dockerfilePath -Raw
$dockerfileMatch = [regex]::Match($dockerfileText, 'ARG AGENT_KERNEL_REV=([0-9a-f]{40})')
if (-not $dockerfileMatch.Success) {
    throw "AGENT_KERNEL_REV pin is missing or invalid in $dockerfilePath"
}
if ($dockerfileMatch.Groups[1].Value -ne $expectedRevision) {
    throw "Dockerfile pin $($dockerfileMatch.Groups[1].Value) does not match manifest pin $expectedRevision"
}

$actualRevision = (& git -C $AgentKernelPath rev-parse HEAD 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve agent-kernel HEAD at ${AgentKernelPath}: $actualRevision"
}
if ($actualRevision -ne $expectedRevision) {
    throw "agent-kernel HEAD $actualRevision does not match expected revision $expectedRevision"
}

Write-Output "agent-kernel revision verified: $actualRevision"
