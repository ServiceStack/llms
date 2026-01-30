import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode

import aiohttp
from aiohttp import web


def install(ctx):
    g_app = ctx.app

    auth_config_file = os.path.join(ctx.get_user_path(), "github_auth", "config.json")

    auth_config = None
    if os.path.exists(auth_config_file):
        try:
            with open(auth_config_file, encoding="utf-8") as f:
                auth_config = json.load(f)
            if "enabled" in auth_config and not auth_config["enabled"]:
                ctx.log("GitHub Auth is disabled in config")
                auth_config = None
        except Exception as e:
            ctx.err("Failed to load GitHub auth config", e)
    else:
        ctx.dbg(f"GitHub Auth config file '{auth_config_file}' not found")

    if not auth_config:
        # don't load extension if auth_config is not found or is disabled
        ctx.disabled = True
        return

    client_id = auth_config.get("client_id", "")
    client_secret = auth_config.get("client_secret", "")
    redirect_uri = auth_config.get("redirect_uri", "")
    restrict_to = auth_config.get("restrict_to", "")

    # Expand environment variables
    if client_id.startswith("$"):
        client_id = client_id[1:]
    if client_secret.startswith("$"):
        client_secret = client_secret[1:]
        client_secret = os.getenv(client_secret)
    if redirect_uri.startswith("$"):
        redirect_uri = redirect_uri[1:]
        redirect_uri = os.getenv(redirect_uri)
    if restrict_to.startswith("$"):
        restrict_to = restrict_to[1:]
        restrict_to = os.getenv(restrict_to)

    # check if client_id is set
    if client_id == "GITHUB_CLIENT_ID":
        client_id = os.getenv(client_id)
    if client_secret == "GITHUB_CLIENT_SECRET":
        client_secret = os.getenv(client_secret)
    if restrict_to == "GITHUB_USERS":
        restrict_to = os.getenv(restrict_to)

    if not client_id or not redirect_uri or not client_secret:
        ctx.disabled = True
        ctx.log("GitHub OAuth client_id, client_secret and redirect_uri are not configured")
        return

    from llms.main import AuthProvider

    class GitHubAuthProvider(AuthProvider):
        def __init__(self, app):
            super().__init__(app)

    # Adding an Auth Provider forces Authentication to be enabled
    auth_provider = GitHubAuthProvider(g_app)
    g_app.auth_providers.append(auth_provider)

    # OAuth handlers
    async def github_auth_handler(request):
        # Generate CSRF state token
        state = secrets.token_urlsafe(32)
        ctx.oauth_states[state] = {"created": time.time(), "redirect_uri": redirect_uri}

        # Clean up old states (older than 10 minutes)
        current_time = time.time()
        expired_states = [s for s, data in ctx.oauth_states.items() if current_time - data["created"] > 600]
        for s in expired_states:
            del ctx.oauth_states[s]

        # Build GitHub authorization URL
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": "read:user user:email",
        }
        auth_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

        return web.HTTPFound(auth_url)

    def validate_user(github_username):
        # If restrict_to is configured, validate the user
        if restrict_to:
            # Parse allowed users (comma or space delimited)
            allowed_users = [u.strip() for u in re.split(r"[,\s]+", restrict_to) if u.strip()]

            # Check if user is in the allowed list
            if not github_username or github_username not in allowed_users:
                ctx.log(f"Access denied for user: {github_username}. Not in allowed list: {allowed_users}")
                return web.Response(
                    text=f"Access denied. User '{github_username}' is not authorized to access this application.",
                    status=403,
                )
        return None

    async def github_callback_handler(request):
        """Handle GitHub OAuth callback"""
        code = request.query.get("code")
        state = request.query.get("state")

        # Handle malformed URLs where query params are appended with & instead of ?
        if not code and "tail" in request.match_info:
            tail = request.match_info["tail"]
            if tail.startswith("&"):
                params = parse_qs(tail[1:])
                code = params.get("code", [None])[0]
                state = params.get("state", [None])[0]

        if not code or not state:
            return web.Response(text="Missing code or state parameter", status=400)

        # Verify state token (CSRF protection)
        if state not in ctx.oauth_states:
            return web.Response(text="Invalid state parameter", status=400)

        ctx.oauth_states.pop(state)

        # Exchange code for access token
        async with aiohttp.ClientSession() as session:
            token_url = "https://github.com/login/oauth/access_token"
            token_data = {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
            headers = {"Accept": "application/json"}

            async with session.post(token_url, data=token_data, headers=headers) as resp:
                token_response = await resp.json()
                access_token = token_response.get("access_token")

                if not access_token:
                    error = token_response.get("error_description", "Failed to get access token")
                    return web.json_response(ctx.create_error_response(f"OAuth error: {error}"), status=400)

            # Fetch user info
            user_url = "https://api.github.com/user"
            headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

            async with session.get(user_url, headers=headers) as resp:
                user_data = await resp.json()

            # Validate user
            error_response = validate_user(user_data.get("login", ""))
            if error_response:
                return error_response

        # Create session
        session_token = secrets.token_urlsafe(32)
        ctx.sessions[session_token] = {
            "userId": str(user_data.get("id", "")),
            "userName": user_data.get("login", ""),
            "displayName": user_data.get("name", ""),
            "profileUrl": user_data.get("avatar_url", ""),
            "email": user_data.get("email", ""),
            "created": time.time(),
        }

        # Redirect to UI with session token
        response = web.HTTPFound(f"/?session={session_token}")
        response.set_cookie("llms-token", session_token, httponly=True, path="/", max_age=86400)
        return response

    async def session_handler(request):
        """Validate and return session info"""
        session_token = auth_provider.get_session_token(request)

        if not session_token or session_token not in ctx.sessions:
            return web.json_response(ctx.create_error_response("Invalid or expired session"), status=401)

        session_data = ctx.sessions[session_token]

        # Clean up old sessions (older than 24 hours)
        current_time = time.time()
        expired_sessions = [token for token, data in ctx.sessions.items() if current_time - data["created"] > 86400]
        for token in expired_sessions:
            del ctx.sessions[token]

        return web.json_response({**session_data, "sessionToken": session_token})

    async def logout_handler(request):
        """End OAuth session"""
        session_token = auth_provider.get_session_token(request)

        if session_token and session_token in g_app.sessions:
            del g_app.sessions[session_token]

        response = web.json_response({"success": True})
        response.del_cookie("llms-token")
        return response

    async def auth_handler(request):
        """Check authentication status and return user info"""
        # Check for OAuth session token
        session_token = auth_provider.get_session_token(request)

        if session_token and session_token in g_app.sessions:
            session_data = g_app.sessions[session_token]
            return web.json_response(
                {
                    "userId": session_data.get("userId", ""),
                    "userName": session_data.get("userName", ""),
                    "displayName": session_data.get("displayName", ""),
                    "profileUrl": session_data.get("profileUrl", ""),
                    "authProvider": "github",
                }
            )

        # Check for API key in Authorization header
        # auth_header = request.headers.get('Authorization', '')
        # if auth_header.startswith('Bearer '):
        #     # For API key auth, return a basic response
        #     # You can customize this based on your API key validation logic
        #     api_key = auth_header[7:]
        #     if api_key:  # Add your API key validation logic here
        #         return web.json_response({
        #             "userId": "1",
        #             "userName": "apiuser",
        #             "displayName": "API User",
        #             "profileUrl": "",
        #             "authProvider": "apikey"
        #         })

        # Not authenticated - return error in expected format
        return web.json_response(g_app.error_auth_required, status=401)

    ctx.add_get("/auth", auth_handler)
    ctx.add_get("/auth/github", github_auth_handler)
    ctx.add_get("/auth/github/callback", github_callback_handler)
    ctx.add_get("/auth/github/callback{tail:.*}", github_callback_handler)
    ctx.add_get("/auth/session", session_handler)
    ctx.add_post("/auth/logout", logout_handler)


__install__ = install
