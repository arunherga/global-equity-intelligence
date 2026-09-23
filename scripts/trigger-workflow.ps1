<#
.SYNOPSIS
    Fire the daily-intelligence workflow through the GitHub API.

.DESCRIPTION
    GitHub's `schedule` trigger has never once delivered for this repository:
    across the first two days, seven workflow runs were recorded and not one
    had event "schedule", while push and workflow_dispatch both worked
    normally. This script does not depend on that scheduler - it calls the
    workflow_dispatch API directly, which has been reliable throughout.

    The token is never passed on the command line and is never written to the
    log. It is read, in order, from:

      1. $env:GEI_GITHUB_TOKEN
      2. `gh auth token`, if the GitHub CLI is installed and logged in

    A fine-grained personal access token needs only:
      Repository access: this repository
      Permissions: Actions -> Read and write

.EXAMPLE
    .\trigger-workflow.ps1
    .\trigger-workflow.ps1 -Ticker WAAREEENER -WhatIfDryRun
#>

[CmdletBinding()]
param(
    [string] $Owner      = 'arunherga',
    [string] $Repo       = 'global-equity-intelligence',
    [string] $WorkflowId = 'daily-intelligence.yml',
    [string] $Ref        = 'main',
    [string] $Ticker     = '',
    [switch] $WhatIfDryRun,
    [string] $LogPath    = "$env:LOCALAPPDATA\global-equity-intelligence\trigger.log"
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Log {
    param([string] $Message)
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'), $Message
    Write-Host $line
    $dir = Split-Path -Parent $LogPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Add-Content -Path $LogPath -Value $line
}

function Get-GitHubToken {
    if ($env:GEI_GITHUB_TOKEN) {
        Write-Log 'token: using $env:GEI_GITHUB_TOKEN'
        return $env:GEI_GITHUB_TOKEN
    }
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        $token = (& gh auth token 2>$null)
        if ($LASTEXITCODE -eq 0 -and $token) {
            Write-Log 'token: using the GitHub CLI login'
            return $token.Trim()
        }
    }
    throw 'No GitHub token. Set GEI_GITHUB_TOKEN, or run "gh auth login".'
}

try {
    $token = Get-GitHubToken

    $inputs = @{}
    if ($Ticker)      { $inputs['ticker']  = $Ticker }
    if ($WhatIfDryRun){ $inputs['dry_run'] = 'true' }

    $body = @{ ref = $Ref }
    if ($inputs.Count -gt 0) { $body['inputs'] = $inputs }

    $uri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$WorkflowId/dispatches"
    Write-Log "dispatching $WorkflowId on $Ref"

    $null = Invoke-RestMethod -Method Post -Uri $uri -Body ($body | ConvertTo-Json -Depth 5) `
        -Headers @{
            Authorization          = "Bearer $token"
            Accept                 = 'application/vnd.github+json'
            'X-GitHub-Api-Version' = '2022-11-28'
            'User-Agent'           = 'global-equity-intelligence-trigger'
        } -ContentType 'application/json'

    # A successful dispatch returns 204 with no body.
    Write-Log "dispatched OK -> https://github.com/$Owner/$Repo/actions"
    exit 0
}
catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    Write-Log ("FAILED{0}: {1}" -f $(if ($status) { " (HTTP $status)" } else { '' }), $_.Exception.Message)
    if ($status -eq 401 -or $status -eq 403) {
        Write-Log 'hint: the token needs Actions -> Read and write on this repository'
    }
    exit 1
}
