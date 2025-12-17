import { ref, computed, watch, inject, onMounted } from "vue"
import ProviderStatus from "./ProviderStatus.mjs"
import ProviderIcon from "./ProviderIcon.mjs"
import { storageObject } from "./utils.mjs"

const SORT_OPTIONS = [
    { id: 'name', label: 'Name' },
    { id: 'knowledge', label: 'Knowledge Cutoff' },
    { id: 'release_date', label: 'Release Date' },
    { id: 'last_updated', label: 'Last Updated' },
    { id: 'cost_input', label: 'Cost (Input)' },
    { id: 'cost_output', label: 'Cost (Output)' },
    { id: 'context', label: 'Context Limit' },
]

const I = x => `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${x}</svg>`
const modalityIcons = {
    text: I(`<polyline points="4,7 4,4 20,4 20,7"></polyline><line x1="9" y1="20" x2="15" y2="20"></line><line x1="12" y1="4" x2="12" y2="20"></line>`),
    image: I(`<rect width="18" height="18" x="3" y="3" rx="2" ry="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path>`),
    audio: I(`<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="m19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>`),
    video: I(`<path d="m22 8-6 4 6 4V8Z"></path><rect width="14" height="12" x="2" y="6" rx="2" ry="2"></rect>`),
    pdf: I(`<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14,2 14,8 20,8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10,9 9,9 8,9"></polyline>`),
}

const STORAGE_KEY = 'llms.modelSelector'
const FAVORITES_KEY = 'llms.favorites'

// Formatting helpers
const numFmt = new Intl.NumberFormat()
const currFmt = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

function formatCost(cost) {
    if (cost == null) return '-'
    const val = parseFloat(cost)
    if (val === 0) return 'Free'
    return currFmt.format(val)
}

function formatNumber(num) {
    if (num == null) return '-'
    return numFmt.format(num)
}

function formatShortNumber(num) {
    if (num == null) return '-'
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
    if (num >= 1000) return (num / 1000).toFixed(0) + 'K'
    return numFmt.format(num)
}

function getModelModalities(model) {
    const mods = new Set()
    const input = model.modalities?.input || []
    const output = model.modalities?.output || []

    // Collect all modalities
    input.forEach(m => mods.add(m))
    output.forEach(m => mods.add(m))

    // Filter out text and ensure we only show known icons
    const allowed = ['image', 'audio', 'video', 'pdf']
    return Array.from(mods).filter(m => m !== 'text' && allowed.includes(m)).sort()
}

