"""Integration tests for CLI configuration affecting runtime behavior."""
import os
import tempfile
from unittest.mock import patch, MagicMock
from pr_ci_dashboard.server import main, CONFIG


def test_db_path_cli_arg_affects_init_db():
    """
    Test that --db-path CLI argument is passed to init_db() and used by the app.

    This verifies the full flow:
    1. parse_cli_args() extracts --db-path
    2. main() updates CONFIG['db_path']
    3. main() calls init_db(CONFIG['db_path'])
    4. app.config['DB_PATH'] is updated
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, 'custom_test.db')

        # Mock dependencies to prevent actual server startup
        with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
             patch('pr_ci_dashboard.server.init_db') as mock_init_db, \
             patch('pr_ci_dashboard.server.fetch_scripts'), \
             patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
             patch('pr_ci_dashboard.server.run_gunicorn'), \
             patch('pr_ci_dashboard.server.app') as mock_app:

            # Simulate --db-path argument
            mock_args = MagicMock()
            mock_args.port = None
            mock_args.search = None
            mock_args.search_override = None
            mock_args.db_path = test_db_path
            mock_args.debug = None
            mock_args.search_terms = []
            mock_parse.return_value = mock_args

            main()

            # Verify init_db was called with the custom path
            mock_init_db.assert_called_once()
            actual_db_path = mock_init_db.call_args[0][0]
            # Should be absolute path
            assert actual_db_path == os.path.abspath(test_db_path)

            # Verify app.config['DB_PATH'] was updated
            mock_app.config.__setitem__.assert_any_call('DB_PATH', os.path.abspath(test_db_path))


def test_port_cli_arg_affects_app_run():
    """
    Test that --port CLI argument affects the port the server runs on.

    Non-debug mode dispatches to gunicorn (production server).
    """
    # Mock dependencies
    with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
         patch('pr_ci_dashboard.server.init_db'), \
         patch('pr_ci_dashboard.server.fetch_scripts'), \
         patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
         patch('pr_ci_dashboard.server.run_gunicorn') as mock_gunicorn, \
         patch('pr_ci_dashboard.server.app') as mock_app:

        # Simulate --port 9999
        mock_args = MagicMock()
        mock_args.port = 9999
        mock_args.search = None
        mock_args.search_override = None
        mock_args.db_path = None
        mock_args.debug = None
        mock_args.search_terms = []
        mock_parse.return_value = mock_args

        # Call main()
        main()

        # Verify gunicorn was started with the custom port (no dev server)
        mock_gunicorn.assert_called_once()
        assert mock_gunicorn.call_args[0][1] == 9999
        mock_app.run.assert_not_called()


def test_search_override_affects_config():
    """
    Test that --search-override CLI argument affects CONFIG['search_query'].
    """
    from pr_ci_dashboard.server import build_config

    # Create args with --search-override
    mock_args = MagicMock()
    mock_args.port = None
    mock_args.search = None
    mock_args.search_override = "is:pr is:merged label:approved"
    mock_args.db_path = None
    mock_args.debug = None
    mock_args.search_terms = []

    # Build config directly
    config = build_config(mock_args, environ={})

    # Verify search_query was overridden
    assert config['search_query'] == "is:pr is:merged label:approved"


def test_search_appends_to_default():
    """
    Test that --search CLI argument appends to DEFAULT_QUERY.
    """
    from pr_ci_dashboard.server import build_config, DEFAULT_QUERY

    # Create args with --search
    mock_args = MagicMock()
    mock_args.port = None
    mock_args.search = "label:lgtm"
    mock_args.search_override = None
    mock_args.db_path = None
    mock_args.debug = None
    mock_args.search_terms = []

    # Build config directly
    config = build_config(mock_args, environ={})

    # Verify search appended to default
    expected = DEFAULT_QUERY + " label:lgtm"
    assert config['search_query'] == expected


def test_env_var_dashboard_port_used_when_no_cli_arg():
    """
    Test that DASHBOARD_PORT env var is used when --port is not provided.
    """
    # Mock dependencies
    with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
         patch('pr_ci_dashboard.server.init_db'), \
         patch('pr_ci_dashboard.server.fetch_scripts'), \
         patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
         patch('pr_ci_dashboard.server.run_gunicorn') as mock_gunicorn, \
         patch('pr_ci_dashboard.server.app'), \
         patch.dict(os.environ, {'DASHBOARD_PORT': '7777'}):

        # Simulate no --port argument
        mock_args = MagicMock()
        mock_args.port = None
        mock_args.search = None
        mock_args.search_override = None
        mock_args.db_path = None
        mock_args.debug = None
        mock_args.search_terms = []
        mock_parse.return_value = mock_args

        # Call main()
        main()

        # Verify gunicorn used env var port
        mock_gunicorn.assert_called_once()
        assert mock_gunicorn.call_args[0][1] == 7777


def test_repeated_main_calls_do_not_leak_state():
    """
    Regression test: repeated main() calls should not inherit CONFIG from prior invocations.

    First call with custom args, second call with no args should return to defaults.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        custom_db_path = os.path.join(tmpdir, 'custom.db')

        # Mock dependencies
        with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
             patch('pr_ci_dashboard.server.init_db') as mock_init_db, \
             patch('pr_ci_dashboard.server.fetch_scripts'), \
             patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
             patch('pr_ci_dashboard.server.run_gunicorn') as mock_gunicorn, \
             patch('pr_ci_dashboard.server.app'):

            # First call: custom --port 9999, --search-override "custom query", --db-path
            mock_args_custom = MagicMock()
            mock_args_custom.port = 9999
            mock_args_custom.search = None
            mock_args_custom.search_override = "custom query"
            mock_args_custom.db_path = custom_db_path
            mock_args_custom.debug = None
            mock_args_custom.search_terms = []
            mock_parse.return_value = mock_args_custom

            main()

            # Verify first call used custom values
            first_init_call = mock_init_db.call_args_list[0]
            assert first_init_call[0][0] == os.path.abspath(custom_db_path)
            first_run_call = mock_gunicorn.call_args_list[0]
            assert first_run_call[0][1] == 9999

            # Reset mocks
            mock_init_db.reset_mock()
            mock_gunicorn.reset_mock()

            # Second call: no args (should return to defaults/env, not inherit from first call)
            mock_args_default = MagicMock()
            mock_args_default.port = None
            mock_args_default.search = None
            mock_args_default.search_override = None
            mock_args_default.db_path = None
            mock_args_default.debug = None
            mock_args_default.search_terms = []
            mock_parse.return_value = mock_args_default

            main()

            # Verify second call used defaults, NOT values from first call
            # Check via the actual function calls, not global CONFIG
            from pr_ci_dashboard.utils.db import DB_PATH
            second_init_call = mock_init_db.call_args_list[0]
            assert second_init_call[0][0] == DB_PATH  # Default DB path, not custom
            second_run_call = mock_gunicorn.call_args_list[0]
            assert second_run_call[0][1] == 5000  # Default port, not 9999


