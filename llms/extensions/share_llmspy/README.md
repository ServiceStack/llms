# llmspy Sharing Extension

Share your conversations, projects, and media with the world — straight from your workspace.

The **share_llmspy** extension publishes chat threads, projects and media to
[ai.llmspy.org](https://ai.llmspy.org). It registers the **ai.llmspy.org** option in the core Share
panel and runs independently of [static folder sharing](../share_static/README.md).
Disable it by adding `"share_llmspy"` to `disable_extensions` in your llms configuration.
No sharing icon appears when no sharing extensions register an option.

---

## Getting Started

### 1. Open the Share Panel

Click the **Share** icon in the top toolbar to open the publishing panel.

<!-- screenshot: share-panel-icon -->

### 2. Register & Connect

Select **ai.llmspy.org** to use remote publishing. If you haven't connected an account,
you'll see a registration form alongside
a short list of benefits:

- ✅ Generate beautiful public read-only links
- ✅ Retain full formatting of code, tables, and prompts
- ✅ Revoke links or disconnect your account at any time

Fill in your details in the embedded form. Registration is **free** and takes less than a minute.
Once complete, your API key is saved automatically and you'll see a green **Connected** badge.

<!-- screenshot: registration-form -->

### 3. Manage Your Connection

Once connected, a **Connected: @username** button appears in the top-right corner of the Share
panel. Click it to view your API key, user info, or to **Disconnect** your account entirely.

<!-- screenshot: connected-account-menu -->

---

## Publishing Chat Threads

Share an entire conversation — including code blocks, tables, and rich formatting — as a
permanent, styled web page.

### How to Publish

1. Open the **Share** panel while you have an active conversation.
2. Select **ai.llmspy.org**, connect an account if needed, then select **Publish Chat Thread**.
3. You'll see the thread title and model displayed in a summary card.
4. Click **Publish Thread** (or **Update Thread** if previously published).
5. A public URL is generated and displayed. Click the link to open it, or click the copy icon
   to grab the URL.

<!-- screenshot: publish-thread-tab -->

### Re-publishing

Already-published threads show a green **Published** status indicator with the timestamp.
Hit **Update Thread** to push any new messages or edits to the same URL.

### What Gets Published

- All user and assistant messages
- Code blocks with syntax highlighting
- Tables, lists, and formatted markdown
- User and assistant avatars
- Referenced images and media cached locally (uploaded automatically)

---

## Publishing Projects

Publish a project build to the public host. Static folder exports are provided independently by
`share_static`; they do not require a publisher account.

### Publish to ai.llmspy.org

1. Select the **ai.llmspy.org** tab and connect a publisher account if needed.
2. Select **Publish Project**, choose the output directory and click **Publish Project**.
3. Your project is packaged as a tarball, uploaded, and a live URL is returned.

Folder and remote publication status are stored separately, so publishing to either destination
preserves the other destination's result.

<!-- screenshot: publish-project-tab -->

### Build Directory Detection

The extension looks for a `dist/` subfolder inside your project's configured paths. If it
finds one, it pre-fills the path automatically. You can override this at any time by editing
the text field or using the folder browser dialog.

<!-- screenshot: folder-browser-dialog -->

### Published Projects Gallery

Here are some example projects that have been published using this extension:

| Project | Description | Link |
|---------|-------------|------|
| 🎮 **Breakout** | Stunning sci-fi breakout game | [Play Breakout](https://ai.llmspy.org/p/llmspy/Breakout) |
| 👾 **Pac Man** | Stunning Sci-Fi Pacman | [Play Pac Man](https://ai.llmspy.org/p/llmspy/Pac_Man) |
| ⚙️ **Pinball** | Stunning Steampunk Workshop themed Pinball | [Play Pinball](https://ai.llmspy.org/p/llmspy/Pinball) |
| 🏓 **Pong** | A stunning pong Web App | [Play Pong](https://ai.llmspy.org/p/llmspy/Pong) |
| 🚀 **Galaga** | Stunning sci-fi Galaga game in React | [Play Galaga](https://ai.llmspy.org/p/llmspy/Galaga) |
| ☄️ **Asteroids** | The classic Asteroids game | [Play Asteroids](https://ai.llmspy.org/p/llmspy/Asteroids) |

---

## Publishing Images

Share AI-generated images directly from the media gallery lightbox.

### How to Publish

1. Open the **Gallery** and click on an image to open the lightbox.
2. At the bottom of the lightbox, a **Share Image** button appears (only visible when your
   publisher account is connected).
3. Click **Share Image** — the image and its metadata are uploaded.
4. Once published, the button is replaced with a green **Published** indicator and a link to
   the public page.

<!-- screenshot: gallery-publish-button -->

### What Gets Uploaded

- The full-resolution image file
- Sidecar metadata (model, prompt, dimensions, generation settings)
- Media type information

---

## Publishing Audio

Share generated audio clips from the audio player.

### How to Publish

1. Locate an audio item in the audio player or media list.
2. Click the **share** pill button next to the audio controls.
3. A brief **publishing...** spinner appears while the file uploads.
4. Once complete, the button transforms into a green **open link** pill that links to the
   public audio page.

<!-- screenshot: audio-publish-button -->

---

## Quick Reference

| Content Type | Where to Find It | Action |
|---|---|---|
| **Chat Thread** | Share panel → *ai.llmspy.org* → *Publish Chat Thread* | Publish / Update Thread |
| **Remote project** | Share panel → *ai.llmspy.org* → *Publish Project* | Publish Project |
| **Image** | Gallery lightbox footer | Share Image |
| **Audio** | Audio player actions | share |

## Disconnecting

To unlink your publisher account:

1. Open the **Share** panel.
2. Click **Connected: @username** in the top-right.
3. Click **Disconnect** in the dropdown.

Your API key will be removed locally. Previously published content remains accessible at its
public URL until you remove it from the hosting platform.

Account configuration is stored under `user/<username>/share_llmspy/config.json`. Existing
`publish/config.json` account files remain readable, migrate on the next save, and are removed
on disconnect so an old grant cannot be restored. Named users inherit only public origin defaults,
never the default account's credentials.
