# Stop Model Depot. Called by Stop.cmd.
#
#   -Quiet   no dialog
#
# The server is asked to stop, which lets it finish what it is writing. Fingerprints already computed are kept;
# a file that was half-way through is simply done again next time. If the server does not stop within 30 seconds
# its process is ended.
param([switch]$Quiet)

. (Join-Path $PSScriptRoot 'common.ps1')
$Silent = [bool]$Quiet

# The Windows app (desktop\app\ModelDepot.exe) keeps an icon in the notification area; stopping Model Depot closes it
# too. It is told through a named signal (see desktop\Program.cs), and ends without touching the server.
$quitName = 'Local\ModelDepot.Desktop.Quit'
if ($env:MODEL_DEPOT_PROFILE) { $quitName += '.' + $env:MODEL_DEPOT_PROFILE.Trim() }
try {
    $quit = [System.Threading.EventWaitHandle]::OpenExisting($quitName)
    $null = $quit.Set()
    $quit.Dispose()
} catch { }

$info = Read-JsonFile $ServerInfo
$url = Get-RunningUrl
if (-not $url) {
    Show-Message (T 'not_running')
    exit 0
}

try { Invoke-Local ($url + '/api/admin/shutdown') 'POST' 10000 | Out-Null } catch { }

$proc = $null
if ($info -and $info.pid) { $proc = Get-Process -Id ([int]$info.pid) -ErrorAction SilentlyContinue }
if ($proc -and $proc.ProcessName -like 'python*') {
    if (-not $proc.WaitForExit(30000)) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
} else {
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-RunningUrl)) { break }
    }
}
Show-Message (T 'stopped')
exit 0