def test_debug_off_by_default_in_app_run():
    """
    Without --debug, the production server (gunicorn) runs and the Werkzeug
    dev server (app.run) is never touched.

    Regression lineage: debug=True was once hardcoded in app.run, exposing
    the Werkzeug interactive debugger in production deployments.
    """
    with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
         patch('pr_ci_dashboard.server.init_db'), \
         patch('pr_ci_dashboard.server.fetch_scripts'), \
         patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
         patch('pr_ci_dashboard.server.run_gunicorn') as mock_gunicorn, \
         patch('pr_ci_dashboard.server.app') as mock_app:

        mock_args = MagicMock()
        mock_args.port = None
        mock_args.search = None
        mock_args.search_override = None
        mock_args.db_path = None
        mock_args.debug = None
        mock_args.search_terms = []
        mock_parse.return_value = mock_args

        main()

        mock_gunicorn.assert_called_once()
        mock_app.run.assert_not_called()


def test_debug_flag_enables_debug_in_app_run():
    """
    Test that --debug results in the Werkzeug dev server with debug=True
    (development mode; gunicorn is not used).
    """
    with patch('pr_ci_dashboard.server.parse_cli_args') as mock_parse, \
         patch('pr_ci_dashboard.server.init_db'), \
         patch('pr_ci_dashboard.server.fetch_scripts'), \
         patch('pr_ci_dashboard.server.check_gh_auth', return_value={'authenticated': True}), \
         patch('pr_ci_dashboard.server.app') as mock_app:

        mock_args = MagicMock()
        mock_args.port = None
        mock_args.search = None
        mock_args.search_override = None
        mock_args.db_path = None
        mock_args.debug = True
        mock_args.search_terms = []
        mock_parse.return_value = mock_args

        main()

        mock_app.run.assert_called_once()
        assert mock_app.run.call_args[1]['debug'] is True
