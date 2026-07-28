[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Instance,
    [ValidateRange(1, 3650)][int]$RetentionDays = 2,
    [string]$InstallDirectory,
    [Alias("Version")][string]$Image = "ghcr.io/hamedsanaei/telegram-assist-bot:1.1.2",
    [string]$MongoDbImage = "mongo:7.0.32",
    [ValidateRange(1, 2147483647)][int]$RuntimeUid = 10001,
    [ValidateRange(1, 2147483647)][int]$RuntimeGid = 10001,
    [string]$AdminUserIds,
    [string]$SourceUsernames,
    [switch]$NonInteractive,
    [switch]$Update,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$BaseUrl = if ($env:TAB_INSTALL_BASE_URL) {
    $env:TAB_INSTALL_BASE_URL
} else {
    "https://raw.githubusercontent.com/HamedSanaei/telegram-assist-bot/main"
}

if ($Help) {
    Write-Output "install.ps1 -Instance NAME [-RetentionDays 2] [-InstallDirectory PATH] [-Image IMAGE:TAG] [-MongoDbImage mongo:7.0.32] [-NonInteractive] [-Update] [-DryRun]"
    exit 0
}
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "PowerShell 5.1 or newer is required."
}
if ($Instance -notmatch "^[a-z][a-z0-9-]{0,31}$") {
    throw "Instance must match [a-z][a-z0-9-]{0,31}."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "A 64-bit Windows installation is required."
}

$InstallDirectory = if ($InstallDirectory) {
    [IO.Path]::GetFullPath($InstallDirectory)
} else {
    Join-Path $env:LOCALAPPDATA "TelegramAssistBot\instances\$Instance"
}
$Project = "telegram-assist-$Instance"
$Database = "telegram_assist_$($Instance.Replace('-', '_'))"
$EnvPath = Join-Path $InstallDirectory ".env"
$ConfigPath = Join-Path $InstallDirectory "config\configuration.json"
$ExistingInstance = Test-Path -LiteralPath $ConfigPath -PathType Leaf

function Get-EnvValue([string]$Path, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $Prefix = "$Name="
    $Line = Get-Content -LiteralPath $Path -Encoding utf8 |
        Where-Object { $_.StartsWith($Prefix, [StringComparison]::Ordinal) } |
        Select-Object -First 1
    if (-not $Line) { return $null }
    return $Line.Substring($Prefix.Length)
}

if (Test-Path -LiteralPath $EnvPath -PathType Leaf) {
    $ExistingMongoDbImage = Get-EnvValue $EnvPath "TAB_MONGODB_IMAGE"
    $MongoDbImage = if ($ExistingMongoDbImage) {
        $ExistingMongoDbImage
    } else {
        "mongo:7.0.32"
    }
    $RuntimeUidText = Get-EnvValue $EnvPath "TAB_RUNTIME_UID"
    $RuntimeGidText = Get-EnvValue $EnvPath "TAB_RUNTIME_GID"
    if ($RuntimeUidText) { $RuntimeUid = [int]$RuntimeUidText }
    if ($RuntimeGidText) { $RuntimeGid = [int]$RuntimeGidText }
}

if ($DryRun) {
    if (-not $AdminUserIds) {
        $AdminUserIds = if ($env:TAB_ADMIN_USER_IDS) {
            $env:TAB_ADMIN_USER_IDS
        } else {
            $env:TAB_ADMIN_USER_ID
        }
    }
    if (-not $SourceUsernames) {
        $SourceUsernames = if ($env:TAB_SOURCE_USERNAMES) {
            $env:TAB_SOURCE_USERNAMES
        } else {
            $env:TAB_SOURCE_USERNAME
        }
    }
    [ordered]@{
        instance = $Instance
        project = $Project
        database = $Database
        install_directory = $InstallDirectory
        image = $Image
        mongodb_image = $MongoDbImage
        runtime_uid = $RuntimeUid
        runtime_gid = $RuntimeGid
        retention_days = $RetentionDays
        admin_count = @($AdminUserIds -split ",").Count
        admin_user_ids = $AdminUserIds
        source_count = @($SourceUsernames -split ",").Count
        source_usernames = $SourceUsernames
        planned_manager_command = "tabctl --instance $Instance status"
    } | ConvertTo-Json
    exit 0
}

