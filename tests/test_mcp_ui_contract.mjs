// Exercise the actual shared ESM UI with lightweight Vue lifecycle/network fixtures.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'

const calls = [], mounted = [], bodies = {}
const channels = []
class BroadcastChannel {
    constructor(name) { assert.equal(name, 'ai-chat-mcp-oauth'); channels.push(this) }
    close() { this.closed = true }
}
let failConnect = false
let connectionState = 'ready'
let configuration = { servers: [], revision: 'initial', canEdit: true }
let currentApproval = { id: 7, source: 'mcp_client', toolCallId: 'call-1', status: 'pending',
    title: 'Work: echo', proposedArgs: { text: 'before' }, schema: { type: 'object' }, sourceMetadata: { serverId: 'work' } }
const definition = name => ({ type: 'function', function: { name } })
const tool = { name: 'mcp_work_echo_hash', remoteName: 'echo', group: 'mcp_work', alwaysApproved: true }
const otherTool = { name: 'mcp_work_search_hash', remoteName: 'search', description: 'Find documents', group: 'mcp_work' }
const headers = {}
const connectionPrefs = {}
const connectionScope = { prefs: connectionPrefs, setPrefs: update => Object.assign(connectionPrefs, update) }
const ctx = { scope: id => { assert.equal(id, 'mcp_client'); return connectionScope }, tools: { setToolPageHeaders: items => Object.assign(headers, items) }, ai: { base: '/chat' }, state: { tool: {} }, prefs: {}, routes: [],
    resolveUrl: url => '/chat' + url,
    setPrefs: update => Object.assign(ctx.prefs, update),
    setUserMenuItems: items => { ctx.menus = items },
    setToolCallBodies: items => Object.assign(bodies, items) }
const Vue = { ref: value => ({ value }), computed: getter => ({ get value() { return getter() } }), inject: () => ctx, onMounted: fn => mounted.push(fn), onUnmounted: () => {}, watch: () => {}, nextTick: fn => fn() }
async function fetch(url, options = {}) {
    calls.push({ url, options })
    let data
    if (url === '/chat/ext/tools') data = { groups: { local: ['calc'], mcp_work: [tool.name] }, definitions: [definition('calc'), definition(tool.name)] }
    else if (url.endsWith('/config.json')) {
        if (options.method === 'POST') configuration = { ...JSON.parse(options.body), revision: 'saved', canEdit: true }
        data = configuration
    }
    else if (url.endsWith('/connections/connect-all')) data = [{id:'work',name:'Work',connected:true}, {id:'other',name:'Other',connected:false,error:'Sign-in required'}]
    else if (url.endsWith('/credentials')) data = { saved: true }
    else if (url.endsWith('/approval-grants/revoke')) data = { revoked: true }
    else if (url.endsWith('/connections')) data = [{ id: 'work', name: 'Work', state: connectionState }]
    else if (url.endsWith('/connections/work/refresh')) { connectionState = 'ready'; data = { id: 'work', name: 'Work', state: 'ready' } }
    else if (url.endsWith('/connect')) { if (failConnect) return { ok: false, json: async () => ({responseStatus:{message:'Sign-in required'}}) }; data = {} }
    else if (url.includes('/connections/') && url.endsWith('/tools')) data = [tool, otherTool]
    else if (url.endsWith('/approvals/1')) data = [currentApproval]
    else if (url.endsWith('/approvals/7/approve')) {
        currentApproval = { ...currentApproval, status: 'completed', effectiveArgs: JSON.parse(options.body).args, result: { content: 'ok' } }
        data = currentApproval
    } else if (url.endsWith('/approvals/7/reconcile')) {
        assert.equal(JSON.parse(options.body).decision, 'continue_without_replay')
        data = currentApproval = { ...currentApproval, status: 'failed' }
    } else throw new Error(`Unexpected URL: ${url}`)
    return { ok: true, json: async () => data }
}
const source = fs.readFileSync(new URL('../llms/extensions/mcp_client/ui/index.mjs', import.meta.url), 'utf8')
const styles = []
const document = { getElementById: id => styles.find(x => x.id === id), createElement: () => ({}), head: { appendChild: x => styles.push(x) } }
const sandbox = { Vue, fetch, URL, JSON, document, BroadcastChannel }
vm.runInNewContext(source.replace("import { ref, computed, inject, onMounted, onUnmounted, watch, nextTick } from 'vue'", 'const {ref, computed, inject, onMounted, onUnmounted, watch, nextTick} = Vue')
    .replace('export default', 'globalThis.extension ='), sandbox)
