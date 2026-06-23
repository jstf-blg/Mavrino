<?php
/**
 * Plugin Name: Mavrino SEO, Ads & Search
 * Description: (1) Outputs per-post JSON-LD schema into <head>. (2) Serves /ads.txt
 *              for Google AdSense. (3) Captures zero-result site searches and exposes
 *              them via a secured REST endpoint. (4) Injects the Mavrino visual system
 *              (trust-tone base + warm high-contrast CTA + reserved ad space).
 *              (5) Emits Search Console / Bing / Pinterest verification tags and serves
 *              the IndexNow key file for instant Bing/Yandex indexing.
 * Version:     1.4
 * Author:      Mavrino
 *
 * INSTALL (one-time):
 *   Zip this file → WP Admin → Plugins → Add New → Upload Plugin → Activate.
 *   (Or drop it in wp-content/mu-plugins/ for auto-activation.)
 */

if (!defined('ABSPATH')) { exit; }

// ── Config ───────────────────────────────────────────────────────────────────
if (!defined('MAVRINO_ADSENSE_PUB_ID')) {
    define('MAVRINO_ADSENSE_PUB_ID', 'pub-2702323126353107');
}
// Shared secret guarding the search-requests REST endpoint (must match the pipeline's
// MAVRINO_SEARCH_SECRET env var). NEVER hardcode it here (this file is in a public repo).
// Set it as a server env var, or define MAVRINO_SEARCH_SECRET in wp-config.php / a
// private mu-plugin. If unset, the REST endpoints fail closed (deny all).
if (!defined('MAVRINO_SEARCH_SECRET')) {
    $mav_env = getenv('MAVRINO_SEARCH_SECRET');
    define('MAVRINO_SEARCH_SECRET', $mav_env ? $mav_env : '');
}

// ── Site verification (paste each token here, then re-upload this plugin) ─────
// Google Search Console: add a URL-prefix property for https://mavrino.com → choose
//   the "HTML tag" method → copy the content="..." value into MAVRINO_GOOGLE_VERIFY.
// Bing Webmaster Tools: same idea → MAVRINO_BING_VERIFY. Pinterest: MAVRINO_PINTEREST_VERIFY.
if (!defined('MAVRINO_GOOGLE_VERIFY'))    { define('MAVRINO_GOOGLE_VERIFY', ''); }
if (!defined('MAVRINO_BING_VERIFY'))      { define('MAVRINO_BING_VERIFY', ''); }
if (!defined('MAVRINO_PINTEREST_VERIFY')) { define('MAVRINO_PINTEREST_VERIFY', ''); }

// IndexNow key — PUBLIC by design (served at /{key}.txt so Bing/Yandex can verify
// instant URL submissions). Must match the pipeline's INDEXNOW_KEY env var.
if (!defined('MAVRINO_INDEXNOW_KEY')) {
    define('MAVRINO_INDEXNOW_KEY', '8ecda0d544414e51af70a9395b0cf8eed6f9e4fc07034acf89cdea18031eac40');
}

// ── 0. Site-verification meta tags (Search Console / Bing / Pinterest) ───────
add_action('wp_head', function () {
    $tags = array(
        'google-site-verification' => MAVRINO_GOOGLE_VERIFY,
        'msvalidate.01'            => MAVRINO_BING_VERIFY,
        'p:domain_verify'          => MAVRINO_PINTEREST_VERIFY,
    );
    foreach ($tags as $name => $val) {
        if ($val) {
            echo '<meta name="' . esc_attr($name) . '" content="' . esc_attr($val) . '" />' . "\n";
        }
    }
}, 1);

// ── 1. JSON-LD schema into <head> ────────────────────────────────────────────
add_action('wp_head', function () {
    if (!is_singular('post')) { return; }
    $schema = get_post_meta(get_the_ID(), 'mavrino_schema_jsonld', true);
    if (!$schema) { return; }
    // Treat the stored meta as untrusted: only emit it if it parses as JSON, then
    // re-encode it ourselves and escape "</" so a string value can never close the
    // <script> tag early (prevents stored-XSS / markup injection via post meta).
    $data = json_decode(trim($schema), true);
    if (!is_array($data)) { return; }
    $json = wp_json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if (!$json) { return; }
    $json = str_replace('</', '<\/', $json);
    echo "\n" . '<script type="application/ld+json">' . $json . '</script>' . "\n";
}, 20);

