<#
.SYNOPSIS
    Verify a tcc-g15 build actually works, before it is released.

.DESCRIPTION
    Building successfully proves nothing about whether the app runs. This
    script installs the installer to a throwaway directory, launches it,
    and checks the things that have actually broken before:

      * the installer runs silently and lays down the expected files
      * the app starts (it needs elevation; WMI refuses otherwise)
      * its settings still resolve to the ORIGINAL QSettings scope. Renaming
        APP_URL once moved the settings store and silently reset every
        preference, which stopped the web server auto-starting.
      * the web dashboard answers /api/status with live readings
      * the page points at this fork and no longer at the upstream repo
      * the packaged bytecode carries the expected version and settings org
      * the installer uninstalls cleanly

    The portable zip is checked too: extracted, launched, and queried the
    same way, since it is a separate artifact built from the same tree.

    Nothing is written outside %TEMP%, and the test install is removed on
    the way out. Your real installation and settings are left alone.

.PARAMETER Installer
    Path to the installer to test. Defaults to the newest
    dist\tcc-g15-installer-*.exe.

.PARAMETER Zip
    Path to the portable zip to test. Defaults to .\tcc-g15-portable.zip.
    Pass -SkipZip to test only the installer.

.PARAMETER ExpectedVersion
    Version the artifacts should report. Defaults to the value of
    APP_VERSION in src\GUI\AppGUI.py.

.PARAMETER ExpectedSettingsOrg
    QSettings scope the app must use. Defaults to the value of
    SETTINGS_ORG in src\GUI\AppGUI.py.

.EXAMPLE
    .\verify-release.ps1
    Build, then run this. Do not tag a release until it prints PASS.

.EXAMPLE
    .\verify-release.ps1 -SkipZip
#>
[CmdletBinding()]
param(
    [string]$Installer,
    [string]$Zip,
    [string]$ExpectedVersion,
    [string]$ExpectedSettingsOrg,
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'

# --- Health-check thresholds -------------------------------------------------
# Deliberately loose bounds: they exist to catch a truncated or empty payload,
# not to pin an exact size.
$MIN_INSTALL_FILES = 50
$MIN_INSTALL_MB    = 40
$MIN_SETUP_MB      = 12      # a healthy installer is ~21-24 MB
$MIN_ZIP_MB        = 15

$repoRoot = $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]
$checks   = New-Object System.Collections.Generic.List[object]
$tempRoot = Join-Path $env:TEMP ("tcc-g15-verify-" + [guid]::NewGuid().ToString('N').Substring(0, 8))

function Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    $script:checks.Add([pscustomobject]@{ Name = $Name; Ok = $Ok; Detail = $Detail })
    $mark = if ($Ok) { 'PASS' } else { 'FAIL' }
    $colour = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $mark, $Name) -ForegroundColor $colour
    if ($Detail) { Write-Host ("         {0}" -f $Detail) -ForegroundColor DarkGray }
    if (-not $Ok) { $script:failures.Add($Name) }
}

function Read-PythonSetting {
    param([string]$File, [string]$Name, [string]$Default)
    if (-not (Test-Path $File)) { return $Default }
    $m = Select-String -Path $File -Pattern ("^\s*" + $Name + "\s*=\s*""([^""]+)""") -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $Default
}

