<#
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   package/build.ps1
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   构建脚本：环境检查、PyInstaller 打包与启动冒烟测试。

.SYNOPSIS
    把 RoomSenseBoard 打包成可直接双击运行的单个 exe。
.EXAMPLE
    .\package\build.ps1 -Mode onedir -Zip
#>

[CmdletBinding()]
param(
    [ValidateSet('onefile', 'onedir')]
    [string]$Mode = 'onefile',

    [string]$DistPath = 'dist',

    [switch]$Zip,

    [switch]$SkipTests,

    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'

$AppName      = 'RoomSenseBoard'
$PackagingDir = $PSScriptRoot
$ProjectRoot  = Split-Path -Parent $PackagingDir
$SpecFile     = Join-Path $PackagingDir 'roomsenseboard.spec'
$VersionFile  = Join-Path $PackagingDir 'version_info.txt'
$BuildDir     = Join-Path $ProjectRoot 'build\pyinstaller'
$DistDir      = if ([System.IO.Path]::IsPathRooted($DistPath)) { $DistPath } else { Join-Path $ProjectRoot $DistPath }
$ReleaseDir   = Join-Path $DistDir $AppName
$ReleaseExe   = Join-Path $ReleaseDir ($AppName + '.exe')
$VenvPython   = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host '==> ' -ForegroundColor Cyan -NoNewline
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Detail {
    param([string]$Message)
    Write-Host ('    ' + $Message) -ForegroundColor DarkGray
}

function Remove-BuildPath {
    # 只删除项目目录内的路径，参数写错时不会误删别处。
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = ([System.IO.Path]::GetFullPath($ProjectRoot)).TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw ('拒绝删除项目目录之外的路径：' + $full)
    }
    if (Test-Path -LiteralPath $full) {
        try {
            Remove-Item -LiteralPath $full -Recurse -Force
        }
        catch {
            throw ('无法删除 ' + $full + '：可能有 RoomSenseBoard.exe 正在运行。请先关闭它，或用 -DistPath 换一个输出目录。' + [Environment]::NewLine + $_.Exception.Message)
        }
    }
}

function Resolve-Python {
    if (Test-Path -LiteralPath $VenvPython) { return $VenvPython }
    $command = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if (-not $command) {
        throw '找不到 Python：请先在项目根目录执行 uv sync 创建 .venv，或把 python 加入 PATH。'
    }
    Write-Detail ('未找到 .venv，改用 ' + $command.Source)
    return $command.Source
}

function Assert-PythonVersion {
    param([string]$Python)

    $raw = (& $Python -c "import sys; print('%d.%d' % sys.version_info[:2])" | Out-String).Trim()
    $parts = $raw.Split('.')
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        throw ('Python 版本过低：' + $raw + '（本项目要求 3.10+）')
    }
    Write-Detail ('Python ' + $raw)
}

function Test-PyInstaller {
    param([string]$Python)

    # 原生命令写 stderr 会触发 ErrorActionPreference，这里只看退出码。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -c 'import PyInstaller' 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Ensure-PyInstaller {
    param([string]$Python)

    if (Test-PyInstaller -Python $Python) {
        $version = (& $Python -c 'import PyInstaller; print(PyInstaller.__version__)' | Out-String).Trim()
        Write-Detail ('PyInstaller ' + $version + ' 已就绪')
        return
    }

    Write-Detail '未安装 PyInstaller，开始安装 …'
    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uv) {
        Write-Detail '使用 uv sync 安装开发依赖'
        & $uv.Source sync --project $ProjectRoot
    }
    else {
        Write-Detail '未找到 uv，改用 pip 安装'
        & $Python -m pip install --quiet --disable-pip-version-check 'pyinstaller>=6.0'
    }
    if ($LASTEXITCODE -ne 0) {
        throw '安装 PyInstaller 失败，请手动执行：uv sync'
    }
    if (-not (Test-PyInstaller -Python $Python)) {
        throw ('PyInstaller 仍不可用，请检查虚拟环境：' + $Python)
    }
}