const ModelSelectorModal = {
    components: {
        ProviderIcon,
    },
    template: `
        <!-- Dialog Overlay -->
        <div class="fixed inset-0 z-50 overflow-hidden" @keydown.escape="closeDialog">
            <!-- Backdrop -->
            <div class="fixed inset-0 bg-black/50 transition-opacity" @click="closeDialog"></div>
            
            <!-- Dialog -->
            <div class="fixed inset-4 md:inset-8 lg:inset-12 flex items-center justify-center">
                <div class="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full h-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden">
                    <!-- Header -->
                    <div class="flex-shrink-0 px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                        <div class="flex items-center justify-between mb-4">
                            <h2 class="text-xl font-semibold text-gray-900 dark:text-gray-100">Select Model</h2>
                            <button type="button" @click="closeDialog" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
                                <svg class="size-6" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                                    <path fill="currentColor" d="M19 6.41L17.59 5L12 10.59L6.41 5L5 6.41L10.59 12L5 17.59L6.41 19L12 13.41L17.59 19L19 17.59L13.41 12z"/>
                                </svg>
                            </button>
                        </div>
                        
                        <!-- Search and Controls -->
                        <div class="flex flex-col md:flex-row gap-3">
                            <!-- Search -->
                            <div class="flex-1 relative">
                                <svg class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
                                </svg>
                                <input type="text" v-model="searchQuery" ref="searchInput"
                                    placeholder="Search models..."
                                    class="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm" />
                            </div>

                            <!-- Modality Filters -->
                            <div class="flex items-center space-x-1">
                                <button v-for="(icon, type) in modalityIcons" :key="type" type="button"
                                    @click="toggleModality(type)"
                                    :title="type"
                                    :class="[
                                        'p-2 rounded-lg transition-colors border',
                                        selectedModalities.has(type)
                                            ? 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800'
                                            : 'bg-white dark:bg-gray-800 text-gray-400 border-transparent hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-200'
                                    ]"
                                    v-html="icon">
                                </button>
                            </div>
                            
                            <!-- Sort -->
                            <div class="flex items-center space-x-2">
                                <label class="text-sm text-gray-600 dark:text-gray-400 whitespace-nowrap">Sort by:</label>
                                <select v-model="sortBy" 
                                    class="px-3 py-2 pr-8 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 min-w-[200px]">
                                    <option v-for="opt in sortOptions" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
                                </select>
                                <button type="button" @click="toggleSortDirection" 
                                    class="p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                    :title="sortAsc ? 'Ascending' : 'Descending'">
                                    <svg v-if="sortAsc" class="size-5 text-gray-600 dark:text-gray-400" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
                                        <path fill="currentColor" d="M19 7h3l-4-4l-4 4h3v14h2M2 17h10v2H2M6 5v2H2V5m0 6h7v2H2z"/>
                                    </svg>
                                    <svg v-else class="size-5 text-gray-600 dark:text-gray-400" style="transform: scaleY(-1)" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
                                        <path fill="currentColor" d="M19 7h3l-4-4l-4 4h3v14h2M2 17h10v2H2M6 5v2H2V5m0 6h7v2H2z"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Provider Filter -->
                        <div class="mt-3 flex flex-wrap gap-2">
                             <button type="button" 
                                @click="activeTab = 'favorites'"
                                :class="[
                                    'flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'favorites'
                                        ? 'bg-yellow-500 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
                                    <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                </svg>
                                <span>Favorites</span>
                                <span v-if="favorites.length > 0" class="ml-1 opacity-75">({{ favorites.length }})</span>
                            </button>
                            <div class="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1 self-center"></div>
                            <button type="button" 
                                @click="setActiveTab('browse', null)"
                                :class="[
                                    'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'browse' && selectedProviders.size === 0
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                All
                            </button>
                            <button v-for="provider in uniqueProviders" :key="provider"
                                type="button"
                                @click="setActiveTab('browse', provider)"
                                :class="[
                                    'flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'browse' && selectedProviders.has(provider)
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                <ProviderIcon :provider="provider" class="size-4" />
                                <span>{{ provider }}</span>
                                <span class="opacity-60">({{ providerCounts[provider] }})</span>
                            </button>
                        </div>
                    </div>
                    
                    <!-- Model List -->
                    <div class="flex-1 overflow-y-auto p-4">
                        <div v-if="filteredModels.length === 0 && !hasUnavailableFavorites" class="text-center py-12 text-gray-500 dark:text-gray-400">
                            No models found matching your criteria.
                        </div>
                        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                            <button v-for="model in filteredModels" :key="model.id + '-' + model.provider"
                                type="button"
                                @click="selectModel(model)"
                                :class="[
                                    'relative text-left p-4 rounded-lg border transition-all group',
                                    modelValue === model.name
                                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 ring-2 ring-blue-500/50'
                                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                                ]">
                                <!-- Favorite Star -->
                                <div @click.stop="toggleFavorite(model)" 
                                    class="absolute top-2 right-2 p-1.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors z-10 cursor-pointer"
                                    :title="isFavorite(model) ? 'Remove from favorites' : 'Add to favorites'">
                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" 
                                        :class="['size-4 transition-colors', isFavorite(model) ? 'text-yellow-400' : 'text-gray-300 dark:text-gray-600 group-hover:text-gray-400 dark:group-hover:text-gray-500']">
                                        <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                    </svg>
                                </div>

                                <div class="flex items-start justify-between mb-2 pr-6">
                                    <div class="flex items-center space-x-2 min-w-0">
                                        <ProviderIcon :provider="model.provider" class="size-5 flex-shrink-0" />
                                        <span class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ model.name }}</span>
                                    </div>
                                    <div v-if="isFreeModel(model)" class="flex-shrink-0 ml-2">
                                        <span class="px-1.5 py-0.5 text-xs font-semibold rounded bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300">FREE</span>
                                    </div>
                                </div>
                                
                                <div class="text-xs text-gray-500 dark:text-gray-400 mb-2 truncate" :title="model.id">{{ model.id }}</div>
                                
                                <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-400">
                                    <span v-if="model.cost && !isFreeModel(model)" :title="'Input: ' + model.cost.input + ' / Output: ' + model.cost.output + ' per 1M tokens'">
                                        💰 {{ formatCost(model.cost.input) }} / {{ formatCost(model.cost.output) }}
                                    </span>
                                    <span v-if="model.limit?.context" :title="'Context window: ' + formatNumber(model.limit.context) + ' tokens'">
                                        📏 {{ formatShortNumber(model.limit.context) }}
                                    </span>
                                    <span v-if="model.knowledge" :title="'Knowledge cutoff: ' + model.knowledge">
                                        📅 {{ model.knowledge }}
                                    </span>
                                </div>
                                
                                <div class="flex flex-wrap gap-1 mt-2">
                                    <span v-if="model.reasoning" class="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300">reasoning</span>
                                    <span v-if="model.tool_call" class="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300">tools</span>
                                    
                                    <!-- Modality Icons -->
                                    <span v-for="mod in getModelModalities(model)" :key="mod" 
                                        class="inline-flex items-center justify-center p-0.5 text-gray-400 dark:text-gray-500"
                                        :title="mod"
                                        v-html="modalityIcons[mod]">
                                    </span>
                                </div>
                            </button>
                        </div>


                        <!-- Unavailable Favorites -->
                        <div v-if="activeTab === 'favorites' && unavailableFavorites.length > 0" class="mt-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                             <div class="text-sm font-medium text-gray-500 dark:text-gray-400 mb-3 ml-1">Unavailable</div>
                             <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 opacity-60 grayscale">
                                <div v-for="model in unavailableFavorites" :key="model.id"
                                    class="relative text-left p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 cursor-not-allowed">
                                    
                                    <!-- Remove from favorites button -->
                                    <div @click.stop="toggleFavorite(model)" 
                                        class="absolute top-2 right-2 p-1.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors z-10 cursor-pointer"
                                        title="Remove from favorites">
                                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-4 text-yellow-400">
                                            <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                        </svg>
                                    </div>

                                    <div class="flex items-start justify-between mb-2 pr-6">
                                        <div class="flex items-center space-x-2 min-w-0">
                                            <ProviderIcon v-if="model.provider" :provider="model.provider" class="size-5 flex-shrink-0" />
                                            <span class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ model.name || model.id }}</span>
                                        </div>
                                    </div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ model.id }}</div>
                                    <div class="mt-2 text-xs italic text-gray-400">Provider unavailable</div>
                                </div>
                             </div>
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div class="flex-shrink-0 px-6 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                        <div class="flex items-center justify-between">
                            <span class="text-sm text-gray-600 dark:text-gray-400">
                                {{ filteredModels.length }} of {{ models.length }} models
                            </span>
                            <button type="button" @click="closeDialog"
                                class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>    
    `,
    emits: ['done'],
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const searchQuery = ref('')
        const searchInput = ref(null)

        // Load preferences
        const prefs = storageObject(STORAGE_KEY)
        const sortBy = ref(prefs.sortBy || 'name')
        const sortAsc = ref(prefs.sortAsc !== false)
        const selectedProviders = ref(new Set())
        const selectedModalities = ref(new Set())
        const models = computed(() => ctx.state.models || [])

        // Favorites State
        const favorites = ref([])
        try {
            favorites.value = JSON.parse(localStorage.getItem(FAVORITES_KEY)) || []
        } catch {
            favorites.value = []
        }

        const activeTab = ref(favorites.value.length > 0 ? 'favorites' : 'browse')

        const sortOptions = SORT_OPTIONS

        // Get unique providers
        const uniqueProviders = computed(() => {
            if (!models.value) return []
            const providers = [...new Set(models.value.map(m => m.provider))].filter(Boolean)
            return providers.sort()
        })

        // Provider counts
        const providerCounts = computed(() => {
            if (!models.value) return {}
            const counts = {}
            models.value.forEach(m => {
                if (m.provider) {
                    counts[m.provider] = (counts[m.provider] || 0) + 1
                }
            })
            return counts
        })

        // Filter and sort helpers
        function getModelKey(model) {
            return `${model.provider}:${model.id}`
        }

        function isFavorite(model) {
            const key = getModelKey(model)
            return favorites.value.includes(key)
        }

        // Unavailable favorites (provider disabled or model removed)
        const unavailableFavorites = computed(() => {
            if (!models.value) return []
            const availableKeys = new Set(models.value.map(getModelKey))
            const missingKeys = favorites.value.filter(key => !availableKeys.has(key))

            return missingKeys.map(key => {
                const [provider, ...idParts] = key.split(':')
                const id = idParts.join(':')
                return {
                    id,
                    provider,
                    name: id // Fallback
                }
            })
        })

        const hasUnavailableFavorites = computed(() => unavailableFavorites.value.length > 0)

        // Filter and sort models
        const filteredModels = computed(() => {
            if (!models.value) return []

            let result = [...models.value]

            // Filter by Tab
            if (activeTab.value === 'favorites') {
                result = result.filter(isFavorite)
            } else {
                // Browse Tab - Filter by provider
                if (selectedProviders.value.size > 0) {
                    result = result.filter(m => selectedProviders.value.has(m.provider))
                }
            }

            // Filter by Modalities (AND logic)
            if (selectedModalities.value.size > 0) {
                result = result.filter(m => {
                    const mods = m.modalities || {}
                    const inputMods = mods.input || []
                    const outputMods = mods.output || []

                    for (const requiredMod of selectedModalities.value) {
                        if (requiredMod === 'audio') {
                            if (!inputMods.includes('audio') && !outputMods.includes('audio')) return false
                        } else if (requiredMod === 'pdf') {
                            if (!inputMods.includes('pdf')) return false
                        } else {
                            // text, image, video mostly in input (though video could be output too, but prompt implies input/output caps)
                            // The user prompt says "input/output modalities", but usually we filter by what the model supports generally.
                            // Let's check both for robust matching if strict input/output isn't specified for that type, 
                            // but based on typical llms.json:
                            // text: input/output
                            // image: input (vision) / output (generation)
                            // video: input
                            // let's check input for everything except where we know otherwise

                            const inIn = inputMods.includes(requiredMod)
                            const inOut = outputMods.includes(requiredMod)
                            if (!inIn && !inOut) return false
                        }
                    }
                    return true
                })
            }

            // Filter by search query
            if (searchQuery.value.trim()) {
                const query = searchQuery.value.toLowerCase()
                result = result.filter(m =>
                    m.name?.toLowerCase().includes(query) ||
                    m.id?.toLowerCase().includes(query) ||
                    m.provider?.toLowerCase().includes(query)
                )
            }

            // Sort
            result.sort((a, b) => {
                let cmp = 0
                switch (sortBy.value) {
                    case 'name':
                        cmp = (a.name || '').localeCompare(b.name || '')
                        break
                    case 'knowledge':
                        cmp = (a.knowledge || '').localeCompare(b.knowledge || '')
                        break
                    case 'release_date':
                        cmp = (a.release_date || '').localeCompare(b.release_date || '')
                        break
                    case 'last_updated':
                        cmp = (a.last_updated || '').localeCompare(b.last_updated || '')
                        break
                    case 'cost_input':
                        cmp = (parseFloat(a.cost?.input) || 0) - (parseFloat(b.cost?.input) || 0)
                        break
                    case 'cost_output':
                        cmp = (parseFloat(a.cost?.output) || 0) - (parseFloat(b.cost?.output) || 0)
                        break
                    case 'context':
                        cmp = (a.limit?.context || 0) - (b.limit?.context || 0)
                        break
                    default:
                        cmp = 0
                }
                return sortAsc.value ? cmp : -cmp
            })

            return result
        })

        function isFreeModel(model) {
            return model.cost && parseFloat(model.cost.input) === 0 && parseFloat(model.cost.output) === 0
        }

        function selectModel(model) {
            ctx.setState({ selectedModel: model.name })
            closeDialog()
        }

        function closeDialog() {
            emit('done')
        }

        function setActiveTab(tab, provider) {
            activeTab.value = tab
            if (tab === 'browse') {
                toggleProvider(provider)
            }
        }

        function toggleProvider(provider) {
            if (provider === null) {
                selectedProviders.value = new Set()
            } else {
                // Exclusive filter: if clicking a new provider, clear others.
                // If clicking the currently selected one, toggle it off.
                const newSet = new Set()
                if (!selectedProviders.value.has(provider)) {
                    newSet.add(provider)
                }
                selectedProviders.value = newSet
            }
        }

        function toggleModality(modality) {
            const newSet = new Set(selectedModalities.value)
            if (newSet.has(modality)) {
                newSet.delete(modality)
            } else {
                newSet.add(modality)
            }
            selectedModalities.value = newSet
        }

        function toggleFavorite(model) {
            const key = getModelKey(model)
            const idx = favorites.value.indexOf(key)
            if (idx === -1) {
                favorites.value.push(key)
            } else {
                favorites.value.splice(idx, 1)
            }
            storageObject(FAVORITES_KEY, favorites.value)
        }

        function toggleSortDirection() {
            sortAsc.value = !sortAsc.value
        }

        // Save preferences when sort changes
        watch([sortBy, sortAsc], () => {
            storageObject(STORAGE_KEY, {
                sortBy: sortBy.value,
                sortAsc: sortAsc.value
            })
        })

        // Deep link logic with Vue Router
        onMounted(() => {
            searchQuery.value = ''
            setTimeout(() => {
                searchInput.value?.focus()
            }, 100)
        })

        return {
            models,
            searchQuery,
            searchInput,
            sortBy,
            sortAsc,
            sortOptions,
            selectedProviders,
            uniqueProviders,
            providerCounts,
            filteredModels,
            formatCost,
            formatNumber,
            formatShortNumber,
            isFreeModel,

            closeDialog,
            selectModel,
            toggleProvider,
            toggleSortDirection,
            favorites,
            activeTab,
            setActiveTab,
            toggleFavorite,
            isFavorite,
            unavailableFavorites,
            hasUnavailableFavorites,
            modalityIcons,
            selectedModalities,
            toggleModality,
            getModelModalities,
        }
    }
}

