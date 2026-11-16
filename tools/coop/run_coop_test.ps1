# DEV TEST for co-op step 1 (docs/COOP_DEV.md): starts our local test server (tools/coop/mini_psnr.py) and one or
# two sandboxed copies of the game, each with its own save folders under -Work (never the player's gamedata),
# GPU power smoothing on from boot, and stops the copies (by their own PIDs) after -Seconds.
#
#   powershell -ExecutionPolicy Bypass -File tools\coop\run_coop_test.ps1 -Work C:\coop_test [-Who both|host|guest]
#       [-Seconds 180] [-Save <a gamedata folder to copy into each sandbox>]
#
# Host = copy A (DES_COOP=host, NP name Host1, P2P port 3658); guest = copy B (DES_COOP=guest, Guest1, 3659).
# Both use DES_COOP_AUTO=1 and a shared mailbox folder. Logs: <Work>\A\logs, <Work>\B\logs, <Work>\psnr.log.
param([Parameter(Mandatory)][string]$Work, [ValidateSet('both','host','guest')][string]$Who = 'both',
      [int]$Seconds = 180, [string]$Save = '', [string]$PadScript = '22:0x0008,26:0x0008,30:0x4000,33:0x4000,36:0x4000,40:0x4000,44:0x4000,48:0x4000')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$exe = Join-Path $Root 'port\build_opt\DemonsSoulsRecomp.exe'
$elf = Join-Path $Root 'game\PS3_GAME\USRDIR\EBOOT.elf'
if (-not (Test-Path $exe) -or -not (Test-Path $elf)) { throw "build or game files missing under $Root" }
$Work = [IO.Path]::GetFullPath($Work)
if ($Work.StartsWith($Root, 'OrdinalIgnoreCase') -and ($Work -match '\\gamedata')) { throw "pick a work folder away from the game's own gamedata" }
New-Item -ItemType Directory -Force "$Work\mailbox" | Out-Null
Remove-Item "$Work\mailbox\*" -ErrorAction SilentlyContinue

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (Test-Path (Join-Path $Root '.venv\Scripts\python.exe')) { $py = Join-Path $Root '.venv\Scripts\python.exe' }
$srv = Start-Process -FilePath $py -ArgumentList @('-I', (Join-Path $PSScriptRoot 'mini_psnr.py'), "$Work\psnr.log") `
       -RedirectStandardOutput "$Work\psnr_stdout.txt" -RedirectStandardError "$Work\psnr_stderr.txt" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2
"test server pid $($srv.Id)"

function Start-Copy([string]$Name, [string]$Mode, [string]$Np, [int]$Port) {
  $T = Join-Path $Work $Name
  New-Item -ItemType Directory -Force "$T\logs" | Out-Null
  if ($Save -and -not (Test-Path "$T\gamedata")) { Copy-Item -Recurse $Save "$T\gamedata" }
  $vars = @{
    PS3_GAMEDATA_ROOT = "$T\gamedata"; PS3_HDD0_ROOT = "$T\hdd0"; PS3_HDD1_ROOT = "$T\hdd1";
    RSX_LIVE_DRAW = '1'; GCM_NO_TITLE_RECYCLE = '1'; SPU_INTERP_UNLIFTED = '1'; SPURS_JC_EVENTS = '0';
    PS3_PRIO_START = '1'; PS3_DEV_FLASH = (Join-Path $Root 'no_flash'); PAD_KEYMAP = 'souls';
    LD_GPU_WARM = '2'; LD_GPU_TS = '1'; DES_SKIP_INTRO = '1'; DES_GOD = '1'; DES_LUA_PRINT = '1';
    PS3_NET_ONLINE = '1'; PSNR_SERVER = '127.0.0.1'; PS3_NP_ONLINE_ID = $Np; PS3_NET_P2P_PORT = "$Port";
    PS3_NP_STRICT_OFFLINE = ''; DES_COOP = $Mode; DES_COOP_AUTO = '1'; DES_COOP_MAILBOX = "$Work\mailbox";
    PAD_SCRIPT = $PadScript; PS3_TITLE = "Demon's Souls - CO-OP TEST $Mode"
  }
  foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $vars[$k], 'Process') }
  $p = Start-Process -FilePath $exe -ArgumentList "`"$elf`"" -WorkingDirectory $T `
       -RedirectStandardOutput "$T\logs\out.txt" -RedirectStandardError "$T\logs\err.txt" -PassThru
  "$Mode copy pid $($p.Id) in $T"
  return $p
}
$procs = @()
if ($Who -ne 'guest') { $procs += Start-Copy 'A' 'host' 'Host1' 3658 }
if ($Who -eq 'both') { Start-Sleep -Seconds 20 }
if ($Who -ne 'host') { $procs += Start-Copy 'B' 'guest' 'Guest1' 3659 }
$deadline = (Get-Date).AddSeconds($Seconds)
foreach ($p in $procs | Where-Object { $_ -is [System.Diagnostics.Process] }) {
  $left = [int][Math]::Max(0, ($deadline - (Get-Date)).TotalMilliseconds)
  if (-not $p.WaitForExit($left)) { Stop-Process -Id $p.Id -Force; "stopped pid $($p.Id)" }
}
Stop-Process -Id $srv.Id -Force -ErrorAction SilentlyContinue
"done. co-op lines:"
Get-ChildItem $Work -Recurse -Filter err.txt | ForEach-Object { "== $($_.FullName)"; Select-String -Path $_.FullName -Pattern '\[coop\]' | ForEach-Object { $_.Line } }
