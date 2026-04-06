"""GitHub webhook receiver — triggers deploy.sh on push to main."""
import hashlib
import hmac
import logging
import os
import subprocess
from fastapi import FastAPI, Request, HTTPException, Header

log = logging.getLogger("webhook")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WEBHOOK_SECRET = os.environ.get("JOSHUA_WEBHOOK_SECRET", "")
DEPLOY_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy.sh")

app = FastAPI(title="Joshua Webhook")


def _verify_signature(body: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    body = await request.body()

    if not _verify_signature(body, x_hub_signature_256):
        log.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "push":
        return {"status": "ignored", "event": x_github_event}

    import json
    payload = json.loads(body)
    ref = payload.get("ref", "")
    if ref != "refs/heads/main":
        return {"status": "ignored", "ref": ref}

    log.info(f"Push to main detected — running deploy.sh")
    subprocess.Popen(
        ["bash", DEPLOY_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    return {"status": "deploying"}


@app.get("/health")
def health():
    return {"status": "ok"}
