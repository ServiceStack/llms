import { reactive } from "vue"

const base = ''
const headers = { 'Accept': 'application/json' }
const prefsKey = 'llms.prefs'

export const o = {
    version: '3.0.0b6',
    base,
    prefsKey,
    welcome: 'Welcome to llms.py',
    auth: null,
    requiresAuth: false,
    authType: 'apikey',  // 'oauth' or 'apikey' - controls which SignIn component to use
    headers,
    isSidebarOpen: true,  // Shared sidebar state (default open for lg+ screens)
    cacheUrlInfo: {},

    get hasAccess() {
        return !this.requiresAuth || this.auth
    },

    resolveUrl(url) {
        return url.startsWith('http') || url.startsWith('/v1') ? url : base + url
    },
    get(url, options) {
        return fetch(this.resolveUrl(url), {
            ...options,
            headers: Object.assign({}, this.headers, options?.headers),
        })
    },
    async getJson(url, options) {
        const res = await this.get(url, options)
        let txt = ''
        try {
            txt = await res.text()
            return JSON.parse(txt)
        } catch (e) {
            console.error('Failed to parse JSON from GET', url, e, txt)
            return { responseStatus: { errorCode: 'Error', message: `GET failed: ${e.message ?? e}` } }
        }
    },
    async post(url, options) {
        return await fetch(this.resolveUrl(url), {
            method: 'POST',
            ...options,
            headers: Object.assign({ 'Content-Type': 'application/json' }, this.headers, options?.headers),
        })
    },
    async postForm(url, options) {
        return await fetch(this.resolveUrl(url), {
            method: 'POST',
            ...options,
            headers: Object.assign({}, options?.headers),
        })
    },
    async postJson(url, options) {
        const res = await this.post(url, options)
        let txt = ''
        try {
            txt = await res.text()
            return JSON.parse(txt)
        } catch (e) {
            console.error('Failed to parse JSON from POST', url, e, txt)
            return { responseStatus: { errorCode: 'Error', message: `POST failed: ${e.message ?? e}` } }
        }
    },

    async getConfig() {
        return this.get('/config')
    },
    async getModels() {
        return this.get('/models')
    },
    async getAuth() {
        return this.requiresAuth
            ? this.get('/auth')
            : new Promise(resolve => resolve({ json: () => ({ responseStatus: { errorCode: '!requiresAuth' } }) }))
    },
    get isAdmin() {
        return !this.requiresAuth || this.auth && this.auth.roles?.includes('Admin')
    },

    signIn(auth) {
        this.auth = auth
        if (auth?.apiKey) {
            this.headers.Authorization = `Bearer ${auth.apiKey}`
        } else {
            if (this.headers.Authorization) {
                delete this.headers.Authorization
            }
        }
    },
    async signOut() {
        try {
            await this.post('/auth/logout')
        } catch (error) {
            console.error('Logout error:', error)
        }
        this.auth = null
        if (this.headers.Authorization) {
            delete this.headers.Authorization
        }
    },
    async init(ctx) {
        // Load models and prompts
        const [configRes, modelsRes, extensionsRes] = await Promise.all([
            this.getConfig(),
            this.getModels(),
            this.get('/ext'),
        ])
        const config = await configRes.json()
        const models = await modelsRes.json()
        const extensions = await extensionsRes.json()

        // Update auth settings from server config
        if (config.requiresAuth != null) {
            this.requiresAuth = config.requiresAuth
        }
        if (config.authType != null) {
            this.authType = config.authType
        }

        // Get auth status
        const authRes = await this.getAuth()
        const auth = this.requiresAuth
            ? await authRes.json()
            : null
        if (auth?.responseStatus?.errorCode) {
            console.error(auth.responseStatus.errorCode, auth.responseStatus.message)
        } else {
            this.signIn(auth)
        }
        return { config, models, extensions, auth }
    },


    async uploadFile(file) {
        const formData = new FormData()
        formData.append('file', file)
        const response = await fetch(this.resolveUrl('/upload'), {
            method: 'POST',
            body: formData
        })
        if (!response.ok) {
            throw new Error(`Upload failed: ${response.statusText}`)
        }
        return response.json()
    },


    getCacheInfo(url) {
        return this.cacheUrlInfo[url]
    },
    async fetchCacheInfos(urls) {
        const infos = {}
        const fetchInfos = []
        for (const url of urls) {
            const info = this.getCacheInfo(url)
            if (info) {
                infos[url] = info
            } else {
                fetchInfos.push(fetch(this.resolveUrl(url + "?info")))
            }
        }
        const responses = await Promise.all(fetchInfos)
        for (let i = 0; i < urls.length; i++) {
            try {
                const info = await responses[i].json()
                this.setCacheInfo(urls[i], info)
                infos[urls[i]] = info
            } catch (e) {
                console.error('Failed to fetch info for', urls[i], e)
            }
        }
        return infos
    },
    setCacheInfo(url, info) {
        this.cacheUrlInfo[url] = info
    }

}


let ai = reactive(o)
export default ai
