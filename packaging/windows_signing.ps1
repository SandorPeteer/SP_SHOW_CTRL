param(
    [Parameter(Mandatory = $true)][string]$AppDirectory,
    [string]$CertificateThumbprint = "",
    [string]$TimestampUrl = "",
    [string]$SignToolPath = "",
    [switch]$RequireSigned,
    [string]$ReportPath = "dist/windows-signatures.json"
)
$ErrorActionPreference = "Stop"
$AppDirectory = (Resolve-Path $AppDirectory).Path

# No certificate is selected, installed or purchased automatically. A caller
# explicitly supplies an existing CurrentUser\My code-signing identity.
if ($CertificateThumbprint) {
    $CertificateThumbprint = $CertificateThumbprint.Replace(" ", "")
    if ($CertificateThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw "Invalid signing certificate thumbprint" }
    if ($TimestampUrl -notmatch '^https?://') { throw "An RFC 3161 timestamp URL is required for signing" }
    $Certificate = Get-Item "Cert:\CurrentUser\My\$CertificateThumbprint"
    if (!$Certificate.HasPrivateKey) { throw "Signing certificate has no accessible private key" }
    if ($Certificate.NotAfter -le (Get-Date)) { throw "Signing certificate has expired" }
    $RequireSigned = $true
}
if ($CertificateThumbprint -or $RequireSigned) {
    if (!$SignToolPath) {
        $Tool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($Tool) { $SignToolPath = $Tool.Source }
        else {
            $Tool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
                Sort-Object FullName -Descending | Select-Object -First 1
            if ($Tool) { $SignToolPath = $Tool.FullName }
        }
    }
    if (!$SignToolPath -or !(Test-Path $SignToolPath)) { throw "Windows SDK signtool.exe is required" }
}

$Report = @()
$Failures = @()
$Files = Get-ChildItem $AppDirectory -Recurse -File | Where-Object { $_.Extension -in '.exe', '.dll', '.pyd', '.com' }
if (!$Files) { throw "No packaged Windows binaries found" }
foreach ($File in $Files) {
    $Signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
    if ($CertificateThumbprint -and $Signature.Status -eq 'NotSigned') {
        & $SignToolPath sign /sha1 $CertificateThumbprint /s My /fd SHA256 /tr $TimestampUrl /td SHA256 $File.FullName
        if ($LASTEXITCODE -ne 0) { throw "Signing failed: $($File.FullName)" }
        $Signature = Get-AuthenticodeSignature -LiteralPath $File.FullName
        if (!$Signature.TimeStamperCertificate) { throw "No trusted timestamp after signing: $($File.FullName)" }
    }
    # Verify upstream signatures too; an invalid signature is never overwritten.
    $Verified = $false
    if ($RequireSigned -and $Signature.Status -eq 'Valid') {
        & $SignToolPath verify /pa /all $File.FullName
        $Verified = $LASTEXITCODE -eq 0
    }
    if ($RequireSigned -and !$Verified) { $Failures += $File.FullName }
    $Stream = [System.IO.File]::OpenRead($File.FullName)
    $Reader = [System.IO.BinaryReader]::new($Stream)
    try {
        if ($Reader.ReadUInt16() -ne 0x5a4d) { throw "Not a PE binary: $($File.FullName)" }
        $Stream.Position = 0x3c
        $Stream.Position = $Reader.ReadUInt32()
        if ($Reader.ReadUInt32() -ne 0x4550) { throw "Invalid PE header: $($File.FullName)" }
        $Machine = $Reader.ReadUInt16()
        if ($Machine -ne 0x8664) { throw "Non-x64 binary in x64 package: $($File.FullName) ($Machine)" }
    } finally { $Reader.Dispose(); $Stream.Dispose() }
    $Report += [ordered]@{
        path = $File.FullName.Substring($AppDirectory.Length).TrimStart('\', '/')
        sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        architecture = 'x64'
        status = $Signature.Status.ToString()
        publisher = $(if ($Signature.SignerCertificate) { $Signature.SignerCertificate.Subject } else { $null })
        timestamped = [bool]$Signature.TimeStamperCertificate
        verified_with_signtool = $Verified
    }
}
$Summary = [ordered]@{
    required_signed = [bool]$RequireSigned
    binaries = $Report.Count
    unsigned = @($Report | Where-Object status -eq 'NotSigned').Count
    valid = @($Report | Where-Object status -eq 'Valid').Count
    signatures = $Report
}
New-Item -ItemType Directory -Force (Split-Path -Parent $ReportPath) | Out-Null
$Summary | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $ReportPath
if ($Failures) { throw "Signature verification failed for $($Failures.Count) binaries; see $ReportPath" }
if (!$RequireSigned) {
    Write-Warning "Signature inventory only: $($Summary.unsigned) unsigned binaries. Smart App Control compatibility is not established."
}
Write-Host "Windows x64/signature report: $ReportPath"
