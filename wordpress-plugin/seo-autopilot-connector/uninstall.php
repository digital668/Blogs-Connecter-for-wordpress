<?php
// Removes everything the connector stored. Posts it created are kept.
if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}
foreach ( array( 'seo_autopilot_key_hash', 'seo_autopilot_key_hint', 'seo_autopilot_user_id', 'seo_autopilot_last_seen', 'seo_autopilot_log', 'seo_autopilot_enabled' ) as $opt ) {
	delete_option( $opt );
}
