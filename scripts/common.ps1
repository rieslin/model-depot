# Shared by start.ps1 and stop.ps1.
# These scripts are ASCII only on purpose: Windows PowerShell 5.1 misreads UTF-8 scripts that have no BOM.
# The sentences they show are in launcher-strings.json, two columns: Chinese, English.

$Root = Split-Path -Parent $PSScriptRoot

function Read-JsonFile([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

# Written by the Windows setup next to run.py: where it put the data folder and the program's own Python.
$InstallInfo = Read-JsonFile (Join-Path $Root 'install.json')
$DataDir = $env:MODEL_DEPOT_DATA
if (-not $DataDir -and $InstallInfo -and $InstallInfo.data_dir) { $DataDir = [string]$InstallInfo.data_dir }
if (-not $DataDir) { $DataDir = Join-Path $Root 'data' }
$LogDir = Join-Path $DataDir 'logs'
$ConfigPath = Join-Path $DataDir 'config.json'
$ServerInfo = Join-Path $DataDir 'server.json'
$LauncherPath = Join-Path $DataDir 'launcher.json'
$RunPy = Join-Path $Root 'run.py'
$AppName = 'Model Depot'
$Silent = $false    # set by the calling script: no dialogs, messages go to the log

# The language of the dialogs: the one chosen in the settings, else the language of Windows.
function Get-LangIndex {
    $lang = ''
    $cfg = Read-JsonFile $ConfigPath
    if ($cfg -and $cfg.language) { $lang = [string]$cfg.language }
    if (-not $lang) { if ((Get-UICulture).Name -like 'zh*') { $lang = 'zh' } else { $lang = 'en' } }
    if ($lang -eq 'zh') { return 0 }
    return 1
}

$Strings = Read-JsonFile (Join-Path $PSScriptRoot 'launcher-strings.json')
$LangIndex = Get-LangIndex

function T([string]$Key) {
    $row = $Strings.$Key
    if (-not $row) { return $Key }
    $text = [string]$row[$LangIndex]
    if ($args.Count -gt 0) { $text = [string]::Format($text, [object[]]$args) }
    return $text
}

function Write-StartLog([string]$Text) {
    try {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
        Add-Content -LiteralPath (Join-Path $LogDir 'start.log') -Value ("{0} {1}" -f (Get-Date -Format s), $Text) -Encoding UTF8
    } catch { }
}

function Show-Message([string]$Text) {
    Write-StartLog $Text
    if ($Silent) { return }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($Text, $AppName) | Out-Null
}

function Ask-YesNo([string]$Text) {
    if ($Silent) { return $false }
    Add-Type -AssemblyName System.Windows.Forms
    $answer = [System.Windows.Forms.MessageBox]::Show($Text, $AppName, [System.Windows.Forms.MessageBoxButtons]::YesNo)
    return ($answer -eq [System.Windows.Forms.DialogResult]::Yes)
}

# A request to this computer that never goes through the system proxy.
function Invoke-Local([string]$Url, [string]$Method = 'GET', [int]$TimeoutMs = 3000) {
    $req = [System.Net.WebRequest]::Create($Url)
    $req.Method = $Method
    $req.Proxy = $null
    $req.Timeout = $TimeoutMs
    $req.KeepAlive = $false
    if ($Method -ne 'GET') {
        $req.ServicePoint.Expect100Continue = $false
        $bytes = [System.Text.Encoding]::UTF8.GetBytes('{}')
        $req.ContentType = 'application/json'
        $req.ContentLength = $bytes.Length
        $stream = $req.GetRequestStream()
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Close()
    }
    $resp = $req.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream(), [System.Text.Encoding]::UTF8)
        return $reader.ReadToEnd()
    } finally {
        $resp.Close()
    }
}

# The address of the running server, taken from the file it writes when it starts; $null when none answers.
function Get-RunningUrl {
    $info = Read-JsonFile $ServerInfo
    if (-not $info -or -not $info.port) { return $null }
    $name = [string]$info.host
    if (-not $name -or $name -eq '0.0.0.0' -or $name -eq '::') { $name = '127.0.0.1' }
    if ($name.Contains(':')) { $name = "[$name]" }
    $url = "http://{0}:{1}" -f $name, $info.port
    try {
        $status = (Invoke-Local ($url + '/api/v1/status')) | ConvertFrom-Json
        if ($status.app -eq $AppName) { return $url }
    } catch { }
    return $null
}
