// Statsig Analytics Integration
// Initialize Statsig SDK and provide tracking functions
//
// CONFIGURATION REQUIRED:
// Set your Statsig Client SDK Key below or via window.STATSIG_CLIENT_KEY
// Get your key from: https://console.statsig.com/ -> Settings -> API Keys
// The key should start with "client-"

// TODO: Replace 'client-default' with your actual Statsig Client SDK Key
const DEFAULT_STATSIG_CLIENT_KEY = 'client-default';

let statsigClient = null;
let statsigInitialized = false;

// Initialize Statsig when SDK is loaded
async function initStatsig() {
  // Try to get Statsig client SDK key from multiple sources:
  // 1. window.STATSIG_CLIENT_KEY (if set manually)
  // 2. Fetch from server endpoint /api/statsig-config
  // 3. Use default (will show warning)
  
  let STATSIG_CLIENT_KEY = window.STATSIG_CLIENT_KEY;
  
  // If not set, try to fetch from server
  if (!STATSIG_CLIENT_KEY || STATSIG_CLIENT_KEY === DEFAULT_STATSIG_CLIENT_KEY) {
    try {
      const response = await fetch('/api/statsig-config');
      const data = await response.json();
      if (data.key) {
        STATSIG_CLIENT_KEY = data.key;
        console.log('[Statsig] Client key loaded from server');
      } else if (data.error) {
        console.warn('[Statsig]', data.error);
        if (data.note) console.warn('[Statsig]', data.note);
      }
    } catch (error) {
      console.warn('[Statsig] Failed to fetch client key from server:', error.message);
    }
  }
  
  // Fall back to default if still not set
  if (!STATSIG_CLIENT_KEY) {
    STATSIG_CLIENT_KEY = DEFAULT_STATSIG_CLIENT_KEY;
  }
  
  // Warn if using default key
  if (STATSIG_CLIENT_KEY === DEFAULT_STATSIG_CLIENT_KEY) {
    console.warn('[Statsig] Using default key. Please set STATSIG_CLIENT_KEY environment variable in Vercel project settings.');
    console.warn('[Statsig] Note: You need a Client SDK Key (starts with "client-"), not a Server Secret (starts with "secret-")');
  }
  
  try {
    // Check if Statsig SDK is available
    // The new @statsig/js-client exposes Statsig.StatsigClient
    if (typeof Statsig !== 'undefined' && typeof Statsig.StatsigClient !== 'undefined') {
      const user = { userID: getUserId() };
      statsigClient = new Statsig.StatsigClient(STATSIG_CLIENT_KEY, user);
      await statsigClient.initializeAsync();
      statsigInitialized = true;
      console.log('[Statsig] Initialized successfully');
      
      // Track app opened event
      logEvent('app_opened', {});
    } else if (typeof StatsigClient !== 'undefined') {
      // Fallback: direct StatsigClient global (if exposed differently)
      const user = { userID: getUserId() };
      statsigClient = new StatsigClient(STATSIG_CLIENT_KEY, user);
      await statsigClient.initializeAsync();
      statsigInitialized = true;
      console.log('[Statsig] Initialized successfully (direct StatsigClient)');
      
      // Track app opened event
      logEvent('app_opened', {});
    } else {
      console.warn('[Statsig] SDK not loaded. Make sure to include Statsig SDK script in HTML.');
      console.warn('[Statsig] Available globals:', Object.keys(window).filter(k => k.toLowerCase().includes('statsig')));
    }
  } catch (error) {
    console.error('[Statsig] Initialization error:', error);
  }
}

// Log an event to Statsig
function logEvent(eventName, eventValue = {}, user = null) {
  if (!statsigInitialized || !statsigClient) {
    console.warn(`[Statsig] Not initialized. Event "${eventName}" not tracked.`);
    return;
  }

  try {
    // New @statsig/js-client SDK API: logEvent(eventName, value, metadata)
    // value can be string or number, metadata is Record<string, string>
    // User is set during initialization, so we don't pass it here
    
    // Get app info and add to metadata
    const appInfo = getAppInfo();
    
    // Convert eventValue object to metadata (string values only for metadata)
    const metadata = {
      app_name: appInfo.appName,
      app_version: appInfo.appVersion
    };
    if (eventValue && typeof eventValue === 'object') {
      for (const [key, value] of Object.entries(eventValue)) {
        metadata[key] = String(value);
      }
    }
    
    // New SDK: logEvent(eventName, value?, metadata?)
    statsigClient.logEvent(eventName, null, metadata);
    console.log(`[Statsig] Event logged: ${eventName}`, metadata);
  } catch (error) {
    console.error(`[Statsig] Error logging event "${eventName}":`, error);
  }
}

