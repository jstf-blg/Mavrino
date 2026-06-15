<?php
/**
 * Plugin Name: Mavrino SEO Schema
 * Description: Outputs the per-post JSON-LD schema (stored by the pipeline in the
 *              _mavrino_schema_jsonld meta) into <head>. WordPress.com strips
 *              <script> from post content, so schema must be emitted here instead.
 * Version:     1.0
 * Author:      Mavrino
 *
 * INSTALL (one-time):
 *   WP Admin → Plugins → Add New → Upload Plugin → choose this file (zip it first)
 *   OR drop it in wp-content/mu-plugins/ for auto-activation.
 *   New posts include the schema meta automatically; older posts get it when next updated.
 */

if (!defined('ABSPATH')) { exit; }

add_action('wp_head', function () {
    if (!is_singular('post')) {
        return;
    }
    $schema = get_post_meta(get_the_ID(), '_mavrino_schema_jsonld', true);
    if (!$schema) {
        return;
    }
    // Meta already holds a full <script type="application/ld+json">…</script> block.
    // Guard against accidental double-wrapping / plain JSON.
    $schema = trim($schema);
    if (strpos($schema, '<script') === false) {
        $schema = '<script type="application/ld+json">' . "\n" . $schema . "\n" . '</script>';
    }
    echo "\n" . $schema . "\n";
}, 20);
