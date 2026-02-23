$templatesPath = "H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app\templates"

Get-ChildItem -Path $templatesPath -Filter *.html | ForEach-Object {
    $content = Get-Content $_.FullName -Raw

    # Fix 1: Change max-width from 500px to 420px
    $content = $content -replace '\.improve-modal-box\{background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba\(0,0,0,\.3\);max-width:500px;', '.improve-modal-box{background:#fff;border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.3);max-width:420px;'

    # Fix 2: Add font-size to h2
    $content = $content -replace '\.improve-modal-box h2\{margin:0 0 16px 0\}', '.improve-modal-box h2{margin:0 0 16px 0;font-size:18px}'

    # Fix 3: Add font-size:14px to input and textarea
    $content = $content -replace '\.improve-modal-box input,\.improve-modal-box textarea\{width:100%;padding:10px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:12px;font-family:inherit\}', '.improve-modal-box input,.improve-modal-box textarea{width:100%;padding:10px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:12px;font-family:inherit;font-size:14px}'

    Set-Content -Path $_.FullName -Value $content -NoNewline
    Write-Host "Fixed: $($_.Name)"
}