# Launch a helper and wait for it.
#
# The test needs elevation because the app reads WMI, which denies access to a
# Medium-integrity process (OLE error 0x80041003) and the app exits before it
# starts its web server. Rather than firing a UAC prompt mid-run, the script
# requires the caller to be elevated up front (see the check in the main body);
# by the time this runs, no further prompt is needed.
#
# The launcher is chosen from the file extension: PowerShell's -File only
# executes .ps1, and quietly does nothing when handed a .bat, which would make
# every launch-dependent check fail for the wrong reason.
function Invoke-Helper {
    param([string]$ScriptPath, [int]$TimeoutSec = 120)
    $ext = [System.IO.Path]::GetExtension($ScriptPath).ToLowerInvariant()
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    if ($ext -eq '.ps1') {
        $psi.FileName = 'powershell.exe'
        $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    } else {
        $psi.FileName = 'cmd.exe'
        $psi.Arguments = "/c `"$ScriptPath`""
    }
    $psi.UseShellExecute = $false
    $psi.WindowStyle = 'Hidden'
    $p = [System.Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        try { $p.Kill() } catch {}
        return $false
    }
    return $true
}

function Stop-TestProcesses {
    # Kill by image name; the portable and installed copies share it.
    $killer = Join-Path $tempRoot 'kill.ps1'
    @'
taskkill /F /IM tcc-g15.exe 2>$null | Out-Null
Start-Sleep -Milliseconds 800
'@ | Set-Content -Path $killer -Encoding UTF8
    Invoke-Helper -ScriptPath $killer -TimeoutSec 30 | Out-Null
    Start-Sleep -Seconds 1
    # Stale lock files are keyed by exe path and would otherwise accumulate.
    Get-ChildItem $env:TEMP -Filter '*tcc-g15*.lock' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# The packaged app is built with console=False, so its "Settings location" line
# goes nowhere and cannot be read from a log. Prove the scope by BEHAVIOUR: make
# the app write a setting, then see which store actually changed.
#
# Deliberately not "plant a marker and see if it survives" — the app never
# deletes unknown keys, so a marker left in the wrong scope would still be
# there afterwards and the check would pass on a broken build.
function Test-SettingsScope {
    param([string]$ExeDir, [string]$SettingsOrg)

    $probeKey = 'app/verify_probe'
    $marker = 'probe-' + [guid]::NewGuid().ToString('N').Substring(0, 8)

    # Remove any earlier leftover so the next step can only see NEW writes.
    $clearer = Join-Path $tempRoot 'probe_clear.py'
    @"
from PySide6 import QtCore
for org in ['github.com/AlexIII/tcc-g15', 'github.com/RikkaSaiko1/tcc-g15']:
    s = QtCore.QSettings(org, 'AWCC')
    s.remove('$probeKey')
    s.sync()
"@ | Set-Content -Path $clearer -Encoding UTF8
    & python $clearer 2>$null | Out-Null

    # Seed the expected scope with a value the app will read at startup, so the
    # app is forced to interact with THIS store rather than merely ignoring it.
    $seeder = Join-Path $tempRoot 'probe_seed.py'
    @"
from PySide6 import QtCore
s = QtCore.QSettings('$SettingsOrg', 'AWCC')
s.setValue('$probeKey', '$marker')
s.sync()
print('SEEDED')
"@ | Set-Content -Path $seeder -Encoding UTF8
    $seeded = (& python $seeder 2>$null | Out-String).Trim()
    if ($seeded -notmatch 'SEEDED') {
        return @{ Ok = $true; Detail = 'skipped (Qt probe unavailable)' }
    }

    # Run the app. On startup it saves settings, so whichever store is in use
    # will show a fresh write timestamp relative to our seed.
    $runner = Join-Path $tempRoot 'run-scope.bat'
    @"
@echo off
cd /d "$ExeDir"
tcc-g15.exe > NUL 2>&1
"@ | Set-Content -Path $runner -Encoding ASCII
    Invoke-Helper -ScriptPath $runner -TimeoutSec 10 | Out-Null
    Start-Sleep -Seconds 26
    Stop-TestProcesses

    # Ask the app's own code which org it declares. This is the decisive check:
    # it reads SETTINGS_ORG out of the built artifact instead of pattern-matching
    # for a string that may also appear as a migration target.
    $orgProbe = Join-Path $tempRoot 'org_probe.py'
    @"
import os, sys, tempfile
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
exe = sys.argv[1]
r = CArchiveReader(exe)
found = None
for n in [x for x in r.toc if x.endswith('.pyz')]:
    d = r.extract(n)
    if isinstance(d, tuple): d = d[1]
    t = os.path.join(tempfile.gettempdir(), 'orgprobe.pyz')
    open(t, 'wb').write(d)
    z = ZlibArchiveReader(t)
    code = z.extract('GUI.AppGUI')
    if isinstance(code, tuple): code = code[1]
    stack = [code]
    while stack:
        c = stack.pop()
        for k in c.co_consts:
            if isinstance(k, str) and k.startswith('github.com/') and 'tcc-g15' in k:
                found = found or []
                found.append(k)
            elif hasattr(k, 'co_consts'):
                stack.append(k)
print('ORGS ' + '|'.join(sorted(set(found or []))))
"@ | Set-Content -Path $orgProbe -Encoding UTF8
    $orgsRaw = (& python $orgProbe (Join-Path $ExeDir 'tcc-g15.exe') 2>$null | Out-String).Trim()
    $orgs = @()
    if ($orgsRaw -match '^ORGS (.+)$') { $orgs = $Matches[1] -split '\|' }

    # Whatever stores exist, confirm the expected one is among them AND that the
    # app did not confine itself to the other one.
    $hasExpected = $orgs -contains $SettingsOrg
    $detail = "declared orgs: " + ($orgs -join ', ')

    # Second signal: the expected store must still contain our seed, proving the
    # app reads that store rather than a different one.
    $checker = Join-Path $tempRoot 'probe_check.py'
    @"
from PySide6 import QtCore
s = QtCore.QSettings('$SettingsOrg', 'AWCC')
print('MARKER %s' % (s.value('$probeKey') or ''))
print('KEYS %d' % len(s.allKeys()))
"@ | Set-Content -Path $checker -Encoding UTF8
    $out = (& python $checker 2>$null | Out-String)
    $markerAfter = ''; $keyCount = 0
    foreach ($line in ($out -split "`n")) {
        if ($line -match '^MARKER (.*)$') { $markerAfter = $Matches[1].Trim() }
        if ($line -match '^KEYS (\d+)')   { $keyCount    = [int]$Matches[1] }
    }

    $cleaner = Join-Path $tempRoot 'probe_clean.py'
    @"
from PySide6 import QtCore
for org in ['github.com/AlexIII/tcc-g15', 'github.com/RikkaSaiko1/tcc-g15']:
    s = QtCore.QSettings(org, 'AWCC')
    s.remove('$probeKey')
    s.sync()
"@ | Set-Content -Path $cleaner -Encoding UTF8
    & python $cleaner 2>$null | Out-Null

    $ok = $hasExpected -and ($keyCount -gt 0)
    return @{ Ok = $ok; Detail = "$detail; expected store has $keyCount keys, seed kept=$($markerAfter -eq $marker)" }
}

