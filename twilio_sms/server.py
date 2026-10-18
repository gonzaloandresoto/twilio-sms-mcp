import logging
import os

import httpx
from dotenv import load_dotenv
from mcp.gumstack import GumstackHost
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from twilio_sms.utils.auth import get_credentials


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", 8000))
TWILIO_API = "https://api.twilio.com/2010-04-01"

mcp = FastMCP("Twilio SMS", host="0.0.0.0", port=PORT)


@mcp.custom_route("/health_check", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class SmsMessage(BaseModel):
    sid: str = Field(description="Twilio message SID (starts with SM)")
    status: str = Field(description="Delivery status: queued, sending, sent, delivered, undelivered, failed")
    to: str = Field(description="Recipient phone number in E.164 format")
    from_number: str = Field(description="Sender phone number in E.164 format")
    body: str = Field(description="Message text")
    date_created: str | None = Field(default=None, description="When Twilio created the message")
    date_sent: str | None = Field(default=None, description="When the carrier accepted the message")
    error_code: int | None = Field(default=None, description="Twilio error code if delivery failed")
    error_message: str | None = Field(default=None, description="Twilio error description if delivery failed")


class MessageList(BaseModel):
    messages: list[SmsMessage] = Field(description="Messages, newest first")
    count: int = Field(description="Number of messages returned")


def _client() -> tuple[httpx.AsyncClient, str, str]:
    creds = get_credentials()
    missing = [k for k in ("account_sid", "api_key_sid", "api_key_secret") if not creds[k]]
    if missing:
        raise ValueError(f"Missing Twilio credentials: {', '.join(missing)}")
    client = httpx.AsyncClient(
        base_url=f"{TWILIO_API}/Accounts/{creds['account_sid']}",
        auth=(creds["api_key_sid"], creds["api_key_secret"]),
        timeout=30,
    )
    return client, creds["account_sid"], creds["from_number"]


def _to_model(m: dict) -> SmsMessage:
    return SmsMessage(
        sid=m["sid"],
        status=m["status"],
        to=m["to"],
        from_number=m.get("from") or "",
        body=m.get("body") or "",
        date_created=m.get("date_created"),
        date_sent=m.get("date_sent"),
        error_code=m.get("error_code"),
        error_message=m.get("error_message"),
    )


def _raise_for_twilio(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error_message") or resp.text
            code = err.get("code")
        except ValueError:
            msg, code = resp.text, None
        raise RuntimeError(f"Twilio error {resp.status_code}" + (f" (code {code})" if code else "") + f": {msg}")


@mcp.tool()
async def send_sms(
    to: str = Field(description="Recipient phone number in E.164 format, e.g. +14155551234"),
    body: str = Field(description="Message text. Up to 1600 characters; over 160 is split into segments."),
    from_number: str | None = Field(
        default=None,
        description="Sender number in E.164 format. Defaults to the server's configured Twilio number.",
    ),
) -> SmsMessage:
    """
    Send an SMS text message via Twilio.

    Returns the created message with its SID and initial status (usually "queued").
    Use get_message_status with the SID to check delivery later.
    """
    client, _, default_from = _client()
    sender = from_number or default_from
    if not sender:
        raise ValueError("No sender number: pass from_number or set TWILIO_FROM_NUMBER")
    async with client:
        resp = await client.post("/Messages.json", data={"To": to, "From": sender, "Body": body})
    _raise_for_twilio(resp)
    m = resp.json()
    logger.info("Sent SMS %s to %s (status=%s)", m["sid"], to, m["status"])
    return _to_model(m)


@mcp.tool()
async def get_message_status(
    message_sid: str = Field(description="Twilio message SID returned by send_sms (starts with SM)"),
) -> SmsMessage:
    """
    Get the current delivery status and details of a previously sent SMS.
    """
    client, _, _ = _client()
    async with client:
        resp = await client.get(f"/Messages/{message_sid}.json")
    _raise_for_twilio(resp)
    return _to_model(resp.json())


@mcp.tool()
async def list_messages(
    to: str | None = Field(default=None, description="Filter by recipient number in E.164 format"),
    from_number: str | None = Field(default=None, description="Filter by sender number in E.164 format"),
    limit: int = Field(default=20, ge=1, le=100, description="Max messages to return (1-100)"),
) -> MessageList:
    """
    List recent SMS messages on the account, newest first. Optionally filter by recipient or sender.
    """
    client, _, _ = _client()
    params: dict[str, str | int] = {"PageSize": limit}
    if to:
        params["To"] = to
    if from_number:
        params["From"] = from_number
    async with client:
        resp = await client.get("/Messages.json", params=params)
    _raise_for_twilio(resp)
    items = [_to_model(m) for m in resp.json().get("messages", [])]
    return MessageList(messages=items, count=len(items))


def main():
    load_dotenv()
    if os.environ.get("ENVIRONMENT") != "local":
        host = GumstackHost(mcp)
        host.run(host="0.0.0.0", port=PORT)
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
