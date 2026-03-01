## Credentials Auth

Username/password authentication for LLMS. Provides a Sign In page, user management for admins, and account self-service for all users

The built-in [credentials](https://github.com/ServiceStack/llms/tree/main/llms/extensions/credentials) extension enables Username/Password authentication for your Application, including a Sign In page, user registration, role assignment, and account locking.

It provides full user management through both the CLI and a web-based Admin UI, along with account self-service for all authenticated users.

## Enable Credentials Auth

Credentials is the **default** Auth Provider (`LLMS_AUTH=credentials`) which is automatically enabled when **at least one user** has been created. If no users exist, the extension disables itself and the app runs as the **default** user without authentication.

## Getting Started

Create your **admin** user from the CLI to enable authentication, the **"admin"** username automatically gets the `Admin` role:

```bash
llms --adduser admin
# Enter password when prompted

# Start server
llms --serve 8000
```

When you start the server, authentication will now be enabled since at least one user exists and you'll be presented with the Sign In page. 

<Screenshot src="/img/auth/signin.webp" alt="Sign In Page" />

After logging in as `admin`, you can create additional users from the **Manage Users** page which can be accessed from the user menu. Only users with the `Admin` role can access the **Manage Users** page.

## UI Features

### Sign In Page

When authentication is enabled, unauthenticated users see a Sign In form with
username and password fields. Validation errors and incorrect credentials are
displayed inline.

### User Menu

After signing in, the user avatar dropdown shows:

- **Display name**
- **Manage Users** link (Admin only)
- **My Account** link
- **Sign Out** button

<Screenshot src="/img/auth/manage-users.webp" alt="Manage Users Page" />

### Manage Users (Admin only)

Accessible at `/admin` for users with the Admin role. Provides a table of all
users showing:

| Column     | Description                              |
|------------|------------------------------------------|
| Username   | Account name                             |
| Roles      | Assigned roles (Admin badge highlighted) |
| Status     | Active or Locked (with lock icon)        |
| Created    | Account creation date                    |
| Last Login | IP address and relative timestamp        |
| Actions    | Per-user action buttons                  |

**Available actions per user:**

- **Change Password** - Set a new password for any user (modal dialog)
- **Lock** - Suspend the account with confirmation (not available for admins or yourself)
- **Unlock** - Restore a locked account
- **Delete** - Permanently remove the account with confirmation (cannot delete yourself)

<ScreenshotsGallery className="mb-8" gridClass="grid grid-cols-1 md:grid-cols-2 gap-4" images={{
    'Create User': '/img/auth/create-user.webp',
    'Change Password': '/img/auth/change-password.webp',
    'Lock User': '/img/auth/lock-user.webp',
    'Delete User': '/img/auth/delete-user.webp',
}} />

**Create User** - Click "New User" to create accounts with a username, password,
and optional Admin role.

### My Account

Accessible at `/account` for all authenticated users. Shows your profile
information (avatar, username, roles) and provides a **Change Password** button
that requires your current password for verification.

<Screenshot src="/img/auth/my-account.webp" alt="My Account Page" />

Users can also change their avatar by clicking on their profile picture and uploading a new image:

<Screenshot src="/img/auth/settings-avatar.webp" alt="Settings Page" />

After uploading, the new avatar is displayed across the app, including the user menu and My Account page:

<Screenshot src="/img/auth/my-account-avatar.webp" alt="Custom Avatar" />

## CLI Commands

All commands operate on the user store at `~/.llms/credentials/users.json`.

### `--adduser USERNAME`

Create a new user or update an existing user's password. Prompts for password
with confirmation.

```bash
# Create a regular user
llms --adduser alice

# Create an admin (the username "admin" auto-assigns the Admin role)
llms --adduser admin
```

### `--removeuser USERNAME`

Delete a user and invalidate all their active sessions.

```bash
llms --removeuser alice
```

### `--listusers`

List all users with their creation date and lock status.

```bash
llms --listusers
#   admin  (created: 2025-03-15 10:30:00)
#   alice  (created: 2025-03-15 11:00:00)
#   bob    (created: 2025-03-16 09:15:00)  LOCKED: Account suspended
```

### `--lockuser [USERNAME]`

Lock a user account, preventing them from signing in. All active sessions are
immediately invalidated. Prompts for a lock reason (defaults to "Account suspended").

```bash
# Lock a specific user
llms --lockuser bob

# List users with lock status (omit username)
llms --lockuser
```

### `--unlockuser USERNAME`

Restore access for a locked user account.

```bash
llms --unlockuser bob
```

## Common Tasks

### Create multiple users

You can create several users in sequence from the CLI — each prompts for a password:

```bash
llms --adduser admin
llms --adduser alice
llms --adduser bob
```

### Reset a password from the CLI

Re-running `--adduser` with an existing username updates their password:

```bash
llms --adduser alice
# "User 'alice' already exists. Updating password."
```

### Reset a password from the UI

Sign in as an Admin, go to **Manage Users**, and click the **key icon** next to the user to open the Change Password dialog.

### Change your own password

Click your avatar, select **My Account**, and click **Change Password**. You'll need to enter your current password for verification.

### Temporarily disable a user

Lock an account from the CLI with an optional reason:

```bash
llms --lockuser bob
# Reason: "On vacation until March"
```

Restore access when ready:

```bash
llms --unlockuser bob
```

Alternatively, use the **lock** and **unlock** icons in **Manage Users**.

### Switch auth provider

```bash
# Use GitHub OAuth instead
llms --auth github_auth

# Or via Environment Variable
LLMS_AUTH=github_auth
```

## Password Storage

Passwords are never stored in plain text. Each password is hashed using **SHA-256** with a unique random salt:

1. A **16-byte random salt** is generated via `secrets.token_hex(16)`
2. The salt is prepended to the password and the combined string is **SHA-256** hashed
3. The result is stored as `salt:hex_digest` in the `password_hash` field of `users.json`

For example, a stored hash looks like:

```
a1b2c3d4e5f6...:e3b0c44298fc1c149afbf4c8996fb924...
│               │
└── salt        └── SHA-256 digest
```

During sign in, the stored salt is extracted from the hash, combined with the provided password, and the resulting digest is compared against the stored value.

## Sessions

Sessions are managed automatically and require no additional configuration:

| Property         | Detail                                                               |
|------------------|----------------------------------------------------------------------|
| **Storage**      | In-memory with persistence to `~/.llms/credentials/sessions/`        |
| **Expiry**       | 30 days from creation                                                |
| **Restarts**     | Sessions survive server restarts (loaded from disk on startup)       |
| **Token**        | Stored in an HTTP-only cookie (`llms-token`)                         |
| **Invalidation** | Locking or deleting a user immediately invalidates all their active sessions |
