#!/usr/bin/env python3
"""Test script to download vonmaur.com image manually using Python requests"""

import requests
import sys

image_url = "https://www.vonmaur.com/Images/Product/2144427/1621855/StillPhoto/1621855_Frt.jpg"
output_file = "test_image_python.jpg"

print(f"Testing download of: {image_url}")
print()

# Enhanced headers matching our implementation
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Sec-Fetch-Dest': 'image',
    'Sec-Fetch-Mode': 'no-cors',
    'Sec-Fetch-Site': 'cross-site',
    'Referer': 'https://www.vonmaur.com/',
    'Origin': 'https://www.vonmaur.com'
}

print("Method 1: Direct request with enhanced headers")
try:
    r = requests.get(image_url, headers=headers, timeout=30, stream=True)
    r.raise_for_status()
    total_bytes = 0
    with open(output_file, 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total_bytes += len(chunk)
    print(f"[SUCCESS] Downloaded {total_bytes} bytes to {output_file}")
    print(f"Status Code: {r.status_code}")
    print(f"Content-Type: {r.headers.get('content-type', 'unknown')}")
except requests.exceptions.ConnectionError as e:
    print(f"[CONNECTION ERROR] {str(e)}")
    print("  This suggests the server is actively closing the connection.")
    print("  Possible causes: bot detection, firewall, or TLS/SSL issues.")
except requests.exceptions.Timeout as e:
    print(f"[TIMEOUT] {str(e)}")
except requests.exceptions.RequestException as e:
    print(f"[REQUEST ERROR] {str(e)}")
    if hasattr(e, 'response') and e.response is not None:
        print(f"  Status Code: {e.response.status_code}")
except Exception as e:
    print(f"[UNEXPECTED ERROR] {str(e)}")
    import traceback
    traceback.print_exc()

print()
print("Method 2: Using Session (like our implementation)")
try:
    session = requests.Session()
    session.headers.update(headers)
    
    r = session.get(image_url, timeout=30, stream=True)
    r.raise_for_status()
    total_bytes = 0
    with open("test_image_session.jpg", 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total_bytes += len(chunk)
    print(f"[SUCCESS] Downloaded {total_bytes} bytes to test_image_session.jpg")
    print(f"Status Code: {r.status_code}")
    session.close()
except requests.exceptions.ConnectionError as e:
    print(f"[CONNECTION ERROR] {str(e)}")
    print("  Server is actively closing the connection.")
except Exception as e:
    print(f"[ERROR] {str(e)}")
    import traceback
    traceback.print_exc()

print()
print("Method 3: Minimal headers (like a simple browser)")
try:
    minimal_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://www.vonmaur.com/'
    }
    r = requests.get(image_url, headers=minimal_headers, timeout=30, stream=True)
    r.raise_for_status()
    total_bytes = 0
    with open("test_image_minimal.jpg", 'wb') as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total_bytes += len(chunk)
    print(f"[SUCCESS] Downloaded {total_bytes} bytes to test_image_minimal.jpg")
    print(f"Status Code: {r.status_code}")
except Exception as e:
    print(f"[ERROR] {str(e)}")
    import traceback
    traceback.print_exc()

print()
print("Test complete. Check for output files:")
print("  - test_image_python.jpg (Method 1)")
print("  - test_image_session.jpg (Method 2)")
print("  - test_image_minimal.jpg (Method 3)")
