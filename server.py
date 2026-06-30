#!/usr/bin/env python3
"""Compatibility wrapper for running server.py directly.

For installed use, prefer the console script: pr-ci-dashboard
"""

if __name__ == '__main__':
    from pr_ci_dashboard.server import main
    main()
