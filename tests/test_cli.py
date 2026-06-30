"""Tests for CLI argument parsing."""
import pytest
import os
from pr_ci_dashboard.server import parse_cli_args


def test_parse_cli_args_defaults():
    """Test that parse_cli_args returns sensible defaults with no arguments."""
    args = parse_cli_args([])
    assert args.port is None  # Will use default 5000 or DASHBOARD_PORT env
    assert args.search is None
    assert args.search_override is None
    assert args.db_path is None  # Will use PR_CI_DASHBOARD_DB env or default
    assert args.debug is None  # Will use DASHBOARD_DEBUG env or default False
    assert args.search_terms == []


def test_parse_cli_args_debug_flag():
    """Test --debug argument enables debug mode."""
    args = parse_cli_args(['--debug'])
    assert args.debug is True


def test_parse_cli_args_positional_search_terms():
    """Test legacy positional search terms are collected."""
    args = parse_cli_args(['author:jluhrsen', 'repo:openshift/ovn-kubernetes'])
    assert args.search_terms == ['author:jluhrsen', 'repo:openshift/ovn-kubernetes']


def test_parse_cli_args_port():
    """Test --port argument."""
    args = parse_cli_args(['--port', '8080'])
    assert args.port == 8080


def test_parse_cli_args_search():
    """Test --search argument appends to default query."""
    args = parse_cli_args(['--search', 'label:approved'])
    assert args.search == 'label:approved'


def test_parse_cli_args_search_override():
    """Test --search-override completely replaces default query."""
    args = parse_cli_args(['--search-override', 'is:pr is:open label:lgtm'])
    assert args.search_override == 'is:pr is:open label:lgtm'


def test_parse_cli_args_db_path():
    """Test --db-path argument."""
    args = parse_cli_args(['--db-path', '/tmp/test.db'])
    assert args.db_path == '/tmp/test.db'


def test_parse_cli_args_multiple():
    """Test multiple arguments together."""
    args = parse_cli_args([
        '--port', '9000',
        '--search', 'label:approved',
        '--db-path', '/tmp/custom.db'
    ])
    assert args.port == 9000
    assert args.search == 'label:approved'
    assert args.db_path == '/tmp/custom.db'


def test_parse_cli_args_help_exits():
    """Test that --help causes SystemExit (argparse behavior)."""
    with pytest.raises(SystemExit) as exc_info:
        parse_cli_args(['--help'])
    # argparse exits with code 0 for --help
    assert exc_info.value.code == 0


def test_search_and_search_override_mutually_exclusive():
    """
    Test that --search and --search-override are mutually exclusive.

    Argparse enforces this - providing both should raise SystemExit.
    """
    # Providing both should cause argparse error
    with pytest.raises(SystemExit) as exc_info:
        parse_cli_args(['--search', 'label:approved', '--search-override', 'is:pr is:merged'])
    # argparse exits with code 2 for usage errors
    assert exc_info.value.code == 2


def test_config_integration_port_cli_override(monkeypatch):
    """
    Test that CLI --port takes precedence over DASHBOARD_PORT env var.

    This is an integration test showing the precedence order:
    CLI arg > env var > default
    """
    # Set env var
    monkeypatch.setenv('DASHBOARD_PORT', '7000')

    # Parse args with CLI --port
    args = parse_cli_args(['--port', '8080'])

    # CLI arg wins
    assert args.port == 8080

    # Without CLI arg, env var would be used in main()
    args_no_cli = parse_cli_args([])
    assert args_no_cli.port is None  # main() will read env var


def test_config_integration_db_path_cli_override(monkeypatch):
    """
    Test that CLI --db-path takes precedence over PR_CI_DASHBOARD_DB env var.
    """
    # Set env var
    monkeypatch.setenv('PR_CI_DASHBOARD_DB', '/env/db.db')

    # Parse args with CLI --db-path
    args = parse_cli_args(['--db-path', '/cli/db.db'])

    # CLI arg wins
    assert args.db_path == '/cli/db.db'

    # Without CLI arg, env var would be used in main()
    args_no_cli = parse_cli_args([])
    assert args_no_cli.db_path is None  # main() will use PR_CI_DASHBOARD_DB


