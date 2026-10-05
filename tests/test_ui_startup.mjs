import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { pathToFileURL } from 'node:url'
import { importExtensions, installExtensions, afterPaint } from '../llms/ui/startup.mjs'

const failures = [], originalError = console.error
console.error = (...args) => failures.push(args)
try {
    const entries = [
        { id: 'late', path: 'late' }, { id: 'failed', path: 'failed' },
        { id: 'early', path: 'early' }, { id: 'invalid', path: 'invalid' },
        { id: 'same-order', path: 'same-order' }, { id: 'no-ui' },
    ]
    const imported = [], installed = []
    const modules = await importExtensions(entries, async path => {
        imported.push(path)
        if (path === 'failed') throw new Error('Network failure')
        if (path === 'invalid') return {}
        return { default: {
            order: path === 'early' ? -100 : 0,
            async install() {
                // A dependent async installer must not begin before its predecessor finishes.
                if (path !== 'early') assert.equal(installed[0], 'early')
                await new Promise(resolve => setTimeout(resolve, 1))
                installed.push(path)
            },
        } }
    })
    assert.equal(imported.length, 5)
    assert.deepEqual(modules.map(x => x.extension.id), ['early', 'late', 'same-order'])
    const ctx = { installedModules: [] }
    await installExtensions(ctx, modules)
    assert.deepEqual(installed, ['early', 'late', 'same-order'])
    assert.equal(ctx.installedModules.length, 3)
    await installExtensions(ctx, [
        { extension: { id: 'bad-install' }, module: { default: { install() { throw Error('Install failure') } } } },
        { extension: { id: 'after-failure' }, module: { default: { install() { installed.push('after-failure') } } } },
    ])
    assert.equal(installed.at(-1), 'after-failure')
    assert.equal(ctx.installedModules.length, 4)
    assert.equal(failures.length, 3)
} finally {
    console.error = originalError
}

let frame
globalThis.requestAnimationFrame = fn => { frame = fn }
let painted = false
const paint = afterPaint().then(() => { painted = true })
await Promise.resolve()
assert.equal(painted, false)
frame()
await paint
assert.equal(painted, true)
// Hidden tabs must also progress even if no animation frame fires.
await afterPaint()

const source = fs.readFileSync('llms/ui/lazy.mjs', 'utf8')
    .replace("from 'vue'", `from '${pathToFileURL(process.cwd() + '/llms/ui/lib/vue.min.mjs')}'`)
const { lazyModule, loadCodeEditor, loadStylesheet } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
let imports = 0, initializations = 0, complete
const load = lazyModule(() => {
    imports++
    return new Promise(resolve => { complete = resolve })
}, () => { initializations++ })
const first = load(), second = load()
assert.equal(first, second)
complete({ page: {} })
assert.equal(await first, await second)
await load()
assert.equal(imports, 1)
assert.equal(initializations, 1)
let retries = 0
const retry = lazyModule(async () => {
    if (++retries === 1) throw Error('Temporary failure')
    return { page: {} }
})
await assert.rejects(retry(), /Temporary failure/)
await retry()
assert.equal(retries, 2)

const elements = []
globalThis.document = {
    createElement: tag => ({ tag, remove() { elements.splice(elements.indexOf(this), 1) } }),
    head: { appendChild: element => { elements.push(element) } },
}
const ctx = { state: { config: { extensions: ['core_tools'] } }, ai: { resolvePath: path => '/chat' + path } }
const editor = loadCodeEditor(ctx), sharedEditor = loadCodeEditor(ctx)
assert.equal(elements.length, 3)
assert.equal(elements.filter(x => x.tag === 'script').length, 1)
assert.ok(elements.every(x => (x.src || x.href).startsWith('/chat/ext/core_tools/')))
elements.slice().forEach(x => x.onload())
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(elements.length, 8)
elements.slice(3).forEach(x => x.onload())
await Promise.all([editor, sharedEditor])
await loadCodeEditor(ctx)
assert.equal(elements.length, 8)
const failedStyle = loadStylesheet('/failed.css')
elements.at(-1).onerror()
await assert.rejects(failedStyle, /Unable to load/)
const retriedStyle = loadStylesheet('/failed.css')
elements.at(-1).onload()
await retriedStyle
await loadCodeEditor({ ...ctx, state: { config: { extensions: [] } } })
assert.equal(elements.length, 9)