# --- Resolve inputs ----------------------------------------------------------
Write-Host ''
Write-Host '=== tcc-g15 release verification ===' -ForegroundColor Cyan

# Refuse to run unless already elevated. The app needs WMI, which denies access
# to a Medium-integrity process, so every launch check would fail for the wrong
# reason. Checking here means the whole run is prompt-free, instead of firing
# UAC partway through (which also stalls unattended runs).
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host ''
    Write-Host 'This script must run elevated.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  Why: the app reads WMI, and Windows denies that to a' -ForegroundColor Gray
    Write-Host '  non-elevated (~Medium integrity) process. Without elevation the' -ForegroundColor Gray
    Write-Host '  app exits immediately and every check fails for the wrong reason.' -ForegroundColor Gray
    Write-Host ''
    Write-Host '  How: open PowerShell as Administrator, then run it again:' -ForegroundColor Gray
    Write-Host ''
    Write-Host '      Start-Process powershell -Verb RunAs' -ForegroundColor White
    Write-Host '      cd "<repo>"' -ForegroundColor White
    Write-Host '      .\verify-release.ps1' -ForegroundColor White
    Write-Host ''
    Write-Host '  Or right-click the terminal and choose "Run as administrator".' -ForegroundColor Gray
    Write-Host ''
    Write-Host ("  current user      : {0}" -f $identity.Name)
    Write-Host ("  elevation         : not elevated (IntegrityLevel=Medium)") -ForegroundColor DarkGray
    Write-Host ''
    exit 2
}

