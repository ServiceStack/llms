import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

// Mock AppContext
class MockAppContext {
    constructor() {
        this.settings = {}
        this.top = {}
        this.left = {}
        this.leftTop = {}
        this.composerTop = {}
    }

    _validateComponents(componentMap) {
        Object.entries(componentMap).forEach(([id, def]) => {
            def.id = id
            if (typeof def.isActive !== 'function') {
                def.isActive = () => false
            }
        })
        return componentMap
    }

    setSettings(componentMap) {
        Object.assign(this.settings, this._validateComponents(componentMap))
    }

    setLeftIcons(icons) {
        Object.assign(this.left, icons)
    }

    setTopIcons(icons) {
        Object.assign(this.top, icons)
    }

    visibleComponents(componentsMap, context) {
        const to = {}
        Object.entries(componentsMap).forEach(([name, def]) => {
            if (typeof def.isVisible === 'function' && !def.isVisible(context)) {
                return
            }
            to[name] = def
        })
        return to
    }
}

// Mock Vue
let injectedCtx = null
const Vue = {
    ref: val => ({ value: val }),
    computed: fn => ({ get value() { return fn() } }),
    inject: () => injectedCtx || ({
        scope: id => ({
            getJson: async () => ({ connected: false }),
            postJson: async () => ({ success: true }),
        }),
        toast: () => {},
    }),
    onMounted: fn => fn(),
    onUnmounted: () => {},
    watch: () => {},
}

const settingsSource = fs.readFileSync(new URL('../llms/extensions/openai_auth/ui/OpenAiSettings.mjs', import.meta.url), 'utf8')
    .replace(/import\s*\{[^}]*\}\s*from\s*['"]vue['"]/, 'const { ref, onMounted, onUnmounted, inject } = Vue')
    .replace('export default', 'globalThis.OpenAiSettings =')

const indexSource = fs.readFileSync(new URL('../llms/extensions/openai_auth/ui/index.mjs', import.meta.url), 'utf8')
    .replace(/import\s+OpenAiSettings\s+from\s+['"][^'"]+['"]/, '')
    .replace('export default', 'globalThis.extension =')

const sandbox = { Vue, URL, JSON, console, window: { location: { href: 'https://app.test/chat/42' } } }
vm.runInNewContext(settingsSource, sandbox)
vm.runInNewContext(indexSource, sandbox)

const extension = sandbox.extension
const mockCtx = new MockAppContext()
extension.install(mockCtx)

// 1. Verify that openai_subscription is registered in ctx.settings
assert.ok(mockCtx.settings.openai_subscription, 'openai_subscription must be registered in ctx.settings')
assert.ok(mockCtx.settings.openai_subscription.component, 'openai_subscription must have a component')
assert.equal(mockCtx.settings.openai_subscription.component, sandbox.OpenAiSettings)

// 2. Verify that visibleComponents includes openai_subscription
const visible = mockCtx.visibleComponents(mockCtx.settings)
assert.ok(visible.openai_subscription, 'openai_subscription must be visible in settings')

// 3. Verify that NO icons were registered in left or top navigation bars
assert.equal(Object.keys(mockCtx.left).length, 0, 'No left icons should be registered by openai_auth')
assert.equal(Object.keys(mockCtx.top).length, 0, 'No top icons should be registered by openai_auth')
assert.equal(Object.keys(mockCtx.leftTop).length, 0, 'No leftTop controls should be registered by openai_auth')

// 4. Verify OpenAiSettings setup lifecycle executes cleanly
const setupResult = sandbox.OpenAiSettings.setup()
assert.ok(setupResult.status)
assert.equal(typeof setupResult.connect, 'function')
assert.equal(typeof setupResult.disconnect, 'function')
assert.equal(typeof setupResult.submitManual, 'function')
assert.equal(typeof setupResult.copySigninLink, 'function')
assert.equal(typeof setupResult.importCodex, 'function')

console.log('✓ openai_auth UI registration and component contract passed')

// Exercise the manual-flow capability returned by both backends, without timers.
let serverStatus = { connected: false, plan_enabled: false, pending: false }
const notices = []
const uiCtx = {
    scope: () => ({
        getJson: async () => serverStatus,
        postJson: async path => path === '/connect'
            ? { auth_url: 'https://auth.openai.com/api/accounts/authorize?state=fixture', manual_callback: true }
            : path === '/disconnect'
                ? { success: true, remote_revocation_confirmed: false }
                : { success: true },
    }),
    toast: message => notices.push(message),
}
injectedCtx = uiCtx
sandbox.setInterval = () => 1
sandbox.clearInterval = () => {}
sandbox.confirm = () => true
sandbox.alert = message => { throw new Error(message) }
const ui = sandbox.OpenAiSettings.setup()
await Promise.resolve()
await ui.connect(false)
assert.equal(ui.showManual.value, true, 'Manual callback entry opens when advertised by the server')
assert.equal(ui.connecting.value, true)
serverStatus = { connected: true, plan_enabled: false, pending: true }
await ui.disconnect()
assert.ok(notices.some(message => message.includes('Remote revocation was not confirmed')))
ui.manualInput.value = 'http://127.0.0.1:1455/auth/callback?code=fixture&state=fixture&client_id=oaiapp_fixture'
await ui.submitManual()
assert.equal(ui.manualInput.value, '')
assert.equal(ui.showManual.value, false)
assert.equal(ui.status.value.plan_enabled, false, 'A declined plan remains visibly disabled')
console.log('✓ full callback entry, declined plan, and revocation notice passed')

let poll, stopped = false
sandbox.setInterval = callback => { poll = callback; stopped = false; return 1 }
sandbox.clearInterval = () => { stopped = true }
uiCtx.scope = () => ({
    getJson: async () => serverStatus,
    postJson: async (path, body) => {
        assert.equal(path, '/connect')
        assert.equal(body.return_url, sandbox.window.location.href)
        return { auth_url: 'https://auth.openai.com/authorize', automatic_callback: true, manual_callback: false }
    },
})
serverStatus = { connected: true, pending: false, automatic_callback: true, manual_callback: false }
const automatic = sandbox.OpenAiSettings.setup()
await Promise.resolve()
await automatic.connect(false)
assert.equal(automatic.showManual.value, false)
assert.equal(automatic.authUrl.value, 'https://auth.openai.com/authorize', 'The active sign-in link is available to the template')
serverStatus = { connected: true, pending: true }
await poll()
assert.equal(stopped, false, 'An existing grant must not stop polling during reauthorization')
serverStatus = { connected: true, pending: false }
await poll()
assert.equal(stopped, true)
assert.equal(automatic.connecting.value, false)
await automatic.connect(false)
serverStatus = { connected: false, pending: false, callback_error: 'OpenAI sign-in was not authorized.' }
await poll()
assert.equal(stopped, true)
assert.equal(automatic.status.value.callback_error, serverStatus.callback_error)
console.log('✓ automatic callback, reauthorization polling, and callback errors passed')
