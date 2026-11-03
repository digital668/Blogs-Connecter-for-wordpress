<?php
/**
 * Plugin Name:       SEO Autopilot Connector
 * Description:       Connects this site to your SEO Autopilot GitHub repo with one key. The key can only read content, upload images and create draft or scheduled posts — it cannot publish instantly, edit or delete anything, or change settings.
 * Version:           1.0.0
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * Author:            SEO Autopilot
 * License:           GPL-2.0-or-later
 * Text Domain:       seo-autopilot-connector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

final class SEO_Autopilot_Connector {

	const VERSION     = '1.0.0';
	const OPT_HASH    = 'seo_autopilot_key_hash';
	const OPT_HINT    = 'seo_autopilot_key_hint';
	const OPT_USER    = 'seo_autopilot_user_id';
	const OPT_LAST    = 'seo_autopilot_last_seen';
	const OPT_LOG     = 'seo_autopilot_log';
	const OPT_ENABLED = 'seo_autopilot_enabled';
	const HEADER      = 'HTTP_X_SEO_AUTOPILOT_KEY';
	const PREFIX      = 'sap_';
	const MAX_FAILS   = 10;   // failed key attempts per IP …
	const FAIL_WINDOW = 900;  // … per 15 minutes

	/** True while the current request is authenticated with a valid connector key. */
	private static $via_key = false;

	public static function init() {
		add_filter( 'determine_current_user', array( __CLASS__, 'authenticate' ), 30 );
		add_filter( 'rest_authentication_errors', array( __CLASS__, 'auth_errors' ), 30 );
		add_filter( 'rest_pre_dispatch', array( __CLASS__, 'guard' ), 10, 3 );
		add_action( 'init', array( __CLASS__, 'register_meta' ) );
		add_action( 'rest_api_init', array( __CLASS__, 'register_routes' ) );
		add_action( 'admin_menu', array( __CLASS__, 'menu' ) );
		add_action( 'admin_post_seo_autopilot_regenerate', array( __CLASS__, 'handle_regenerate' ) );
		add_action( 'admin_post_seo_autopilot_toggle', array( __CLASS__, 'handle_toggle' ) );
		add_action( 'admin_post_seo_autopilot_user', array( __CLASS__, 'handle_user' ) );
		add_filter( 'plugin_action_links_' . plugin_basename( __FILE__ ), array( __CLASS__, 'action_links' ) );
	}

	/* ─────────────────────────── key handling ─────────────────────────── */

	private static function new_key() {
		return self::PREFIX . wp_generate_password( 44, false, false );
	}

	private static function hash( $key ) {
		return hash_hmac( 'sha256', $key, wp_salt( 'auth' ) );
	}

	/** Creates a key, stores only its hash, and returns the plain key (shown to the admin once). */
	public static function rotate_key() {
		$key = self::new_key();
		update_option( self::OPT_HASH, self::hash( $key ), false );
		update_option( self::OPT_HINT, substr( $key, 0, 8 ) . '…' . substr( $key, -4 ), false );
		self::log( 'New connection key created — any old key stopped working.' );
		return $key;
	}

	private static function presented_key() {
		if ( empty( $_SERVER[ self::HEADER ] ) ) {
			return null;
		}
		return trim( sanitize_text_field( wp_unslash( $_SERVER[ self::HEADER ] ) ) );
	}

	private static function client_ip() {
		return isset( $_SERVER['REMOTE_ADDR'] ) ? sanitize_text_field( wp_unslash( $_SERVER['REMOTE_ADDR'] ) ) : 'unknown';
	}

	private static function fails_key() {
		return 'seo_autopilot_fails_' . md5( self::client_ip() );
	}

	private static function is_rest_request() {
		$uri    = isset( $_SERVER['REQUEST_URI'] ) ? wp_unslash( $_SERVER['REQUEST_URI'] ) : '';
		$prefix = '/' . trim( rest_get_url_prefix(), '/' ) . '/';
		return false !== strpos( $uri, $prefix ) || isset( $_GET['rest_route'] ); // phpcs:ignore WordPress.Security.NonceVerification
	}

	/** determine_current_user: log the request in as the chosen user when the key is valid. */
	public static function authenticate( $user_id ) {
		$key = self::presented_key();
		if ( null === $key || ! self::is_rest_request() ) {
			return $user_id;
		}
		if ( ! get_option( self::OPT_ENABLED, 1 ) ) {
			return $user_id;
		}
		if ( (int) get_transient( self::fails_key() ) >= self::MAX_FAILS ) {
			return $user_id;
		}
		$hash = get_option( self::OPT_HASH );
		if ( ! $hash || 0 !== strpos( $key, self::PREFIX ) || ! hash_equals( $hash, self::hash( $key ) ) ) {
			set_transient( self::fails_key(), (int) get_transient( self::fails_key() ) + 1, self::FAIL_WINDOW );
			return $user_id;
		}
		$uid = self::author_id();
		if ( ! $uid ) {
			return $user_id;
		}
		self::$via_key = true;
		update_option( self::OPT_LAST, array( 'time' => time(), 'ip' => self::client_ip() ), false );
		return $uid;
	}

	/** Clear error for a wrong / blocked key instead of silently acting as a logged-out visitor. */
	public static function auth_errors( $result ) {
		if ( null === self::presented_key() || self::$via_key || ! empty( $result ) ) {
			return $result;
		}
		if ( ! get_option( self::OPT_ENABLED, 1 ) ) {
			return new WP_Error( 'seo_autopilot_disabled', 'SEO Autopilot connection is switched off in Settings → SEO Autopilot.', array( 'status' => 403 ) );
		}
		if ( (int) get_transient( self::fails_key() ) >= self::MAX_FAILS ) {
			return new WP_Error( 'seo_autopilot_locked', 'Too many wrong SEO Autopilot keys from this address. Try again in 15 minutes.', array( 'status' => 429 ) );
		}
		if ( ! self::author_id() ) {
			return new WP_Error( 'seo_autopilot_no_user', 'Choose a WordPress user in Settings → SEO Autopilot.', array( 'status' => 403 ) );
		}
		return new WP_Error( 'seo_autopilot_bad_key', 'Wrong SEO Autopilot key. Copy the current key from Settings → SEO Autopilot into your GitHub secret.', array( 'status' => 401 ) );
	}

	/* ─────────────────────── what the key may do ─────────────────────── */

	/** Allow-list of [method, route regex]. Everything else is refused for key-authenticated requests. */
	private static function allowed( $method, $route ) {
		$rules = array(
			array( 'GET', '#^/?$#' ),
			array( 'GET', '#^/wp/v2/users/me$#' ),
			array( 'GET', '#^/wp/v2/(posts|pages|categories|tags|media)(/\d+)?$#' ),
			array( 'POST', '#^/wp/v2/posts$#' ),               // create only
			array( 'POST', '#^/wp/v2/media$#' ),               // upload image
			array( 'POST', '#^/wp/v2/media/\d+$#' ),           // set alt text / caption on an upload
			array( 'POST', '#^/wp/v2/(categories|tags)$#' ),   // create a missing category / tag
			array( 'GET', '#^/seo-autopilot/v1/status$#' ),
		);
		foreach ( $rules as $r ) {
			if ( $r[0] === $method && preg_match( $r[1], $route ) ) {
				return true;
			}
		}
		return false;
	}

	public static function guard( $result, $server, $request ) {
		if ( ! self::$via_key || null !== $result ) {
			return $result;
		}
		$method = $request->get_method();
		$route  = untrailingslashit( $request->get_route() );
		if ( 'HEAD' === $method ) {
			$method = 'GET';
		}
		if ( ! self::allowed( $method, $route ) ) {
			self::log( sprintf( 'Blocked %s %s', $method, $route ) );
			return new WP_Error( 'seo_autopilot_not_allowed', 'The SEO Autopilot key is not allowed to do this (' . $method . ' ' . $route . ').', array( 'status' => 403 ) );
		}
		if ( 'POST' === $method && '/wp/v2/posts' === $route ) {
			$status = $request->get_param( 'status' ) ? $request->get_param( 'status' ) : 'draft';
			if ( ! in_array( $status, array( 'draft', 'future', 'pending' ), true ) ) {
				self::log( 'Blocked instant publish — posts must be draft or scheduled.' );
				return new WP_Error( 'seo_autopilot_status', 'SEO Autopilot may only create draft or scheduled posts.', array( 'status' => 403 ) );
			}
			if ( 'future' === $status ) {
				$date = $request->get_param( 'date_gmt' ) ? $request->get_param( 'date_gmt' ) : $request->get_param( 'date' );
				if ( ! $date || strtotime( $date . ( $request->get_param( 'date_gmt' ) ? ' UTC' : '' ) ) < time() + 3600 ) {
					return new WP_Error( 'seo_autopilot_schedule', 'Scheduled posts must be at least 1 hour in the future.', array( 'status' => 403 ) );
				}
			}
			self::log( sprintf( 'Created %s post: %s', $status, wp_strip_all_tags( (string) $request->get_param( 'title' ) ) ) );
		}
		if ( 'POST' === $method && preg_match( '#^/wp/v2/media/(\d+)$#', $route, $m ) ) {
			// Only images this connector uploaded may be edited.
			if ( ! get_post_meta( (int) $m[1], '_seo_autopilot_upload', true ) ) {
				return new WP_Error( 'seo_autopilot_not_allowed', 'SEO Autopilot can only edit images it uploaded.', array( 'status' => 403 ) );
			}
		}
		return $result;
	}

	/* ─────────────────────── meta + small helpers ─────────────────────── */

	public static function register_meta() {
		$keys = array(
			'rank_math_title', 'rank_math_description', 'rank_math_focus_keyword',
			'_yoast_wpseo_title', '_yoast_wpseo_metadesc', '_yoast_wpseo_focuskw',
			'_et_pb_use_builder', '_et_pb_page_layout', '_et_pb_old_content',
		);
		foreach ( $keys as $key ) {
			register_post_meta( 'post', $key, array(
				'type'          => 'string',
				'single'        => true,
				'show_in_rest'  => true,
				'auth_callback' => function () {
					return current_user_can( 'edit_posts' );
				},
			) );
		}
		// Mark uploads made through the key, so only those can be edited later.
		add_action( 'add_attachment', function ( $post_id ) {
			if ( self::$via_key ) {
				update_post_meta( $post_id, '_seo_autopilot_upload', 1 );
			}
		} );
	}

	public static function register_routes() {
		register_rest_route( 'seo-autopilot/v1', '/status', array(
			'methods'             => 'GET',
			'permission_callback' => function () {
				return current_user_can( 'edit_posts' );
			},
			'callback'            => function () {
				return array(
					'connector' => self::VERSION,
					'user'      => wp_get_current_user()->user_login,
					'theme'     => wp_get_theme()->get_template(),
					'builders'  => array(
						'elementor' => defined( 'ELEMENTOR_VERSION' ),
						'divi'      => function_exists( 'et_setup_theme' ) || defined( 'ET_BUILDER_PLUGIN_VERSION' ) || 'Divi' === wp_get_theme()->get_template(),
					),
					'seo_plugin' => defined( 'RANK_MATH_VERSION' ) ? 'rank-math' : ( defined( 'WPSEO_VERSION' ) ? 'yoast' : 'none' ),
				);
			},
		) );
	}

	/** The user posts are created as: the saved choice, or the first Administrator. */
	private static function author_id() {
		$uid = (int) get_option( self::OPT_USER );
		if ( $uid && get_userdata( $uid ) ) {
			return $uid;
		}
		$admins = get_users( array( 'role' => 'administrator', 'number' => 1, 'orderby' => 'ID', 'fields' => 'ID' ) );
		return $admins ? (int) $admins[0] : 0;
	}

	private static function log( $msg ) {
		$log   = get_option( self::OPT_LOG, array() );
		$log[] = array( 'time' => time(), 'msg' => $msg );
		update_option( self::OPT_LOG, array_slice( $log, -30 ), false );
	}

	public static function action_links( $links ) {
		array_unshift( $links, '<a href="' . esc_url( admin_url( 'options-general.php?page=seo-autopilot' ) ) . '">Settings</a>' );
		return $links;
	}

	/* ─────────────────────────── admin screen ─────────────────────────── */

	public static function menu() {
		add_options_page( 'SEO Autopilot', 'SEO Autopilot', 'manage_options', 'seo-autopilot', array( __CLASS__, 'page' ) );
	}

	private static function check_admin( $action ) {
		if ( ! current_user_can( 'manage_options' ) ) {
			wp_die( 'Not allowed.' );
		}
		check_admin_referer( $action );
	}

	public static function handle_regenerate() {
		self::check_admin( 'seo_autopilot_regenerate' );
		set_transient( 'seo_autopilot_show_key_' . get_current_user_id(), self::rotate_key(), 600 );
		wp_safe_redirect( admin_url( 'options-general.php?page=seo-autopilot&new=1' ) );
		exit;
	}

	public static function handle_toggle() {
		self::check_admin( 'seo_autopilot_toggle' );
		update_option( self::OPT_ENABLED, get_option( self::OPT_ENABLED, 1 ) ? 0 : 1, false );
		self::log( get_option( self::OPT_ENABLED ) ? 'Connection switched ON.' : 'Connection switched OFF.' );
		wp_safe_redirect( admin_url( 'options-general.php?page=seo-autopilot' ) );
		exit;
	}

	public static function handle_user() {
		self::check_admin( 'seo_autopilot_user' );
		$uid  = isset( $_POST['user_id'] ) ? absint( $_POST['user_id'] ) : 0;
		$user = get_userdata( $uid );
		if ( $user && user_can( $user, 'publish_posts' ) ) {
			update_option( self::OPT_USER, $uid, false );
			self::log( 'Posts will now be created as ' . $user->user_login . '.' );
		}
		wp_safe_redirect( admin_url( 'options-general.php?page=seo-autopilot' ) );
		exit;
	}

	public static function page() {
		if ( ! current_user_can( 'manage_options' ) ) {
			return;
		}
		$uid      = get_current_user_id();
		$new_key  = get_transient( 'seo_autopilot_show_key_' . $uid );
		if ( $new_key ) {
			delete_transient( 'seo_autopilot_show_key_' . $uid );
		}
		$enabled  = (bool) get_option( self::OPT_ENABLED, 1 );
		$last     = get_option( self::OPT_LAST );
		$author   = get_userdata( self::author_id() );
		$authors  = get_users( array( 'capability' => 'publish_posts', 'fields' => array( 'ID', 'display_name', 'user_login' ) ) );
		$log      = array_reverse( get_option( self::OPT_LOG, array() ) );
		?>
		<div class="wrap" style="max-width:860px">
			<h1>SEO Autopilot Connector</h1>
			<p>Connects this site to your SEO Autopilot GitHub repo. The key can <strong>only</strong> read content, upload images and create <strong>draft or scheduled</strong> posts. It cannot publish instantly, edit or delete existing content, or change any settings.</p>

			<?php if ( $new_key ) : ?>
				<div class="notice notice-success" style="padding:16px">
					<h2 style="margin-top:0">Your connection key</h2>
					<p><strong>Copy it now — for security it is shown only once.</strong> If you lose it, just create a new one.</p>
					<p><input type="text" id="sap-key" readonly value="<?php echo esc_attr( $new_key ); ?>" style="width:100%;font-family:monospace;font-size:14px;padding:8px" onclick="this.select()">
					<button type="button" class="button button-primary" style="margin-top:8px" onclick="navigator.clipboard.writeText(document.getElementById('sap-key').value);this.textContent='Copied ✓'">Copy key</button></p>
					<p><strong>Next:</strong> in GitHub → your repo → <em>Settings → Secrets and variables → Actions → New repository secret</em>. Name: <code>WP_PASS_&lt;YOUR SITE KEY&gt;</code> (e.g. <code>WP_PASS_KAIZEN_KLUB</code> for <code>key: kaizen-klub</code> in sites.yaml). Value: paste this key.</p>
				</div>
			<?php endif; ?>

			<table class="form-table" role="presentation">
				<tr>
					<th>Status</th>
					<td>
						<?php if ( ! get_option( self::OPT_HASH ) ) : ?>
							<span style="color:#b32d2e">● No key yet</span>
						<?php elseif ( ! $enabled ) : ?>
							<span style="color:#b32d2e">● Switched off — the repo cannot connect</span>
						<?php elseif ( $last ) : ?>
							<span style="color:#008a20">● Connected</span> — last request <?php echo esc_html( human_time_diff( $last['time'] ) ); ?> ago
						<?php else : ?>
							<span style="color:#996800">● Waiting for the first connection</span> — run <code>test</code> in GitHub Actions
						<?php endif; ?>
					</td>
				</tr>
				<tr>
					<th>Current key</th>
					<td>
						<?php echo get_option( self::OPT_HINT ) ? '<code>' . esc_html( get_option( self::OPT_HINT ) ) . '</code>' : '—'; ?>
						<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" style="display:inline;margin-left:12px">
							<input type="hidden" name="action" value="seo_autopilot_regenerate">
							<?php wp_nonce_field( 'seo_autopilot_regenerate' ); ?>
							<button class="button button-primary" onclick="return <?php echo get_option( self::OPT_HASH ) ? "confirm('This stops the old key immediately. You will need to paste the new key into GitHub. Continue?')" : 'true'; ?>"><?php echo get_option( self::OPT_HASH ) ? 'Create new key' : 'Create connection key'; ?></button>
						</form>
					</td>
				</tr>
				<tr>
					<th>Create posts as</th>
					<td>
						<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
							<input type="hidden" name="action" value="seo_autopilot_user">
							<?php wp_nonce_field( 'seo_autopilot_user' ); ?>
							<select name="user_id">
								<?php foreach ( $authors as $a ) : ?>
									<option value="<?php echo esc_attr( $a->ID ); ?>" <?php selected( $author ? $author->ID : 0, $a->ID ); ?>><?php echo esc_html( $a->display_name . ' (' . $a->user_login . ')' ); ?></option>
								<?php endforeach; ?>
							</select>
							<button class="button">Save</button>
							<p class="description">Shown as the post author. Pick a real person with a bio (good for E-E-A-T). An Administrator also lets FAQ schema be added.</p>
						</form>
					</td>
				</tr>
				<tr>
					<th>Connection</th>
					<td>
						<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
							<input type="hidden" name="action" value="seo_autopilot_toggle">
							<?php wp_nonce_field( 'seo_autopilot_toggle' ); ?>
							<button class="button"><?php echo $enabled ? 'Switch OFF (pause the repo)' : 'Switch ON'; ?></button>
						</form>
					</td>
				</tr>
			</table>

			<h2>Recent activity</h2>
			<?php if ( $log ) : ?>
				<table class="widefat striped" style="max-width:860px">
					<?php foreach ( array_slice( $log, 0, 15 ) as $row ) : ?>
						<tr><td style="width:160px"><?php echo esc_html( wp_date( 'd M, H:i', $row['time'] ) ); ?></td><td><?php echo esc_html( $row['msg'] ); ?></td></tr>
					<?php endforeach; ?>
				</table>
			<?php else : ?>
				<p>No activity yet.</p>
			<?php endif; ?>
		</div>
		<?php
	}
}

SEO_Autopilot_Connector::init();

register_activation_hook( __FILE__, function () {
	if ( ! get_option( SEO_Autopilot_Connector::OPT_USER ) ) {
		update_option( SEO_Autopilot_Connector::OPT_USER, get_current_user_id(), false );
	}
	add_option( SEO_Autopilot_Connector::OPT_ENABLED, 1, '', false );
} );

add_action( 'activated_plugin', function ( $plugin ) {
	if ( plugin_basename( __FILE__ ) === $plugin && ! wp_doing_ajax() && ! ( defined( 'WP_CLI' ) && WP_CLI ) && ! isset( $_GET['activate-multi'] ) ) { // phpcs:ignore
		wp_safe_redirect( admin_url( 'options-general.php?page=seo-autopilot' ) );
		exit;
	}
} );