// ── 1b. Visual system: trust-tone base + warm CTA + reserved ad space ────────
// Colour psychology research: a calm blue/green base reads as trustworthy/credible
// (ideal for a real-data review brand), while ONE warm, high-contrast accent reserved
// for the affiliate CTA makes it the single clearest action on the page. Injected here
// (not the theme) so it ships with the pipeline and overrides theme defaults.
add_action('wp_head', function () {
    if (is_admin()) { return; }
    echo '<style id="mavrino-visual">'
       . ':root{--mav-trust:#0e7490;--mav-cta:#d4521e;--mav-cta-hover:#b8431a;--mav-ink:#1e293b;}'
       // trust-tone: in-content links + headings
       . '.entry-content a:not(.wp-block-button__link){color:var(--mav-trust);}'
       . '.entry-content h2,.entry-content h3{color:var(--mav-ink);}'
       // the affiliate CTA = the single highest-contrast action (warm, bold, lifts on hover)
       . 'a.wp-block-button__link[rel*="sponsored"]{background:var(--mav-cta)!important;color:#fff!important;'
       . 'font-weight:700;padding:14px 24px;border-radius:6px;box-shadow:0 2px 6px rgba(0,0,0,.18);'
       . 'transition:transform .08s ease,background .15s ease;}'
       . 'a.wp-block-button__link[rel*="sponsored"]:hover{background:var(--mav-cta-hover)!important;transform:translateY(-1px);}'
       // reserved ad space + transparency label (CWV + Better Ads)
       . '.mavrino-ad{min-height:250px;display:flex;align-items:center;justify-content:center;margin:24px auto;position:relative;}'
       . '.mavrino-ad::before{content:"Advertisement";position:absolute;top:2px;left:0;right:0;font-size:10px;'
       . 'letter-spacing:1px;text-transform:uppercase;color:#b6b1a9;}'
       . '.trust-signals{list-style:none;padding-left:0;}'
       // left/right skyscraper ad rails — only on screens wide enough that they don't crowd content
       . '.mav-ad-rail{position:fixed;top:130px;width:160px;min-height:600px;z-index:5;}'
       . '.mav-ad-rail-left{left:20px;}.mav-ad-rail-right{right:20px;}'
       . '@media(max-width:1560px){.mav-ad-rail{display:none;}}'
       . '</style>' . "\n";
}, 30);

// ── 1c. Left/right skyscraper ad rails (fill via AdSense / an ad plugin) ──────
add_action('wp_footer', function () {
    if (is_admin()) { return; }
    echo '<div class="mav-ad-rail mav-ad-rail-left"><div class="mavrino-ad" data-ad-slot="rail-left" style="min-height:600px"></div></div>'
       . '<div class="mav-ad-rail mav-ad-rail-right"><div class="mavrino-ad" data-ad-slot="rail-right" style="min-height:600px"></div></div>' . "\n";
});

// ── 2. Serve /ads.txt ────────────────────────────────────────────────────────
add_action('init', function () {
    $uri = isset($_SERVER['REQUEST_URI']) ? strtok($_SERVER['REQUEST_URI'], '?') : '';
    if (rtrim($uri, '/') !== '/ads.txt') { return; }
    $pub = MAVRINO_ADSENSE_PUB_ID;
    if (!$pub || strpos($pub, 'XXXX') !== false) { return; }
    header('Content-Type: text/plain; charset=utf-8');
    echo "google.com, " . $pub . ", DIRECT, f08c47fec0942fa0\n";
    exit;
});

// ── 2b. Serve the IndexNow key file at /{key}.txt ────────────────────────────
add_action('init', function () {
    $uri = isset($_SERVER['REQUEST_URI']) ? strtok($_SERVER['REQUEST_URI'], '?') : '';
    $key = MAVRINO_INDEXNOW_KEY;
    if (!$key || rtrim($uri, '/') !== '/' . $key . '.txt') { return; }
    header('Content-Type: text/plain; charset=utf-8');
    echo $key;
    exit;
});

// ── 3. Capture zero-result searches ──────────────────────────────────────────
add_action('template_redirect', function () {
    if (!is_search()) { return; }
    global $wp_query;
    if ((int) $wp_query->found_posts > 0) { return; }       // only no-result searches
    $q = trim(get_search_query());
    if ($q === '' || strlen($q) > 120) { return; }
    // Light per-IP throttle so a single visitor can't flood the demand queue.
    $ip   = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : 'unknown';
    $tkey = 'mav_sr_' . md5($ip);
    $hits = (int) get_transient($tkey);
    if ($hits >= 20) { return; }                 // max ~20 no-result searches / IP / hour
    set_transient($tkey, $hits + 1, HOUR_IN_SECONDS);
    $key  = strtolower($q);
    $reqs = get_option('mavrino_search_requests', array());
    if (!isset($reqs[$key])) {
        $reqs[$key] = array('query' => $q, 'count' => 0, 'status' => 'pending', 'first' => time());
    }
    $reqs[$key]['count'] = (int) $reqs[$key]['count'] + 1;
    if (count($reqs) > 500) { array_shift($reqs); }          // keep storage bounded
    update_option('mavrino_search_requests', $reqs, false);
});

// ── REST: read pending requests + mark resolved (secret-guarded) ─────────────
function mavrino_check_secret($req) {
    $configured = (string) MAVRINO_SEARCH_SECRET;
    if ($configured === '') { return false; }          // not configured → deny all
    // Header only — never accept the secret via ?secret= (it would leak into logs).
    $secret = (string) $req->get_header('X-Mavrino-Secret');
    return $secret !== '' && hash_equals($configured, $secret);
}

add_action('rest_api_init', function () {
    register_rest_route('mavrino/v1', '/search-requests', array(
        'methods'             => 'GET',
        'permission_callback' => 'mavrino_check_secret',
        'callback'            => function () {
            $reqs = get_option('mavrino_search_requests', array());
            return array_values(array_filter($reqs, function ($r) {
                return (isset($r['status']) ? $r['status'] : '') === 'pending';
            }));
        },
    ));
    register_rest_route('mavrino/v1', '/search-requests/resolve', array(
        'methods'             => 'POST',
        'permission_callback' => 'mavrino_check_secret',
        'callback'            => function ($req) {
            $q    = strtolower(trim((string) $req->get_param('query')));
            $reqs = get_option('mavrino_search_requests', array());
            if (isset($reqs[$q])) {
                $reqs[$q]['status'] = 'resolved';
                update_option('mavrino_search_requests', $reqs, false);
            }
            return array('ok' => true);
        },
    ));
});
