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
HA_WEBHOOK_URL = "http://sidmsmith.zapto.org:8123/api/webhook/manhattan_item_generator"

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
    site_query = " OR ".join(f"site:{s}" for s in sites) if sites else ""
    query = f"{product_name} ({site_query})" if site_query else product_name

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

    last_error = None
    variants = []

    for attempt in range(3):
        try:
            r = requests.get(URL_DOWN, params=params, timeout=15)
            if r.status_code == 429:
                last_error = "Google API rate limited (429)"
                continue
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items:
                last_error = "No images returned"
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
            break
        except requests.exceptions.Timeout:
            last_error = "Google API timeout"
        except requests.exceptions.RequestException as e:
            last_error = f"Google API error: {str(e)[:80]}"
            break

    return variants, last_error

# =============================================================================
# API ROUTES
# =============================================================================

@app.route('/api/app_opened', methods=['POST'])
def app_opened():
    """Track app opened event"""
    try:
        payload = {
            "event": "app_opened",
            "version": "v0.0.5",
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
    
    Supports pagination via 'start_index' parameter for "Add New Images" functionality.
    If start_index is provided, it will fetch images starting from that position.
    """
    data = request.json
    products = data.get('products', [])
    item_numbers = data.get('item_numbers', [])
    sites_str = data.get('sites', DEFAULT_SITES)
    images_per_item = int(data.get('images_per_item', DEFAULT_IMAGES_PER_ITEM))
    filter_str = data.get('image_filters', '').strip()
    start_index = int(data.get('start_index', 1))  # For pagination (1-based)

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

@app.route('/api/gallery_finalize', methods=['POST'])
def gallery_finalize():
    """Finalize gallery selections by re-downloading selected images and building CSV/ZIP"""
    data = request.json
    selections = data.get('selections', [])
    prefix = ensure_prefix_url(data.get('prefix', '').strip())
    reference_items = data.get('reference_items', [])
    required_items = data.get('required_items', [])

    if not selections:
        return jsonify({"success": False, "error": "No selections were provided."}), 400

    selection_map = {s.get('itemId'): s for s in selections if s.get('itemId')}

    if required_items:
        missing_required = [item for item in required_items if item not in selection_map]
        if missing_required:
            return jsonify({"success": False, "error": f"Missing selections for: {', '.join(missing_required)}"}), 400

    temp_dir = tempfile.mkdtemp(prefix="gallery_finalize_")
    csv_rows = []
    image_files = []

    try:
        for item_id, selection in selection_map.items():
            original_url = selection.get('originalUrl')
            file_name = selection.get('fileName') or item_id
            short_desc = selection.get('shortDescription') or item_id
            description = selection.get('description') or short_desc
            source = selection.get('source') or ""
            product_name = selection.get('productName') or item_id
            reference_id = selection.get('referenceId')

            if not original_url:
                raise ValueError(f"No image URL provided for {item_id}")

            img_r = requests.get(original_url, timeout=20)
            img_r.raise_for_status()

            extension = get_extension_from_headers(img_r.headers.get("content-type", ""), ".jpg")
            if not file_name.lower().endswith(extension):
                file_name = re.sub(r'\.[^.]+$', '', file_name)
                file_name = f"{file_name}{extension}"

            base_slug_source = product_name or short_desc or item_id
            base_slug = re.sub(r'[^A-Za-z0-9]+', '_', base_slug_source).strip('_')
            if not base_slug:
                base_slug = "image"
            name_without_ext = os.path.splitext(file_name)[0]
            match_suffix = re.search(r'(_v\d+)$', name_without_ext, re.IGNORECASE)
            variant_suffix = match_suffix.group(1) if match_suffix else ''
            safe_base = re.sub(r'[^A-Za-z0-9_-]', '_', base_slug) + variant_suffix
            safe_name = f"{safe_base}{extension}"
            file_path = os.path.join(temp_dir, safe_name)
            with open(file_path, "wb") as f:
                f.write(img_r.content)

            image_files.append(file_path)

            csv_row = [""] * 16
            csv_row[0] = reference_id or item_id
            csv_row[1] = short_desc
            csv_row[2] = description
            csv_row[3] = f"{prefix}{safe_name}" if prefix else safe_name
            csv_row[15] = source
            csv_rows.append(csv_row)

        if reference_items:
            replicated_rows = []
            source_rows = [row for row in csv_rows if any(row)]
            if not source_rows:
                source_rows = [[""] * 16]
            idx = 0
            for ref in reference_items:
                template = source_rows[idx % len(source_rows)]
                new_row = list(template)
                new_row[0] = ref
                replicated_rows.append(new_row)
                idx += 1
            csv_rows = replicated_rows

        headers = [
            "ItemId", "ShortDescription", "Description", "ImageUrl",
            "", "", "", "", "", "", "", "", "", "", "", "Source"
        ]

        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(headers)
        writer.writerows(csv_rows)
        csv_content = csv_buffer.getvalue()
        csv_buffer.close()

        csv_base64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')
        unique_suffix = uuid.uuid4().hex[:8]
        csv_filename = f"imagedownload_{unique_suffix}.csv"

        zip_filename = f"downloaded_items_{unique_suffix}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for image_path in image_files:
                zipf.write(image_path, arcname=os.path.basename(image_path))

        with open(zip_path, "rb") as zip_file:
            zip_base64 = base64.b64encode(zip_file.read()).decode("utf-8")

        log_to_console(f"Gallery finalize complete for {len(selection_map)} items", "[API]")

        return jsonify({
            "success": True,
            "csv_content": csv_base64,
            "csv_filename": csv_filename,
            "zip_content": zip_base64,
            "zip_filename": zip_filename,
            "row_count": len(csv_rows)
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
                yield f"data: {json.dumps({'type': 'error', 'message': 'No files provided'})}\n\n"
                return
            
            files = request.files.getlist('files')
            if not files or files[0].filename == '':
                yield f"data: {json.dumps({'type': 'error', 'message': 'No files selected'})}\n\n"
                return
            
            # Filter image files only
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
            image_files = []
            for f in files:
                filename = f.filename.lower()
                if any(filename.endswith(ext) for ext in image_extensions):
                    image_files.append(f)
            
            if not image_files:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No valid image files found. Supported formats: JPG, PNG, GIF, WebP, BMP'})}\n\n"
                return
            
            upload_start_time = datetime.now()
            
            # Send start event
            yield f"data: {json.dumps({'type': 'start', 'total': len(image_files), 'folder': upload_folder, 'preset': upload_preset})}\n\n"
            
            uploaded_results = []
            failed_uploads = []
            
            for idx, img_file in enumerate(image_files):
                file_start_time = datetime.now()
                try:
                    filename = img_file.filename
                    filename_only = os.path.basename(filename)
                    
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
                    
                    # Send success event
                    yield f"data: {json.dumps({'type': 'success', 'index': idx + 1, 'total': len(image_files), 'filename': filename_only, 'cloudinary_url': cloudinary_url, 'duration': round(duration, 2)})}\n\n"
                    
                except cloudinary.exceptions.Error as e:
                    file_end_time = datetime.now()
                    duration = (file_end_time - file_start_time).total_seconds()
                    error_msg = str(e)
                    filename_only = os.path.basename(filename) if filename else "unknown"
                    
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
            
            # Send complete event
            yield f"data: {json.dumps({'type': 'complete', 'successful': len(uploaded_results), 'failed': len(failed_uploads), 'total': len(image_files), 'total_duration': round(total_duration, 2), 'uploaded': uploaded_results, 'failed': failed_uploads})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
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

if __name__ == '__main__':
    app.run(debug=True)

