
import { reactive } from 'vue'
import { EventBus, humanize, combinePaths } from "@servicestack/client"
import { storageObject } from './utils.mjs'

export class ExtensionScope {
    constructor(ctx, id) {
        /**@type {AppContext} */
        this.ctx = ctx
        this.id = id
        this.baseUrl = `${ctx.ai.base}/ext/${this.id}`
        this.storageKey = `llms.${this.id}`
        this.state = reactive({})
        this.prefs = reactive(storageObject(this.storageKey))
    }
    getPrefs() {
        return this.prefs
    }
    setPrefs(o) {
        storageObject(this.storageKey, Object.assign(this.prefs, o))
    }
    get(url, options) {
        return this.ctx.ai.get(combinePaths(this.baseUrl, url), options)
    }
    async getJson(url, options) {
        return this.ctx.ai.getJson(combinePaths(this.baseUrl, url), options)
    }
    post(url, options) {
        return this.ctx.ai.post(combinePaths(this.baseUrl, url), options)
    }
    async postForm(url, options) {
        return await this.ctx.ai.postForm(combinePaths(this.baseUrl, url), options)
    }
    async postJson(url, options) {
        return this.ctx.ai.postJson(combinePaths(this.baseUrl, url), options)
    }
}

export class AppContext {
    constructor({ app, routes, ai, fmt, utils }) {
        this.app = app
        this.routes = routes
        this.ai = ai
        this.fmt = fmt
        this.utils = utils
        this._components = {}

        this.state = reactive({})
        this.events = new EventBus()
        this.modalComponents = {}
        this.extensions = []
        this.chatRequestFilters = []
        this.chatResponseFilters = []
        this.chatErrorFilters = []
        this.createThreadFilters = []
        this.updateThreadFilters = []
        this.top = {}
        this.left = {}
        this.layout = reactive(storageObject(`llms.layout`))
        this.prefs = reactive(storageObject(ai.prefsKey))
        this._onRouterBeforeEach = []

        if (!Array.isArray(this.layout.hide)) {
            this.layout.hide = []
        }
        Object.assign(app.config.globalProperties, {
            $ctx: this,
            $prefs: this.prefs,
            $state: this.state,
            $layout: this.layout,
            $ai: ai,
            $fmt: fmt,
            $utils: utils,
        })
        Object.keys(app.config.globalProperties).forEach(key => {
            globalThis[key] = app.config.globalProperties[key]
        })
        document.addEventListener('keydown', (e) => this.handleKeydown(e))
    }
    async init() {
        Object.assign(this.state, await this.ai.init(this))
    }
    setGlobals(globals) {
        Object.entries(globals).forEach(([name, global]) => {
            const globalName = '$' + name
            globalThis[globalName] = this.app.config.globalProperties[globalName] = global
            this[name] = global
        })
    }
    getPrefs() {
        return this.prefs
    }
    setPrefs(o) {
        storageObject(this.ai.prefsKey, Object.assign(this.prefs, o))
    }
    _validateIcons(icons) {
        Object.entries(icons).forEach(([id, icon]) => {
            if (!icon.component) {
                console.error(`Icon ${id} is missing component property`)
            }
            icon.id = id
            if (!icon.name) {
                icon.name = humanize(id)
            }
            if (typeof icon.isActive != 'function') {
                icon.isActive = () => false
            }
        })
        return icons
    }
    setTopIcons(icons) {
        Object.assign(this.top, this._validateIcons(icons))
    }
    setLeftIcons(icons) {
        Object.assign(this.left, this._validateIcons(icons))
    }
    component(name, component) {
        if (!name) return name
        if (component) {
            this._components[name] = component
        }
        return component || this._components[name] || this.app.component(name)
    }
    components(components) {
        if (components) {
            Object.keys(components).forEach(name => {
                this._components[name] = components[name]
            })
        }
        return this._components
    }
    scope(extension) {
        return new ExtensionScope(this, extension)
    }
    modals(modals) {
        Object.keys(modals).forEach(name => {
            this.modalComponents[name] = modals[name]
            this.component(name, modals[name])
        })
    }
    openModal(name) {
        const component = this.modalComponents[name]
        if (!component) {
            console.error(`Modal ${name} not found`)
            return
        }
        console.debug('openModal', name)
        this.router.push({ query: { open: name } })
        this.events.publish('modal:open', name)
        return component
    }
    closeModal(name) {
        console.debug('closeModal', name)
        this.router.push({ query: { open: undefined } })
        this.events.publish('modal:close', name)
    }
    handleKeydown(e) {
        if (e.key === 'Escape') {
            const modal = this.router.currentRoute.value?.query?.open
            if (modal) {
                this.closeModal(modal)
            }
            this.events.publish(`keydown:Escape`, e)
        }
    }
    setState(o) {
        Object.assign(this.state, o)
        //this.events.publish('update:state', this.state)
    }
    setLayout(o) {
        Object.assign(this.layout, o)
        storageObject(`llms.layout`, this.layout)
    }
    toggleLayout(key, toggle = undefined) {
        const hide = toggle == undefined
            ? !this.layout.hide.includes(key)
            : !toggle
        console.log('toggleLayout', key, hide)
        if (hide) {
            this.layout.hide.push(key)
        } else {
            this.layout.hide = this.layout.hide.filter(k => k != key)
        }
        storageObject(`llms.layout`, this.layout)
    }
    layoutVisible(key) {
        return !this.layout.hide.includes(key)
    }
    toggleTop(name, toggle) {
        if (toggle === false) {
            this.layout.top = undefined
        } else if (toggle === true) {
            this.layout.top = name
        } else {
            this.layout.top = this.layout.top == name ? undefined : name
        }
        storageObject(`llms.layout`, this.layout)
        console.log('toggleTop', name, toggle, this.layout.top, this.layout.top === name)
        return this.layout.top === name
    }
    togglePath(path, toggle) {
        const currentPath = this.router.currentRoute.value?.path
        console.log('togglePath', path, currentPath, toggle)
        if (currentPath != path) {
            if (toggle === undefined) {
                toggle = true
            }
            this.router.push({ path })
        }
        this.toggleLayout('left', toggle)
        return toggle
    }
    async getJson(url, options) {
        return await this.ai.getJson(url, options)
    }
    async post(url, options) {
        return await this.ai.post(url, options)
    }
    async postForm(url, options) {
        return await this.ai.postForm(url, options)
    }
    async postJson(url, options) {
        return await this.ai.postJson(url, options)
    }
    to(route) {
        if (typeof route == 'string') {
            route = route.startsWith(this.ai.base)
                ? route
                : combinePaths(this.ai.base, route)
            const path = { path: route }
            console.log('to', path)
            this.router.push(path)
        } else {
            route.path = route.path.startsWith(this.ai.base)
                ? route.path
                : combinePaths(this.ai.base, route.path)
            console.log('to', route)
            this.router.push(route)
        }
    }

    // Events
    onRouterBeforeEach(callback) {
        this._onRouterBeforeEach.push(callback)
    }
}