// Exercise the actual bootstrap API with small app/router/network fixtures.
let loads = 0, importsStarted = 0, finishLoad
const loading = new Promise(resolve => { finishLoad = resolve })
const customHome = { template: 'Custom home' }, page = { template: 'PDF page' }
const builtin = { install(ctx) { ctx.components({ Home: {} }); ctx.routes.push({ path: '/', component: {} }) } }
class Context {
    constructor({ app, routes, ai }) {
        Object.assign(this, { app, routes, ai, state: { extensions: [{ id: 'fixture', path: 'fixture' }], startupReady: false },
            layout: { path: '/chat/pdf' }, _components: {}, _onRouterBeforeEach: [] })
    }
    async init() {}
    async load() { loads++; await loading }
    components(components) { Object.assign(this._components, components) }
    component(name) { return this._components[name] }
    setState(state) { Object.assign(this.state, state) }
    setLayout(layout) { Object.assign(this.layout, layout) }
}
const sandbox = {
    createApp() {
        const components = {}
        return { use() {}, provide() {}, component(name, component) { if (component) components[name] = component; return components[name] },
            mount() { this.mounted = true } }
    },
    createRouter({ routes }) {
        return { records: [...routes], currentRoute: { value: { fullPath: '/chat/pdf?open=dialog' } },
            beforeEach() {}, addRoute(route) { this.records = this.records.filter(x => !route.name || x.name !== route.name); this.records.push(route) },
            async isReady() {}, async replace(path) { this.replaced = path } }
    },
    createWebHistory() {}, nextTick: async () => {}, useFormatters: () => ({}), utilsFunctions: () => ({}), utilsFormatters: () => ({}),
    ServiceStackVue: {}, App: {}, AppContext: Context, marked: {}, markedFallback: {},
    ModelPicker: { template: 'Model picker' }, CheckBox: { template: 'Checkbox' },
    LayoutModule: builtin, ChatModule: { install() {} }, ModelSelectorModule: { install() {} }, IconsModule: { install() {} },
    ai: { base: '/chat', hasAccess: true, resolvePath: path => '/chat' + path },
    afterPaint: async () => {}, installExtensions,
    importExtensions: extensions => importExtensions(extensions, async () => {
        importsStarted++
        return { default: { install(ctx) { ctx.components({ Home: customHome }); ctx.routes.push({ path: '/pdf', component: page }) } } }
    }),
    location: { pathname: '/chat/pdf', search: '?open=dialog' }, document: {}, performance: { mark() {} },
    console: { ...console, log() {}, debug() {} },
}
const bootstrap = fs.readFileSync('llms/ui/index.mjs', 'utf8')
    .replace(/^import .*$/gm, '').replace('export async function createContext', 'async function createContext')
vm.runInNewContext(bootstrap + '\nglobalThis.createContext = createContext', sandbox)
const deferred = await sandbox.createContext({ deferExtensions: true })
assert.equal(deferred.app.component('ModelPicker'), sandbox.ModelPicker)
assert.equal(deferred.app.component('CheckBox'), sandbox.CheckBox)
assert.equal(importsStarted, 0)
assert.equal(deferred.state.startupReady, false)
deferred.app.mount()
const starting = deferred.start()
assert.equal(starting, deferred.start())
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(loads, 1)
assert.equal(deferred.state.startupReady, false)
finishLoad()
await starting
assert.equal(deferred.state.startupReady, true)
assert.equal(deferred.router.replaced, '/chat/pdf?open=dialog')
assert.ok(deferred.router.records.some(route => route.path === '/chat/pdf' && route.component === page))
assert.equal(deferred.router.records.find(route => route.name === 'llms-fallback').component, customHome)
const compatible = await sandbox.createContext()
assert.equal(compatible.state.startupReady, true)
assert.equal(loads, 2)
sandbox.location = { pathname: '/chat', search: '' }
const mountedRoot = await sandbox.createContext()
assert.equal(mountedRoot.router.replaced, '/chat/pdf')
console.log('Async extension order, failure isolation, paint scheduling, lazy retries and editor dependencies passed')
