"""Host-owned shared connections and owner-scoped personal connection files."""
import json
import os
import tempfile
from pathlib import Path

from .common import McpError, config_hash, digest, dumps

PERSONAL_KEYS = {"id", "displayName", "endpoint", "auth", "allowedTools", "deniedTools",
                 "includeInAll", "oauthClientId", "oauthIssuer", "oauthScopes"}
SHARED_KEYS = PERSONAL_KEYS | {"allowedUsers", "requiredRoles", "approval", "toolsWithoutApproval", "revision"}
EMPTY = '{"servers":[]}'


class Configuration:
    def editable_default(self, owner):
        return owner == "default" and self.config.get("singleUserMode") and not self.ctx.is_auth_enabled()

    def configuration_path(self, owner):
        if not isinstance(owner, str) or not owner.strip() or owner in (".", "..", "all", "*") or any(c in owner for c in "/\\\0"):
            raise McpError("access_denied")
        return Path(self.ctx.get_user_path(owner)) / "mcp_client/config.json"

    def read_configuration(self, owner):
        path = self.configuration_path(owner)
        if not path.exists():
            return EMPTY
        with path.open("rb") as f:
            data = f.read(65537)
        if len(data) > 65536:
            raise McpError("invalid_configuration", "MCP configuration is too large")
        try:
            return data.decode("utf-8-sig")
        except UnicodeError:
            raise McpError("invalid_configuration", "MCP configuration must be UTF-8") from None

    def parse_configuration(self, text, owner=None):
        try:
            if len(text.encode("utf-8")) > 65536:
                raise ValueError()
            doc = json.loads(text)
            if not isinstance(doc, dict) or set(doc) != {"servers"} or not isinstance(doc["servers"], list) or len(doc["servers"]) > 64:
                raise ValueError()
            for server in doc["servers"]:
                if not isinstance(server, dict) or set(server) - (PERSONAL_KEYS if owner else SHARED_KEYS):
                    raise ValueError()
                if any(v is None for v in server.values()):
                    raise ValueError()
                for key in ("id", "displayName", "endpoint", "oauthClientId", "oauthIssuer", "approval"):
                    if key in server and not isinstance(server[key], str):
                        raise ValueError()
                if "includeInAll" in server and type(server["includeInAll"]) is not bool:
                    raise ValueError()
                if "revision" in server and type(server["revision"]) is not int:
                    raise ValueError()
                auth = server.setdefault("auth", {"mode": "anonymous"})
                if (not isinstance(auth, dict) or set(auth) - {"mode", "secretReference"}
                        or any(not isinstance(v, str) for v in auth.values())):
                    raise ValueError()
                if owner:
                    if auth.get("mode") not in ("anonymous", "user_oauth", "bearer") or "secretReference" in auth:
                        raise ValueError()
                    server["_scope"] = "personal:" + owner
                if auth.get("mode") == "bearer" and not (self.ctx.is_auth_enabled() or self.config.get("singleUserMode")):
                    raise ValueError()
                if server.get("auth", {}).get("mode") == "user_oauth" and not (self.ctx.is_auth_enabled() or self.config.get("singleUserMode")):
                    raise ValueError()
            self.validate_servers(doc["servers"])
            return doc["servers"]
        except (ValueError, TypeError, KeyError, McpError):
            raise McpError("invalid_configuration", "Invalid MCP configuration. Check connection settings and host policy.") from None

    def get_servers(self, owner=None):
        shared = list(self.servers)
        if self.editable_default(owner):
            personal = self.parse_configuration(self.read_configuration("default"), "default")
        else:
            shared += self.parse_configuration(self.read_configuration("default"))
            personal = self.parse_configuration(self.read_configuration(owner), owner) if owner and owner != "default" else []
        result = shared + personal
        if len({s["id"] for s in result}) != len(result):
            raise McpError("invalid_configuration", "Personal MCP connection IDs must differ from shared connections")
        return result

    def personal_configuration(self, owner):
        can_edit = owner != "default" or self.editable_default(owner)
        text = self.read_configuration(owner) if can_edit else EMPTY
        self.parse_configuration(text, owner)
        return {**json.loads(text), "revision": digest(text), "canEdit": can_edit,
                "oauthRedirectUri": self.config.get("oauthRedirectUri")}

    def save_configuration(self, owner, body):
        if owner == "default" and not self.editable_default(owner):
            raise McpError("access_denied", "The default configuration is managed by the host")
        if not isinstance(body, dict) or set(body) - {"servers", "revision"}:
            raise McpError("invalid_configuration")
        try:
            text = dumps({"servers": body.get("servers")})
        except (ValueError, TypeError):
            raise McpError("invalid_configuration") from None
        if len(text.encode()) > 65536:
            raise McpError("invalid_configuration")
        servers = self.parse_configuration(text, owner)
        shared = list(self.servers) if self.editable_default(owner) else self.get_servers()
        if {s["id"] for s in shared} & {s["id"] for s in servers}:
            raise McpError("invalid_configuration", "A personal connection cannot override a shared connection")
        path = self.configuration_path(owner)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path) + ".lock", "a") as lock:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            before = self.read_configuration(owner)
            if body.get("revision") != digest(before):
                raise McpError("stale_tool", "Configuration changed. Reload before saving.")
            previous = self.parse_configuration(before, owner)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                    temp = f.name
                    f.write(text)
                os.replace(temp, path)
            finally:
                if temp and os.path.exists(temp):
                    os.unlink(temp)
            for old in previous:
                if not any(config_hash(s) == config_hash(old) for s in servers):
                    self.invalidate(owner, old["id"])
                    self.store.connect(owner, old["id"], disconnected=True, delete=True)
        return self.personal_configuration(owner)