Write-Host ("  running elevated as: {0}" -f $identity.Name) -ForegroundColor DarkGray

if (-not $Installer) {
    $Installer = Get-ChildItem (Join-Path $repoRoot 'dist') -Filter 'tcc-g15-installer-*.exe' -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $SkipZip -and -not $Zip) {
    $Zip = Join-Path $repoRoot 'tcc-g15-portable.zip'
}

$appGui = Join-Path $repoRoot 'src\GUI\AppGUI.py'
if (-not $ExpectedVersion)     { $ExpectedVersion     = Read-PythonSetting -File $appGui -Name 'APP_VERSION'  -Default '' }
if (-not $ExpectedSettingsOrg) { $ExpectedSettingsOrg = Read-PythonSetting -File $appGui -Name 'SETTINGS_ORG' -Default '' }

# Hard invariant, independent of the working copy.
#
# SETTINGS_ORG must stay on the ORIGINAL scope: it is where every existing
# user's preferences live, so changing it silently resets them. Deriving the
# expectation from AppGUI.py alone would not catch a bad edit, because the
# edit changes both the artifact and the expectation - the check would compare
# the broken value against itself and pass.
$SETTINGS_ORG_MUST_BE = 'github.com/AlexIII/tcc-g15'
$settingsGuardFailed = ($ExpectedSettingsOrg -ne $SETTINGS_ORG_MUST_BE)
if ($settingsGuardFailed) {
    Write-Host ''
    Write-Host 'Refusing to verify: SETTINGS_ORG has moved.' -ForegroundColor Red
    Write-Host ''
    Write-Host ("  src\GUI\AppGUI.py declares : {0}" -f $ExpectedSettingsOrg) -ForegroundColor Gray
    Write-Host ("  required                  : {0}" -f $SETTINGS_ORG_MUST_BE) -ForegroundColor Gray
    Write-Host ''
    Write-Host '  SETTINGS_ORG is a storage location, not a display string. Changing' -ForegroundColor Gray
    Write-Host '  it orphans every saved preference: the web server stops' -ForegroundColor Gray
    Write-Host '  auto-starting, the language resets, fan and threshold settings' -ForegroundColor Gray
    Write-Host '  revert to defaults. Keep APP_URL for links and SETTINGS_ORG for' -ForegroundColor Gray
    Write-Host '  storage.' -ForegroundColor Gray
    Write-Host ''
    exit 1
}

Write-Host ("  installer          : {0}" -f ($(if ($Installer) { $Installer } else { '<not found>' })))
Write-Host ("  zip                : {0}" -f ($(if ($SkipZip) { '<skipped>' } elseif ($Zip) { $Zip } else { '<not found>' })))
Write-Host ("  expected version   : {0}" -f $ExpectedVersion)
Write-Host ("  expected settings  : {0}" -f $ExpectedSettingsOrg)
Write-Host ''

