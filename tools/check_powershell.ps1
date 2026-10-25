$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$paths = @(
    (Join-Path $root 'src\lanbridge\resources\windows.ps1'),
    (Join-Path $root 'build.ps1'),
    (Join-Path $root 'run.ps1'),
    (Join-Path $root 'tools\download_wheels.ps1'),
    $PSCommandPath
)
$failureCount = 0
foreach ($path in $paths) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors) | Out-Null
    foreach ($parseError in @($parseErrors)) {
        if ($null -ne $parseError) {
            Write-Host ($path + ':' + $parseError.Extent.StartLineNumber + ': ' + $parseError.Message)
            $failureCount += 1
        }
    }
}
if ($failureCount -gt 0) { throw "$failureCount PowerShell syntax error(s)." }
Write-Host "PowerShell syntax checked: $($paths.Count) scripts; no changes executed."
