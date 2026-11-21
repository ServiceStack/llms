# Gemini File Search Store Extension

A complete solution for managing Gemini's [File Search Stores](https://ai.google.dev/api/file-search), enabling **RAG (Retrieval Augmented Generation)** workflows with automatic document uploads, category organization, and bidirectional sync between your local database and Gemini's cloud storage.

Features include background processing, SHA-256 deduplication, state tracking, and a RESTful API for seamless integration.
It talks directly to Gemini's documented HTTP API and adds no Python dependencies beyond llms.py.

## Key Features

### Intelligent Document Management
- **Automatic Deduplication**: SHA-256 hash-based duplicate detection prevents redundant uploads
- **Category Organization**: Organize documents into logical categories for better management
- **Custom Metadata**: Track documents with ID, hash, and category metadata
- **State Tracking**: Monitor document states (PENDING, ACTIVE, FAILED) throughout their lifecycle

### Background Upload Worker
- **Asynchronous Processing**: Automatically processes pending uploads in the background
- **Auto-start on Upload**: Worker automatically starts when new documents are uploaded
- **Startup Processing**: Processes any pending uploads from previous sessions on extension startup
- **Operation Recovery**: Commits accepted operation IDs to SQLite before polling; retries and restarts
  check the same operation for unchanged content, metadata and store rather than uploading duplicates.
  Receipts remain internal and are excluded from API DTOs. Successful completion and terminal indexing
  errors clear them; explicit retry after a terminal error starts a new upload.
- **Polling Resilience**: Retries transient HTTP/connection errors with backoff (up to
  `GEMINI_UPLOAD_MAX_RETRIES` consecutive attempts, default 4). Errors identify the operation and HTTP
  status. Python continues waiting while Gemini reports a valid pending operation; cancellation
  interrupts polling/backoff and preserves the receipt. Unlike C#, there is no overall indexing deadline.
  Older failed uploads have no receipt: reconcile the store before retrying to avoid remote duplicates.
- **Batch Processing**: Efficiently handles multiple documents in batches of 10
- **Automatic Metadata Updates**: Keeps filestore statistics up-to-date after uploads complete

### Smart Synchronization
- **Bidirectional Sync**: Identify documents missing from local or remote stores
- **Metadata Validation**: Detect and fix metadata mismatches between local and remote
- **Duplicate Detection**: Find and flag duplicate documents in remote stores
- **State Management**: Automatically update document states based on sync results
- **Detailed Reporting**: Comprehensive sync reports with counts and sample documents

### Custom MIME Type Support
- **Configurable Types**: Override MIME types for specific file extensions via environment variable
- **Markdown Extensions**: Pre-configured support for mdx, l, ss, sc extensions as text/markdown
- **Upload Optimization**: Ensures correct MIME types for better search indexing

## Configuration

### Environment Variables

To use this extension, you must configure your Gemini API key.

