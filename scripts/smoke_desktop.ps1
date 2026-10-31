param([Parameter(Mandatory = $true)][string]$Executable)
$ErrorActionPreference = 'Stop'
$executablePath = (Resolve-Path -LiteralPath $Executable).Path
$profileDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ('rka-desktop-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $profileDirectory | Out-Null
$baseline = @(Get-Process -Name rka-backend -ErrorAction SilentlyContinue | ForEach-Object Id)
$start = [System.Diagnostics.ProcessStartInfo]::new($executablePath)
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$start.WorkingDirectory = $profileDirectory
$start.Environment['USERPROFILE'] = $profileDirectory
$start.Environment['RUST_BACKTRACE'] = '1'
$start.Environment.Remove('RKA_PYTHON') | Out-Null
$desktop = [System.Diagnostics.Process]::Start($start)
$stdout = $desktop.StandardOutput.ReadToEndAsync()
$stderr = $desktop.StandardError.ReadToEndAsync()
try {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 250
        $desktop.Refresh()
        if ($desktop.HasExited) { throw "Desktop exited during startup: $($desktop.ExitCode)" }
    } until ($desktop.MainWindowHandle -ne 0 -or (Get-Date) -gt $deadline)
    if ($desktop.MainWindowHandle -eq 0) { throw 'No desktop window appeared within 45 seconds' }
    # Window creation can precede setup; allow for frozen extraction and initial requests.
    do {
        Start-Sleep -Milliseconds 250
        $desktop.Refresh()
        if ($desktop.HasExited) { throw "Desktop exited during setup: $($desktop.ExitCode)" }
        $sidecars = @(Get-Process -Name rka-backend -ErrorAction SilentlyContinue |
            Where-Object { $_.Id -notin $baseline })
    } until ($sidecars.Count -gt 0 -or (Get-Date) -gt $deadline)
    if ($sidecars.Count -eq 0) { throw 'Packaged Python backend is missing' }
    Start-Sleep -Seconds 2
    $desktop.Refresh()
    if ($desktop.HasExited) { throw "Desktop exited after backend launch: $($desktop.ExitCode)" }
    if (-not $desktop.CloseMainWindow()) { throw 'Could not request normal window close' }
    if (-not $desktop.WaitForExit(20000)) { throw 'Desktop did not exit after window close' }
    if ($desktop.ExitCode -ne 0) { throw "Desktop exit code: $($desktop.ExitCode)" }
    foreach ($sidecar in $sidecars) {
        if (-not $sidecar.WaitForExit(10000)) { throw "Orphaned Python process: $($sidecar.Id)" }
    }
    Write-Output 'Native desktop: window startup, bundled backend and graceful close passed.'
} finally {
    if (-not $desktop.HasExited) { & taskkill /PID $desktop.Id /T /F | Out-Null }
    $desktop.WaitForExit()
    Write-Output $stdout.GetAwaiter().GetResult()
    Write-Output $stderr.GetAwaiter().GetResult()
    Get-ChildItem -LiteralPath $profileDirectory -Recurse -Filter '*.log' |
        ForEach-Object { Get-Content -LiteralPath $_.FullName }
    $desktop.Dispose()
    # Keep the isolated profile available as CI diagnostic evidence on failure.
    Write-Output "Smoke profile: $profileDirectory"
}
