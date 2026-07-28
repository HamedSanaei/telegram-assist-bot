param(
    [Parameter(Position = 0)][ValidateSet("start", "stop", "restart", "status", "logs", "update", "login", "config-check", "repair", "backup", "uninstall", "purge")]
    [string]$Action = "status",
    [Parameter(Position = 1)][ValidateSet("permissions")]
    [string]$RepairTarget = "permissions",
    [switch]$Yes
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectLine = Get-Content (Join-Path $Root ".env") |
    Where-Object { $_ -like "COMPOSE_PROJECT_NAME=*" } |
    Select-Object -First 1
$Project = $ProjectLine.Split("=", 2)[1]
$Compose = @("compose", "--project-name", $Project, "--env-file", (Join-Path $Root ".env"), "-f", (Join-Path $Root "compose.yaml"))
switch ($Action) {
    "start" { & docker @Compose up -d }
    "stop" { & docker @Compose stop }
    "restart" { & docker @Compose restart }
    "status" { & docker @Compose ps }
    "logs" { & docker @Compose logs --tail 200 -f }
    "update" { & docker @Compose pull; & docker @Compose up -d }
    "login" { & docker @Compose run --rm runtime login --config /app/config/configuration.json }
    "config-check" { & docker @Compose run --rm runtime check --config /app/config/configuration.json }
    "repair" {
        if ($RepairTarget -ne "permissions") {
            throw "Only 'repair permissions' is available in this release."
        }
        $RuntimeUidLine = Get-Content (Join-Path $Root ".env") |
            Where-Object { $_ -like "TAB_RUNTIME_UID=*" } |
            Select-Object -First 1
        $RuntimeGidLine = Get-Content (Join-Path $Root ".env") |
            Where-Object { $_ -like "TAB_RUNTIME_GID=*" } |
            Select-Object -First 1
        $RuntimeUid = if ($RuntimeUidLine) {
            [int]$RuntimeUidLine.Split("=", 2)[1]
        } else { 10001 }
        $RuntimeGid = if ($RuntimeGidLine) {
            [int]$RuntimeGidLine.Split("=", 2)[1]
        } else { 10001 }
        & (Join-Path $Root "permissions.ps1") repair `
            -InstanceDirectory $Root `
            -RuntimeUid $RuntimeUid `
            -RuntimeGid $RuntimeGid
    }
    "backup" {
        $Backup = Join-Path $Root "backups"
        New-Item -ItemType Directory -Force -Path $Backup | Out-Null
        $File = Join-Path $Backup "mongodb-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')).archive.gz"
        & docker @Compose exec -T mongodb sh -c 'mongodump --quiet --archive --gzip --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' > $File
    }
    "uninstall" { & docker @Compose down; Write-Output "Data volumes and instance files were preserved." }
    "purge" {
        if (-not $Yes) { throw "Run manage.ps1 purge -Yes to delete only this instance's volumes." }
        & docker @Compose down --volumes --remove-orphans
    }
}