1.  Obtain an API key from [Google AI Studio](https://aistudio.google.com/).
2.  Add it to your environment variables or `.env` file:

#### Required
```bash
GOOGLE_API_KEY=your_api_key_here
# or
GEMINI_API_KEY=your_api_key_here
```

Both are checked, with `GOOGLE_API_KEY` taking precedence — matching the `google` provider's
own precedence, so configuring chat is enough to enable this extension too. If neither is set
the extension logs `GOOGLE_API_KEY or GEMINI_API_KEY is not configured` and disables itself.

#### Optional
```bash
# Override MIME types for specific file extensions (comma-separated)
# Format: extension:mime/type,extension:mime/type
GEMINI_UPLOAD_MIME_TYPES="mdx:text/markdown,ss:text/markdown"
```

### Database Storage
The extension automatically creates a SQLite database at:
```
.llms/user/default/gemini/gemini.sqlite
```

### File Cache
Uploaded files are stored in the cache directory with SHA-256 hash-based filenames:
```
~/.llms/cache/[hash_prefix]/[hash].[ext]
~/.llms/cache/[hash_prefix]/[hash].info.json
```

## API Endpoints

### Filestore Management

#### Query Filestores
```
GET /filestores?take=50&skip=0&sort=-id&q=search_term
```
Query parameters:
- `take`: Number of results (default: 50, max: 1000)
- `skip`: Offset for pagination
- `sort`: Sort order (`-id`, `id`, `failed`)
- `q`: Search by display name
- `null`: Comma-separated columns that should be NULL
- `not_null`: Comma-separated columns that should NOT be NULL

#### Create Filestore
```
POST /filestores
Content-Type: application/json

{
  "displayName": "My Documents"
}
```

#### Delete Filestore
```
DELETE /filestores/{id}
```
Deletes the filestore and all associated documents (uses `force: true`).

#### Get Filestore Categories
```
GET /filestores/{id}/categories
```
Returns categories with document counts and total sizes.

#### Sync Filestore Documents
```
POST /filestores/{id}/sync
```
Synchronizes local documents with remote Gemini file search store. Returns detailed report:
```json
{
  "Missing from Local": { "count": 0, "docs": [] },
  "Missing from Gemini": { "count": 2, "docs": ["category/file1.pdf"] },
  "Missing Metadata": { "count": 0, "docs": [] },
  "Metadata Mismatch": { "count": 1, "docs": ["category/file2.txt"] },
  "Unmatched Fields": { "count": 3, "docs": ["category/file3.md"] },
  "Duplicate Documents": { "count": 0, "docs": [] },
  "Summary": {
    "Local Documents": 45,
    "Remote Documents": 43,
    "Matched Documents": 43
  }
}
```

#### List Remote Documents
```
GET /filestores/{id}/documents
```
Fetches current state of all documents from Gemini API.

### Document Management

#### Upload Documents
```
POST /filestores/{id}/upload?category=my_category&sourceUrl=https://docs.acme.com/guide
Content-Type: multipart/form-data

file: [binary file data]
file: [binary file data]
...
```
Uploads one or more files to the filestore. Documents are:
1. Hashed (SHA-256) for deduplication
2. Saved to cache directory
3. Added to local database
4. Queued for background upload to Gemini

`category` and `sourceUrl` can also be sent as **form fields**, in which case they apply to every
file that follows them in the request until the next one overrides them. This lets a single
request carry a whole crawl, each page with its own public URL:

```
POST /filestores/1/upload
Content-Type: multipart/form-data

category: guides
sourceUrl: https://docs.acme.com/guides/auth
file: auth.md
sourceUrl: https://docs.acme.com/guides/limits
file: limits.md
```

`sourceUrl` is the page a reader should be sent to. It's stored on the document, pushed to Gemini
as custom metadata, and used to link citations — so an answer points at the customer's live docs
page instead of a download of the cached upload.

#### Query Documents
```
GET /documents?filestoreId=1&take=50&skip=0&sort=-id
```
Query parameters:
- `filestoreId`: Filter by filestore
- `category`: Filter by category
- `take`, `skip`, `sort`: Pagination and sorting
- `q`: Search by display name
- `ids_in`: Comma-separated IDs
- `displayNames`: Filter by display names
- `null`, `not_null`: Column filters
- Sort options: `-id`, `uploading`, `failed`, `issues`

#### Delete Document
```
DELETE /documents/{id}
```
Deletes document from both local database and Gemini (handles 404s gracefully).

#### Retry Upload
```
POST /documents/{id}/upload
```
Manually retry uploading a failed document. Waits for upload to complete and returns updated document state.

## Usage Examples

### Creating a Filestore
```bash
curl -X POST http://localhost:8080/ext/gemini/filestores \
  -H "Content-Type: application/json" \
  -d '{"displayName": "Technical Documentation"}'
```

### Uploading Documents with Categories
```bash
curl -X POST http://localhost:8080/ext/gemini/filestores/1/upload?category=guides \
  -F "file=@guide1.pdf" \
  -F "file=@guide2.pdf"
```

### Syncing Documents
```bash
curl -X POST http://localhost:8080/ext/gemini/filestores/1/sync
```

### Querying Failed Uploads
```bash
curl "http://localhost:8080/ext/gemini/documents?sort=failed&not_null=error"
```

### Querying by Category
```bash
curl "http://localhost:8080/ext/gemini/documents?filestoreId=1&category=guides&sort=-id&take=20"
```

## How It Works

### Upload Process
1. **File Reception**: Multipart file upload with optional category
2. **Hashing**: SHA-256 hash calculated for deduplication
3. **Deduplication Check**: Existing documents with same hash are deleted
4. **Local Storage**: File saved to cache directory with hash-based filename
5. **Database Record**: Document metadata stored in SQLite
6. **Worker Trigger**: Background upload worker starts automatically
7. **Gemini Upload**: Worker uploads file to Gemini file search store
8. **Metadata Update**: Document updated with Gemini response data

### Background Worker
The upload worker:
- Starts automatically on extension initialization to process pending uploads
- Starts automatically when new documents are uploaded
- Processes up to 10 documents per batch
- Polls Gemini operations until completion
- Updates filestore statistics after batch completion
- Handles errors gracefully and stores error messages
- Stops automatically when queue is empty

### Synchronization Process
The sync endpoint:
1. **Fetches Local Documents**: Queries all documents for the filestore
2. **Builds Hash Lookup**: Creates maps by hash and name for fast matching
3. **Lists Remote Documents**: Fetches all documents from Gemini API
4. **Matching Logic**:
   - Matches by custom metadata hash (preferred)
   - Falls back to matching by document name
5. **Detects Issues**:
   - Documents in Gemini but not in local database
   - Documents in local database but not in Gemini
   - Documents missing custom metadata (id, hash, category)
   - Metadata mismatches (wrong id or hash in metadata)
   - Field mismatches (name, size, mime type, etc.)
   - Duplicate hashes in remote store
6. **Updates States**: Automatically updates document states based on findings
7. **Returns Report**: Detailed breakdown with counts and sample filenames

### MIME Type Handling
- Default MIME type determined by file extension using Python's `mimetypes`
- Custom overrides via `GEMINI_UPLOAD_MIME_TYPES` environment variable
- JSON files uploaded without explicit MIME type (Gemini API limitation workaround)
- Custom MIME types applied during upload configuration

## Database Schema

### Filestore Table
Stores Gemini file search store metadata:
- `id`, `user`, `createdAt`, `updatedAt`
- `name`: Gemini resource name (e.g., "fileSearchStores/...")
- `displayName`: Human-readable name
- `createTime`, `updateTime`: Gemini timestamps
- `activeDocumentsCount`, `pendingDocumentsCount`, `failedDocumentsCount`
- `sizeBytes`: Total size of all documents
- `metadata`: JSON metadata
- `error`: Last error message
- `ref`: Optional reference field

### Document Table
Stores document metadata:
- `id`, `filestoreId`, `user`, `createdAt`
- `filename`: SHA-256 based filename
- `url`: Cache URL path
- `hash`: SHA-256 hash of content
- `size`: File size in bytes
- `displayName`: Original filename
- `name`: Gemini resource name
- `customMetadata`: JSON metadata (id, hash, category, sourceUrl)
- `createTime`, `updateTime`: Gemini timestamps
- `sizeBytes`: Size reported by Gemini
- `mimeType`: MIME type
- `state`: Document state (STATE_PENDING, STATE_ACTIVE, STATE_FAILED, etc.)
- `category`: User-defined category
- `sourceUrl`: Public URL this document was published at, used to link citations
- `tags`: JSON tags
- `startedAt`, `uploadedAt`: Upload timing
- `metadata`: JSON metadata
- `error`: Upload error message
- `ref`: Optional reference field

## Benefits

### For Development Workflows
- **Automatic Processing**: Set it and forget it - uploads happen in the background
- **Persistent Queue**: Pending uploads survive restarts
- **Fast Uploads**: Multipart support for batch operations
- **Easy Testing**: Example JSON file included for quick testing

### For Document Management
- **Space Efficiency**: Deduplication prevents storing identical files multiple times
- **Organization**: Category-based organization with statistics
- **Traceability**: Complete audit trail with timestamps and states
- **Search Ready**: Proper MIME types ensure optimal Gemini search indexing

### For System Integration
- **RESTful API**: Standard HTTP endpoints for easy integration
- **User Isolation**: Multi-user support with user-scoped data
- **Extensible**: JSON metadata fields for custom extensions
- **Observable**: Comprehensive logging and error messages

### For Data Integrity
- **Sync Validation**: Regular sync checks ensure consistency
- **Error Recovery**: Failed uploads can be retried
- **State Tracking**: Always know the current state of your documents
- **Duplicate Detection**: Identify and handle duplicate uploads

## Troubleshooting

### Check Upload Worker Status
The worker logs when it starts and stops. Check your logs for:
```
UploadWorker started
UploadWorker stopped
```

### View Pending Uploads
```bash
curl "http://localhost:8080/ext/gemini/documents?sort=uploading&null=uploadedAt,error"
```

### View Failed Uploads
```bash
curl "http://localhost:8080/ext/gemini/documents?sort=failed&not_null=error"
```

### Retry Failed Upload
```bash
curl -X POST http://localhost:8080/ext/gemini/documents/{id}/upload
```

### Check Sync Status
```bash
curl -X POST http://localhost:8080/ext/gemini/filestores/{id}/sync
```

### Enable Debug Logging
Set the debug flag in your context to see detailed operation logs including:

```bash
DEBUG=1 ./llms.sh --serve 8000 --verbose
```

- Gemini API requests and responses
- Document matching logic
- Metadata updates
- Upload configurations

## Integration with Gemini AI

This extension uses Google's Gemini AI [File Search API](https://ai.google.dev/api/file-search) to:
- Create and manage file search stores
- Upload documents for AI-powered search
- Attach custom metadata for enhanced organization
- Query document status and retrieve results

Once documents are uploaded, they can be referenced in Gemini chat sessions using the `fileSearch` tool, enabling the AI to search through your document collection and provide informed responses based on your content.

### Citations

Every answer grounded in a file search store carries its sources:

- **Inline `[n]` markers** are placed on the sentences they support, using the byte ranges Gemini
  returns in `groundingSupports`. Clicking one opens that document's `sourceUrl` (falling back to
  the cached upload). Markers are added at render time — the stored message stays exactly what the
  model wrote, so copying an answer copies clean text.
- **A numbered Sources list** under each answer, with an expandable excerpt of the retrieved text.

Sources are attached to the **message** that cited them rather than the thread, so scrolling back
through a conversation shows each answer with its own documents. They survive streaming: the
grounding metadata is merged as the response arrives rather than read from a final payload.

A span that can't be placed in the final text — a truncated or cancelled stream, or a response
split across multiple text parts — is dropped rather than guessed at, so the Sources list is still
shown but the inline markers for it are not.

## Authentication

Extension routes are not auth-gated by the server, and an anonymous caller resolves to the shared
(`user IS NULL`) scope. When auth is enabled, the endpoints that mutate state — creating, deleting,
uploading to, re-uploading and syncing — require a signed-in user and return `401` otherwise.
Reads are left open, since the null-user scope is the deliberate "shared with everyone here"
bucket.

## License

This extension is released under the [New BSD License](https://github.com/ServiceStack/llms/blob/main/LICENSE).
