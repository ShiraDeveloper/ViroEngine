<#
.SYNOPSIS
  Publish the committed project to a Hugging Face Docker Space (API + web app in one container).

.DESCRIPTION
  Takes ONLY what is committed in git (`git archive HEAD`), so `.env`, `reference/` (the sample
  PDFs) and anything else git-ignored can never reach the Space. Replaces README.md with the
  Space's README (deploy/huggingface/README.md, which carries the `sdk: docker` front matter),
  drops binary files the Space would refuse without LFS, and force-pushes one commit to the Space.

  Secrets are NOT sent by this script: set them once in the Space's Settings -> Variables and
  secrets (see docs/DEPLOY.md).

.EXAMPLE
  $env:HF_TOKEN = "hf_xxx"   # a WRITE token from https://huggingface.co/settings/tokens
  ./scripts/deploy_hf.ps1 -Space "my-user/safe-line"
#>
param(
  [Parameter(Mandatory = $true)][string]$Space,
  [string]$Token = $env:HF_TOKEN
)

$ErrorActionPreference = "Stop"
if (-not $Token) { throw "Set HF_TOKEN (a write token from https://huggingface.co/settings/tokens)." }
if ($Space -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Space must look like user/space-name." }

$root = (git rev-parse --show-toplevel).Trim()
$dirty = git -C $root status --porcelain
if ($dirty) { Write-Warning "Uncommitted changes are NOT deployed - only the last commit is." }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("safeline-space-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
  $archive = Join-Path $work "src.tar"
  git -C $root archive --format=tar -o $archive HEAD
  $site = Join-Path $work "space"
  New-Item -ItemType Directory -Path $site | Out-Null
  tar -xf $archive -C $site
  Remove-Item $archive

  Copy-Item (Join-Path $site "deploy/huggingface/README.md") (Join-Path $site "README.md") -Force
  # A Space refuses binary files that are not in LFS; the image does not need them.
  Get-ChildItem $site -Recurse -File -Include *.png, *.jpg, *.jpeg, *.pdf, *.onnx |
    Remove-Item -Force

  Push-Location $site
  try {
    git init -q -b main
    git add -A
    git -c user.name="deploy" -c user.email="deploy@localhost" commit -q -m "Deploy Safe Line"
    $remote = "https://user:$Token@huggingface.co/spaces/$Space"
    git push -q --force $remote main
  } finally {
    Pop-Location
  }
  Write-Host "Pushed. The Space builds now (5-10 minutes): https://huggingface.co/spaces/$Space"
  $host_name = ($Space -replace '/', '-').ToLower() -replace '[._]', '-'
  Write-Host "When it is running, open: https://$host_name.hf.space"
} finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
