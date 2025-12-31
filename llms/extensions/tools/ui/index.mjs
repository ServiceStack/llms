import { inject, computed } from "vue"

const Tools = {
    template: `
    <div class="p-4 md:p-6 max-w-7xl mx-auto w-full">
        <div class="mb-6">
            <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">Tools</h1>
            <p class="text-gray-600 dark:text-gray-400 mt-1">
                {{ ($state.tools || []).length }} tools available
            </p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="tool in ($state.tools || [])" :key="tool.function.name"
                class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden flex flex-col">
                
                <div class="p-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                    <div class="font-bold text-lg text-gray-900 dark:text-gray-100 font-mono break-all">
                        {{ tool.function.name }}
                    </div>
                </div>

                <div class="p-4 flex-1 flex flex-col">
                     <p v-if="tool.function.description" class="text-sm text-gray-600 dark:text-gray-300 mb-4 flex-1">
                        {{ tool.function.description }}
                     </p>
                     <p v-else class="text-sm text-gray-400 italic mb-4 flex-1">
                        No description provided
                     </p>

                     <div v-if="tool.function.parameters?.properties && Object.keys(tool.function.parameters.properties).length > 0">
                        <div class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Parameters</div>
                        <div class="space-y-3">
                            <div v-for="(prop, name) in tool.function.parameters.properties" :key="name" class="text-sm bg-gray-50 dark:bg-gray-700/30 rounded p-2">
                                <div class="flex flex-wrap items-baseline gap-2 mb-1">
                                    <span class="font-mono font-medium text-blue-600 dark:text-blue-400">{{ name }}</span>
                                    <span class="text-gray-500 text-xs">({{ prop.type }})</span>
                                    <span v-if="tool.function.parameters.required?.includes(name)" 
                                        class="px-1.5 py-0.5 text-[10px] rounded bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 font-medium">
                                        REQUIRED
                                    </span>
                                </div>
                                <div v-if="prop.description" class="text-gray-600 dark:text-gray-400 text-xs">
                                    {{ prop.description }}
                                </div>
                            </div>
                        </div>
                     </div>
                     <div v-else class="text-sm text-gray-400 italic border-t border-gray-100 dark:border-gray-700 pt-2 mt-auto">
                        No parameters
                     </div>
                </div>
            </div>
        </div>
    </div>
    `,
    setup() {

    }
}

const ToolSelector = {
    template: `
        <div class="px-4 py-2 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
            <div class="flex flex-wrap items-center gap-2 text-sm">
                
                <!-- All -->
                <button @click="$ctx.setPrefs({ onlyTools: null })"
                    class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none"
                    :class="$prefs.onlyTools == null
                        ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 border-green-200 dark:border-green-800' 
                        : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:border-gray-700 dark:text-gray-400 border-gray-200 dark:hover:border-gray-600 hover:border-gray-300'">
                    All
                </button>

                <!-- None -->
                <button @click="$ctx.setPrefs({ onlyTools:[] })"
                    class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none"
                    :class="$prefs.onlyTools?.length === 0
                        ? 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-800 dark:text-fuchsia-300 border-fuchsia-200 dark:border-fuchsia-800' 
                        : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                    None
                </button>

                <div class="border-l h-4"></div>

                <!-- Tools -->
                <button v-for="tool in availableTools" :key="tool.function.name" type="button"
                    @click="toggleTool(tool.function.name)"
                    :title="tool.function.description"
                    class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none"
                    :class="isToolActive(tool.function.name)
                        ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-800' 
                        : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                    {{ tool.function.name }}
                </button>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')

        const availableTools = computed(() => ctx.state.tools || [])

        function isToolActive(name) {
            const only = ctx.prefs.onlyTools
            if (only == null) return true
            if (Array.isArray(only)) return only.includes(name)
            return false
        }

        function toggleTool(name) {
            let onlyTools = ctx.prefs.onlyTools

            // If currently 'All', clicking a tool means we enter custom mode with all OTHER tools selected (deselecting clicked)
            if (onlyTools == null) {
                onlyTools = availableTools.value.map(t => t.function.name).filter(t => t !== name)
            } else {
                // Currently Custom or None
                if (onlyTools.includes(name)) {
                    onlyTools = onlyTools.filter(t => t !== name)
                } else {
                    onlyTools = [...onlyTools, name]
                }
            }

            ctx.setPrefs({ onlyTools })
        }

        return {
            availableTools,
            isToolActive,
            toggleTool
        }
    }
}

export default {
    order: 10 - 100,

    install(ctx) {

        ctx.components({
            Tools,
            ToolSelector,
        })

        const svg = (attrs, title) => `<svg ${attrs} xmlns="http://www.w4.org/2000/svg" viewBox="0 0 24 24">${title ? "<title>" + title + "</title>" : ''}<path fill="currentColor" d="M5.33 3.272a3.5 3.5 0 0 1 4.472 4.473L20.647 18.59l-2.122 2.122L7.68 9.867a3.5 3.5 0 0 1-4.472-4.474L5.444 7.63a1.5 1.5 0 0 0 2.121-2.121zm10.367 1.883l3.182-1.768l1.414 1.415l-1.768 3.182l-1.768.353l-2.12 2.121l-1.415-1.414l2.121-2.121zm-7.071 7.778l2.121 2.122l-4.95 4.95A1.5 1.5 0 0 1 3.58 17.99l.097-.107z"/></svg>`

        ctx.setLeftIcons({
            tools: {
                component: {
                    template: svg('@click=$ctx.togglePath("/tools")'),
                },
                isActive({ path }) {
                    return path === '/tools'
                }
            }
        })

        ctx.setTopIcons({
            tools: {
                component: {
                    template: svg([
                        `@click=$ctx.toggleTop("ToolSelector")`,
                        `:class="$prefs.onlyTools == null ? 'text-green-600 dark:text-green-300' : $prefs.onlyTools.length ? 'text-blue-600! dark:text-blue-300!' : ''"`
                    ].join(' ')),
                    // , "{{$prefs.onlyTools == null ? 'Include All Tools' : $prefs.onlyTools.length ? 'Include Selected Tools' : 'All Tools Excluded'}}"
                },
                isActive({ top }) {
                    return top === 'ToolSelector'
                },
                get title() {
                    return ctx.prefs.onlyTools == null
                        ? `All Tools Included`
                        : ctx.prefs.onlyTools.length
                            ? `${ctx.prefs.onlyTools.length} ${ctx.utils.pluralize('Tool', ctx.prefs.onlyTools.length)} Included`
                            : 'No Tools Included'
                }
            }
        })

        ctx.chatRequestFilters.push(({ request, thread }) => {
            // Tool Preferences
            const prefs = ctx.prefs
            if (prefs.onlyTools != null) {
                if (Array.isArray(prefs.onlyTools)) {
                    request.metadata.only_tools = prefs.onlyTools.length > 0
                        ? prefs.onlyTools.join(',')
                        : 'none'
                }
            } else {
                request.metadata.only_tools = 'all'
            }
        })

        ctx.routes.push({ path: '/tools', component: Tools, meta: { title: 'View Tools' } })
    },

    async load(ctx) {
        const ext = ctx.scope('tools')
        ctx.state.tools = await ext.getJson('/')
    }
}