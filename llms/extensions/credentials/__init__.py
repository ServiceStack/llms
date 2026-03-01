import getpass
import glob
import hashlib
import json
import os
import secrets
import time

from aiohttp import web

SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def _get_credentials_dir(ctx):
    return os.path.join(ctx.get_user_path(), "credentials")


def _get_users_file(ctx):
    return os.path.join(_get_credentials_dir(ctx), "users.json")


def _get_sessions_dir(ctx):
    return os.path.join(_get_credentials_dir(ctx), "sessions")


def _load_users(users_file):
    if os.path.exists(users_file):
        with open(users_file, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_users(users_file, users):
    os.makedirs(os.path.dirname(users_file), exist_ok=True)
    with open(users_file, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def _hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}:{digest}"


def _verify_password(password, password_hash):
    salt, _ = password_hash.split(":", 1)
    return _hash_password(password, salt) == password_hash


def parser(parser):
    parser.add_argument("--adduser", default=None, metavar="USERNAME", help="Add a credentials user")
    parser.add_argument("--removeuser", default=None, metavar="USERNAME", help="Remove a credentials user")
    parser.add_argument("--listusers", action="store_true", help="List credentials users")
    parser.add_argument(
        "--lockuser",
        default=None,
        nargs="?",
        const="",
        metavar="USERNAME",
        help="Lock a user account (prompts for reason)",
    )
    parser.add_argument("--unlockuser", default=None, metavar="USERNAME", help="Unlock a user account")


def run(ctx):
    args = ctx.cli_args

    enabled_auth = ctx.enabled_auth()
    if enabled_auth != "credentials":
        return True

    if getattr(args, "adduser", None):
        username = args.adduser
        users_file = _get_users_file(ctx)
        users = _load_users(users_file)

        if username in users:
            print(f"User '{username}' already exists. Updating password.")

        password = getpass.getpass(f"Enter password for '{username}': ")
        if not password:
            print("Password cannot be empty.")
            return True
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            return True

        users[username] = {
            "password_hash": _hash_password(password),
            "created": time.time(),
            "roles": ["Admin"] if username == "admin" else [],
        }
        _save_users(users_file, users)
        print(f"User '{username}' added.")
        return True

    if getattr(args, "removeuser", None):
        username = args.removeuser
        users_file = _get_users_file(ctx)
        users = _load_users(users_file)

        if username not in users:
            print(f"User '{username}' not found.")
            return True

        del users[username]
        _save_users(users_file, users)

        # Remove any sessions for this user
        sessions_dir = _get_sessions_dir(ctx)
        if os.path.isdir(sessions_dir):
            for session_file in glob.glob(os.path.join(sessions_dir, "*.json")):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        session_data = json.load(f)
                    if session_data.get("userName") == username:
                        os.remove(session_file)
                except Exception:
                    pass

        print(f"User '{username}' removed.")
        return True

    if getattr(args, "listusers", False):
        users_file = _get_users_file(ctx)
        users = _load_users(users_file)
        if not users:
            print("No users configured.")
        else:
            for username, info in users.items():
                created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info.get("created", 0)))
                locked = info.get("locked")
                status = f"  LOCKED: {locked}" if locked else ""
                print(f"  {username}  (created: {created}){status}")
        return True

    if getattr(args, "lockuser", None) is not None:
        username = args.lockuser
        users_file = _get_users_file(ctx)
        users = _load_users(users_file)

        if not username:
            # No username provided, list users to help pick one
            if not users:
                print("No users configured.")
            else:
                print("Users:")
                for u in users:
                    locked = users[u].get("locked")
                    status = f"  (LOCKED: {locked})" if locked else ""
                    print(f"  {u}{status}")
            return True

        if username not in users:
            print(f"User '{username}' not found.")
            return True

        reason = input(f"Reason for locking '{username}': ").strip()
        if not reason:
            reason = "Account suspended"

        users[username]["locked"] = reason
        _save_users(users_file, users)

        # Invalidate active sessions for this user
        sessions_dir = _get_sessions_dir(ctx)
        if os.path.isdir(sessions_dir):
            for session_file in glob.glob(os.path.join(sessions_dir, "*.json")):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        session_data = json.load(f)
                    if session_data.get("userName") == username:
                        os.remove(session_file)
                except Exception:
                    pass

        print(f"User '{username}' locked: {reason}")
        return True

    if getattr(args, "unlockuser", None):
        username = args.unlockuser
        users_file = _get_users_file(ctx)
        users = _load_users(users_file)

        if username not in users:
            print(f"User '{username}' not found.")
            return True

        if "locked" in users[username]:
            del users[username]["locked"]
            _save_users(users_file, users)
            print(f"User '{username}' unlocked.")
        else:
            print(f"User '{username}' is not locked.")
        return True

    return False


