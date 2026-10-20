param([string]$InputDocx, [string]$OutputPdf)
$ErrorActionPreference = 'Stop'
$wordApp = $null
$wordDoc = $null
try {
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    $wordApp.AutomationSecurity = 3
    $wordDoc = $wordApp.Documents.Open($InputDocx, $false, $true)
    $wordDoc.ExportAsFixedFormat($OutputPdf, 17)
    Write-Output "PDF exported: $OutputPdf"
} finally {
    if ($null -ne $wordDoc) { $wordDoc.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordDoc) }
    if ($null -ne $wordApp) { $wordApp.Quit(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp) }
}