def test_search_query_override_precedence():
    """
    Test search query precedence: --search-override > --search > default.

    Since they are mutually exclusive, only one can be provided at a time.
    """
    # --search-override alone
    args = parse_cli_args(['--search-override', 'custom query'])
    assert args.search_override == 'custom query'
    assert args.search is None

    # --search alone
    args = parse_cli_args(['--search', 'additional'])
    assert args.search == 'additional'
    assert args.search_override is None

    # Neither provided
    args = parse_cli_args([])
    assert args.search is None
    assert args.search_override is None


def test_build_config_db_path_precedence():
    """
    Test build_config DB path precedence: CLI arg > PR_CI_DASHBOARD_DB env > default.
    """
    from pr_ci_dashboard.server import build_config, DB_PATH
    from unittest.mock import MagicMock

    # CLI arg wins over env var
    args_with_cli = MagicMock()
    args_with_cli.port = None
    args_with_cli.search = None
    args_with_cli.search_override = None
    args_with_cli.db_path = '/cli/custom.db'
    args_with_cli.debug = None
    args_with_cli.search_terms = []

    config = build_config(args_with_cli, environ={'PR_CI_DASHBOARD_DB': '/env/db.db'})
    assert config['db_path'] == '/cli/custom.db'  # CLI wins

    # Env var used when no CLI arg
    args_no_cli = MagicMock()
    args_no_cli.port = None
    args_no_cli.search = None
    args_no_cli.search_override = None
    args_no_cli.db_path = None
    args_no_cli.debug = None
    args_no_cli.search_terms = []

    config = build_config(args_no_cli, environ={'PR_CI_DASHBOARD_DB': '~/.custom/db.db'})
    # Should expand ~ and use env var
    import os
    expected = os.path.expanduser('~/.custom/db.db')
    assert config['db_path'] == expected

    # Default used when neither CLI nor env provided
    config = build_config(args_no_cli, environ={})
    assert config['db_path'] == DB_PATH


def test_build_config_debug_default_off():
    """Test debug defaults to False with no CLI flag and no env var."""
    from pr_ci_dashboard.server import build_config

    config = build_config(parse_cli_args([]), environ={})
    assert config['debug'] is False


def test_build_config_debug_cli_flag():
    """Test --debug enables debug mode."""
    from pr_ci_dashboard.server import build_config

    config = build_config(parse_cli_args(['--debug']), environ={})
    assert config['debug'] is True


def test_build_config_debug_env_var():
    """Test DASHBOARD_DEBUG env var enables debug when no CLI flag given."""
    from pr_ci_dashboard.server import build_config

    for truthy in ('1', 'true', 'TRUE', 'yes', 'on'):
        config = build_config(parse_cli_args([]), environ={'DASHBOARD_DEBUG': truthy})
        assert config['debug'] is True, f"DASHBOARD_DEBUG={truthy} should enable debug"

    for falsy in ('0', 'false', 'no', 'off', ''):
        config = build_config(parse_cli_args([]), environ={'DASHBOARD_DEBUG': falsy})
        assert config['debug'] is False, f"DASHBOARD_DEBUG={falsy} should not enable debug"


def test_build_config_invalid_dashboard_port_exits():
    """Test that a non-integer DASHBOARD_PORT fails with a clear error, not a traceback."""
    from pr_ci_dashboard.server import build_config

    with pytest.raises(SystemExit) as exc_info:
        build_config(parse_cli_args([]), environ={'DASHBOARD_PORT': 'not-a-port'})
    assert 'DASHBOARD_PORT' in str(exc_info.value)


def test_build_config_positional_terms_appended():
    """Test legacy positional search terms are appended to the query."""
    from pr_ci_dashboard.server import build_config, DEFAULT_QUERY

    # Positionals alone append to the default query
    config = build_config(parse_cli_args(['author:foo']), environ={})
    assert config['search_query'] == DEFAULT_QUERY + " author:foo"

    # Positionals combine with --search
    config = build_config(parse_cli_args(['--search', 'label:lgtm', 'author:foo']), environ={})
    assert config['search_query'] == DEFAULT_QUERY + " label:lgtm author:foo"

    # Positionals append to --search-override as well
    config = build_config(parse_cli_args(['--search-override', 'is:pr', 'author:foo']), environ={})
    assert config['search_query'] == "is:pr author:foo"
