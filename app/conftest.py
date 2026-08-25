"""DO NOT DELETE THIS FILE.

This file's presence at the root of the `app/` directory is required by pytest.
It acts as an anchor point, automatically adding the root directory to
Python's sys.path so that absolute imports in the test suite resolve correctly
without ModuleNotFound errors.
"""

import os

# Set dummy AWS credentials if none exist in the environment.
# Guarantees that boto3 top-level client instantiations during unit test imports
# complete offline without attempting local AWS SSO/CLI profile discovery.
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock-access-key-id")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock-secret-access-key")
