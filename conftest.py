"""Root pytest configuration.

Sets default dummy AWS credentials when none are present in the environment so
boto3 top-level client instantiations during offline unit test imports do not
attempt local AWS SSO/CLI profile discovery.
"""

import os

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock-access-key-id")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock-secret-access-key")
