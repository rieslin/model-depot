# Start Model Depot in the background (no console window) and open its page in the browser.
# Called by Start.cmd.
#
#   -Quiet      do not open the browser
#   -NoPrompt   never show a dialog; what would have been shown goes to <data>\logs\start.log
#   -Setup      prepare the program's own Python environment (.venv) and exit, without starting anything
#
# Which Python runs the program, in this order:
#   1. the one named in the environment variable MODEL_DEPOT_PYTHON, or in <data>\launcher.json ({"python": "..."})
#   2. the one the Windows setup installed with the program (install.json next to run.py)
#   3. .venv\Scripts\python.exe in the program folder
#   4. an installed Python (3.10 or newer) that already has aiohttp and Pillow
#   5. an installed Python without them: after asking, .venv is created and the two packages are downloaded into it
#   6. none installed: after asking, a python.exe is picked by hand (ComfyUI's python_embeded\python.exe works)
#      and remembered in <data>\launcher.json
param([switch]$Quiet, [switch]$NoPrompt, [switch]$Setup)

. (Join-Path $PSScriptRoot 'common.ps1')
$Silent = [bool]$NoPrompt

# Can this Python run the program? Code 0: yes. 4: a package or the version is missing. Else: not a working Python.
function Test-Python([string]$Exe) {
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return @{ Code = 1; Text = 'not found' } }
    try {
        $out = (& $Exe $RunPy --check | Out-String).Trim()
        return @{ Code = $LASTEXITCODE; Text = $out }
    } catch {
        return @{ Code = 1; Text = [string]$_ }
    }
}

function Get-PythonVersion([string]$Exe) {
    try {
        $text = (& $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" | Out-String).Trim()
        return [version]$text
    } catch {
        return $null
    }
}

# Installed Pythons, newest first, 3.10 and up. The Microsoft Store placeholder is not a Python.
function Get-InstalledPythons {
    $found = New-Object System.Collections.Generic.List[string]
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($line in (& $launcher.Source -0p)) {
            if ([string]$line -match '([A-Za-z]:\\.*python[\w.]*\.exe)\s*$') { $found.Add($Matches[1]) }
        }
    }
    foreach ($name in 'python.exe', 'python3.exe') {
        foreach ($cmd in (Get-Command $name -All -ErrorAction SilentlyContinue)) { $found.Add($cmd.Source) }
    }
    $usable = @()
    foreach ($exe in ($found | Select-Object -Unique)) {
        if (-not $exe -or $exe -like '*\WindowsApps\*' -or -not (Test-Path -LiteralPath $exe)) { continue }
        $version = Get-PythonVersion $exe
        if ($version -and $version -ge [version]'3.10') { $usable += [pscustomobject]@{ Exe = $exe; Version = $version } }
    }
    return @($usable | Sort-Object Version -Descending | ForEach-Object { $_.Exe })
}

# Create .venv with the given Python and install the two packages. Returns $null, or what went wrong.
function New-Environment([string]$BasePython) {
    $venvDir = Join-Path $Root '.venv'
    $p = Start-Process -FilePath $BasePython -ArgumentList @('-m', 'venv', "`"$venvDir`"") -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { return "python -m venv: exit code $($p.ExitCode)" }
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    $requirements = Join-Path $Root 'requirements.txt'
    $style = 'Normal'
    if ($Silent) { $style = 'Hidden' }
    $p = Start-Process -FilePath $venvPython -ArgumentList @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', "`"$requirements`"") -Wait -PassThru -WindowStyle $style
    if ($p.ExitCode -ne 0) { return "pip install: exit code $($p.ExitCode)" }
    $check = Test-Python $venvPython
    if ($check.Code -ne 0) { return $check.Text }
    return $null
}

function Resolve-Python {
    $venvPython = Join-Path $Root '.venv\Scripts\python.exe'

    $explicit = $env:MODEL_DEPOT_PYTHON
    if (-not $explicit) {
        $saved = Read-JsonFile $LauncherPath
        if ($saved -and $saved.python) { $explicit = [string]$saved.python }
    }
    if ($explicit) {
        $check = Test-Python $explicit
        if ($check.Code -eq 0) { return $explicit }
        Show-Message (T 'python_bad' $explicit $check.Text)     # then try the other ways
    }

    # the Python the Windows setup installed with the program
    if ($InstallInfo -and $InstallInfo.python) {
        $bundled = [string]$InstallInfo.python
        $check = Test-Python $bundled
        if ($check.Code -eq 0) { return $bundled }
        Show-Message (T 'python_bad' $bundled $check.Text)
    }

    if ((Test-Python $venvPython).Code -eq 0) { return $venvPython }

    $installed = @(Get-InstalledPythons)    # @(): a single result would otherwise arrive as a bare string
    foreach ($exe in $installed) {
        if ((Test-Python $exe).Code -eq 0) { return $exe }
    }

    if ($installed.Count -gt 0) {
        if (-not (Ask-YesNo (T 'need_packages'))) { return $null }
        $problem = New-Environment $installed[0]
        if ($problem) { Show-Message (T 'setup_failed' $problem); return $null }
        return $venvPython
    }

    if (-not (Ask-YesNo (T 'no_python'))) { return $null }
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = T 'pick_title'
    $dialog.Filter = 'python.exe|python.exe'
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { return $null }
    $check = Test-Python $dialog.FileName
    if ($check.Code -ne 0) { Show-Message (T 'python_bad' $dialog.FileName $check.Text); return $null }
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $json = @{ python = $dialog.FileName } | ConvertTo-Json
    [System.IO.File]::WriteAllText($LauncherPath, $json, (New-Object System.Text.UTF8Encoding($false)))
    return $dialog.FileName
}

if ($Setup) {
    $installed = @(Get-InstalledPythons)
    if ($installed.Count -eq 0) { Show-Message (T 'setup_failed' 'no Python 3.10 or newer is installed'); exit 1 }
    $problem = New-Environment $installed[0]
    if ($problem) { Show-Message (T 'setup_failed' $problem); exit 1 }
    Write-StartLog ("environment ready, made with " + $installed[0])
    exit 0
}

$url = Get-RunningUrl
if ($url) {
    if (-not $Quiet) { Start-Process ($url + '/') }
    exit 0
}

$python = Resolve-Python
if (-not $python) { exit 1 }

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stdout = Join-Path $LogDir 'stdout.log'
$stderr = Join-Path $LogDir 'stderr.log'
# The path may contain spaces, so it is quoted inside the argument string.
$proc = Start-Process -FilePath $python -ArgumentList @("`"$RunPy`"") -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$null = $proc.Handle    # keeps the exit code readable after the process ends

for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $url = Get-RunningUrl
    if ($url) {
        if (-not $Quiet) { Start-Process ($url + '/') }
        exit 0
    }
    if ($proc.HasExited) { break }
}

if ($proc.HasExited -and $proc.ExitCode -eq 3) {
    Show-Message (T 'port_busy' $ConfigPath)
    exit 3
}
$tail = ''
if (Test-Path -LiteralPath $stderr) { $tail = (Get-Content -LiteralPath $stderr -Tail 12 -Encoding UTF8) -join "`n" }
Show-Message (T 'start_failed' $tail $LogDir)
exit 1
