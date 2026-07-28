[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("audit", "repair")]
    [string]$Mode = "audit",
    [Parameter(Mandatory = $true)]
    [string]$InstanceDirectory,
    [ValidateRange(1, 2147483647)]
    [int]$RuntimeUid = 10001,
    [ValidateRange(1, 2147483647)]
    [int]$RuntimeGid = 10001
)

$ErrorActionPreference = "Stop"
$InstanceDirectory = [IO.Path]::GetFullPath($InstanceDirectory)
$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Show-PathPermission([string]$Label, [string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Output "$Label missing"
        return
    }
    $Item = Get-Item -LiteralPath $Path
    $Acl = Get-Acl -LiteralPath $Path
    Write-Output "$Label owner=$($Acl.Owner) directory=$($Item.PSIsContainer)"
}

function Show-PermissionAudit {
    Show-PathPermission "instance" $InstanceDirectory
    Show-PathPermission "env" (Join-Path $InstanceDirectory ".env")
    Show-PathPermission "config_directory" (Join-Path $InstanceDirectory "config")
    Show-PathPermission "config" (
        Join-Path $InstanceDirectory "config\configuration.json"
    )
    Show-PathPermission "backups" (Join-Path $InstanceDirectory "backups")
    Show-PathPermission "metadata" (Join-Path $InstanceDirectory "metadata")
    Write-Output (
        "docker_volume_policy runtime_uid={0} runtime_gid={1}" -f
        $RuntimeUid, $RuntimeGid
    )
}

if ($Mode -eq "audit") {
    Show-PermissionAudit
    exit 0
}

foreach ($Directory in @(
    $InstanceDirectory,
    (Join-Path $InstanceDirectory "config"),
    (Join-Path $InstanceDirectory "backups"),
    (Join-Path $InstanceDirectory "metadata")
)) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
    & icacls $Directory /inheritance:r /grant:r "${CurrentIdentity}:(OI)(CI)F" |
        Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply the private instance-directory ACL."
    }
}
foreach ($PrivateFile in @(
    (Join-Path $InstanceDirectory ".env"),
    (Join-Path $InstanceDirectory "config\configuration.json"),
    (Join-Path $InstanceDirectory "metadata\instance.json")
)) {
    if (Test-Path -LiteralPath $PrivateFile -PathType Leaf) {
        & icacls $PrivateFile /inheritance:r /grant:r "${CurrentIdentity}:F" |
            Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to apply a private instance-file ACL."
        }
    }
}
Show-PermissionAudit
