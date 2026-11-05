param(
    [Parameter(Mandatory = $true)]
    [string] $SourceDirectory
)

$ErrorActionPreference = 'Stop'
$pinPath = Join-Path $PSScriptRoot 'source-pin.json'
$pin = Get-Content -LiteralPath $pinPath -Raw | ConvertFrom-Json
$source = [System.IO.Path]::GetFullPath($SourceDirectory)
if (Test-Path -LiteralPath $source) {
    throw "Source destination already exists; choose a new empty path: $source"
}
$parent = Split-Path -Parent $source
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$scratch = Join-Path $parent ('.cm093-source-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$archive = Join-Path $scratch 'hermes-agent-pinned.zip'
$unpack = Join-Path $scratch 'hermes-agent-unpack'

try {
    Invoke-WebRequest -Uri $pin.archive_url -OutFile $archive
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($archiveHash -ne $pin.archive_sha256) {
        throw "Official source archive hash mismatch: $archiveHash"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $unpack
    $roots = @(Get-ChildItem -LiteralPath $unpack -Directory)
    if ($roots.Count -ne 1) {
        throw "Expected one source root from the pinned archive; found $($roots.Count)"
    }
    $unpackRoot = [System.IO.Path]::GetFullPath($unpack).TrimEnd('\') + '\'
    $extractedRoot = [System.IO.Path]::GetFullPath($roots[0].FullName)
    if (-not $extractedRoot.StartsWith($unpackRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Extracted source escaped the owned unpack directory.'
    }
    if ([System.IO.Path]::GetFullPath((Split-Path -Parent $source)) -ne $parent) {
        throw 'Source destination escaped the explicitly selected parent directory.'
    }
    Move-Item -LiteralPath $extractedRoot -Destination $source

    foreach ($entry in $pin.critical_files.PSObject.Properties) {
        $path = Join-Path $source $entry.Name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Pinned source file is missing: $($entry.Name)"
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($actual -ne $entry.Value) {
            throw "Pinned source file hash mismatch for $($entry.Name): $actual"
        }
    }

    $receipt = [ordered]@{
        repository = $pin.repository
        commit = $pin.commit
        archive_url = $pin.archive_url
        archive_sha256 = $archiveHash
        verified_critical_files = @($pin.critical_files.PSObject.Properties.Name)
        verification = 'Official commit-addressed archive SHA256 and critical source file SHA256 checks passed.'
    }
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $source '.cm093-source-receipt.json') -Encoding utf8
    Write-Output "Verified pinned Hermes source at $source"
    Write-Output "Commit: $($pin.commit)"
    Write-Output "Archive SHA256: $archiveHash"
}
finally {
    $scratchRoot = [System.IO.Path]::GetFullPath($scratch)
    $parentRoot = [System.IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
    if (-not $scratchRoot.StartsWith($parentRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing cleanup outside the selected source parent.'
    }
    if (Test-Path -LiteralPath $scratchRoot) {
        Remove-Item -LiteralPath $scratchRoot -Recurse -Force
    }
}
