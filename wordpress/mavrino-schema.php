<?php
/**
 * Plugin Name: Mavrino SEO, Ads & Search
 * Description: (1) Outputs per-post JSON-LD schema into <head>. (2) Serves /ads.txt
 *              for Google AdSense. (3) Captures zero-result site searches and exposes
 *              them via a secured REST endpoint so the content pipeline can turn real
 *              visitor demand into new guides.
 * Version:     1.2
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