// Check a feature gate
async function checkGate(gateName, user = null) {
  if (!statsigInitialized || !statsigClient) {
    console.warn(`[Statsig] Not initialized. Gate "${gateName}" check skipped.`);
    return false;
  }

  try {
    // New @statsig/js-client SDK: checkGate(name, options?)
    // User is set during initialization, so we just pass the gate name
    return statsigClient.checkGate(gateName);
  } catch (error) {
    console.error(`[Statsig] Error checking gate "${gateName}":`, error);
    return false;
  }
}

// Get experiment value
async function getExperiment(experimentName, user = null) {
  if (!statsigInitialized || !statsigClient) {
    console.warn(`[Statsig] Not initialized. Experiment "${experimentName}" check skipped.`);
    return null;
  }

  try {
    // New @statsig/js-client SDK: getExperiment(name, options?)
    // User is set during initialization, so we just pass the experiment name
    return statsigClient.getExperiment(experimentName);
  } catch (error) {
    console.error(`[Statsig] Error getting experiment "${experimentName}":`, error);
    return null;
  }
}

// Helper: Get current user ID (generate if not available)
function getUserId() {
  let userId = localStorage.getItem('statsig_user_id');
  if (!userId) {
    // Generate a simple user ID based on browser fingerprint
    userId = 'user_' + Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
    localStorage.setItem('statsig_user_id', userId);
  }
  return userId;
}

// Helper: Get current org from the form
function getCurrentOrg() {
  const orgInput = document.getElementById('org');
  return orgInput ? orgInput.value.trim() : 'unknown';
}

// Helper: Get app name and version from page title
function getAppInfo() {
  const title = document.title || '';
  // Extract version from title like "POS Items v0.1.7"
  const versionMatch = title.match(/v(\d+\.\d+\.\d+)/i);
  const version = versionMatch ? versionMatch[1] : 'unknown';
  // Extract app name (everything before "v" or just use title)
  const appNameMatch = title.match(/^(.+?)\s+v\d+/i);
  const appName = appNameMatch ? appNameMatch[1].trim() : (title.split(' v')[0] || 'POS Items');
  return { appName, appVersion: version };
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    // Load Statsig SDK from CDN first, then initialize
    loadStatsigSDK();
  });
} else {
  loadStatsigSDK();
}

// Load Statsig SDK from CDN
function loadStatsigSDK() {
  // Check if already loaded
  if (typeof Statsig !== 'undefined' || typeof StatsigClient !== 'undefined') {
    initStatsig();
    return;
  }

  // Try multiple sources for Statsig SDK
  // Using the new @statsig/js-client package (statsig-js is deprecated)
  // First try local file, then CDN
  const sources = [
    '/statsig-js-client.min.js',  // Local file (copied from node_modules)
    'https://cdn.jsdelivr.net/npm/@statsig/js-client@latest/build/statsig-js-client.min.js',
    'https://unpkg.com/@statsig/js-client@latest/build/statsig-js-client.min.js'
  ];

  let currentSource = 0;

  function tryLoadSource() {
    if (currentSource >= sources.length) {
      console.error('[Statsig] Failed to load SDK from all sources');
      console.warn('[Statsig] Make sure @statsig/js-client is installed via npm');
      return;
    }

    const script = document.createElement('script');
    script.src = sources[currentSource];
    script.async = true;
    script.onload = () => {
      const sourceName = sources[currentSource].startsWith('/') ? 'local file' : 'CDN';
      console.log(`[Statsig] SDK loaded from ${sourceName}: ${sources[currentSource]}`);
      initStatsig();
    };
    script.onerror = () => {
      console.warn(`[Statsig] Failed to load from ${sources[currentSource]}, trying next source...`);
      currentSource++;
      tryLoadSource();
    };
    document.head.appendChild(script);
  }

  tryLoadSource();
}

// Export functions for use in other scripts
window.StatsigTracking = {
  logEvent,
  checkGate,
  getExperiment,
  isInitialized: () => statsigInitialized
};