def install(ctx):
    g_app = ctx.app

    enabled_auth = ctx.enabled_auth()
    if enabled_auth != "credentials":
        ctx.log(f"{enabled_auth} is enabled, skipping credentials auth provider.")
        ctx.disabled = True
        return

    users_file = _get_users_file(ctx)
    users = _load_users(users_file)

    if not users:
        ctx.disabled = True
        ctx.dbg("No credentials users configured. Use --adduser to add users.")
        return

    from llms.main import AuthProvider

    sessions_dir = _get_sessions_dir(ctx)
    os.makedirs(sessions_dir, exist_ok=True)

    class CredentialsAuthProvider(AuthProvider):
        def __init__(self, app):
            super().__init__(app)

        def get_session(self, request):
            # Check in-memory first (base class behavior)
            session = super().get_session(request)
            if session:
                return session

            # Check on-disk session files
            session_token = self.get_session_token(request)
            if not session_token:
                return None

            session_file = os.path.join(sessions_dir, f"{session_token}.json")
            if not os.path.exists(session_file):
                return None

            try:
                with open(session_file, encoding="utf-8") as f:
                    session_data = json.load(f)

                # Check expiry
                if time.time() - session_data.get("created", 0) > SESSION_MAX_AGE:
                    os.remove(session_file)
                    return None

                # Load into in-memory cache
                self.app.sessions[session_token] = session_data
                return session_data
            except Exception:
                return None

    auth_provider = CredentialsAuthProvider(g_app)
    ctx.set_auth_provider(auth_provider)

    # Load existing sessions from disk into memory
    current_time = time.time()
    for session_file in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            with open(session_file, encoding="utf-8") as f:
                session_data = json.load(f)
            if current_time - session_data.get("created", 0) > SESSION_MAX_AGE:
                os.remove(session_file)
                continue
            token = os.path.splitext(os.path.basename(session_file))[0]
            ctx.sessions[token] = session_data
        except Exception:
            pass

    ctx.log(f"Credentials auth enabled with {len(users)} user(s)")

    async def login_handler(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response(ctx.create_error_response("Invalid JSON body"), status=400)

        username = body.get("username", "").strip()
        password = body.get("password", "")

        if not username or not password:
            return web.json_response(ctx.create_error_response("Username and password are required"), status=400)

        # Reload users file to pick up changes
        current_users = _load_users(users_file)
        user = current_users.get(username)

        if not user or not _verify_password(password, user.get("password_hash", "")):
            return web.json_response(ctx.create_error_response("Invalid username or password"), status=401)

        locked = user.get("locked")
        if locked:
            return web.json_response(ctx.create_error_response(locked), status=403)

        # Capture client IP and login time
        last_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote
        user["lastIp"] = last_ip
        user["lastLogin"] = time.time()
        current_users[username] = user
        _save_users(users_file, current_users)

        session_token = secrets.token_urlsafe(32)
        session_data = {
            "userId": username,
            "userName": username,
            "displayName": username,
            "profileUrl": "/avatar/user",
            "roles": user.get("roles", []),
            "lastIp": last_ip,
            "created": time.time(),
        }

        # Store in memory and on disk
        ctx.sessions[session_token] = session_data
        session_file = os.path.join(sessions_dir, f"{session_token}.json")
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        response = web.json_response({**session_data, "sessionToken": session_token})
        response.set_cookie("llms-token", session_token, httponly=True, path="/", max_age=SESSION_MAX_AGE)
        return response

    async def logout_handler(request):
        session_token = auth_provider.get_session_token(request)

        if session_token:
            if session_token in ctx.sessions:
                del ctx.sessions[session_token]
            session_file = os.path.join(sessions_dir, f"{session_token}.json")
            if os.path.exists(session_file):
                os.remove(session_file)

        response = web.json_response({"success": True})
        response.del_cookie("llms-token")
        return response

    async def auth_handler(request):
        session_token = auth_provider.get_session_token(request)

        if session_token and session_token in ctx.sessions:
            session_data = ctx.sessions[session_token]
            return web.json_response(
                {
                    "userId": session_data.get("userId", ""),
                    "userName": session_data.get("userName", ""),
                    "displayName": session_data.get("displayName", ""),
                    "profileUrl": session_data.get("profileUrl", ""),
                    "roles": session_data.get("roles", []),
                    "authProvider": "credentials",
                }
            )

        return web.json_response(g_app.error_auth_required, status=401)

    def _require_admin(request):
        """Check if request is from an authenticated admin user. Returns (session, error_response)."""
        session = auth_provider.get_session(request)
        if not session:
            return None, web.json_response(
                ctx.create_error_response("Authentication required", "Unauthorized"), status=401
            )
        if "Admin" not in session.get("roles", []):
            return None, web.json_response(ctx.create_error_response("Admin role required", "Forbidden"), status=403)
        return session, None

    async def admin_list_users(request):
        _, err = _require_admin(request)
        if err:
            return err
        current_users = _load_users(users_file)
        result = []
        for username, info in current_users.items():
            result.append(
                {
                    "userName": username,
                    "created": info.get("created", 0),
                    "roles": info.get("roles", []),
                    "locked": info.get("locked"),
                    "lastIp": info.get("lastIp"),
                    "lastLogin": info.get("lastLogin"),
                }
            )
        return web.json_response({"users": result})

    async def admin_create_user(request):
        _, err = _require_admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response(ctx.create_error_response("Invalid JSON body"), status=400)

        username = body.get("userName", "").strip()
        password = body.get("password", "")
        roles = body.get("roles", [])

        if not username:
            return web.json_response(ctx.create_error_response("Username is required"), status=400)
        if not password:
            return web.json_response(ctx.create_error_response("Password is required"), status=400)

        current_users = _load_users(users_file)
        if username in current_users:
            return web.json_response(ctx.create_error_response(f"User '{username}' already exists"), status=400)

        if username == "admin" and "Admin" not in roles:
            roles = roles + ["Admin"]
        current_users[username] = {
            "password_hash": _hash_password(password),
            "created": time.time(),
            "roles": roles,
        }
        _save_users(users_file, current_users)
        return web.json_response({"success": True, "userName": username})

    async def admin_update_user(request):
        session, err = _require_admin(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return web.json_response(ctx.create_error_response("Invalid JSON body"), status=400)

        username = body.get("userName", "").strip()
        if not username:
            return web.json_response(ctx.create_error_response("Username is required"), status=400)

        current_users = _load_users(users_file)
        if username not in current_users:
            return web.json_response(ctx.create_error_response(f"User '{username}' not found"), status=404)

        user = current_users[username]

        # Update password if provided
        password = body.get("password")
        if password:
            user["password_hash"] = _hash_password(password)

        # Update roles if provided
        if "roles" in body:
            user["roles"] = body["roles"]

        # Update locked status if provided
        if "locked" in body:
            locked = body["locked"]
            if locked:
                # Prevent locking yourself
                if session.get("userName") == username:
                    return web.json_response(ctx.create_error_response("Cannot lock your own account"), status=400)
                # Prevent locking other admins
                if "Admin" in user.get("roles", []):
                    return web.json_response(ctx.create_error_response("Cannot lock an Admin user"), status=400)
                user["locked"] = locked if isinstance(locked, str) else "Account suspended"
                # Invalidate active sessions for this user
                for token, sess in list(ctx.sessions.items()):
                    if sess.get("userName") == username:
                        del ctx.sessions[token]
                        sf = os.path.join(sessions_dir, f"{token}.json")
                        if os.path.exists(sf):
                            os.remove(sf)
            else:
                user.pop("locked", None)

        current_users[username] = user
        _save_users(users_file, current_users)
        return web.json_response({"success": True, "userName": username})

    async def admin_delete_user(request):
        session, err = _require_admin(request)
        if err:
            return err
        username = request.match_info.get("username", "").strip()
        if not username:
            return web.json_response(ctx.create_error_response("Username is required"), status=400)

        current_users = _load_users(users_file)
        if username not in current_users:
            return web.json_response(ctx.create_error_response(f"User '{username}' not found"), status=404)

        # Prevent deleting yourself
        if session.get("userName") == username:
            return web.json_response(ctx.create_error_response("Cannot delete your own account"), status=400)

        del current_users[username]
        _save_users(users_file, current_users)

        # Remove sessions for deleted user
        for token, sess in list(ctx.sessions.items()):
            if sess.get("userName") == username:
                del ctx.sessions[token]
                sf = os.path.join(sessions_dir, f"{token}.json")
                if os.path.exists(sf):
                    os.remove(sf)

        return web.json_response({"success": True, "userName": username})

    async def account_change_password(request):
        session = auth_provider.get_session(request)
        if not session:
            return web.json_response(ctx.create_error_response("Authentication required", "Unauthorized"), status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response(ctx.create_error_response("Invalid JSON body"), status=400)

        current_password = body.get("currentPassword", "")
        new_password = body.get("newPassword", "")
        if not current_password or not new_password:
            return web.json_response(
                ctx.create_error_response("Current password and new password are required"), status=400
            )

        username = session.get("userName")
        current_users = _load_users(users_file)
        user = current_users.get(username)
        if not user:
            return web.json_response(ctx.create_error_response("User not found"), status=404)

        if not _verify_password(current_password, user.get("password_hash", "")):
            return web.json_response(ctx.create_error_response("Current password is incorrect"), status=400)

        user["password_hash"] = _hash_password(new_password)
        current_users[username] = user
        _save_users(users_file, current_users)
        return web.json_response({"success": True})

    ctx.add_post("/auth/login", login_handler)
    ctx.add_post("/auth/logout", logout_handler)
    ctx.add_get("/auth", auth_handler)
    ctx.add_post("/account/change-password", account_change_password)
    ctx.add_get("/admin/users", admin_list_users)
    ctx.add_post("/admin/users", admin_create_user)
    ctx.add_put("/admin/users", admin_update_user)
    ctx.add_delete("/admin/users/{username}", admin_delete_user)


__parser__ = parser
__install__ = install
__run__ = run