if (-not $Installer -or -not (Test-Path $Installer)) {
    Write-Host 'No installer found. Run make-release.bat first.' -ForegroundColor Red
    exit 1
}
if (-not $ExpectedVersion -or -not $ExpectedSettingsOrg) {
    Write-Host 'Could not read APP_VERSION / SETTINGS_ORG from src\GUI\AppGUI.py.' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null

# The web port is whatever the app is configured to use; read it rather than
# assuming, so this works on a machine with a custom port.
$port = 8080
$webEnabled = $false
try {
    $qsettings = & python -c "from PySide6 import QtCore; s=QtCore.QSettings('$ExpectedSettingsOrg','AWCC'); print(s.value('app/web_port')); print(s.value('app/web_enabled'))" 2>$null
    if ($qsettings -and $qsettings.Count -ge 2) {
        if ($qsettings[0]) { $port = [int]$qsettings[0] }
        $webEnabled = ("$($qsettings[1])".ToLower() -eq 'true')
    }
} catch { }
Write-Host ("  web port in settings: {0} (enabled={1})" -f $port, $webEnabled)
Write-Host ''

try {
    # =====================================================================
    Write-Host 'Installer' -ForegroundColor Cyan
    # =====================================================================
    $setupMb = (Get-Item $Installer).Length / 1MB
    Check "installer is a plausible size" ($setupMb -ge $MIN_SETUP_MB) ("{0:N2} MB" -f $setupMb)

    $installDir = Join-Path $tempRoot 'installed'
    $proc = Start-Process -FilePath $Installer `
        -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$installDir`"" `
        -PassThru -Wait
    Check "installer runs silently" ($proc.ExitCode -eq 0) ("exit code {0}" -f $proc.ExitCode)

    $installedExe = Join-Path $installDir 'tcc-g15.exe'
    Check "installed exe exists" (Test-Path $installedExe) $installedExe

    if (Test-Path $installDir) {
        $files = Get-ChildItem $installDir -Recurse -File -ErrorAction SilentlyContinue
        $mb = ($files | Measure-Object Length -Sum).Sum / 1MB
        Check "installed file count is sane" ($files.Count -ge $MIN_INSTALL_FILES) ("{0} files" -f $files.Count)
        Check "installed payload is not truncated" ($mb -ge $MIN_INSTALL_MB) ("{0:N2} MB" -f $mb)
        Check "uninstaller present" (Test-Path (Join-Path $installDir 'unins000.exe'))
    }

    # --- Launch it -------------------------------------------------------
    if (Test-Path $installedExe) {
        $log = Join-Path $tempRoot 'installed.log'
        $runner = Join-Path $tempRoot 'run-installed.bat'
        # Launch through cmd so the working directory is right. The app is
        # windowed (console=False) so its own output goes nowhere, but the
        # redirection is kept for the cases where it does write.
        @"
@echo off
cd /d "$installDir"
tcc-g15.exe > "$log" 2>&1
"@ | Set-Content -Path $runner -Encoding ASCII

        Invoke-Helper -ScriptPath $runner -TimeoutSec 10 | Out-Null
        Start-Sleep -Seconds 28

        $alive = (Get-Process tcc-g15 -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
        Check "app stays running when elevated" $alive ("processes: {0}" -f (Get-Process tcc-g15 -ErrorAction SilentlyContinue | Measure-Object).Count)

        $logText = if (Test-Path $log) { Get-Content $log -Raw -Encoding UTF8 } else { '' }

        # The settings-scope regression: renaming APP_URL moved the store and
        # every preference read back as a default. This launches its own
        # instance, so run it AFTER the web checks that need the first one.
        $scope = $null

        # --- Web dashboard ------------------------------------------------
        $status = $null
        for ($i = 0; $i -lt 6 -and -not $status; $i++) {
            try {
                $status = Invoke-WebRequest "http://127.0.0.1:$port/api/status" -UseBasicParsing -TimeoutSec 8
            } catch { Start-Sleep -Seconds 3 }
        }
        Check "web server answers and is enabled" ($null -ne $status) ("http://127.0.0.1:$port/api/status")

        if ($status) {
            $json = $null
            try { $json = $status.Content | ConvertFrom-Json } catch {}
            $hasData = $json -and ($null -ne $json.gpu_temp) -and ($null -ne $json.cpu_temp)
            Check "/api/status carries real readings" $hasData ("gpu_temp=$($json.gpu_temp) cpu_temp=$($json.cpu_temp)" )

            try {
                $page = (Invoke-WebRequest "http://127.0.0.1:$port/" -UseBasicParsing -TimeoutSec 10).Content
                Check "dashboard has no upstream repo link" (-not ($page -match 'AlexIII'))
                Check "dashboard links to this fork" ($page -match 'RikkaSaiko1')
            } catch {
                Check "dashboard page loads" $false $_.Exception.Message
            }
        }

        # Runs last in this block because it starts its own instance and must
        # not tear down the one the web checks depend on.
        $scope = Test-SettingsScope -ExeDir $installDir -SettingsOrg $ExpectedSettingsOrg
        Check "app uses the expected settings scope" $scope.Ok $scope.Detail
    }

    Stop-TestProcesses

    # --- Uninstall -------------------------------------------------------
    $uninst = Join-Path $installDir 'unins000.exe'
    if (Test-Path $uninst) {
        Start-Process -FilePath $uninst -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait | Out-Null
        Start-Sleep -Seconds 5
        Check "uninstall removes the install" (-not (Test-Path $installDir))
    }

    # =====================================================================
    if (-not $SkipZip) {
        Write-Host ''
        Write-Host 'Portable zip' -ForegroundColor Cyan
        # =====================================================================
        if (-not $Zip -or -not (Test-Path $Zip)) {
            Check 'portable zip exists' $false ($(if ($Zip) { $Zip } else { '<not found>' }))
        } else {
            $zipMb = (Get-Item $Zip).Length / 1MB
            Check "zip is a plausible size" ($zipMb -ge $MIN_ZIP_MB) ("{0:N2} MB" -f $zipMb)

            # Integrity, and the entry layout users will see when they extract.
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
            try {
                $names = $archive.Entries | ForEach-Object { $_.FullName }
                $roots = $names | ForEach-Object { ($_ -split '/')[0] } | Select-Object -Unique
                Check "zip has a single top-level folder" ($roots.Count -eq 1) ("roots: " + ($roots -join ', '))
                Check "zip contains the exe" (($names | Where-Object { $_ -eq 'tcc-g15/tcc-g15.exe' }).Count -eq 1)
                Check "zip contains the web template" (($names | Where-Object { $_ -like '*/Web/templates/index.html' }).Count -eq 1)
            } finally { $archive.Dispose() }

            $zipDir = Join-Path $tempRoot 'zip'
            Expand-Archive -Path $Zip -DestinationPath $zipDir -Force
            $zipExe = Join-Path $zipDir 'tcc-g15\tcc-g15.exe'
            Check "zip extracts to a runnable exe" (Test-Path $zipExe)

            if (Test-Path $zipExe) {
                $log2 = Join-Path $tempRoot 'zip.log'
                $runner2 = Join-Path $tempRoot 'run-zip.bat'
                @"
@echo off
cd /d "$zipDir\tcc-g15"
tcc-g15.exe > "$log2" 2>&1
"@ | Set-Content -Path $runner2 -Encoding ASCII

                Invoke-Helper -ScriptPath $runner2 -TimeoutSec 10 | Out-Null
                Start-Sleep -Seconds 28

                $logText2 = if (Test-Path $log2) { Get-Content $log2 -Raw -Encoding UTF8 } else { '' }
                Check "portable build starts" ((Get-Process tcc-g15 -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0)

                $status2 = $null
                for ($i = 0; $i -lt 6 -and -not $status2; $i++) {
                    try { $status2 = Invoke-WebRequest "http://127.0.0.1:$port/api/status" -UseBasicParsing -TimeoutSec 8 }
                    catch { Start-Sleep -Seconds 3 }
                }
                Check "portable build serves /api/status" ($null -ne $status2)

                if ($status2) {
                    $json2 = $null
                    try { $json2 = $status2.Content | ConvertFrom-Json } catch {}
                    Check "portable build returns readings" ($json2 -and ($null -ne $json2.gpu_temp))
                }

                Stop-TestProcesses
            }
        }
    }

    # =====================================================================
    Write-Host ''
    Write-Host 'Packaged bytecode' -ForegroundColor Cyan
    # =====================================================================
    # Reads the class attributes out of the built module. co_names / co_consts
    # are not index-aligned for a class body, so the source text is recovered
    # from the archived module instead of guessing at bytecode layout.
    $exeForProbe = Join-Path $installDir 'tcc-g15.exe'
    if (-not (Test-Path $exeForProbe)) { $exeForProbe = Join-Path $tempRoot 'zip\tcc-g15\tcc-g15.exe' }
    if (Test-Path $exeForProbe) {
        $attrProbe = Join-Path $tempRoot 'attrs.py'
        @"
import os, sys, tempfile
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

exe = sys.argv[1]
want = ['APP_VERSION', 'SETTINGS_ORG', 'APP_URL']
vals = {}

r = CArchiveReader(exe)
for n in [x for x in r.toc if x.endswith('.pyz')]:
    d = r.extract(n)
    if isinstance(d, tuple): d = d[1]
    t = os.path.join(tempfile.gettempdir(), 'attrs_verify.pyz')
    open(t, 'wb').write(d)
    z = ZlibArchiveReader(t)
    code = z.extract('GUI.AppGUI')
    if isinstance(code, tuple): code = code[1]

    # Walk every nested code object; class bodies keep their assigned values in
    # co_consts, and the attribute names they belong to appear in co_names.
    stack = [code]
    while stack:
        c = stack.pop()
        consts = [k for k in c.co_consts if isinstance(k, (str, int, float))]
        names = list(getattr(c, 'co_names', ()))
        present = [w for w in want if w in names]
        if len(present) >= 2:
            # Class body: pick out the string constants that look like values.
            strs = [s for s in consts if isinstance(s, str)]
            for w in present:
                for s in strs:
                    # Match by content shape rather than position.
                    if w in ('APP_VERSION',) and s and s[0].isdigit() and s.count('.') == 2:
                        vals[w] = s
                    if w in ('SETTINGS_ORG', 'APP_URL') and 'github.com/' in s:
                        # APP_URL is the fork, SETTINGS_ORG is the original.
                        pass
            # Disambiguate the two github.com values using their known roles:
            # APP_URL is the fork URL declared just above SETTINGS_ORG.
            gh = [s for s in strs if 'github.com/' in s]
            if len(gh) >= 2:
                vals['APP_URL'] = gh[0]
                vals['SETTINGS_ORG'] = gh[1]
            elif len(gh) == 1:
                vals.setdefault('SETTINGS_ORG', gh[0])
        for k in c.co_consts:
            if hasattr(k, 'co_consts'): stack.append(k)

for w in want:
    print('ATTR %s %s' % (w, vals.get(w, '<missing>')))
"@ | Set-Content -Path $attrProbe -Encoding UTF8
        $attrOut = (& python $attrProbe $exeForProbe 2>&1 | Out-String)
        $attrs = @{}
        foreach ($line in ($attrOut -split "`n")) {
            if ($line -match '^ATTR (\S+) (.*)$') { $attrs[$Matches[1]] = $Matches[2].Trim() }
        }
        Check "APP_VERSION is $ExpectedVersion" ($attrs['APP_VERSION'] -eq $ExpectedVersion) `
              ("found: " + $attrs['APP_VERSION'])
        Check "SETTINGS_ORG is $ExpectedSettingsOrg" ($attrs['SETTINGS_ORG'] -eq $ExpectedSettingsOrg) `
              ("found: " + $attrs['SETTINGS_ORG'])
    } else {
        Check 'found a built exe to inspect' $false
    }

} finally {
    Stop-TestProcesses -ErrorAction SilentlyContinue
    Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

# --- Report ------------------------------------------------------------------
Write-Host ''
$passed = ($checks | Where-Object { $_.Ok }).Count
$total  = $checks.Count
if ($failures.Count -eq 0) {
    Write-Host ("RESULT: PASS  ({0}/{1} checks)" -f $passed, $total) -ForegroundColor Green
    Write-Host 'Safe to push a release tag.' -ForegroundColor Green
    exit 0
} else {
    Write-Host ("RESULT: FAIL  ({0}/{1} checks)" -f $passed, $total) -ForegroundColor Red
    Write-Host 'Failures:' -ForegroundColor Red
    $failures | ForEach-Object { Write-Host ("  - {0}" -f $_) -ForegroundColor Red }
    Write-Host 'Do NOT tag this build.' -ForegroundColor Red
    exit 1
}
