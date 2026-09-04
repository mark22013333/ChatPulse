#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
uv run --with google-api-python-client --with google-auth-oauthlib --with google-auth-httplib2 --with requests \
  python3 "$DIR/mcp_app/setup_wizard.py"
