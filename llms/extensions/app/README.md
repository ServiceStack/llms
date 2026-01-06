# App Extension

This extension provides the core application logic and data persistence for the LLMs platform.

## Data Storage & Architecture

### Server-Side SQLite Migration
The application has migrated from client-side IndexedDB storage to a robust server-side SQLite solution. This architectural shift ensures better data consistency, improved performance, and enables multi-device access to your chat history.

### Asset Management
To keep the database efficient and portable, binary assets (images, audio, etc.) are not stored directly in the SQLite database. Instead:
- All generated assets are stored in the local file system cache at `~/.llms/cache`.
- The database stores only **relative URLs** pointing to these assets.
- This approach allows for efficient caching and serving of static media.

### Concurrency Model
To ensure data integrity and high performance without complex locking mechanisms, the system utilizes a **single background thread** for managing all write operations to the database. This design improves concurrency handling and eliminates database locking issues during high-load scenarios.

### Multi-Tenancy & Security
When authentication is enabled, data isolation is automatically enforced. All core tables, including `threads` and `requests`, are scoped to the authenticated user, ensuring that users can only access their own data.
