# api/index.py
from flask import Flask, request, jsonify, send_file, Response, stream_with_context
import json
import os
import requests
from requests.auth import HTTPBasicAuth
import urllib3
import csv
import re
import tempfile
import shutil
import zipfile
import uuid
import io
import base64
from datetime import datetime
from collections import defaultdict
import cloudinary
import cloudinary.uploader
import cloudinary.api
import cloudinary.exceptions

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# =============================================================================
# ENVIRONMENT VARIABLES (Auth Only)
# =============================================================================
# These should be set in Vercel environment variables
MANHATTAN_PASSWORD = os.getenv("MANHATTAN_PASSWORD")
MANHATTAN_SECRET = os.getenv("MANHATTAN_SECRET")

# =============================================================================
# HARDCODED CONFIGURATION (Well-Organized for Easy Updates)
# =============================================================================

# --- Manhattan WMS API Configuration ---
AUTH_HOST = "salep-auth.sce.manh.com"
API_HOST = "salep.sce.manh.com"
USERNAME_BASE = "sdtadmin@"
CLIENT_ID = "omnicomponent.1.0.0"
BULK_IMPORT_URL = f"https://{API_HOST}/item-master/api/item-master/item/bulkImport?stopOnFirstError=true"

# --- xAI Grok API Configuration ---
BASE_URL_GEN = "https://api.x.ai/v1"
MODEL = "grok-3"
API_KEY_GEN = os.getenv("XAI_API_KEY", "")

# --- Google Custom Search API Configuration ---
URL_DOWN = "https://www.googleapis.com/customsearch/v1"
API_KEY_DOWN = os.getenv("GOOGLE_API_KEY", "")
CX = "64924b251f5014f7c"

# --- Cloudinary Configuration ---
CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "com-manh-cp")
API_KEY_CLOUD = os.getenv("CLOUDINARY_API_KEY", "")
API_SECRET_CLOUD = os.getenv("CLOUDINARY_API_SECRET", "")

# --- Home Assistant Webhook Configuration ---
HA_WEBHOOK_URL = "http://sidmsmith.zapto.org:8123/api/webhook/manhattan_pos_items"

# --- Default Values (matching Python script) ---
DEFAULT_COMPANY = "Nike"
DEFAULT_WEBSITE = "nike.com"
DEFAULT_COUNT = 30
DEFAULT_SITES = "nike.com, amazon.com"
DEFAULT_IMAGES_PER_ITEM = 3
DEFAULT_PREFIX = "https://res.cloudinary.com/com-manh-cp/image/upload/v1752528139/sidney/"
DEFAULT_FOLDER = "sidney"
DEFAULT_PROFILE = ""
DEFAULT_EXTRA_PROMPT = ""
DEFAULT_IMAGE_FILTERS = ""
DEFAULT_ITEM_NUMBERS_CSV = ""

