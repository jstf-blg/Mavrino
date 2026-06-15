<?php
/**
 * Plugin Name: Mavrino SEO & Ads
 * Description: Outputs per-post JSON-LD schema into <head> (WordPress.com strips
 *              <script> from post content) and serves /ads.txt for Google AdSense.
 * Version:     1.1
 * Author:      Mavrino
 *
 * INSTALL (one-time):
 *   Zip this file → WP Admin → Plugins → Add New → Upload Plugin → Activate.
 *   (Or drop it in wp-content/mu-plugins/ for auto-activation.)
 *
 * ADS.TXT: set your AdSense publisher ID below (looks like "pub-1234567890123456").
 *   Once set, https://mavrino.com/ads.txt is served automatically.
 */

if (!defined('ABSPATH')) { exit; }

// ── Set this to your Google AdSense publisher ID (without the "ca-" prefix) ──
if (!defined('MAVRINO_ADSENSE_PUB_ID')) {
    define('MAVRINO_ADSENSE_PUB_ID', 'pub-2702323126353107');
}

// ── JSON-LD schema into <head> ───────────────────────────────────────────────
add_action('wp_head', function () {
    if (!is_singular('post')) {
        return;
    }
    $schema = get_post_meta(get_the_ID(), 'mavrino_schema_jsonld', true);
    if (!$schema) {
        return;
    }
    $schema = trim($schema);
    if (strpos($schema, '<script') === false) {
        $schema = '<script type="application/ld+json">' . "\n" . $schema . "\n" . '</script>';
    }
    echo "\n" . $schema . "\n";
}, 20);

// ── Serve /ads.txt for Google AdSense ────────────────────────────────────────
add_action('init', function () {
    $uri = isset($_SERVER['REQUEST_URI']) ? strtok($_SERVER['REQUEST_URI'], '?') : '';
    if (rtrim($uri, '/') !== '/ads.txt') {
        return;
    }
    $pub = MAVRINO_ADSENSE_PUB_ID;
    if (!$pub || strpos($pub, 'XXXX') !== false) {
        return; // not configured yet — let WordPress 404 normally
    }
    header('Content-Type: text/plain; charset=utf-8');
    echo "google.com, " . $pub . ", DIRECT, f08c47fec0942fa0\n";
    exit;
});
