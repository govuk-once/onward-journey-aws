test-command:
    echo "this is a test command"

get-version:
    just --version

check:
    -cd app && uv run ruff check
    -cd app && uv run ruff format --check