# =============================================================================
# MIME MAP FOR IMAGE EXTENSIONS
# =============================================================================
MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/svg+xml": ".svg"
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_manhattan_token(org):
    """Get Manhattan WMS authentication token"""
    if not MANHATTAN_PASSWORD or not MANHATTAN_SECRET:
        return None
    
    url = f"https://{AUTH_HOST}/oauth/token"
    username = f"{USERNAME_BASE}{org.lower()}"
    data = {
        "grant_type": "password",
        "username": username,
        "password": MANHATTAN_PASSWORD
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = HTTPBasicAuth(CLIENT_ID, MANHATTAN_SECRET)
    try:
        r = requests.post(url, data=data, headers=headers, auth=auth, timeout=60, verify=False)
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception as e:
        print(f"[AUTH] Error: {e}")
    return None

def clean_url(url):
    """Clean URL by removing protocol and www"""
    if not url:
        return ""
    url = url.strip().lower()
    if url.startswith("http://") or url.startswith("https://"):
        url = url.split("://", 1)[1]
    if url.startswith("www."):
        url = url[4:]
    return url.rstrip("/")

def clean_sites(sites_str):
    """Parse sites string into list of cleaned URLs"""
    return [clean_url(s.strip()) for s in sites_str.split(",") if s.strip()]

def parse_image_filters(filter_str):
    """Parse image filter string into API parameters"""
    if not filter_str:
        return {}, None

    words = [w.strip().lower() for w in re.split(r'[,\s;]+', filter_str) if w.strip()]

    valid_size = {"large", "big", "medium", "small", "tiny"}
    valid_type = {"photo", "real", "clipart", "cartoon", "lineart", "drawing"}
    valid_file = {"png", "jpg", "jpeg", "gif", "webp"}
    valid_color = {"red", "blue", "green", "yellow", "orange", "purple", "pink", "brown", "gray", "black", "white", "teal"}

    filters = {}
    for word in words:
        if word in valid_size:
            filters['imgSize'] = 'large' if word in {'large', 'big'} else 'medium' if word == 'medium' else 'small'
        elif word in valid_type:
            filters['imgType'] = 'photo' if word in {'photo', 'real'} else 'clipart' if word in {'clipart', 'cartoon'} else 'lineart'
        elif word in valid_file:
            filters['fileType'] = 'jpg' if word in {'jpg', 'jpeg'} else word
        elif word in valid_color:
            filters['imgDominantColor'] = word
        else:
            return None, f"Invalid filter: '{word}'. Try: large, photo, png, red, etc."

    return filters, None

def log_to_console(message, prefix="[API]"):
    """Log message for console output"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"{timestamp} {prefix} {message}")

def ensure_prefix_url(prefix):
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"

def get_extension_from_headers(content_type, fallback=".jpg"):
    if not content_type:
        return fallback
    content_type = content_type.split(";")[0].strip().lower()
    return MIME_EXTENSION_MAP.get(content_type, fallback)

def fetch_image_variants(product_name, item_id, sites, images_per_item, filters, start=1):
    """Fetch image metadata for a product without writing to disk
    
    Args:
        product_name: Product name to search for
        item_id: Item identifier
        sites: List of sites to search
        images_per_item: Number of images to fetch
        filters: Image filter parameters
        start: Starting index for pagination (1-based, default=1)
    """
    log_to_console(f"[GOOGLE-API] fetch_image_variants called: product='{product_name}', item_id={item_id}, count={images_per_item}, start={start}", "[INFO]")
    
    # Check API key
    if not API_KEY_DOWN:
        log_to_console(f"[GOOGLE-API] ERROR: GOOGLE_API_KEY environment variable not set!", "[ERROR]")
        return [], "Google API key not configured"
    
    log_to_console(f"[GOOGLE-API] API key present: {API_KEY_DOWN[:10]}...", "[INFO]")
    log_to_console(f"[GOOGLE-API] CX (Search Engine ID): {CX}", "[INFO]")
    
    site_query = " OR ".join(f"site:{s}" for s in sites) if sites else ""
    query = f"{product_name} ({site_query})" if site_query else product_name
    log_to_console(f"[GOOGLE-API] Query: '{query}'", "[INFO]")
    log_to_console(f"[GOOGLE-API] Sites: {sites}", "[INFO]")

    params = {
        "key": API_KEY_DOWN,
        "cx": CX,
        "q": query,
        "searchType": "image",
        "num": min(10, images_per_item),  # Google API max is 10 per request
        "start": start  # Pagination: start index (1-based)
    }
    if filters:
        params.update(filters)
        log_to_console(f"[GOOGLE-API] Filters applied: {filters}", "[INFO]")
    
    log_to_console(f"[GOOGLE-API] Request params: num={params['num']}, start={params['start']}, searchType={params['searchType']}", "[INFO]")
    log_to_console(f"[GOOGLE-API] Making request to: {URL_DOWN}", "[INFO]")

    last_error = None
    variants = []

    for attempt in range(3):
        try:
            log_to_console(f"[GOOGLE-API] Attempt {attempt + 1}/3", "[INFO]")
            r = requests.get(URL_DOWN, params=params, timeout=15)
            log_to_console(f"[GOOGLE-API] Response status: {r.status_code}", "[INFO]" if r.status_code == 200 else "[WARNING]")
            
            if r.status_code == 429:
                last_error = "Google API rate limited (429)"
                log_to_console(f"[GOOGLE-API] Rate limited (429), will retry", "[WARNING]")
                continue
            r.raise_for_status()
            data = r.json()
            
            # Log response structure
            log_to_console(f"[GOOGLE-API] Response keys: {list(data.keys())}", "[INFO]")
            if "error" in data:
                log_to_console(f"[GOOGLE-API] API Error: {data['error']}", "[ERROR]")
                last_error = f"Google API error: {data['error']}"
                break
            
            items = data.get("items", [])
            log_to_console(f"[GOOGLE-API] Found {len(items)} items in response", "[INFO]")
            if not items:
                last_error = "No images returned"
                log_to_console(f"[GOOGLE-API] No items in response, searchInfo: {data.get('searchInformation', {})}", "[WARNING]")
                break

            for idx, item in enumerate(items):
                # Calculate variant number based on start position + index
                # This ensures unique fileNames across pagination calls
                variant_number = start + idx
                img_url = item.get("link")
                if not img_url:
                    continue
                thumb_url = item.get("image", {}).get("thumbnailLink") or img_url

                parsed_source = ""
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(img_url)
                    parsed_source = parsed.netloc.lower().replace("www.", "")
                except:
                    parsed_source = ""

                filename_base = re.sub(r'[\\/:*?"<>|]', "", item_id)
                # Use start index + current position to ensure unique fileNames across pagination
                # start is 1-based, idx is 0-based, so variant number = start + idx
                variant_number = start + idx
                file_name = f"{filename_base}_v{variant_number:02d}"

                variants.append({
                    "fileName": file_name,
                    "originalUrl": img_url,
                    "previewUrl": thumb_url,
                    "source": parsed_source,
                    "shortDescription": product_name,
                    "description": product_name,
                    "itemId": item_id
                })

                if len(variants) >= images_per_item:
                    break
            
            log_to_console(f"[GOOGLE-API] Successfully processed {len(variants)} variants", "[SUCCESS]")
            break
        except requests.exceptions.Timeout:
            last_error = "Google API timeout"
            log_to_console(f"[GOOGLE-API] Timeout on attempt {attempt + 1}", "[ERROR]")
        except requests.exceptions.RequestException as e:
            last_error = f"Google API error: {str(e)[:80]}"
            log_to_console(f"[GOOGLE-API] Request exception on attempt {attempt + 1}: {last_error}", "[ERROR]")
            break
        except Exception as e:
            last_error = f"Unexpected error: {str(e)[:80]}"
            log_to_console(f"[GOOGLE-API] Unexpected exception on attempt {attempt + 1}: {last_error}", "[ERROR]")
            break

    if last_error:
        log_to_console(f"[GOOGLE-API] Returning {len(variants)} variants with error: {last_error}", "[WARNING]")
    else:
        log_to_console(f"[GOOGLE-API] ✓ Returning {len(variants)} variants successfully", "[SUCCESS]")
    
    return variants, last_error

def download_image_from_url(image_url, item_id, url_type="URL1"):
    """Download an image from a URL and return variant metadata
    
    Args:
        image_url: URL of the image to download
        item_id: Item identifier
        url_type: Type of URL (URL1 or URL2) for labeling
    
    Returns:
        tuple: (variant_dict or None, error_message or None)
        If successful, returns variant dict. If failed, returns None and error message.
    """
    if not image_url or not image_url.strip():
        return None, "Empty URL"
    
    try:
        # Try to download the image to verify it's accessible
        r = requests.get(image_url, timeout=10, stream=True)
        r.raise_for_status()
        
        # Check if it's actually an image
        content_type = r.headers.get("content-type", "").lower()
        if not content_type.startswith("image/"):
            return None, f"URL does not point to an image (content-type: {content_type})"
        
        # Get file extension from content-type or URL
        extension = get_extension_from_headers(content_type, ".jpg")
        
        # Create filename
        filename_base = re.sub(r'[\\/:*?"<>|]', "", item_id)
        file_name = f"{filename_base}_{url_type.lower()}{extension}"
        
        # Parse source from URL
        parsed_source = ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(image_url)
            parsed_source = parsed.netloc.lower().replace("www.", "")
        except:
            parsed_source = "Direct URL"
        
        variant = {
            "fileName": file_name,
            "originalUrl": image_url,
            "previewUrl": image_url,  # Use same URL for preview
            "source": parsed_source,
            "shortDescription": f"{url_type} Image",
            "description": f"{url_type} Image from {parsed_source}",
            "itemId": item_id,
            "isPlaceholder": False
        }
        
        return variant, None
    except requests.exceptions.Timeout:
        return None, "Timeout downloading image"
    except requests.exceptions.RequestException as e:
        return None, f"Failed to download: {str(e)[:80]}"
    except Exception as e:
        return None, f"Error: {str(e)[:80]}"

def create_placeholder_variant(item_id, url_type="URL1"):
    """Create a placeholder variant for failed image downloads
    
    Args:
        item_id: Item identifier
        url_type: Type of URL (URL1 or URL2) for labeling
    
    Returns:
        dict: Placeholder variant metadata
    """
    filename_base = re.sub(r'[\\/:*?"<>|]', "", item_id)
    file_name = f"{filename_base}_{url_type.lower()}_placeholder"
    
    return {
        "fileName": file_name,
        "originalUrl": "",
        "previewUrl": "",
        "source": "Failed",
        "shortDescription": f"{url_type} Image",
        "description": f"{url_type} Image - Failed to load",
        "itemId": item_id,
        "isPlaceholder": True
    }

# =============================================================================
# API ROUTES
# =============================================================================

@app.route('/api/app_opened', methods=['POST'])
def app_opened():
    """Track app opened event"""
    try:
        payload = {
            "event": "app_opened",
            "version": "v0.1.5",
            "timestamp": datetime.now().isoformat()
        }
        requests.post(HA_WEBHOOK_URL, json=payload, timeout=5)
    except:
        pass
    return jsonify({"success": True})

@app.route('/api/auth', methods=['POST'])
def auth():
    """Authenticate with Manhattan WMS"""
    org = request.json.get('org', '').strip()
    if not org:
        return jsonify({"success": False, "error": "ORG required"})
    
    log_to_console(f"Authenticating for ORG: {org}")
    token = get_manhattan_token(org)
    if token:
        log_to_console(f"Auth success for ORG: {org}")
        return jsonify({"success": True, "token": token})
    
    log_to_console(f"Auth failed for ORG: {org}")
    return jsonify({"success": False, "error": "Authentication failed"})

@app.route('/api/generate_items', methods=['POST'])
def generate_items():
    """Generate product list using xAI Grok API"""
    data = request.json
    company = data.get('company', '').strip()
    website = clean_url(data.get('website', '').strip())
    count = int(data.get('count', DEFAULT_COUNT))
    extra_prompt = data.get('extra_prompt', '').strip()

    if not company:
        return jsonify({"success": False, "error": "Company is required"})

    try:
        company_ref = company + (f" ({website})" if website else "")
        prompt = f"""Return a Python list of exactly {count} {company_ref} products as strings, in this exact format:

["Product1", "Product2", "Product3", ..., "Product{count}"]

- Sort alphabetically
- Double quotes
- Comma + space
- No extra text
- One per entry
- Natural spaces (e.g., "Running Shoe")"""

        if extra_prompt:
            prompt += f"\n\n{extra_prompt}"

        log_to_console(f"Calling xAI Grok API for {count} {company} products")
        
        client = requests.Session()
        client.headers.update({"Authorization": f"Bearer {API_KEY_GEN}"})

        response = client.post(
            f"{BASE_URL_GEN}/chat/completions",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 1000
            },
            timeout=60
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        list_match = re.search(r'\[\s*(.*?)\s*\]', content, re.DOTALL)
        if not list_match:
            return jsonify({"success": False, "error": "No valid list found in API response"})
        
        products = [item.strip().strip('"\'') for item in re.split(r'\s*,\s*', list_match.group(1)) if item.strip().strip('"\'')]
        products = sorted(set(products))[:count]
        while len(products) < count:
            products.append(f"{company} Item {len(products)+1}")

        formatted_list = '["' + '", "'.join(products) + '"]'
        output_text = f"""# {count} {company} Products (via xAI Grok API)
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# Reference: {company_ref}
# Model: {MODEL}
{formatted_list}
"""

        log_to_console(f"Generated {len(products)} products successfully")
        
        return jsonify({
            "success": True,
            "products": products,
            "output_text": output_text,
            "count": len(products)
        })
    except Exception as e:
        log_to_console(f"Generate failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/download_images', methods=['POST'])
def download_images():
    """Download images for products using Google Custom Search"""
    data = request.json
    products = data.get('products', [])
    sites_str = data.get('sites', DEFAULT_SITES)
    images_per_item = int(data.get('images_per_item', DEFAULT_IMAGES_PER_ITEM))
    prefix = data.get('prefix', DEFAULT_PREFIX).strip()
    item_numbers = data.get('item_numbers', [])
    filter_str = data.get('image_filters', '').strip()
    save_dir = data.get('save_dir', '').strip()

    if not products:
        return jsonify({"success": False, "error": "No products provided"})

    filters, filter_error = parse_image_filters(filter_str)
    if filter_error:
        return jsonify({"success": False, "error": filter_error})

    try:
        # Always use temporary directory on server (web apps can't write to user's local paths)
        # The save_dir from frontend is ignored - files will be returned as downloads
        save_dir = tempfile.mkdtemp(prefix="item_gen_")

        sites = clean_sites(sites_str)
        site_query = " OR ".join(f"site:{s}" for s in sites)
        queries = [f"{p} ({site_query})" for p in products]

        csv_rows = []
        item_counter = 0

        for q_idx, q in enumerate(queries):
            original_name = q.split(" (")[0].strip()
            clean_name = re.sub(r'[\\/:*?"<>|]', "", original_name).strip().replace(" ", "_")
            item_counter += 1

            params = {
                "key": API_KEY_DOWN,
                "cx": CX,
                "q": q,
                "searchType": "image",
                "num": images_per_item
            }
            if filters:
                params.update(filters)

            success = False
            last_error = None
            items = []

            for attempt in range(3):
                try:
                    r = requests.get(URL_DOWN, params=params, timeout=15)
                    if r.status_code == 429:
                        last_error = "Rate limited (429) - retrying"
                        continue
                    r.raise_for_status()
                    data = r.json()
                    items = data.get("items", [])
                    success = True
                    break
                except requests.exceptions.Timeout:
                    last_error = "Timeout - retrying"
                except requests.exceptions.RequestException as e:
                    last_error = f"API error: {str(e)[:60]}"
                    break

            if not success:
                for i in range(1, images_per_item + 1):
                    row = [""] * 16
                    if item_counter <= len(item_numbers):
                        row[0] = item_numbers[item_counter - 1]
                    row[1] = original_name
                    row[2] = original_name
                    row[3] = f"FAILED: {last_error or 'Unknown'}"
                    csv_rows.append(row)
                continue

            valid_count = 0
            search_idx = 0

            while valid_count < images_per_item and search_idx < len(items):
                item = items[search_idx]
                search_idx += 1
                img_url = item["link"]

                img_success = False
                for _ in range(3):
                    try:
                        img_r = requests.get(img_url, timeout=20)
                        img_r.raise_for_status()

                        if len(img_r.content) < 1500:
                            break

                        ctype = img_r.headers.get("content-type", "")
                        if not ctype.startswith("image/"):
                            break

                        if "gif" in ctype and len(img_r.content) < 8000:
                            break

                        ext = os.path.splitext(item.get("image", {}).get("thumbnailLink", ""))[1]
                        if ext.lower() not in {'.jpg','.jpeg','.png','.gif','.webp'}:
                            ext = ".jpg"

                        base_fn = f"{clean_name}_v{valid_count + 1}{ext}"
                        final_fn = base_fn
                        counter = 1
                        while os.path.exists(os.path.join(save_dir, final_fn)):
                            name, e = os.path.splitext(base_fn)
                            final_fn = f"{name}_copy{counter}{e}"
                            counter += 1

                        path = os.path.join(save_dir, final_fn)
                        with open(path, "wb") as f:
                            f.write(img_r.content)

                        public_url = prefix + final_fn if prefix else ""
                        item_id = item_numbers[item_counter - 1] if item_counter <= len(item_numbers) else f"ITEM{item_counter:03d}"

                        row = [""] * 16
                        row[0] = item_id
                        row[1] = original_name
                        row[2] = original_name
                        row[3] = public_url
                        from urllib.parse import urlparse
                        row[15] = urlparse(img_url).netloc.lower().replace("www.", "")
                        csv_rows.append(row)

                        valid_count += 1
                        img_success = True
                        break

                    except Exception:
                        break

                if not img_success:
                    pass

            while valid_count < images_per_item:
                row = [""] * 16
                if item_counter <= len(item_numbers):
                    row[0] = item_numbers[item_counter - 1]
                row[1] = original_name
                row[2] = original_name
                row[3] = "DL_FAILED: No valid image"
                csv_rows.append(row)
                valid_count += 1
                item_counter += 1

        for i in range(item_counter, len(item_numbers) + 1):
            if i > len(item_numbers):
                break
            row = [""] * 16
            row[0] = item_numbers[i - 1]
            row[1] = ""
            row[2] = ""
            row[3] = ""
            csv_rows.append(row)

        headers = [
            "ItemId", "ShortDescription", "Description", "ImageUrl",
            "", "", "", "", "", "", "", "", "", "", "", "Source"
        ]

        # Create CSV in memory and on disk
        csv_buffer = io.StringIO()
        w = csv.writer(csv_buffer)
        w.writerow(headers)
        w.writerows(csv_rows)
        csv_content = csv_buffer.getvalue()
        csv_buffer.close()

        csv_filename = "imagedownload.csv"
        csv_path = os.path.join(save_dir, csv_filename)
        with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
            csv_file.write(csv_content)

        # Encode CSV as base64 for download
        csv_base64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')

        # Create ZIP containing all files (images + CSV)
        zip_unique = uuid.uuid4().hex[:8]
        zip_filename = f"downloaded_items_{zip_unique}.zip"
        zip_path = os.path.join(save_dir, zip_filename)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for entry in os.listdir(save_dir):
                entry_path = os.path.join(save_dir, entry)
                if os.path.isfile(entry_path) and entry not in (zip_filename, csv_filename):
                    zipf.write(entry_path, arcname=entry)

        with open(zip_path, "rb") as zip_file:
            zip_base64 = base64.b64encode(zip_file.read()).decode("utf-8")

        log_to_console(f"Downloaded images for {len(products)} products, created CSV with {len(csv_rows)} rows and ZIP package {zip_filename}")

        try:
            shutil.rmtree(save_dir, ignore_errors=True)
        except Exception:
            pass

        return jsonify({
            "success": True,
            "csv_content": csv_base64,
            "csv_filename": csv_filename,
            "zip_content": zip_base64,
            "zip_filename": zip_filename,
            "row_count": len(csv_rows),
            "image_count": len([r for r in csv_rows if r[3] and not r[3].startswith("FAILED") and not r[3].startswith("DL_FAILED")])
        })
    except Exception as e:
        log_to_console(f"Download failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/gallery_generate', methods=['POST'])
def gallery_generate():
    """Generate gallery metadata for stateless image selection
    
    Supports both legacy format (products array) and new POS items format (posItems array).
    For POS items, downloads ImageURL1 and ImageURL2, then searches Google Images using ShortDescription.
    """
    data = request.json
    pos_items = data.get('posItems', [])  # New POS items format
    products = data.get('products', [])  # Legacy format
    item_numbers = data.get('item_numbers', [])
    sites_str = data.get('sites', DEFAULT_SITES)
    images_per_item = int(data.get('images_per_item', DEFAULT_IMAGES_PER_ITEM))
    filter_str = data.get('image_filters', '').strip()
    start_index = int(data.get('start_index', 1))  # For pagination (1-based)

    # Check if using new POS items format
    if pos_items:
        return handle_pos_items_gallery(pos_items, sites_str, images_per_item, filter_str, start_index)
    
    # Legacy format handling
    if not products:
        return jsonify({"success": False, "error": "No product names were provided."}), 400

    filters, filter_error = parse_image_filters(filter_str)
    if filter_error:
        return jsonify({"success": False, "error": filter_error}), 400

    # Remove artificial limit - allow any number, will paginate if > 10
    images_per_item = max(1, images_per_item)
    sites = clean_sites(sites_str)

    items_payload = []
    missing_items = []

    try:
        for idx, product_name in enumerate(products):
            reference_id = item_numbers[idx] if idx < len(item_numbers) else None
            item_id = reference_id or f"gallery_item_{idx + 1}"

            # If requesting more than 10 images, paginate through multiple API calls
            all_variants = []
            if images_per_item <= 10:
                # Single API call (use start_index if provided for pagination)
                variants, last_error = fetch_image_variants(product_name, item_id, sites, images_per_item, filters, start=start_index)
                all_variants = variants
            else:
                # Multiple API calls with pagination
                remaining = images_per_item
                current_start = start_index
                last_error = None
                
                while remaining > 0 and len(all_variants) < images_per_item:
                    batch_size = min(10, remaining)  # Google API max is 10 per request
                    variants, batch_error = fetch_image_variants(product_name, item_id, sites, batch_size, filters, start=current_start)
                    
                    if batch_error:
                        last_error = batch_error
                        if not variants:  # If no variants returned, stop paginating
                            break
                    
                    all_variants.extend(variants)
                    remaining -= len(variants)
                    current_start += len(variants)
                    
                    # If we got fewer than requested, we've reached the end
                    if len(variants) < batch_size:
                        break
                
                # Limit to requested number
                all_variants = all_variants[:images_per_item]
            
            variants = all_variants

            if not variants:
                missing_items.append({
                    "itemId": item_id,
                    "productName": product_name,
                    "reason": last_error or "No valid images returned"
                })

            unique_sources = {v['source'] for v in variants if v.get('source')}
            group_source = next(iter(unique_sources)) if len(unique_sources) == 1 else ""

            items_payload.append({
                "itemId": item_id,
                "productName": product_name,
                "referenceId": reference_id,
                "variants": [
                    {
                        "fileName": v["fileName"],
                        "previewUrl": v["previewUrl"],
                        "originalUrl": v["originalUrl"],
                        "source": v["source"],
                        "shortDescription": v["shortDescription"],
                        "description": v["description"]
                    }
                    for v in variants
                ],
                "source": group_source
            })

        return jsonify({
            "success": True,
            "items": items_payload,
            "missingItems": missing_items
        })
    except Exception as e:
        log_to_console(f"Gallery generate failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)}), 500

def handle_pos_items_gallery(pos_items, sites_str, images_per_item, filter_str, start_index):
    """Handle gallery generation for POS items format
    
    For each POS item:
    1. Download ImageURL1 (or create placeholder if failed)
    2. Download ImageURL2 (or create placeholder if failed)
    3. Search Google Images using ShortDescription
    4. Build url1Variants and url2Variants arrays
    """
    filters, filter_error = parse_image_filters(filter_str)
    if filter_error:
        return jsonify({"success": False, "error": filter_error}), 400

    images_per_item = max(1, images_per_item)
    sites = clean_sites(sites_str)

    items_payload = []
    missing_items = []

    try:
        for pos_item in pos_items:
            item_id = pos_item.get('itemId', '')
            image_url1 = pos_item.get('imageURL1', '').strip()
            image_url2 = pos_item.get('imageURL2', '').strip()
            short_description = pos_item.get('shortDescription', item_id).strip()

            if not item_id:
                missing_items.append({
                    "itemId": item_id or "Unknown",
                    "productName": short_description,
                    "reason": "Missing ItemID"
                })
                continue

            # Download ImageURL1
            url1_variants = []
            if image_url1:
                variant, error = download_image_from_url(image_url1, item_id, "URL1")
                if variant:
                    url1_variants.append(variant)
                else:
                    # Create placeholder for failed URL1
                    url1_variants.append(create_placeholder_variant(item_id, "URL1"))
                    log_to_console(f"URL1 failed for {item_id}: {error}", "[WARNING]")
            else:
                # Create placeholder for empty URL1
                url1_variants.append(create_placeholder_variant(item_id, "URL1"))
                log_to_console(f"URL1 empty for {item_id}", "[WARNING]")

            # Download ImageURL2
            url2_variants = []
            if image_url2:
                variant, error = download_image_from_url(image_url2, item_id, "URL2")
                if variant:
                    url2_variants.append(variant)
                else:
                    # Create placeholder for failed URL2
                    url2_variants.append(create_placeholder_variant(item_id, "URL2"))
                    log_to_console(f"URL2 failed for {item_id}: {error}", "[WARNING]")
            else:
                # Create placeholder for empty URL2
                url2_variants.append(create_placeholder_variant(item_id, "URL2"))
                log_to_console(f"URL2 empty for {item_id}", "[WARNING]")

            # Search Google Images using ShortDescription
            google_variants = []
            log_to_console(f"[GOOGLE] Starting Google Images search for {item_id}", "[INFO]")
            log_to_console(f"[GOOGLE] ShortDescription: '{short_description}'", "[INFO]")
            log_to_console(f"[GOOGLE] Images per item: {images_per_item}, Start index: {start_index}", "[INFO]")
            log_to_console(f"[GOOGLE] Sites: {sites}, Filters: {filters}", "[INFO]")
            
            if short_description:
                log_to_console(f"[GOOGLE] ShortDescription found, proceeding with Google Images search", "[INFO]")
                # Fetch Google Images variants
                if images_per_item <= 10:
                    log_to_console(f"[GOOGLE] Single API call (images_per_item={images_per_item} <= 10)", "[INFO]")
                    variants, last_error = fetch_image_variants(short_description, item_id, sites, images_per_item, filters, start=start_index)
                    google_variants = variants
                    log_to_console(f"[GOOGLE] Single call returned {len(variants)} variants, error: {last_error or 'None'}", "[INFO]" if not last_error else "[WARNING]")
                else:
                    log_to_console(f"[GOOGLE] Multiple API calls needed (images_per_item={images_per_item} > 10)", "[INFO]")
                    # Multiple API calls with pagination
                    remaining = images_per_item
                    current_start = start_index
                    last_error = None
                    all_google_variants = []
                    
                    while remaining > 0 and len(all_google_variants) < images_per_item:
                        batch_size = min(10, remaining)
                        log_to_console(f"[GOOGLE] Batch call: batch_size={batch_size}, start={current_start}, remaining={remaining}", "[INFO]")
                        variants, batch_error = fetch_image_variants(short_description, item_id, sites, batch_size, filters, start=current_start)
                        log_to_console(f"[GOOGLE] Batch returned {len(variants)} variants, error: {batch_error or 'None'}", "[INFO]" if not batch_error else "[WARNING]")
                        
                        if batch_error:
                            last_error = batch_error
                            if not variants:
                                log_to_console(f"[GOOGLE] Batch error and no variants, stopping pagination", "[WARNING]")
                                break
                        
                        all_google_variants.extend(variants)
                        remaining -= len(variants)
                        current_start += len(variants)
                        
                        if len(variants) < batch_size:
                            log_to_console(f"[GOOGLE] Received fewer variants than requested ({len(variants)} < {batch_size}), stopping", "[INFO]")
                            break
                    
                    google_variants = all_google_variants[:images_per_item]
                    log_to_console(f"[GOOGLE] Total Google variants collected: {len(google_variants)}", "[INFO]")
                
                if not google_variants:
                    log_to_console(f"[GOOGLE] No Google Images found for {item_id} (search: '{short_description}')", "[WARNING]")
                else:
                    log_to_console(f"[GOOGLE] ✓ Successfully fetched {len(google_variants)} Google Images for {item_id}", "[SUCCESS]")
            else:
                log_to_console(f"[GOOGLE] No ShortDescription for {item_id}, skipping Google Images search", "[WARNING]")

            # Keep URL1 and URL2 separate, Google images separate
            # Build item payload with separate arrays
            items_payload.append({
                "itemId": item_id,
                "ShortDescription": pos_item.get("shortDescription", ""),  # Preserve ShortDescription from input
                "url1Variants": [
                    {
                        "fileName": v["fileName"],
                        "previewUrl": v["previewUrl"],
                        "originalUrl": v["originalUrl"],
                        "source": v["source"],
                        "shortDescription": v["shortDescription"],
                        "description": v["description"],
                        "isPlaceholder": v.get("isPlaceholder", False)
                    }
                    for v in url1_variants
                ],
                "url2Variants": [
                    {
                        "fileName": v["fileName"],
                        "previewUrl": v["previewUrl"],
                        "originalUrl": v["originalUrl"],
                        "source": v["source"],
                        "shortDescription": v["shortDescription"],
                        "description": v["description"],
                        "isPlaceholder": v.get("isPlaceholder", False)
                    }
                    for v in url2_variants
                ],
                "googleVariants": [
                    {
                        "fileName": v["fileName"],
                        "previewUrl": v["previewUrl"],
                        "originalUrl": v["originalUrl"],
                        "source": v["source"],
                        "shortDescription": v["shortDescription"],
                        "description": v["description"],
                        "isPlaceholder": v.get("isPlaceholder", False)
                    }
                    for v in google_variants
                ]
            })

        return jsonify({
            "success": True,
            "items": items_payload,
            "missingItems": missing_items
        })
    except Exception as e:
        log_to_console(f"POS items gallery generate failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/gallery_finalize', methods=['POST'])
def gallery_finalize():
    """Finalize gallery selections by re-downloading selected images and building ZIP
    
    NOTE: CSV generation is currently commented out for testing purposes.
    The code is preserved but disabled to avoid requiring Reference Items CSV updates.
    """
    data = request.json
    selections = data.get('selections', [])
    prefix = ensure_prefix_url(data.get('prefix', '').strip())
    # reference_items = data.get('reference_items', [])  # Commented out - not needed for testing
    # required_items = data.get('required_items', [])  # Commented out - not needed for testing

    if not selections:
        return jsonify({"success": False, "error": "No selections were provided."}), 400

    selection_map = {s.get('itemId'): s for s in selections if s.get('itemId')}

    # Commented out - CSV validation not needed for testing
    # if required_items:
    #     missing_required = [item for item in required_items if item not in selection_map]
    #     if missing_required:
    #         return jsonify({"success": False, "error": f"Missing selections for: {', '.join(missing_required)}"}), 400

    temp_dir = tempfile.mkdtemp(prefix="gallery_finalize_")
    # csv_rows = []  # Commented out - CSV generation disabled
    image_files = []

    try:
        for item_id, selection in selection_map.items():
            original_url = selection.get('originalUrl')
            file_name = selection.get('fileName') or item_id
            # short_desc = selection.get('shortDescription') or item_id  # Commented out - CSV only
            # description = selection.get('description') or short_desc  # Commented out - CSV only
            # source = selection.get('source') or ""  # Commented out - CSV only
            # product_name = selection.get('productName') or item_id  # Commented out - CSV only
            # reference_id = selection.get('referenceId')  # Commented out - CSV only

            if not original_url:
                raise ValueError(f"No image URL provided for {item_id}")

            img_r = requests.get(original_url, timeout=20)
            img_r.raise_for_status()

            extension = get_extension_from_headers(img_r.headers.get("content-type", ""), ".jpg")
            if not file_name.lower().endswith(extension):
                file_name = re.sub(r'\.[^.]+$', '', file_name)
                file_name = f"{file_name}{extension}"

            # base_slug_source = product_name or short_desc or item_id  # Commented out - CSV only
            # base_slug = re.sub(r'[^A-Za-z0-9]+', '_', base_slug_source).strip('_')  # Commented out - CSV only
            # if not base_slug:  # Commented out - CSV only
            #     base_slug = "image"  # Commented out - CSV only
            # name_without_ext = os.path.splitext(file_name)[0]  # Commented out - CSV only
            # match_suffix = re.search(r'(_v\d+)$', name_without_ext, re.IGNORECASE)  # Commented out - CSV only
            # variant_suffix = match_suffix.group(1) if match_suffix else ''  # Commented out - CSV only
            # safe_base = re.sub(r'[^A-Za-z0-9_-]', '_', base_slug) + variant_suffix  # Commented out - CSV only
            # safe_name = f"{safe_base}{extension}"  # Commented out - CSV only
            safe_name = file_name  # Use file_name directly for ZIP
            file_path = os.path.join(temp_dir, safe_name)
            with open(file_path, "wb") as f:
                f.write(img_r.content)

            image_files.append(file_path)

            # CSV row generation commented out - not needed for testing
            # csv_row = [""] * 16
            # csv_row[0] = reference_id or item_id
            # csv_row[1] = short_desc
            # csv_row[2] = description
            # csv_row[3] = f"{prefix}{safe_name}" if prefix else safe_name
            # csv_row[15] = source
            # csv_rows.append(csv_row)

        # CSV replication logic commented out - not needed for testing
        # if reference_items:
        #     replicated_rows = []
        #     source_rows = [row for row in csv_rows if any(row)]
        #     if not source_rows:
        #         source_rows = [[""] * 16]
        #     idx = 0
        #     for ref in reference_items:
        #         template = source_rows[idx % len(source_rows)]
        #         new_row = list(template)
        #         new_row[0] = ref
        #         replicated_rows.append(new_row)
        #         idx += 1
        #     csv_rows = replicated_rows

        # CSV generation commented out - not needed for testing
        # headers = [
        #     "ItemId", "ShortDescription", "Description", "ImageUrl",
        #     "", "", "", "", "", "", "", "", "", "", "", "Source"
        # ]
        # csv_buffer = io.StringIO()
        # writer = csv.writer(csv_buffer)
        # writer.writerow(headers)
        # writer.writerows(csv_rows)
        # csv_content = csv_buffer.getvalue()
        # csv_buffer.close()
        # csv_base64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')
        # from datetime import datetime
        # timestamp = datetime.now().strftime('%y%m%d-%H%M')
        # csv_filename = f"imagedownload_{timestamp}.csv"

        # Generate timestamp for ZIP filename
        from datetime import datetime
        timestamp = datetime.now().strftime('%y%m%d-%H%M')
        zip_filename = f"downloaded_items_{timestamp}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for image_path in image_files:
                zipf.write(image_path, arcname=os.path.basename(image_path))

        with open(zip_path, "rb") as zip_file:
            zip_base64 = base64.b64encode(zip_file.read()).decode("utf-8")

        log_to_console(f"Gallery finalize complete for {len(selection_map)} items (ZIP only, CSV disabled)", "[API]")

        return jsonify({
            "success": True,
            # "csv_content": csv_base64,  # Commented out - CSV generation disabled
            # "csv_filename": csv_filename,  # Commented out - CSV generation disabled
            "zip_content": zip_base64,
            "zip_filename": zip_filename,
            # "row_count": len(csv_rows)  # Commented out - CSV generation disabled
            "image_count": len(image_files)
        })
    except Exception as e:
        log_to_console(f"Gallery finalize failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

@app.route('/api/cleanup_csv', methods=['POST'])
def cleanup_csv():
    """Clean up and align CSV with item numbers"""
    # This will be implemented to validate images and align CSV
    # For now, return placeholder
    return jsonify({"success": False, "error": "Not yet implemented"})

@app.route('/api/upload_cloudinary', methods=['POST'])
def upload_cloudinary():
    """Upload images to Cloudinary"""
    try:
        # Configure Cloudinary
        cloudinary.config(
            cloud_name=CLOUD_NAME,
            api_key=API_KEY_CLOUD,
            api_secret=API_SECRET_CLOUD
        )
        
        # Get form data
        upload_folder = request.form.get('folder', '').strip()
        upload_preset = request.form.get('preset', '').strip()
        
        # Get uploaded files
        if 'files' not in request.files:
            return jsonify({"success": False, "error": "No files provided"})
        
        files = request.files.getlist('files')
        if not files or files[0].filename == '':
            return jsonify({"success": False, "error": "No files selected"})
        
        # Filter image files only
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        image_files = []
        for f in files:
            filename = f.filename.lower()
            if any(filename.endswith(ext) for ext in image_extensions):
                image_files.append(f)
        
        if not image_files:
            return jsonify({"success": False, "error": "No valid image files found. Supported formats: JPG, PNG, GIF, WebP, BMP"})
        
        uploaded_results = []
        failed_uploads = []
        upload_start_time = datetime.now()
        
        log_to_console(f"Starting upload of {len(image_files)} images to Cloudinary...")
        
        for idx, img_file in enumerate(image_files):
            file_start_time = datetime.now()
            try:
                filename = img_file.filename
                # Extract just the filename (basename) to ignore any directory structure
                # This ensures files are uploaded directly to the specified folder
                filename_only = os.path.basename(filename)
                log_to_console(f"Uploading {idx + 1}/{len(image_files)}: {filename_only} (from {filename})")
                
                # Prepare upload options
                upload_options = {}
                if upload_folder:
                    # Use folder in public_id: "folder/filename" (without extension)
                    # Only use the filename, not any directory path
                    name_without_ext = os.path.splitext(filename_only)[0]
                    upload_options['public_id'] = f"{upload_folder}/{name_without_ext}"
                else:
                    # No folder, use just the filename without extension
                    upload_options['public_id'] = os.path.splitext(filename_only)[0]
                
                if upload_preset:
                    upload_options['upload_preset'] = upload_preset
                
                # Read file content
                img_file.seek(0)
                file_content = img_file.read()
                
                # Upload to Cloudinary
                result = cloudinary.uploader.upload(
                    file_content,
                    **upload_options
                )
                
                file_end_time = datetime.now()
                duration = (file_end_time - file_start_time).total_seconds()
                
                cloudinary_url = result.get('secure_url') or result.get('url', '')
                
                uploaded_results.append({
                    "filename": filename,
                    "filename_only": filename_only,
                    "cloudinary_url": cloudinary_url,
                    "public_id": result.get('public_id', ''),
                    "success": True,
                    "duration": round(duration, 2),
                    "index": idx + 1,
                    "total": len(image_files)
                })
                
                log_to_console(f"✓ Successfully uploaded {idx + 1}/{len(image_files)}: {filename_only} ({duration:.2f}s)")
                
            except cloudinary.exceptions.Error as e:
                file_end_time = datetime.now()
                duration = (file_end_time - file_start_time).total_seconds()
                error_msg = str(e)
                filename_only = os.path.basename(filename) if filename else "unknown"
                log_to_console(f"✗ Failed to upload {idx + 1}/{len(image_files)}: {filename_only} ({duration:.2f}s) - {error_msg}", "[ERROR]")
                failed_uploads.append({
                    "filename": filename,
                    "filename_only": filename_only,
                    "error": error_msg,
                    "success": False,
                    "duration": round(duration, 2),
                    "index": idx + 1,
                    "total": len(image_files)
                })
            except Exception as e:
                file_end_time = datetime.now()
                duration = (file_end_time - file_start_time).total_seconds()
                error_msg = str(e)
                filename_only = os.path.basename(filename) if filename else "unknown"
                log_to_console(f"✗ Error uploading {idx + 1}/{len(image_files)}: {filename_only} ({duration:.2f}s) - {error_msg}", "[ERROR]")
                failed_uploads.append({
                    "filename": filename,
                    "filename_only": filename_only,
                    "error": error_msg,
                    "success": False,
                    "duration": round(duration, 2),
                    "index": idx + 1,
                    "total": len(image_files)
                })
        
        upload_end_time = datetime.now()
        total_duration = (upload_end_time - upload_start_time).total_seconds()
        log_to_console(f"Upload complete: {len(uploaded_results)} successful, {len(failed_uploads)} failed (Total time: {total_duration:.2f}s)")
        
        return jsonify({
            "success": True,
            "uploaded": uploaded_results,
            "failed": failed_uploads,
            "total": len(image_files),
            "successful": len(uploaded_results),
            "failed_count": len(failed_uploads)
        })
        
    except Exception as e:
        log_to_console(f"Upload to Cloudinary failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/upload_cloudinary_stream', methods=['POST'])
def upload_cloudinary_stream():
    """Upload images to Cloudinary with Server-Sent Events for real-time progress"""
    def generate():
        try:
            log_to_console('[CLOUDINARY] ========================================')
            log_to_console('[CLOUDINARY] upload_cloudinary_stream() called')
            log_to_console('[CLOUDINARY] ========================================')
            
            # Configure Cloudinary
            log_to_console(f'[CLOUDINARY] Configuring Cloudinary: cloud_name={CLOUD_NAME}, api_key={API_KEY_CLOUD[:10] if API_KEY_CLOUD else "N/A"}...')
            cloudinary.config(
                cloud_name=CLOUD_NAME,
                api_key=API_KEY_CLOUD,
                api_secret=API_SECRET_CLOUD
            )
            log_to_console('[CLOUDINARY] Cloudinary configuration complete')
            
            # Get form data
            upload_folder = request.form.get('folder', '').strip()
            upload_preset = request.form.get('preset', '').strip()
            log_to_console(f'[CLOUDINARY] Upload parameters: folder="{upload_folder}", preset="{upload_preset}"')
            
            # Get uploaded files
            if 'files' not in request.files:
                log_to_console('[CLOUDINARY] ERROR: No files in request.files')
                yield f"data: {json.dumps({'type': 'error', 'message': 'No files provided'})}\n\n"
                return
            
            files = request.files.getlist('files')
            log_to_console(f'[CLOUDINARY] Received {len(files)} file(s) from request')
            if not files or files[0].filename == '':
                log_to_console('[CLOUDINARY] ERROR: No files selected or empty filename')
                yield f"data: {json.dumps({'type': 'error', 'message': 'No files selected'})}\n\n"
                return
            
            # Filter image files only
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
            image_files = []
            total_size = 0
            for f in files:
                filename = f.filename.lower()
                if any(filename.endswith(ext) for ext in image_extensions):
                    f.seek(0, 2)  # Seek to end
                    file_size = f.tell()
                    f.seek(0)  # Reset to beginning
                    total_size += file_size
                    image_files.append(f)
                    log_to_console(f'[CLOUDINARY] Added image file: {f.filename} ({file_size} bytes)')
            
            log_to_console(f'[CLOUDINARY] Filtered to {len(image_files)} image file(s). Total size: {total_size} bytes ({total_size / 1024 / 1024:.2f} MB)')
            
            if not image_files:
                log_to_console('[CLOUDINARY] ERROR: No valid image files found')
                yield f"data: {json.dumps({'type': 'error', 'message': 'No valid image files found. Supported formats: JPG, PNG, GIF, WebP, BMP'})}\n\n"
                return
            
            upload_start_time = datetime.now()
            log_to_console(f'[CLOUDINARY] Starting upload of {len(image_files)} images at {upload_start_time.strftime("%H:%M:%S")}')
            
            # Send start event
            yield f"data: {json.dumps({'type': 'start', 'total': len(image_files), 'folder': upload_folder, 'preset': upload_preset})}\n\n"
            
            uploaded_results = []
            failed_uploads = []
            
            for idx, img_file in enumerate(image_files):
                file_start_time = datetime.now()
                try:
                    filename = img_file.filename
                    filename_only = os.path.basename(filename)
                    
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Processing: {filename_only}')
                    
                    # Get file size
                    img_file.seek(0, 2)
                    file_size = img_file.tell()
                    img_file.seek(0)
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] File size: {file_size} bytes ({file_size / 1024:.2f} KB)')
                    
                    # Send progress event
                    yield f"data: {json.dumps({'type': 'progress', 'index': idx + 1, 'total': len(image_files), 'filename': filename_only, 'status': 'uploading'})}\n\n"
                    
                    # Prepare upload options
                    upload_options = {}
                    if upload_folder:
                        name_without_ext = os.path.splitext(filename_only)[0]
                        upload_options['public_id'] = f"{upload_folder}/{name_without_ext}"
                    else:
                        upload_options['public_id'] = os.path.splitext(filename_only)[0]
                    
                    if upload_preset:
                        upload_options['upload_preset'] = upload_preset
                    
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Upload options: {json.dumps(upload_options)}')
                    
                    # Read file content
                    read_start = datetime.now()
                    img_file.seek(0)
                    file_content = img_file.read()
                    read_duration = (datetime.now() - read_start).total_seconds()
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] File read complete ({read_duration:.3f}s). Content size: {len(file_content)} bytes')
                    
                    # Upload to Cloudinary
                    upload_api_start = datetime.now()
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Calling cloudinary.uploader.upload()...')
                    result = cloudinary.uploader.upload(
                        file_content,
                        **upload_options
                    )
                    upload_api_duration = (datetime.now() - upload_api_start).total_seconds()
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Cloudinary API call completed ({upload_api_duration:.3f}s)')
                    
                    file_end_time = datetime.now()
                    duration = (file_end_time - file_start_time).total_seconds()
                    
                    cloudinary_url = result.get('secure_url') or result.get('url', '')
                    public_id = result.get('public_id', '')
                    
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Upload SUCCESS: {cloudinary_url} (total duration: {duration:.2f}s)')
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Public ID: {public_id}')
                    
                    uploaded_results.append({
                        "filename": filename,
                        "filename_only": filename_only,
                        "cloudinary_url": cloudinary_url,
                        "public_id": public_id,
                        "success": True,
                        "duration": round(duration, 2),
                        "index": idx + 1,
                        "total": len(image_files)
                    })
                    
                    # Send success event
                    yield f"data: {json.dumps({'type': 'success', 'index': idx + 1, 'total': len(image_files), 'filename': filename_only, 'cloudinary_url': cloudinary_url, 'duration': round(duration, 2)})}\n\n"
                    
                except cloudinary.exceptions.Error as e:
                    file_end_time = datetime.now()
                    duration = (file_end_time - file_start_time).total_seconds()
                    error_msg = str(e)
                    filename_only = os.path.basename(filename) if filename else "unknown"
                    
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Cloudinary API ERROR: {error_msg}', 'error')
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Error type: {type(e).__name__}', 'error')
                    import traceback
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Traceback: {traceback.format_exc()}', 'error')
                    
                    failed_uploads.append({
                        "filename": filename,
                        "filename_only": filename_only,
                        "error": error_msg,
                        "success": False,
                        "duration": round(duration, 2),
                        "index": idx + 1,
                        "total": len(image_files)
                    })
                    
                    # Send error event
                    yield f"data: {json.dumps({'type': 'error', 'index': idx + 1, 'total': len(image_files), 'filename': filename_only, 'error': error_msg, 'duration': round(duration, 2)})}\n\n"
                    
                except Exception as e:
                    file_end_time = datetime.now()
                    duration = (file_end_time - file_start_time).total_seconds()
                    error_msg = str(e)
                    filename_only = os.path.basename(filename) if filename else "unknown"
                    
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] EXCEPTION: {error_msg}', 'error')
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Exception type: {type(e).__name__}', 'error')
                    import traceback
                    log_to_console(f'[CLOUDINARY] [{idx + 1}/{len(image_files)}] Traceback: {traceback.format_exc()}', 'error')
                    
                    failed_uploads.append({
                        "filename": filename,
                        "filename_only": filename_only,
                        "error": error_msg,
                        "success": False,
                        "duration": round(duration, 2),
                        "index": idx + 1,
                        "total": len(image_files)
                    })
                    
                    # Send error event
                    yield f"data: {json.dumps({'type': 'error', 'index': idx + 1, 'total': len(image_files), 'filename': filename_only, 'error': error_msg, 'duration': round(duration, 2)})}\n\n"
            
            upload_end_time = datetime.now()
            total_duration = (upload_end_time - upload_start_time).total_seconds()
            
            log_to_console(f'[CLOUDINARY] Upload complete: {len(uploaded_results)} successful, {len(failed_uploads)} failed. Total duration: {total_duration:.2f}s')
            if failed_uploads:
                log_to_console(f'[CLOUDINARY] Failed uploads: {json.dumps([f["filename_only"] for f in failed_uploads])}', 'error')
            
            # Send complete event
            yield f"data: {json.dumps({'type': 'complete', 'successful': len(uploaded_results), 'failed': len(failed_uploads), 'total': len(image_files), 'total_duration': round(total_duration, 2), 'uploaded': uploaded_results, 'failed': failed_uploads})}\n\n"
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            error_traceback = traceback.format_exc()
            log_to_console(f'[CLOUDINARY] FATAL ERROR in generate(): {error_msg}', 'error')
            log_to_console(f'[CLOUDINARY] Traceback: {error_traceback}', 'error')
            yield f"data: {json.dumps({'type': 'error', 'message': error_msg, 'traceback': error_traceback})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/api/update_wm', methods=['POST'])
def update_wm():
    """Bulk import items to Manhattan WMS"""
    data = request.json
    org = data.get('org', '').strip()
    token = data.get('token', '').strip()
    csv_data = data.get('csv_data', [])  # Array of rows

    if not org or not token:
        return jsonify({"success": False, "error": "ORG and token required"})

    if not csv_data:
        return jsonify({"success": False, "error": "No CSV data provided"})

    try:
        data_payload = []
        for row in csv_data:
            if len(row) >= 4 and row[0].strip() and row[3].strip():
                data_payload.append({
                    "ItemId": row[0].strip(),
                    "ShortDescription": row[1].strip(),
                    "Description": row[2].strip(),
                    "ImageUrl": row[3].strip()
                })

        if not data_payload:
            return jsonify({"success": False, "error": "No valid items in CSV data"})

        headers_dict = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "selectedOrganization": org.upper(),
            "selectedLocation": f"{org.upper()}-DM1"
        }

        log_to_console(f"Uploading {len(data_payload)} items to WMS for ORG: {org}")

        r = requests.post(
            BULK_IMPORT_URL,
            json={"Data": data_payload},
            headers=headers_dict,
            timeout=60,
            verify=False
        )

        trace_id = r.headers.get("CP-TRACE-ID", "N/A")
        success = False
        messages = []
        exceptions = []

        if r.status_code == 401:
            return jsonify({"success": False, "error": "Token expired", "requires_reauth": True})

        try:
            resp_json = r.json()
            success = bool(resp_json.get("success"))
            msg_list = resp_json.get("messages", {}).get("Message", [])
            messages = [m.get("Description", "") for m in msg_list if m.get("Description")]
            exceptions = [
                f"{e.get('messageKey', 'Error')}: {e.get('message', '')}"
                for e in resp_json.get("exceptions", [])
            ]
        except:
            messages = [r.text[:500]]

        failed_count = len(exceptions) if success else len(data_payload)

        log_to_console(f"WM Update complete: {len(data_payload) - failed_count} success, {failed_count} failed")

        return jsonify({
            "success": success,
            "total": len(data_payload),
            "success_count": len(data_payload) - failed_count,
            "failed_count": failed_count,
            "trace_id": trace_id,
            "messages": messages,
            "exceptions": exceptions
        })
    except Exception as e:
        log_to_console(f"WM Update failed: {str(e)}", "[ERROR]")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/cloudinary_config', methods=['GET'])
def cloudinary_config():
    """Get Cloudinary configuration for client-side uploads (safe to expose)"""
    return jsonify({
        "success": True,
        "cloud_name": CLOUD_NAME,
        "upload_preset": ""  # User provides this via UI
    })

if __name__ == '__main__':
    app.run(debug=True)