function Get-AppVersion {
    $initFile = Join-Path $ProjectRoot 'roomsense\__init__.py'
    if (-not (Test-Path -LiteralPath $initFile)) {
        throw ('找不到版本号来源：' + $initFile)
    }
    $text = Get-Content -LiteralPath $initFile -Raw -Encoding UTF8
    if ($text -notmatch "__version__\s*=\s*'([^']+)'") {
        throw ('无法从 ' + $initFile + ' 解析 __version__')
    }
    return $Matches[1]
}

function Sync-VersionInfo {
    # 让 exe 的版本号跟随 roomsense/__init__.py。
    param(
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$Path
    )

    # 0.1.0 / 0.1.0rc1 之类都归一化成四段数字。
    $numeric = $Version -replace '[^0-9.].*$', ''
    $parts = @($numeric.Split('.') | Where-Object { $_ -ne '' })
    while ($parts.Count -lt 4) { $parts += '0' }
    $tuple = '(' + ($parts[0..3] -join ', ') + ')'

    $original = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $updated = $original
    $updated = $updated -replace 'filevers=\([^)]*\)', ('filevers=' + $tuple)
    $updated = $updated -replace 'prodvers=\([^)]*\)', ('prodvers=' + $tuple)
    # 整段替换字面量：$1 后紧跟数字会被当成第 10 组引用。
    $updated = $updated -replace "StringStruct\('FileVersion',\s*'[^']*'\)", ("StringStruct('FileVersion', '" + $Version + "')")
    $updated = $updated -replace "StringStruct\('ProductVersion',\s*'[^']*'\)", ("StringStruct('ProductVersion', '" + $Version + "')")

    if ($updated -ne $original) {
        Set-Content -LiteralPath $Path -Value $updated -Encoding UTF8 -NoNewline
        Write-Detail ('已把 version_info.txt 的版本号同步为 ' + $Version)
    }
}

function Invoke-BootTest {
    # 在临时目录里跑一遍首次运行：资源要释放、面板要能打开。
    param([string]$SourceDir)

    $sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ('rsb-boot-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $logDir = [System.IO.Path]::GetTempPath()
    $stdout = Join-Path $logDir ((Split-Path -Leaf $sandbox) + '.out.log')
    $stderr = $stdout + '.err'
    $process = $null
    try {
        New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
        Copy-Item -Path (Join-Path $SourceDir '*') -Destination $sandbox -Recurse -Force
        $exe = Join-Path $sandbox ($AppName + '.exe')
        Write-Detail ('临时目录 ' + $sandbox)
        $process = Start-Process -FilePath $exe -ArgumentList '--port', '0' -WorkingDirectory $sandbox -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -NoNewWindow

        $address = $null
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Path -LiteralPath $stdout) {
                $text = Get-Content -LiteralPath $stdout -Raw -Encoding UTF8
                if ($text -match 'http://[0-9.]+:(\d+)/') {
                    $address = $Matches[0]
                    break
                }
            }
            if ($process.HasExited) { break }
        }
        if (-not $address) {
            $log = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
            $err = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
            throw ('exe 没有在预期时间内启动。' + [Environment]::NewLine + $log + [Environment]::NewLine + $err)
        }

        $expected = @('conf\config.json', 'data\assets.json', 'web\index.html', 'doc\protocol.md', 'README.md', 'LICENSE')
        $missing = @($expected | Where-Object { -not (Test-Path -LiteralPath (Join-Path $sandbox $_)) })
        if ($missing.Count -gt 0) {
            throw ('首次运行没有释放这些资源：' + ($missing -join '、'))
        }

        $config = Get-Content -LiteralPath (Join-Path $sandbox 'conf\config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($config.simulator.enable -ne $false) {
            throw '释放出来的默认配置仍然启用了模拟器（config.simulator.enable 应为 false）'
        }

        $response = Invoke-WebRequest -Uri $address -UseBasicParsing -TimeoutSec 20
        if ($response.StatusCode -ne 200 -or $response.Content -notmatch '<title>') {
            throw ('面板页面异常：HTTP ' + $response.StatusCode)
        }
        Write-Detail ('首次运行释放正常，面板 ' + $address + ' 可访问')
    }
    finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 600
        }
        Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

Write-Step '检查打包环境'
$python = Resolve-Python
Assert-PythonVersion -Python $python
Ensure-PyInstaller -Python $python