const extension = sandbox.extension
extension.install(ctx)
await extension.load(ctx)
assert.equal(ctx.routes.length, 0)
assert.equal(ctx.menus, undefined)
assert.ok(headers.mcp_client)
const header = headers.mcp_client.setup()
assert.equal(header.expanded.value, false)
header.toggleExpanded()
assert.equal(connectionPrefs.expanded, true)
assert.equal(headers.mcp_client.setup().expanded.value, true)
header.toggleExpanded()
assert.equal(headers.mcp_client.setup().expanded.value, false)
assert.equal(styles.length, 0)
assert.equal(ctx.resolveUrl('data:image/png;base64,a'), 'data:image/png;base64,a')
assert.equal(ctx.resolveUrl('/file.png'), '/chat/file.png')
assert.equal(Object.keys(bodies).length, 1)
const connections = headers.mcp_client.components.Connections.setup()
await mounted.pop()()
await connections.showTools({ id: 'work' })
connections.select(tool)
assert.deepEqual([...ctx.prefs.onlyTools], ['calc', tool.name])
connections.select(tool)
assert.deepEqual([...ctx.prefs.onlyTools], ['calc'])
assert.equal(connections.expandedTools.value, 'work')
connections.toolSearch.value = 'echo'
connections.toggleFiltered({ id: 'work' })
assert.deepEqual([...ctx.prefs.onlyTools], ['calc', tool.name])
connections.toolSearch.value = 'documents'
connections.toggleFiltered({ id: 'work' })
assert.deepEqual([...ctx.prefs.onlyTools], ['calc', tool.name, otherTool.name])
connections.toolSearch.value = 'echo'
assert.equal(connections.allFilteredSelected({ id: 'work' }), true)
connections.toggleFiltered({ id: 'work' })
assert.deepEqual([...ctx.prefs.onlyTools], ['calc', otherTool.name])
ctx.setPrefs({ onlyTools: ['calc'] })
assert.ok(headers.mcp_client.components.Connections.template.includes('mcp-approved-badge'))
await connections.revokeGrant({id:'work'}, tool)
assert.equal(connections.catalogs.value.work[0].alwaysApproved, false)
connections.toolSearch.value = 'does-not-exist'
assert.equal(connections.filteredTools({id:'work'}).length, 0)
await connections.showTools({id:'work'})
assert.equal(connections.expandedTools.value, null)
connections.edit()
connections.editor.value.displayName = 'Personal'
assert.ok(!headers.mcp_client.components.Connections.template.includes('Advanced settings'))
assert.ok(!headers.mcp_client.components.Connections.template.includes('Allowed tools'))
connections.editor.value.endpoint = 'https://example.com/mcp'

