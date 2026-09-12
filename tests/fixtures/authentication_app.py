"""Deterministic FastAPI target used by credential end-to-end tests."""

import base64
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


@dataclass
class AuthenticationFixtureState:
    users: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "alice": {"password": "alice-password", "email": "alice@example.test", "tenant": "tenant-a", "role": "member"},
        "bob": {"password": "bob-password", "email": "bob@example.test", "tenant": "tenant-b", "role": "reader"},
    })
    mailboxes: dict[str, list[str]] = field(default_factory=dict)
    sessions: dict[str, str] = field(default_factory=dict)
    uidvalidity: str = "1"


class Registration(BaseModel):
    username: str
    password: str
    email: str
    tenant: str = "tenant-a"
    role: str = "member"


class Login(BaseModel):
    username: str
    password: str


class MfaRequest(BaseModel):
    username: str


class MfaVerification(BaseModel):
    username: str
    code: str


class FixtureImapClient:
    """Small IMAP-compatible adapter for the fixture mailbox, without external mail infrastructure."""

    def __init__(self, state: AuthenticationFixtureState):
        self.state = state
        self.email = ""

    def login(self, email: str, _password: str):
        self.email = email
        return "OK", [b"logged in"]

    def select(self, _folder: str, readonly: bool = True):
        return "OK", [str(len(self.state.mailboxes.get(self.email, []))).encode("ascii")]

    def search(self, *_args):
        messages = self.state.mailboxes.get(self.email, [])
        return "OK", [b" ".join(str(index + 1).encode("ascii") for index in range(len(messages)))]

    def uid(self, command: str, _charset, query: str):
        if command != "search":
            return "NO", []
        messages = self.state.mailboxes.get(self.email, [])
        if query == "ALL":
            return "OK", [b" ".join(str(index + 1).encode("ascii") for index in range(len(messages)))]
        first = int(query.split(":", 1)[0])
        return "OK", [b" ".join(str(index + 1).encode("ascii") for index in range(first - 1, len(messages)))]

    def response(self, name: str):
        return ("UIDVALIDITY", [self.state.uidvalidity.encode("ascii")]) if name == "UIDVALIDITY" else (name, [b""])

    def fetch(self, message_id: bytes, _query: str):
        index = int(message_id) - 1
        message = self.state.mailboxes[self.email][index].encode("utf-8")
        timestamp = datetime.now(UTC).strftime("%d-%b-%Y %H:%M:%S +0000")
        return "OK", [(f'1 (INTERNALDATE "{timestamp}")'.encode("ascii"), message)]

    def logout(self):
        return "BYE", [b"logout"]


def create_authentication_app(state: AuthenticationFixtureState) -> FastAPI:
    app = FastAPI()

    @app.post("/register")
    def register(payload: Registration):
        if payload.username in state.users:
            raise HTTPException(status_code=409, detail="username exists")
        state.users[payload.username] = payload.model_dump()
        return {"registered": payload.username, "tenant": payload.tenant, "role": payload.role}

    @app.post("/login")
    def login(payload: Login):
        user = state.users.get(payload.username)
        if not user or user["password"] != payload.password:
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = f"session-{payload.username}"
        state.sessions[token] = payload.username
        return {"access_token": token, "token_type": "Bearer"}

    @app.post("/mfa/request")
    def request_mfa(payload: MfaRequest):
        user = state.users.get(payload.username)
        if not user:
            raise HTTPException(status_code=404, detail="unknown user")
        code = "246810"
        state.mailboxes.setdefault(user["email"], []).append(
            f"From: identity@example.test\nSubject: MFA code\n\nYour code is {code}"
        )
        return {"challenge": "email", "email": user["email"]}

    @app.post("/mfa/verify")
    def verify_mfa(payload: MfaVerification):
        if payload.username not in state.users or payload.code != "246810":
            raise HTTPException(status_code=401, detail="invalid MFA code")
        return {"verified": True}

    @app.post("/oauth/token")
    def oauth_token(authorization: str | None = Header(default=None)):
        expected = base64.b64encode(b"fixture-client:fixture-secret").decode("ascii")
        if authorization != f"Basic {expected}":
            raise HTTPException(status_code=401, detail="invalid client")
        return {"access_token": "fixture-access-token", "token_type": "Bearer", "expires_in": 300}

    @app.get("/api-key")
    def api_key(x_api_key: str | None = Header(default=None)):
        if x_api_key != "fixture-api-key":
            raise HTTPException(status_code=401, detail="invalid api key")
        return {"authorized": True}

    @app.get("/oauth-protected")
    def oauth_protected(authorization: str | None = Header(default=None)):
        if authorization != "Bearer fixture-access-token":
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return {"authorized": True}

    @app.get("/tenants/{tenant}/records/{record_id}")
    def tenant_record(tenant: str, record_id: str, authorization: str | None = Header(default=None)):
        username = state.sessions.get((authorization or "").removeprefix("Bearer "))
        if not username:
            raise HTTPException(status_code=401, detail="login required")
        # Deliberately vulnerable: the authenticated user's tenant is not compared to the requested tenant.
        return {"tenant": tenant, "record_id": record_id, "requested_by": username}

    return app


@contextmanager
def running_authentication_app() -> Iterator[tuple[str, AuthenticationFixtureState]]:
    """Serve the fixture on loopback so HTTP clients exercise a real network boundary."""

    state = AuthenticationFixtureState()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        host, port = reservation.getsockname()
    server = uvicorn.Server(uvicorn.Config(create_authentication_app(state), host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.01)
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.should_exit = True
        thread.join(timeout=5)