Write-Step '读取版本号'
$version = Get-AppVersion
Sync-VersionInfo -Version $version -Path $VersionFile
Write-Detail ('版本 ' + $version)

if ($SkipTests) {
    Write-Step '跳过单元测试（-SkipTests）'
}
else {
    Write-Step '运行单元测试'
    Push-Location -LiteralPath $ProjectRoot
    try {
        & $python -m unittest discover -s tests -q
        $testCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($testCode -ne 0) {
        throw ('单元测试未通过（退出码 ' + $testCode + '），已中止打包；确实要跳过请加 -SkipTests')
    }
}

Write-Step '清理旧产物'
Remove-BuildPath -Path $BuildDir
Remove-BuildPath -Path $ReleaseDir
Remove-BuildPath -Path (Join-Path $DistDir ($AppName + '.exe'))
Write-Detail ('中间目录 ' + $BuildDir)
Write-Detail ('输出目录 ' + $ReleaseDir)

Write-Step ('调用 PyInstaller（' + $Mode + '）')
$env:ROOMSENSE_ONEFILE = if ($Mode -eq 'onefile') { '1' } else { '0' }
try {
    & $python -m PyInstaller --noconfirm --clean --distpath $DistDir --workpath $BuildDir $SpecFile
    if ($LASTEXITCODE -ne 0) {
        throw ('PyInstaller 构建失败，退出码 ' + $LASTEXITCODE + '；详细分析日志在 ' + $BuildDir)
    }
}
finally {
    Remove-Item Env:\ROOMSENSE_ONEFILE -ErrorAction SilentlyContinue
}

Write-Step '整理发布目录'
if ($Mode -eq 'onefile') {
    $looseExe = Join-Path $DistDir ($AppName + '.exe')
    if (-not (Test-Path -LiteralPath $looseExe)) {
        throw ('没有找到单文件产物：' + $looseExe)
    }
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    Move-Item -LiteralPath $looseExe -Destination $ReleaseExe -Force
}
if (-not (Test-Path -LiteralPath $ReleaseExe)) {
    throw ('没有找到可执行文件：' + $ReleaseExe)
}

# 分发包只放 exe 与 LICENSE，其余资源由首次运行释放。
$licenseFile = Join-Path $ProjectRoot 'LICENSE'
if (Test-Path -LiteralPath $licenseFile) {
    Copy-Item -LiteralPath $licenseFile -Destination (Join-Path $ReleaseDir 'LICENSE') -Force
}
Write-Detail '发布目录只保留 exe 与 LICENSE，其余资源由首次运行释放'

if ($SkipSmokeTest) {
    Write-Step '跳过冒烟测试（-SkipSmokeTest）'
}
else {
    Write-Step '冒烟测试'
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $ReleaseDir
    try {
        $helpText = (& $ReleaseExe --help 2>&1 | Out-String)
        $helpCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $previous
    }
    if ($helpCode -ne 0 -or $helpText -notmatch 'RoomSenseBoard') {
        throw ('--help 冒烟测试失败（退出码 ' + $helpCode + '）：' + [Environment]::NewLine + $helpText)
    }
    Write-Detail 'exe 可以启动并响应 --help'

    Invoke-BootTest -SourceDir $ReleaseDir
}

if ($Zip) {
    Write-Step '生成压缩包'
    $zipPath = Join-Path $DistDir ($AppName + '-' + $version + '-' + $Mode + '-win64.zip')
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
    Compress-Archive -LiteralPath $ReleaseDir -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Detail $zipPath
}

$totalBytes = (Get-ChildItem -LiteralPath $ReleaseDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
$sizeMb = [math]::Round($totalBytes / 1MB, 1)

Write-Host ''
Write-Host '打包完成' -ForegroundColor Green
Write-Host ('  版本：' + $version + '（' + $Mode + '）')
Write-Host ('  目录：' + $ReleaseDir + '（' + $sizeMb + ' MB）')
Write-Host ('  启动：双击 ' + $ReleaseExe)
Write-Host '  首次运行会在 exe 旁边释放 conf/、data/、web/ 等资源并打开浏览器；'
Write-Host '  以后换版本只要用新的 exe 覆盖旧的，配置、数据库与改过的文件都会保留。'
if ($Zip) {
    Write-Host ('  压缩包：' + $zipPath)
}