function Ensure-Docker {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        docker compose version | Out-Null
        return
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Docker Desktop is missing. Install Docker Desktop with WSL2, then rerun this command."
    }
    $wsl = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
    $vm = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform
    if ($wsl.State -ne "Enabled" -or $vm.State -ne "Enabled") {
        New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
        "resume" | Set-Content -Encoding utf8 -Path (Join-Path $InstallDirectory ".resume")
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart | Out-Null
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart | Out-Null
        throw "WSL2 prerequisites were enabled. Restart Windows, then rerun the same installer command."
    }
    winget install --exact --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktop) {
        Start-Process -FilePath $dockerDesktop
    }
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        $ready = Get-Command docker -ErrorAction SilentlyContinue
    } while (-not $ready -and [DateTime]::UtcNow -lt $deadline)
    if (-not $ready) {
        throw "Docker Desktop installation requires a restart. Rerun this installer afterward."
    }
}

function Read-Required([string]$Name, [string]$Prompt, [switch]$Secret) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and -not $NonInteractive) {
        if ($Secret) {
            $secure = Read-Host $Prompt -AsSecureString
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $value = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        } else {
            $value = Read-Host $Prompt
        }
    }
    if (-not $value) { throw "Missing required value: $Name" }
    return $value
}

Ensure-Docker
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDirectory "config") | Out-Null
Invoke-WebRequest "$BaseUrl/compose.yaml" -OutFile (Join-Path $InstallDirectory "compose.yaml")
Invoke-WebRequest "$BaseUrl/config/configuration.example.json" -OutFile (Join-Path $InstallDirectory "configuration.example.json")
Invoke-WebRequest "$BaseUrl/deploy/manage.ps1" -OutFile (Join-Path $InstallDirectory "manage.ps1")
Invoke-WebRequest "$BaseUrl/deploy/permissions.ps1" -OutFile (
    Join-Path $InstallDirectory "permissions.ps1"
)
& (Join-Path $InstallDirectory "permissions.ps1") repair `
    -InstanceDirectory $InstallDirectory `
    -RuntimeUid $RuntimeUid `
    -RuntimeGid $RuntimeGid

if ($ExistingInstance -and -not $Update) {
    throw "Instance already exists; use -Update to refresh assets without overwriting Config."
}

if (-not (Test-Path $EnvPath)) {
    $ApiId = Read-Required "TAB_TELEGRAM_API_ID" "Telegram API ID" -Secret
    $ApiHash = Read-Required "TAB_TELEGRAM_API_HASH" "Telegram API Hash" -Secret
    $Phone = Read-Required "TAB_TELEGRAM_PHONE_NUMBER" "Telegram phone number" -Secret
    $BotToken = Read-Required "TAB_TELEGRAM_BOT_TOKEN" "Telegram Bot Token" -Secret
    $ApprovalChat = Read-Required "TAB_APPROVAL_CHAT_ID" "Approval chat ID"
    if (-not $AdminUserIds) {
        $AdminUserIds = if ($env:TAB_ADMIN_USER_IDS) {
            $env:TAB_ADMIN_USER_IDS
        } elseif ($env:TAB_ADMIN_USER_ID) {
            $env:TAB_ADMIN_USER_ID
        } elseif ($NonInteractive) {
            throw "Missing required value: TAB_ADMIN_USER_IDS"
        } else {
            Read-Host "Administrator IDs (comma-separated)"
        }
    }
    if (-not $SourceUsernames) {
        $SourceUsernames = if ($env:TAB_SOURCE_USERNAMES) {
            $env:TAB_SOURCE_USERNAMES
        } elseif ($env:TAB_SOURCE_USERNAME) {
            $env:TAB_SOURCE_USERNAME
        } elseif ($NonInteractive) {
            throw "Missing required value: TAB_SOURCE_USERNAMES"
        } else {
            Read-Host "Source channels (comma-separated)"
        }
    }
    if (-not $AdminUserIds) { throw "Missing required value: TAB_ADMIN_USER_IDS" }
    if (-not $SourceUsernames) { throw "Missing required value: TAB_SOURCE_USERNAMES" }
    $DestinationName = Read-Required "TAB_DESTINATION_NAME" "Destination name"
    $DestinationId = Read-Required "TAB_DESTINATION_ID" "Destination channel ID"
    $DestinationUsername = $env:TAB_DESTINATION_USERNAME
    $Timezone = if ($env:TAB_TIMEZONE) { $env:TAB_TIMEZONE } else { "Asia/Tehran" }
    $MongoPassword = -join ((1..48) | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
    @(
        "COMPOSE_PROJECT_NAME=$Project"
        "TAB_INSTANCE_DIR=$InstallDirectory"
        "TAB_IMAGE=$Image"
        "TAB_RUNTIME_UID=$RuntimeUid"
        "TAB_RUNTIME_GID=$RuntimeGid"
        "TAB_MONGODB_IMAGE=$MongoDbImage"
        "TAB_MONGODB_DATABASE=$Database"
        "TAB_MONGODB_USERNAME=telegram_assist"
        "TAB_MONGODB_PASSWORD=$MongoPassword"
        "TAB_MONGODB_URI=mongodb://telegram_assist:$MongoPassword@mongodb:27017/?authSource=admin&directConnection=true"
        "TAB_TELEGRAM_API_ID=$ApiId"
        "TAB_TELEGRAM_API_HASH=$ApiHash"
        "TAB_TELEGRAM_PHONE_NUMBER=$Phone"
        "TAB_TELEGRAM_BOT_TOKEN=$BotToken"
    ) | Set-Content -Encoding utf8 -Path $EnvPath
    & icacls $EnvPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
}
& (Join-Path $InstallDirectory "permissions.ps1") repair `
    -InstanceDirectory $InstallDirectory `
    -RuntimeUid $RuntimeUid `
    -RuntimeGid $RuntimeGid

