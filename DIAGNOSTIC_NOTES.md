# Diagnostic Notes: vonmaur.com Connection Issues

## Error: "An existing connection was forcibly closed by the remote host"

This error indicates that **vonmaur.com is actively closing the connection** before data can be transferred. This is different from a timeout or a 403/404 error.

### Possible Causes:

1. **Bot Detection / Anti-Scraping Protection**
   - vonmaur.com may use Cloudflare, Akamai, or similar services
   - These services can detect automated requests and close connections
   - Even with proper headers, behavioral patterns can trigger blocks

2. **TLS/SSL Handshake Issues**
   - The server might require specific TLS versions or cipher suites
   - Python requests might not match what Chrome uses exactly

3. **Rate Limiting / Connection Limits**
   - Too many connections from the same IP
   - Connection pattern detection (rapid sequential requests)

4. **Vercel Environment Restrictions**
   - Vercel's serverless functions run on AWS Lambda
   - Lambda IPs might be flagged as suspicious
   - Geographic restrictions

5. **Missing Browser Fingerprint**
   - Modern bot detection looks at:
     - TLS fingerprint
     - HTTP/2 settings
     - Connection timing
     - Browser-specific behaviors

### Why It Works in Chrome But Not in Code:

Chrome has:
- Full TLS/SSL stack matching browser expectations
- Complete browser fingerprint
- Established session/cookies
- Proper HTTP/2 support
- Real browser rendering engine

Our code has:
- Python requests library (different TLS stack)
- No JavaScript execution
- No cookies/session (unless we establish one)
- Different connection patterns

### Potential Solutions:

1. **Use a Headless Browser (Selenium/Playwright)**
   - Most reliable but slowest
   - Requires additional dependencies
   - Can be detected if not configured properly

2. **Use a Proxy Service**
   - Rotate IPs
   - Use residential proxies
   - More expensive

3. **Pre-fetch with Browser Extension**
   - User clicks "Download" in browser
   - Extension downloads images
   - Bypasses server-side restrictions

4. **Accept Preview URLs Only**
   - Google's CDN is more permissive
   - Lower quality but reliable
   - Already implemented as fallback

5. **Use Cloudflare Workers / Browser Extension**
   - Run code in browser context
   - Has full browser fingerprint
   - More complex to implement

### Testing Recommendations:

1. Test from different networks (home vs office)
2. Test from Vercel's environment directly
3. Check if vonmaur.com uses Cloudflare (look for `cf-ray` header)
4. Try establishing a session first (visit homepage, then download)
5. Add delays between requests
6. Try different User-Agent strings

### Current Status:

- ✅ Preview URLs work (Google CDN)
- ❌ Original URLs fail (vonmaur.com closes connection)
- ✅ Fallback mechanism works
- ⚠️ Need to decide: Accept lower quality or implement more complex solution
