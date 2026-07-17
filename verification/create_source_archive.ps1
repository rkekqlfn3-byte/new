[CmdletBinding()]
param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "source_archives"
}

Push-Location $projectRoot
try {
    $dirty = @(git status --porcelain)
    if ($LASTEXITCODE -ne 0) {
        throw "Git 작업 트리 상태를 확인하지 못했습니다."
    }
    if ($dirty.Count -gt 0) {
        throw "소스 archive는 clean commit에서만 만들 수 있습니다."
    }

    $version = python -c "from engine.version import APP_VERSION; print(APP_VERSION)"
    if ($LASTEXITCODE -ne 0 -or -not $version) {
        throw "APP_VERSION을 확인하지 못했습니다."
    }
    $commit = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or -not $commit) {
        throw "Git commit을 확인하지 못했습니다."
    }
    $shortCommit = $commit.Substring(0, 12)
    $targetDir = [System.IO.Path]::GetFullPath($OutputDirectory)
    [System.IO.Directory]::CreateDirectory($targetDir) | Out-Null
    $archive = Join-Path $targetDir "Jarvis-source-$version-$shortCommit.zip"

    git archive --format=zip --output=$archive HEAD
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archive)) {
        throw "git archive 생성에 실패했습니다."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $forbidden = @(
            "(^|/)(\.git|\.venv|build|dist|__pycache__|temp)(/|$)",
            "(^|/)verification/.*_report\.json$",
            "(^|/)verification/.*_progress\.txt$",
            "(^|/).*\.zip$"
        )
        $bad = @()
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace("\", "/")
            foreach ($pattern in $forbidden) {
                if ($name -match $pattern) {
                    $bad += $name
                    break
                }
            }
        }
        if ($bad.Count -gt 0) {
            throw "금지된 파일이 source archive에 포함됐습니다: $($bad -join ', ')"
        }
        $fileCount = $zip.Entries.Count
    }
    finally {
        $zip.Dispose()
    }

    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    [pscustomobject]@{
        archive = $archive
        app_version = $version
        commit = $commit
        sha256 = $hash
        file_count = $fileCount
        clean_worktree = $true
    } | ConvertTo-Json -Depth 3
}
finally {
    Pop-Location
}
