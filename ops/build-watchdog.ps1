param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\dist\water_house_watchdog.exe'),
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolsRoot = Join-Path $projectRoot 'build\watchdog-tools'
New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null
function Get-VerifiedArchive {
    param([string]$Url, [string]$Hash, [string]$Name)
    $archivePath = Join-Path $toolsRoot "$Name.zip"
    if (!(Test-Path -LiteralPath $archivePath) -or
        (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) {
        Invoke-WebRequest -Uri $Url -OutFile $archivePath
    }
    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $Hash) { throw "SHA256 verification failed for $Name" }
    $destination = Join-Path $toolsRoot $Name
    Expand-Archive -LiteralPath $archivePath -DestinationPath $destination -Force
    return $destination
}
# Official release archives, pinned with their published SHA256 digests.
$runtimeRoot = Get-VerifiedArchive `
    -Url 'https://github.com/AutoHotkey/AutoHotkey/releases/download/v2.0.31/AutoHotkey_2.0.31.zip' `
    -Hash 'ca1a595aca4528755d8d4cd7fed23d497a37bd9a0b6892072612d68dee1bd00a' `
    -Name 'AutoHotkey-2.0.31'
$compilerRoot = Get-VerifiedArchive `
    -Url 'https://github.com/AutoHotkey/Ahk2Exe/releases/download/Ahk2Exe1.1.37.02a2/Ahk2Exe1.1.37.02a2.zip' `
    -Hash 'c29b8c3a5124850d79fc9e66e2ca79677c377d7f31631ad3022ba159c5d9e3be' `
    -Name 'Ahk2Exe-1.1.37.02a2'
$runtime = Join-Path $runtimeRoot 'AutoHotkey64.exe'
$source = Join-Path $projectRoot 'water_house_watchdog.ahk'
$iconPath = Join-Path $projectRoot 'img\享溫泉.ico'
if (!(Test-Path -LiteralPath $iconPath)) { throw "Watchdog icon is missing: $iconPath" }
$sourceResult = Join-Path $toolsRoot 'source-validation.json'
[IO.File]::WriteAllText($sourceResult, '')
$validation = Start-Process -FilePath $runtime -ArgumentList @('/ErrorStdOut', "`"$source`"", '--validate-only', "`"$sourceResult`"") -WindowStyle Hidden -Wait -PassThru
if ($validation.ExitCode -ne 0) { throw 'Watchdog self-test failed' }
if (!(Test-Path -LiteralPath $sourceResult) -or !(Get-Content $sourceResult -Raw | ConvertFrom-Json).passed) {
    throw 'Watchdog native validation did not report success'
}
if ($ValidateOnly) { return }
$compiler = Get-ChildItem -LiteralPath $compilerRoot -Filter 'Ahk2Exe.exe' -Recurse |
    Select-Object -First 1 -ExpandProperty FullName
if (!$compiler) { throw 'Ahk2Exe.exe not present in pinned compiler archive' }
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
$compileArgs = @('/in', "`"$source`"", '/out', "`"$resolvedOutput`"", '/base', "`"$runtime`"", '/icon', "`"$iconPath`"", '/silent')
$process = Start-Process -FilePath $compiler -ArgumentList $compileArgs -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $resolvedOutput)) {
    throw "Watchdog compilation failed: exit code $($process.ExitCode)"
}
$compiledResult = Join-Path $toolsRoot 'compiled-validation.json'
[IO.File]::WriteAllText($compiledResult, '')
$check = Start-Process -FilePath $resolvedOutput -ArgumentList @('--validate-only', "`"$compiledResult`"") -WindowStyle Hidden -Wait -PassThru
if ($check.ExitCode -ne 0) { throw "Compiled watchdog self-test failed: $($check.ExitCode)" }
if (!(Test-Path -LiteralPath $compiledResult) -or !(Get-Content $compiledResult -Raw | ConvertFrom-Json).passed) {
    throw 'Compiled watchdog validation did not report success'
}
Get-FileHash -LiteralPath $resolvedOutput -Algorithm SHA256
