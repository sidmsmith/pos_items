# Test script to download vonmaur.com image manually
# Run this from PowerShell: .\test_download.ps1
# NOTE: This is a PowerShell script (.ps1), NOT a Python script!
#       Run with: .\test_download.ps1
#       NOT with: python test_download.ps1

$imageUrl = "https://www.vonmaur.com/Images/Product/2144427/1621855/StillPhoto/1621855_Frt.jpg"
$outputFile = "test_image.jpg"

Write-Host "Testing download of: $imageUrl" -ForegroundColor Cyan
Write-Host ""

# Method 1: Using PowerShell Invoke-WebRequest with enhanced headers
Write-Host "Method 1: PowerShell Invoke-WebRequest" -ForegroundColor Yellow
try {
    $headers = @{
        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        'Accept' = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        'Accept-Language' = 'en-US,en;q=0.9'
        'Accept-Encoding' = 'gzip, deflate, br'
        'Connection' = 'keep-alive'
        'Cache-Control' = 'no-cache'
        'Pragma' = 'no-cache'
        'Sec-Fetch-Dest' = 'image'
        'Sec-Fetch-Mode' = 'no-cors'
        'Sec-Fetch-Site' = 'cross-site'
        'Referer' = 'https://www.vonmaur.com/'
        'Origin' = 'https://www.vonmaur.com'
    }
    
    $response = Invoke-WebRequest -Uri $imageUrl -Headers $headers -TimeoutSec 30 -OutFile $outputFile
    Write-Host "✓ SUCCESS: Downloaded $($response.ContentLength) bytes to $outputFile" -ForegroundColor Green
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "✗ FAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.Exception.Response) {
        Write-Host "Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
    }
}

Write-Host ""

# Method 2: Using curl.exe (if available)
Write-Host "Method 2: curl.exe" -ForegroundColor Yellow
try {
    $curlArgs = @(
        '-L',  # Follow redirects
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '-H', 'Accept: image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        '-H', 'Accept-Language: en-US,en;q=0.9',
        '-H', 'Accept-Encoding: gzip, deflate, br',
        '-H', 'Connection: keep-alive',
        '-H', 'Referer: https://www.vonmaur.com/',
        '-H', 'Origin: https://www.vonmaur.com',
        '--max-time', '30',
        '-o', 'test_image_curl.jpg',
        $imageUrl
    )
    
    & curl.exe $curlArgs
    if ($LASTEXITCODE -eq 0) {
        $fileInfo = Get-Item 'test_image_curl.jpg' -ErrorAction SilentlyContinue
        if ($fileInfo) {
            Write-Host "✓ SUCCESS: Downloaded $($fileInfo.Length) bytes to test_image_curl.jpg" -ForegroundColor Green
        }
    } else {
        Write-Host "✗ FAILED: curl returned exit code $LASTEXITCODE" -ForegroundColor Red
    }
} catch {
    Write-Host "✗ FAILED: curl.exe not found or error: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""

# Method 3: Python one-liner (if Python is available)
Write-Host "Method 3: Python requests" -ForegroundColor Yellow
$pythonScript = @"
import requests
import sys

url = "$imageUrl"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://www.vonmaur.com/',
    'Origin': 'https://www.vonmaur.com'
}

try:
    r = requests.get(url, headers=headers, timeout=30, stream=True)
    r.raise_for_status()
    with open('test_image_python.jpg', 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    print(f'✓ SUCCESS: Downloaded {len(r.content)} bytes to test_image_python.jpg')
    print(f'Status Code: {r.status_code}')
    sys.exit(0)
except Exception as e:
    print(f'✗ FAILED: {str(e)}')
    sys.exit(1)
"@

try {
    $pythonScript | python.exe
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Python test completed successfully" -ForegroundColor Green
    } else {
        Write-Host "Python test failed or Python not found" -ForegroundColor Red
    }
} catch {
    Write-Host "✗ Python not available or error: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "Test complete. Check for output files:" -ForegroundColor Cyan
Write-Host "  - test_image.jpg (PowerShell)" -ForegroundColor Cyan
Write-Host "  - test_image_curl.jpg (curl)" -ForegroundColor Cyan
Write-Host "  - test_image_python.jpg (Python)" -ForegroundColor Cyan