await connections.save()
assert.equal(connections.error.value, '')
assert.equal(configuration.servers[0].id, 'personal')
assert.deepEqual([...configuration.servers[0].allowedTools], ['*'])
configuration.servers[0].allowedTools = []
assert.equal(connections.hasHiddenTools({ id: 'personal', scope: 'personal' }), true)
connections.showAllTools({ id: 'personal', scope: 'personal' })
assert.deepEqual([...connections.editor.value.allowedTools], ['*'])
assert.equal(connections.restoreTools.value, true)
connections.cancelEdit()
configuration.servers[0].allowedTools = ['*']
connections.edit('personal')
connections.editor.value.displayName = 'Updated'
connections.editor.value.allowedTools = ['echo']
await connections.save()
assert.equal(configuration.servers[0].displayName, 'Updated')
assert.deepEqual([...configuration.servers[0].allowedTools], ['echo'])
await connections.remove('personal')
assert.equal(configuration.servers.length, 0)
connections.edit()
connections.editor.value.displayName = 'Requires sign in'
connections.editor.value.endpoint = 'https://example.com/mcp'
failConnect = true
await connections.save()
assert.equal(connections.connectionIssue.value, 'requires_sign_in')
assert.ok(connections.error.value.includes('Connection saved'))
assert.equal(configuration.servers.length, 1)
failConnect = false
connections.edit()
connections.editor.value.displayName = 'GitHub'
connections.editor.value.endpoint = 'https://api.githubcopilot.com/mcp/'
connections.editor.value.auth.mode = 'bearer'
connections.bearerToken.value = 'fixture-pat'
await connections.save()
assert.equal(connections.error.value, '')
assert.equal(connections.bearerToken.value, '')
assert.ok(!JSON.stringify(configuration).includes('fixture-pat'))
assert.equal(JSON.parse(calls.find(x => x.url.endsWith('/github/credentials')).options.body).token, 'fixture-pat')
connections.edit('github')
await connections.save()
assert.equal(calls.filter(x => x.url.endsWith('/github/credentials')).length, 1)
assert.ok(bodies[tool.name].component.template.includes('mcp-approval-json'))
assert.ok(bodies[tool.name].component.template.includes('Tool response'))
assert.ok(bodies[tool.name].component.template.includes('<JsonBlock :text="schemaJson" />'))
assert.ok(bodies[tool.name].component.template.includes('<JsonBlock :text="sentArgsJson" />'))
assert.ok(bodies[tool.name].component.components.JsonBlock.template.includes('dark:bg-slate-900 dark:text-slate-100'))
assert.ok(bodies[tool.name].component.template.includes('approval.effectiveArgs" open'))
assert.ok(!bodies[tool.name].component.components.JsonBlock.template.includes('<pre'))
assert.ok(bodies[tool.name].component.template.includes('Always approve this tool'))
assert.ok(bodies[tool.name].component.template.includes('I checked — continue without replay'))
const approval = bodies[tool.name].component.setup({ thread: { id: 1 }, tool: { id: 'call-1' } })
await mounted.pop()()
assert.equal(approval.statusLabel('pending'), 'Needs approval')
assert.equal(approval.statusLabel('outcome_unknown'), 'Needs review')
approval.args.value = '{"text":"after"}'
await approval.decide('approve', true)
assert.equal(JSON.parse(calls.find(x => x.url.endsWith('/approvals/7/approve')).options.body).alwaysApprove, true)
assert.equal(approval.approval.value.effectiveArgs.text, 'after')
assert.equal(approval.approval.value.status, 'completed')
assert.equal(approval.sentArgsJson.value, '{\n  "text": "after"\n}')
assert.equal(approval.resultJson.value, '{\n  "content": "ok"\n}')
currentApproval = { ...currentApproval, status: 'outcome_unknown' }
await approval.decide('reconcile')
await connections.connectAll()
assert.ok(connections.error.value.includes('1 of 2'))
assert.ok(connections.error.value.includes('Other: Sign-in required'))
assert.equal(connections.busy.value, false)
connections.signIn.value = { id: 'work', url: 'https://example.com/authorize' }
const refreshesBeforeCallback = calls.filter(x => x.url.endsWith('/connections/work/refresh')).length
await channels[0].onmessage({ data: { type: 'authorized', serverId: 'work' } })
assert.equal(connections.signIn.value, null)
assert.equal(connections.expandedTools.value, 'work')
assert.equal(connections.notice.value, 'Work connected. Choose its tools below.')
assert.equal(calls.filter(x => x.url.endsWith('/connections/work/refresh')).length, refreshesBeforeCallback)
connectionState = 'disconnected'
connections.signIn.value = { id: 'work', url: 'https://example.com/authorize' }
await channels[0].onmessage({ data: { type: 'authorized', serverId: 'work' } })
assert.equal(connections.signIn.value, null)
assert.equal(connections.expandedTools.value, 'work')
assert.equal(calls.filter(x => x.url.endsWith('/connections/work/refresh')).length, refreshesBeforeCallback + 1)
const writes = calls.filter(x => x.options.method === 'POST')
assert.equal(writes.length, 17)
for (const call of writes) {
    assert.equal(call.options.credentials, 'same-origin')
    assert.equal(call.options.headers['X-Mcp-Client'], '1')
}
console.log('Shared MCP UI: routes, prefixed URLs, selection, approval edits, and reconciliation passed')
