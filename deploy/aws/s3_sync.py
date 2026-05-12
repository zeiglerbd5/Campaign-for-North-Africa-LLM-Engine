"""Upload every file under a local directory to S3 with a run-id prefix."""
from __future__ import annotations
import os
import sys

import boto3


def main() -> None:
    if len(sys.argv) < 4:
        print("usage: s3_sync.py BUCKET RUN_ID LOCAL_DIR", file=sys.stderr)
        sys.exit(1)

    bucket, run_id, local_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    s3 = boto3.client("s3")

    count = 0
    for dirpath, _dirs, filenames in os.walk(local_dir):
        for fname in filenames:
            local_path = os.path.join(dirpath, fname)
            rel = os.path.relpath(local_path, local_dir)
            key = f"runs/{run_id}/{rel}"
            s3.upload_file(local_path, bucket, key)
            count += 1

    print(f"Uploaded {count} files to s3://{bucket}/runs/{run_id}/")


if __name__ == "__main__":
    main()