const ModelTooltip = {
    template: `
        <div v-if="model" 
            class="absolute z-50 mt-10 ml-0 p-3 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 text-sm w-72">
            <div class="font-semibold text-gray-900 dark:text-gray-100 mb-2">{{ model.name }}</div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-2">{{ model.provider }}</div>
            
            <div v-if="model.cost" class="mb-2">
                <div class="text-xs font-medium text-gray-700 dark:text-gray-300">Cost per 1M tokens:</div>
                <div class="text-xs text-gray-600 dark:text-gray-400 ml-2">
                    Input: {{ formatCost(model.cost.input) }} · Output: {{ formatCost(model.cost.output) }}
                </div>
            </div>
            
            <div v-if="model.limit" class="mb-2">
                <div class="text-xs font-medium text-gray-700 dark:text-gray-300">Limits:</div>
                <div class="text-xs text-gray-600 dark:text-gray-400 ml-2">
                    Context: {{ formatNumber(model.limit.context) }} · Output: {{ formatNumber(model.limit.output) }}
                </div>
            </div>
            
            <div v-if="model.knowledge" class="text-xs text-gray-600 dark:text-gray-400">
                Knowledge: {{ model.knowledge }}
            </div>
            
            <div class="flex flex-wrap gap-1 mt-2">
                <span v-if="model.reasoning" class="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300">reasoning</span>
                <span v-if="model.tool_call" class="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300">tools</span>
                
                <!-- Modality Icons -->
                <span v-for="mod in getModelModalities(model)" :key="mod" 
                    class="inline-flex items-center justify-center p-0.5 text-gray-400 dark:text-gray-500"
                    :title="mod"
                    v-html="modalityIcons[mod]">
                </span>
            </div>
        </div>
    `,
    props: {
        model: Object,
    },
    setup(props) {
        return {
            formatCost,
            formatNumber,
            getModelModalities,
            modalityIcons,
        }
    }
}

