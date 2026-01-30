# GitHub Auth Extension

The GitHub Auth extension enables OAuth 2.0 authentication via GitHub for your llms application. When enabled, users must sign in with their GitHub account before accessing the application.

## Features

- **GitHub OAuth 2.0** - Standard OAuth flow with CSRF protection
- **User Restrictions** - Optionally restrict access to specific GitHub users
- **Session Management** - Automatic session handling with 24-hour expiry
- **Environment Variables** - Credentials can use env vars for secure deployment

## Configuration

Create a config file at `~/.llms/users/default/github_auth/config.json`:

```json
{
  "enabled": true,
  "client_id": "$GITHUB_CLIENT_ID",
  "client_secret": "$GITHUB_CLIENT_SECRET",
  "redirect_uri": "http://localhost:8000/auth/github/callback",
  "restrict_to": "$GITHUB_USERS"
}
```

| Property        | Description |
|-----------------|-------------|
| `client_id`     | GitHub OAuth App client ID |
| `client_secret` | GitHub OAuth App client secret |
| `redirect_uri`  | Callback URL registered with GitHub |
| `restrict_to`   | Optional comma/space-delimited list of allowed GitHub usernames |
| `enabled`       | Set to `false` to disable the extension |

Values prefixed with `$` are resolved from environment variables.

## Creating a GitHub OAuth App

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **New OAuth App**
3. Fill in the application details:
   - **Application name**: Your app name
   - **Homepage URL**: Your app's homepage (e.g., `http://localhost:8000`)
   - **Authorization callback URL**: Must match your `redirect_uri` (e.g., `http://localhost:8000/auth/github/callback`)
4. Click **Register application**
5. Copy the **Client ID** and generate a **Client Secret**

## API Endpoints

The extension registers these routes:

| Method | Endpoint                | Description |
|--------|-------------------------|-------------|
| GET    | `/auth`                 | Check authentication status |
| GET    | `/auth/github`          | Initiate GitHub OAuth flow |
| GET    | `/auth/github/callback` | OAuth callback handler |
| GET    | `/auth/session`         | Get current session info |
| POST   | `/auth/logout`          | End the current session |

### GET /auth

Returns the authenticated user's info or a 401 error:

```json
{
  "userId": "12345",
  "userName": "octocat",
  "displayName": "The Octocat",
  "profileUrl": "https://avatars.githubusercontent.com/u/12345",
  "authProvider": "github"
}
```

### GET /auth/session

Returns full session details including the session token:

```json
{
  "userId": "12345",
  "userName": "octocat",
  "displayName": "The Octocat",
  "profileUrl": "https://avatars.githubusercontent.com/u/12345",
  "email": "octocat@github.com",
  "created": 1706600000.123,
  "sessionToken": "..."
}
```

## OAuth Flow

```
┌─────────┐          ┌─────────┐          ┌────────┐
│ Browser │          │  llms   │          │ GitHub │
└────┬────┘          └────┬────┘          └───┬────┘
     │                    │                   │
     │ GET /auth/github   │                   │
     ├───────────────────►│                   │
     │                    │                   │
     │   302 Redirect     │                   │
     │◄───────────────────┤                   │
     │                    │                   │
     │  /login/oauth/authorize?...            │
     ├────────────────────────────────────────►
     │                    │                   │
     │         User grants access             │
     │◄────────────────────────────────────────
     │                    │                   │
     │ GET /auth/github/callback?code=...     │
     ├───────────────────►│                   │
     │                    │                   │
     │                    │ POST /access_token │
     │                    ├──────────────────►│
     │                    │                   │
     │                    │   access_token    │
     │                    │◄──────────────────┤
     │                    │                   │
     │                    │ GET /user         │
     │                    ├──────────────────►│
     │                    │                   │
     │                    │   user info       │
     │                    │◄──────────────────┤
     │                    │                   │
     │  302 /?session=... │                   │
     │  Set-Cookie: token │                   │
     │◄───────────────────┤                   │
     │                    │                   │
```

1. User clicks "Sign in with GitHub" → redirects to `/auth/github`
2. Server generates CSRF state token and redirects to GitHub
3. User authorizes the app on GitHub
4. GitHub redirects back with authorization code
5. Server exchanges code for access token
6. Server fetches user info from GitHub API
7. Server creates session and sets cookie

## Restricting Access

To limit access to specific GitHub users, set `restrict_to` in your config:

```json
{
  "client_id": "...",
  "client_secret": "...",
  "redirect_uri": "...",
  "restrict_to": "alice bob charlie"
}
```

Users not in this list receive a `403 Forbidden` response.

## UI Component

The extension provides a custom `SignIn` component that displays a "Sign in with GitHub" button. This component automatically overrides the default sign-in UI when the extension is loaded.

## Session Storage

Sessions are stored in memory with:
- **Token**: Cryptographically secure random string
- **User data**: GitHub user ID, username, display name, avatar URL, email
- **Expiry**: Automatic cleanup after 24 hours
- **Cookie**: `llms-token` with `httponly` flag for security

## Security Notes

- **CSRF Protection**: OAuth state tokens prevent cross-site request forgery
- **State Cleanup**: Expired state tokens (>10 min) are automatically removed
- **Session Cleanup**: Sessions older than 24 hours are pruned
- **HttpOnly Cookie**: Session token is not accessible via JavaScript