$Compose = @("compose", "--project-name", $Project, "--env-file", $EnvPath, "-f", (Join-Path $InstallDirectory "compose.yaml"))
$DockerKernel = (& docker info --format "{{.KernelVersion}}").Trim()
& docker pull $Image
& docker run --rm $Image deployment-preflight `
    --kernel-version $DockerKernel `
    --mongodb-image $MongoDbImage
if ($LASTEXITCODE -ne 0) {
    throw "MongoDB image is incompatible with the Docker Linux kernel."
}
& docker @Compose config | Out-Null
& docker @Compose pull
& docker @Compose up -d mongodb
if (-not (Test-Path $ConfigPath)) {
    $Render = @(
        "run", "--rm", "--user", "${RuntimeUid}:${RuntimeGid}",
        "--env-file", $EnvPath,
        "-v", "${InstallDirectory}:/instance", $Image,
        "render-instance-config", "--template", "/instance/configuration.example.json",
        "--output", "/instance/config/configuration.json", "--instance", $Instance,
        "--retention-days", "$RetentionDays", "--approval-chat-id", $ApprovalChat,
        "--admin-user-ids", $AdminUserIds, "--source-usernames", $SourceUsernames,
        "--destination-name", $DestinationName, "--destination-id", $DestinationId,
        "--timezone", $Timezone
    )
    if ($DestinationUsername) { $Render += @("--destination-username", $DestinationUsername) }
    & docker @Render
    & (Join-Path $InstallDirectory "permissions.ps1") repair `
        -InstanceDirectory $InstallDirectory `
        -RuntimeUid $RuntimeUid `
        -RuntimeGid $RuntimeGid
}
& docker @Compose run --rm runtime check --config /app/config/configuration.json
if ($ExistingInstance) {
    Write-Output "Existing Telegram session preserved; login was skipped during update."
} else {
    & docker @Compose run --rm runtime login --config /app/config/configuration.json
    if ($LASTEXITCODE -ne 0) {
        throw "Login was not completed. MongoDB and instance files were preserved."
    }
}
& docker @Compose up -d
$ManagerBin = Join-Path $env:LOCALAPPDATA "TelegramAssistBot\bin"
New-Item -ItemType Directory -Force -Path $ManagerBin | Out-Null
if (
    -not (Get-Command py -ErrorAction SilentlyContinue) -and
    -not (Get-Command python -ErrorAction SilentlyContinue) -and
    -not (Test-Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is required for the global tabctl manager."
    }
    winget install --exact --id Python.Python.3.12 `
        --accept-package-agreements --accept-source-agreements
}
Invoke-WebRequest "$BaseUrl/deploy/tabctl.py" -OutFile (Join-Path $ManagerBin "tabctl.py")
Invoke-WebRequest "$BaseUrl/deploy/tabctl.ps1" -OutFile (Join-Path $ManagerBin "tabctl.ps1")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $ManagerBin) {
    [Environment]::SetEnvironmentVariable(
        "Path",
        "$ManagerBin;$UserPath",
        "User"
    )
}
& (Join-Path $ManagerBin "tabctl.ps1") instance import `
    --path $InstallDirectory `
    --name $Instance | Out-Null
Write-Output "Installed $Instance. Run: tabctl --instance $Instance status"