const ModelSelector = {
    components: {
        ProviderStatus,
        ProviderIcon,
        ModelSelectorModal,
        ModelTooltip,
    },
    template: `
        <!-- Model Selector Button -->
        <div class="pl-1 flex space-x-2">
            <button type="button" @click="openDialog"
                class="flex items-center space-x-2 px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 transition-colors min-w-48 max-w-96"
                @mouseenter="showTooltip = true"
                @mouseleave="showTooltip = false">
                <ProviderIcon v-if="selectedModel?.provider" :provider="selectedModel.provider" class="size-5 flex-shrink-0" />
                <span class="truncate flex-1 text-left">{{ selectedModel?.name || 'Select Model...' }}</span>
                <svg class="size-4 flex-shrink-0 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                </svg>
            </button>

            <!-- Info Tooltip (on hover) -->
            <ModelTooltip v-if="showTooltip" :model="selectedModel" />

            <ProviderStatus @updated="$emit('updated', $event)" />
        </div>
    `,
    emits: ['updated', 'update:modelValue'],
    props: {
        models: Array,
        modelValue: String,
    },
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const showTooltip = ref(false)

        // Get selected model object
        const selectedModel = computed(() => {
            if (!props.modelValue || !props.models) return null
            return props.models.find(m => m.name === props.modelValue) || props.models.find(m => m.id === props.modelValue)
        })

        function openDialog() {
            ctx.state.models = props.models
            ctx.openModal('models')
        }

        watch(() => ctx.state.selectedModel, (newVal) => {
            emit('update:modelValue', newVal)
        })

        onMounted(() => {
            ctx.state.models = props.models
        })

        return {
            showTooltip,
            openDialog,
            selectedModel,
        }
    }
}

export default {
    install(ctx) {
        ctx.components({
            ModelSelector,
        })
        ctx.modals({
            'models': ModelSelectorModal,
        })
    }
}