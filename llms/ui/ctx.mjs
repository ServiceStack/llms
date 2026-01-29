
import { reactive, markRaw } from 'vue'
import { EventBus, humanize, combinePaths } from "@servicestack/client"
import { storageObject, isHtml, sanitizeHtml } from './utils.mjs'

export class ExtensionScope {
    constructor(ctx, id) {
        /**@type {AppContext} */
        this.ctx = ctx
        this.router = ctx.router
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
    savePrefs() {
        storageObject(this.storageKey, this.prefs)
    }
    setState(o) {
        Object.assign(this.state, o)
    }
    get(url, options) {
        return this.ctx.ai.get(combinePaths(this.baseUrl, url), options)
    }
    delete(url, options) {
        this.ctx.clearError()
        return this.ctx.ai.get(combinePaths(this.baseUrl, url), {
            ...options,
            method: 'DELETE'
        })
    }
    async getJson(url, options) {
        return this.ctx.ai.getJson(combinePaths(this.baseUrl, url), options)
    }
    async deleteJson(url, options) {
        this.ctx.clearError()
        return this.ctx.ai.getJson(combinePaths(this.baseUrl, url), {
            ...options,
            method: 'DELETE'
        })
    }
    post(url, options) {
        this.ctx.clearError()
        return this.ctx.ai.post(combinePaths(this.baseUrl, url), options)
    }
    put(url, options) {
        this.ctx.clearError()
        return this.ctx.ai.post(combinePaths(this.baseUrl, url), {
            ...options,
            method: 'PUT'
        })
    }
    patch(url, options) {
        this.ctx.clearError()
        return this.ctx.ai.post(combinePaths(this.baseUrl, url), {
            ...options,
            method: 'PATCH'
        })
    }
    async postForm(url, options) {
        this.ctx.clearError()
        return await this.ctx.ai.postForm(combinePaths(this.baseUrl, url), options)
    }
    async postJson(url, body) {
        this.ctx.clearError()
        return this.ctx.ai.postJson(combinePaths(this.baseUrl, url), {
            body: body instanceof FormData ? body : JSON.stringify(body)
        })
    }
    async putJson(url, body) {
        this.ctx.clearError()
        return this.ctx.ai.postJson(combinePaths(this.baseUrl, url), {
            method: 'PUT',
            body: body instanceof FormData ? body : JSON.stringify(body)
        })
    }
    async patchJson(url, body) {
        this.ctx.clearError()
        return this.ctx.ai.postJson(combinePaths(this.baseUrl, url), {
            method: 'PATCH',
            body: body instanceof FormData ? body : JSON.stringify(body)
        })
    }
    async createJsonResult(res) {
        return this.ctx.ai.createJsonResult(res)
    }
    createErrorStatus(status) {
        return this.ctx.ai.createErrorStatus(status)
    }
    createErrorResult(e) {
        return this.ctx.ai.createErrorResult(e)
    }
    setError(e, msg = null) {
        const prefix = this.id ? `[${this.id}] ` : ''
        this.ctx.setError(e, msg ? `${prefix} ${msg}` : prefix)
    }
    clearError() {
        this.ctx.clearError()
    }
    toast(msg) {
        this.ctx.toast(msg)
    }
    to(route) {
        if (typeof route == 'string') {
            route = route.startsWith(this.baseUrl)
                ? route
                : combinePaths(this.baseUrl, route)
            const path = { path: route }
            console.log(`to/${this.id}`, path)
            this.router.push(path)
        } else {
            route.path = route.path.startsWith(this.baseUrl)
                ? route.path
                : combinePaths(this.baseUrl, route.path)
            console.log(`to/${this.id}`, route)
            this.router.push(route)
        }
    }
}

export class AppContext {
    constructor({ app, routes, ai, fmt, utils, marked, markedFallback }) {
        this.app = app
        this.routes = routes
        this.ai = ai
        this.fmt = fmt
        this.utils = utils
        this._components = {}
        this.marked = marked
        this.markedFallback = markedFallback

        this.state = reactive({})
        this.events = new EventBus()
        this.modalComponents = {}
        this.extensions = []
        this.markedFilters = []
        this.chatRequestFilters = []
        this.chatResponseFilters = []
        this.chatErrorFilters = []
        this.createThreadFilters = []
        this.updateThreadFilters = []
        this.threadHeaderComponents = {}
        this.threadFooterComponents = {}
        this.top = {}
        this.left = {}
        this.layout = reactive(storageObject(`llms.layout`))
        this.prefs = reactive(storageObject(ai.prefsKey))
        this._onRouterBeforeEach = []
        this._onClass = []

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
        Object.assign(this.fmt, {
            markdown: this.renderMarkdown.bind(this),
            content: this.renderContent.bind(this),
        })
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
            const modal = markRaw(modals[name])
            this.modalComponents[name] = modal
            this.component(name, modal)
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
    togglePath(path, { left = true } = {}) {
        const currentPath = this.router.currentRoute.value?.path
        console.log('togglePath', path, currentPath, left)
        if (currentPath != path) {
            this.router.push({ path })
        }
        if (left !== undefined) {
            this.toggleLayout('left', left)
        }
        return left
    }
    setThreadHeaders(components) {
        Object.assign(this.threadHeaderComponents, components)
    }
    setThreadFooters(components) {
        Object.assign(this.threadFooterComponents, components)
    }

    createErrorStatus(status) {
        return this.ai.createErrorStatus(status)
    }
    createErrorResult(e) {
        return this.ai.createErrorResult(e)
    }
    setError(error, msg = null) {
        this.state.error = error
        if (error) {
            if (msg) {
                console.error(error.message, msg, error)
            } else {
                console.error(error.message, error)
            }
        }
    }
    clearError() {
        this.state.error = null
    }

    resolveUrl(url) {
        return this.ai.resolveUrl(url)
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

    onClass(callback) {
        this._onClass.push(callback)
    }

    cls(id, cls) {
        if (this._onClass.length) {
            this._onClass.forEach(callback => {
                cls = callback(id, cls) ?? cls
            })
        }
        return cls
    }
    toast(msg) {
        this.setState({ toast: msg })
    }

    renderMarkdown(content) {
        if (Array.isArray(content)) {
            content = content.filter(c => c.type === 'text').map(c => c.text).join('\n')
        }
        if (content && content.startsWith('---')) {
            const headerEnd = content.indexOf('---', 3)
            const header = content.substring(3, headerEnd).trim()
            content = '<div class="frontmatter">' + header + '</div>\n' + content.substring(headerEnd + 3)
        }
        let html = content || ''
        try {
            html = this.marked.parse(content || '')
        } catch (e) {
            console.log('Failed to parse markdown, using fallback', e)
            try {
                html = this.markedFallback.parse(content || '')
            } catch (e2) {
                console.log('Failed to parse markdown, using raw content', e2)
                html = content || ''
            }
        }
        return sanitizeHtml(html)
    }

    renderContent(content) {
        // Check for HTML tags to detect HTML content
        if (isHtml(content)) {
            // If this is HTML content, return it in an iframe so it doesn't break the page
            return `<iframe src="data:text/html;charset=utf-8,${encodeURIComponent(content)}"></iframe>`
        }
        return this.renderMarkdown(content)
    }

    createChatContext({ request, thread, context }) {
        if (!request.messages) request.messages = []
        if (!request.metadata) request.metadata = {}
        if (!context) context = {}
        Object.assign(context, {
            systemPrompt: '',
            requiredSystemPrompts: [],
        }, context)
        return {
            request,
            thread,
            context,
        }
    }

    completeChatContext({ request, thread, context }) {

        let existingSystemPrompt = request.messages.find(m => m.role === 'system')?.content

        let existingMessages = request.messages.filter(m => m.role == 'assistant' || m.role == 'tool')
        if (existingMessages.length) {
            const messageTypes = {}
            request.messages.forEach(m => {
                messageTypes[m.role] = (messageTypes[m.role] || 0) + 1
            })
            const summary = JSON.stringify(messageTypes).replace(/"/g, '')
            console.debug(`completeChatContext(${summary})`, request)
            return
        }

        let newSystemPrompts = context.requiredSystemPrompts ?? []
        if (context.systemPrompt) {
            newSystemPrompts.push(context.systemPrompt)
        }
        if (existingSystemPrompt) {
            newSystemPrompts.push(existingSystemPrompt)
        }

        let newSystemPrompt = newSystemPrompts.join('\n\n')
        if (newSystemPrompt) {
            // add or replace system prompt
            request.messages = request.messages.filter(m => m.role !== 'system')
            request.messages.unshift({ role: 'system', content: newSystemPrompt })
        }

        console.debug(`completeChatContext()`, request)
    }
}
