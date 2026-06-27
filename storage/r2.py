"""Cloudflare R2 upload via the boto3 S3-compatible client.

The final MP4 is uploaded to the configured R2 bucket and a **public** URL is
returned (not the private S3 endpoint, which requires signing). Enable public
access on the bucket in the Cloudflare dashboard ("R2 > your bucket > Settings >
Public Development URL"), then copy the exact domain it shows (e.g.
`pub-abcd1234....r2.dev` or your custom domain) into CLOUDFLARE_R2_PUBLIC_DOMAIN.
If unset, we fall back to the conventional `pub-<account[:8]>.r2.dev` form.
"""

import os
import asyncio
import logging

import boto3
from botocore.config import Config as BotoConfig

log = logging.getLogger("videoforge.r2")

ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("CLOUDFLARE_R2_SECRET_KEY", "")
BUCKET_NAME = os.getenv("CLOUDFLARE_R2_BUCKET", "videoforge-outputs")
# Exact public domain from the bucket's Public Development URL settings page,
# e.g. "pub-abcd1234....r2.dev" or a custom domain "cdn.example.com" (no scheme).
PUBLIC_R2_DOMAIN = (
    os.getenv("CLOUDFLARE_R2_PUBLIC_DOMAIN", "")
    .strip()
    .replace("https://", "")
    .replace("http://", "")
    .rstrip("/")
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


async def upload(job_id: str, video_path: str) -> str:
    """Upload the video to R2 and return its public URL. Blocking boto3 work is
    offloaded to a thread."""
    return await asyncio.to_thread(_upload_sync, job_id, video_path)


def _upload_sync(job_id: str, video_path: str) -> str:
    s3 = _get_client()
    key = f"videos/{job_id}/output.mp4"

    # R2 ignores most canned ACLs; attempt with public-read, fall back without.
    try:
        s3.upload_file(
            video_path, BUCKET_NAME, key,
            ExtraArgs={"ContentType": "video/mp4", "ACL": "public-read"},
        )
    except Exception as e:
        log.warning("Upload with ACL failed (%s); retrying without ACL", e)
        s3.upload_file(
            video_path, BUCKET_NAME, key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

    domain = PUBLIC_R2_DOMAIN or f"pub-{ACCOUNT_ID[:8]}.r2.dev"
    url = f"https://{domain}/{key}"

    log.info("Uploaded video to R2: %s", url)
    return url
