[CmdletBinding()]
param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar

function Assert-WorkspacePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Workspace 밖의 경로는 정리할 수 없습니다: $resolved"
    }
    return $resolved
}

function Get-DirectoryBytes {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [int64]0
    }
    $measurement = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    if ($null -eq $measurement.Sum) {
        return [int64]0
    }
    return [int64]$measurement.Sum
}

$generatedDirectories = @("build", "dist", "temp") | ForEach-Object {
    Assert-WorkspacePath (Join-Path $projectRoot $_)
}
$cacheDirectories = Get-ChildItem -LiteralPath $projectRoot -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Assert-WorkspacePath $_.FullName } |
    Sort-Object -Unique |
    Sort-Object { $_.Length } -Descending
$looseBytecode = Get-ChildItem -LiteralPath $projectRoot -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {
        if ($_.Extension -notin @(".pyc", ".pyo")) {
            return $false
        }
        if ($_.FullName -match "\\__pycache__\\") {
            return $false
        }
        foreach ($generated in $generatedDirectories) {
            $generatedPrefix = $generated.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
            if ($_.FullName.StartsWith($generatedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $false
            }
        }
        return $true
    } |
    ForEach-Object { Assert-WorkspacePath $_.FullName }

$targets = @($generatedDirectories) + @($cacheDirectories)
$beforeBytes = [int64]0
foreach ($target in $targets) {
    $beforeBytes += Get-DirectoryBytes $target
}
foreach ($file in $looseBytecode) {
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        $beforeBytes += (Get-Item -LiteralPath $file).Length
    }
}

if ($Execute) {
    foreach ($target in $targets | Sort-Object -Unique | Sort-Object { $_.Length } -Descending) {
        if (Test-Path -LiteralPath $target -PathType Container) {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
    foreach ($file in $looseBytecode) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            Remove-Item -LiteralPath $file -Force
        }
    }
}

$remainingBytes = [int64]0
if (-not $Execute) {
    $remainingBytes = $beforeBytes
}

[pscustomobject]@{
    project_root = $projectRoot
    mode = if ($Execute) { "executed" } else { "dry_run" }
    generated_directories = $generatedDirectories
    cache_directory_count = @($cacheDirectories).Count
    loose_bytecode_count = @($looseBytecode).Count
    reclaimable_bytes = $beforeBytes
    reclaimable_megabytes = [math]::Round($beforeBytes / 1MB, 2)
    remaining_target_bytes = $remainingBytes
} | ConvertTo-Json -Depth 4
