
import { createApp, reactive, defineAsyncComponent } from 'vue'
import { createWebHistory, createRouter } from "vue-router"
import { EventBus, humanize } from "@servicestack/client"
import ServiceStackVue from "@servicestack/vue"
import App from '/ui/App.mjs'
import ai from '/ui/ai.mjs'
import threadStore, { useThreadStore } from './threadStore.mjs'
import SettingsDialog from '/ui/SettingsDialog.mjs'
import ModelSelectorInstaller from '/ui/model-selector.mjs'
import { storageObject } from './utils.mjs'

const { config, models } = await ai.init()
const MainComponent = defineAsyncComponent(() => import(ai.base + '/ui/Main.mjs'))
const RecentsComponent = defineAsyncComponent(() => import(ai.base + '/ui/Recents.mjs'))
const AnalyticsComponent = defineAsyncComponent(() => import(ai.base + '/ui/Analytics.mjs'))

const Components = {
    SettingsDialog,
}

const BuiltInModules = [
    ModelSelectorInstaller,
    threadStore,
]

const routes = [
    { path: '/', component: MainComponent },
    { path: '/c/:id', component: MainComponent },
    { path: '/recents', component: RecentsComponent },
    { path: '/analytics', component: AnalyticsComponent },
    { path: '/:fallback(.*)*', component: MainComponent }
]
routes.forEach(r => r.path = ai.base + r.path)
const router = createRouter({
    history: createWebHistory(),
    routes,
})


class AppExtension {
    constructor(ctx, ext) {
        this.ctx = ctx
        Object.assign(this, ext)
        this.baseUrl = `${ctx.ai.base}/ext/${this.id}`
        this.storageKey = `llms.${this.id}`
        if (!this.name) {
            this.name = humanize(this.id)
        }
    }
    storageObject(o) {
        return storageObject(this.storageKey, o)
    }
}

class AppContext {
    constructor({ app, config, models, extensions, routes, ai, router, threadStore, modules }) {
        this.app = app
        this.state = reactive({
            config,
            models,
            extensions,
        })
        this.routes = routes
        this.ai = ai
        this.router = router
        this.threadStore = threadStore
        this.modules = modules
        this.events = new EventBus()
        this.modalComponents = {}
        this.extensions = []
        this.layout = reactive(storageObject(`llms.layout`))
        this.chatRequestFilters = []
        this.chatResponseFilters = []
        this.chatErrorFilters = []
        this.createThreadFilters = []
        this.updateThreadFilters = []

        app.config.globalProperties.$ctx = this
        app.config.globalProperties.$state = this.state
        app.config.globalProperties.$layout = this.layout
        document.addEventListener('keydown', (e) => this.handleKeydown(e))
    }
    component(name, component) {
        if (!name) return name
        if (component) {
            this.app.component(name, component)
        }
        return component || this.app.component(name)
    }
    components(components) {
        Object.keys(components).forEach(name => {
            this.app.component(name, components[name])
        })
    }
    extension(extension) {
        const ext = new AppExtension(this, extension)
        this.extensions.push(ext)
        return ext
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
    getPrefs() {
        return storageObject(this.ai.prefsKey)
    }
    setPrefs(o) {
        storageObject(this.ai.prefsKey, o)
    }
    toggleLayout(o) {
        Object.keys(o).forEach(key => {
            this.layout[key] = this.layout[key] == o[key] ? undefined : o[key]
        })
        storageObject(`llms.layout`, this.layout)
    }
    getCurrentThread() {
        return this.threadStore.currentThread.value
    }
}

export async function createContext() {
    const app = createApp(App, { config, models })

    app.use(router)
    app.use(ServiceStackVue)
    Object.keys(Components).forEach(name => {
        app.component(name, Components[name])
    })


    window.ai = app.config.globalProperties.$ai = ai

    // Load Extensions
    const exts = await (await fetch("/ext")).json()

    // Load modules in parallel
    const validExtensions = exts.filter(x => x.path);
    const modules = await Promise.all(validExtensions.map(async extension => {
        try {
            const module = await import(extension.path)
            return { extension, module }
        } catch (e) {
            console.error(`Failed to load extension module ${extension.name}:`, e)
            return null
        }
    }))

    const threadStore = useThreadStore()

    const ctx = new AppContext({
        app,
        config,
        models,
        routes,
        ai,
        router,
        threadStore,
        exts,
        modules,
    })
    app.provide('ctx', ctx)

    BuiltInModules.forEach(ext => ext.install(ctx))

    // Install sequentially
    for (const result of modules) {
        if (result && result.module.default && result.module.default.install) {
            try {
                result.module.default.install(ctx)
                console.log(`Installed extension: ${result.extension.id}`)
            } catch (e) {
                console.error(`Failed to install extension ${result.extension.id}:`, e)
            }
        }
    }

    return ctx
}
