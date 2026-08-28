[CmdletBinding()]
param(
    [Parameter()]
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),

    [Parameter()]
    [string]$PortableRuntimePath = $(
        if ($env:PORTABLE_RUNTIME_CONTEXT) {
            $env:PORTABLE_RUNTIME_CONTEXT
        }
        else {
            'D:\agent\portable-runtime'
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
if (-not (Test-Path -LiteralPath $PortableRuntimePath -PathType Container)) {
    throw "portable-runtime context not found: $PortableRuntimePath"
}

$manifestMatch = Select-String -LiteralPath $manifestPath -Pattern '^portable_runtime_revision\s*=\s*"([0-9a-f]{40})"\s*$'
if (-not $manifestMatch) {
    throw "portable_runtime_revision is missing or invalid in $manifestPath"
}
$expectedRevision = $manifestMatch.Matches[0].Groups[1].Value

$dockerfileText = Get-Content -LiteralPath $dockerfilePath -Raw
$dockerfileMatch = [regex]::Match($dockerfileText, 'ARG PORTABLE_RUNTIME_REV=([0-9a-f]{40})')
if (-not $dockerfileMatch.Success) {
    throw "PORTABLE_RUNTIME_REV pin is missing or invalid in $dockerfilePath"
}
if ($dockerfileMatch.Groups[1].Value -ne $expectedRevision) {
    throw "Dockerfile pin $($dockerfileMatch.Groups[1].Value) does not match manifest pin $expectedRevision"
}

$actualRevision = (& git -C $PortableRuntimePath rev-parse HEAD 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve portable-runtime HEAD at ${PortableRuntimePath}: $actualRevision"
}
if ($actualRevision -ne $expectedRevision) {
    throw "portable-runtime HEAD $actualRevision does not match expected revision $expectedRevision"
}

Write-Output "portable-runtime revision verified: $actualRevision"
