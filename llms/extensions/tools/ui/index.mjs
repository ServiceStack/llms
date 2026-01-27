import { inject, computed, ref, onMounted } from "vue"

let ext

function useTools(ctx) {

    const availableTools = computed(() => ctx.state.tool.definitions.filter(x => x.function))
    const toolPageHeaders = {}

    function setToolPageHeaders(components) {
        Object.assign(toolPageHeaders, components)
    }

    function selectTool({ group, tool }) {
        ext.setPrefs({ selectedGroup: group, selectedTool: tool })
    }

    function getToolDefinition(name) {
        return ctx.state.tool.definitions.find(d => d.function?.name === name)
    }

    function isToolEnabled(name) {
        const toolDef = getToolDefinition(name)
        if (!toolDef) return false
        const onlyTools = ctx.prefs.onlyTools
        if (onlyTools == null) return true
        return Array.isArray(onlyTools) && onlyTools.includes(name)
    }

    function enableTool(name) {
        let onlyTools = ctx.prefs.onlyTools
        if (onlyTools == null) return // All tools are enabled

        if (!Array.isArray(onlyTools)) {
            onlyTools = [onlyTools]
        }
        else if (!onlyTools.includes(name)) {
            onlyTools.push(name)
        } else {
            return // Already enabled
        }
        ctx.setPrefs({ onlyTools })
    }

    function disableTool(name) {
        let onlyTools = ctx.prefs.onlyTools
        if (onlyTools == null) {
            // If currently 'All', clicking a tool means we enter custom mode with all OTHER tools selected
            onlyTools = availableTools.value.map(t => t.function.name).filter(t => t !== name)
        } else if (!Array.isArray(onlyTools)) {
            onlyTools = []
        } else {
            onlyTools = onlyTools.filter(t => t !== name)
        }
        ctx.setPrefs({ onlyTools })
    }

    function toggleTool(name, enable = null) {
        if (enable == null) {
            enable = !isToolEnabled(name)
        }
        if (enable) {
            enableTool(name)
        } else {
            disableTool(name)
        }
    }

    return {
        availableTools,
        toolPageHeaders,
        setToolPageHeaders,
        selectTool,
        getToolDefinition,
        isToolEnabled,
        enableTool,
        disableTool,
        toggleTool,
        get selectedGroup() { return ext.prefs.selectedGroup },
        get selectedTool() { return ext.prefs.selectedTool },
    }
}


const ToolResult = {
    template: `
    <div>
        <div class="flex items-center gap-2 text-[10px] uppercase tracking-wider font-medium select-none">
            <span @click="ext.setPrefs({ toolFormat: 'text' })" 
                class="cursor-pointer transition-colors"
                :class="ext.prefs.toolFormat !== 'preview' ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'">
                text
            </span>
            <span class="text-gray-300 dark:text-gray-700">|</span>
            <span @click="ext.setPrefs({ toolFormat: 'preview' })" 
                class="cursor-pointer transition-colors"
                :class="ext.prefs.toolFormat == 'preview' ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'">
                preview
            </span>
        </div>
        <div class="not-prose py-2">
            <pre v-if="ext.prefs.toolFormat !== 'preview'" class="tool-output">{{ origResult }}</pre>
            <div v-else>
                <ViewTypes v-if="Array.isArray(result) && result[0]?.type" :results="result" />
                <ViewType v-else :result="result" />
            </div>
        </div>
    </div>
    `,
    props: {
        result: {
            required: true
        }
    },
    setup(props) {

        const origResult = computed(() => {
            let ret = props.result
            if (Array.isArray(props.result) && props.result.length == 1) {
                ret = props.result[0]
            }
            if (ret.type) {
                if (ret.type === "text") {
                    return ret.text
                }
            }
            return props.result
        })
        const displayResult = computed(() => {
            try {
                let result = typeof props.result == 'string'
                    ? JSON.parse(props.result)
                    : props.result
                if (Array.isArray(result) && result.length == 1) {
                    result = result[0]
                }
                if (result.type) {
                    if (result.type === "text") {
                        try {
                            return JSON.parse(result.text)
                        } catch (e) {
                            return result.text
                        }
                    }
                }
                return result
            } catch (e) {
                return props.result
            }
        })
        return {
            ext,
            origResult,
            displayResult,
        }
    }
}

const JsonInput = {
    template: `
    <div class="flex flex-col gap-1">
        <div class="relative">
            <textarea 
                v-model="localJson" 
                @input="validate"
                rows="5"
                class="w-full p-2 font-mono text-xs border rounded-md resize-y focus:outline-none focus:ring-2 transition-colors"
                :class="error 
                    ? 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/10 focus:ring-red-500' 
                    : 'border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 focus:ring-blue-500'"
                spellcheck="false"
            ></textarea>
            <div v-if="isValid" class="absolute bottom-2 right-2 text-green-500 bg-white dark:bg-gray-800 rounded-full p-1 shadow-sm">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
                </svg>
            </div>
        </div>
        <div v-if="error" class="text-xs text-red-600 dark:text-red-400 font-medium px-1">
            {{ error }}
        </div>
    </div>
    `,
    props: {
        modelValue: {
            required: true
        }
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
        // Initialize with formatted JSON
        const localJson = ref(
            props.modelValue !== undefined
                ? JSON.stringify(props.modelValue, null, 4)
                : ''
        )
        const error = ref(null)
        const isValid = ref(true)

        function validate() {
            try {
                if (!localJson.value.trim()) {
                    // Decide if empty string is valid object/array or undefined
                    // For now, let's say empty is NOT valid if the prop expects object
                    // But maybe we can treat it as valid undefined/null?
                    // Let's enforce valid JSON.
                    if (localJson.value === '') {
                        error.value = null
                        isValid.value = true
                        emit('update:modelValue', undefined)
                        return
                    }
                }

                const parsed = JSON.parse(localJson.value)
                error.value = null
                isValid.value = true
                emit('update:modelValue', parsed)
            } catch (e) {
                error.value = e.message
                isValid.value = false
                // Do not emit invalid values
            }
        }

        // Watch external changes only if they differ significantly from local
        /* 
           Note: two-way binding with text representation is tricky.
           If we watch props.modelValue, we might re-format user's in-progress typing if we aren't careful.
           Usually better to only update localJson if the prop changes "from outside".
           For this simple tool, initial value is likely enough, or we can watch with a deep compare check.
           For now, let's stick to initial + internal validation. 
        */

        return {
            localJson,
            error,
            isValid,
            validate
        }
    }
}

const Tools = {
    template: `
    <div class="p-4 md:p-6 max-w-7xl mx-auto w-full relative">
        <div v-if="Object.keys($ctx.tools.toolPageHeaders).length">
            <div v-for="(component, key) in $ctx.tools.toolPageHeaders" :key="key">
                <component :is="component" />
            </div>
        </div>
        <div ref="refTop" class="mb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
                <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">Tools</h1>
                <p class="text-gray-600 dark:text-gray-400 mt-1">
                    {{ filteredTools.length }} tools available
                </p>
            </div>

            <div v-if="groups.length > 0" class="flex flex-wrap items-center gap-2">
                 <button @click="ext.setPrefs({ selectedGroup: 'All' })"
                    class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none"
                    :class="ext.prefs.selectedGroup === 'All'
                        ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 border-green-300 dark:border-green-800' 
                        : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                    All
                </button>

                <div class="border-l h-4 mx-1 border-gray-300 dark:border-gray-600"></div>

                <button v-for="group in groups" :key="group"
                    @click="ext.setPrefs({ selectedGroup: group})"
                    class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none"
                    :class="ext.prefs.selectedGroup === group
                        ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-800' 
                        : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                    {{ group }}
                </button>
            </div>
        </div>

        <!-- Execution Form Panel -->
        <div v-if="executingTool" class="mb-8 bg-white dark:bg-gray-800 rounded-lg border border-blue-200 dark:border-blue-800 shadow-sm overflow-hidden animate-in fade-in slide-in-from-top-4 duration-200">
            <div class="bg-blue-50 dark:bg-blue-900/30 px-4 py-3 border-b border-blue-100 dark:border-blue-800 flex justify-between items-center">
                <div class="flex items-center gap-2">
                    <h3 class="font-bold text-gray-900 dark:text-gray-100">Execute: <span class="font-mono text-blue-600 dark:text-blue-400">{{ executingTool.function.name }}</span></h3>
                </div>
                <button @click="closeExec" type="button" class="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
                    </svg>
                </button>
            </div>
            
            <div class="p-4 md:p-6">
                <form ref="refForm" @submit.prevent="execTool" class="space-y-4">
                    <div v-if="Object.keys(executingTool.function.parameters?.properties || {}).length > 0" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div v-for="(prop, name) in executingTool.function.parameters.properties" :key="name">
                            <label :for="'input-' + name" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                {{ name }}
                                <span v-if="executingTool.function.parameters.required?.includes(name)" class="text-red-500">*</span>
                            </label>
                            
                            <div v-if="prop.enum">
                                <select v-model="execForm[name]" :id="'input-' + name" class="block w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm">
                                    <option :value="undefined" disabled>Select...</option>
                                    <option v-for="opt in prop.enum" :key="opt" :value="opt">{{ opt }}</option>
                                </select>
                            </div>
                            
                            <div v-else-if="prop.type === 'boolean'">
                                <select v-model="execForm[name]" :id="'input-' + name" class="block w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm">
                                     <option :value="false">False</option>
                                     <option :value="true">True</option>
                                </select>
                            </div>

                            <div v-else-if="prop.type === 'object' || prop.type === 'array'">
                                <JsonInput v-model="execForm[name]" />
                            </div>

                            <div v-else>
                                <input :type="prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'" 
                                       v-model="execForm[name]" 
                                       :id="'input-' + name"
                                       :placeholder="prop.description"
                                       :step="prop.type === 'integer' ? 1 : 0.01"
                                       class="block w-full rounded-md border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm">
                            </div>
                            <p v-if="prop.description" class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ prop.description }}</p>
                        </div>
                    </div>
                    <div v-else class="text-gray-500 dark:text-gray-400 italic">
                        No parameters required.
                    </div>

                    <div class="flex items-center gap-3 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <button type="submit" :disabled="loading"
                            class="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed">
                            <svg v-if="loading" class="animate-spin -ml-1 mr-2 h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            {{ loading ? 'Executing...' : 'Run Tool' }}
                        </button>
                    </div>
                </form>

                <div v-if="execResult !== null || execError" class="mt-6">
                    <h4 class="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">Response:</h4>
                     <div v-if="execError" class="p-4 rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 font-mono text-sm whitespace-pre-wrap">
                        {{ execError }}
                    </div>
                    <ToolResult v-else :result="execResult" />
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="tool in filteredTools" :key="tool.function.name" :id="'tool-' + tool.function.name"
                class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col">
                
                <div class="p-4 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-between items-center">
                    <div class="font-bold text-lg text-gray-900 dark:text-gray-100 font-mono break-all mr-2">
                        {{ tool.function.name }}
                    </div>
                    <button @click="startExec(tool)" type="button" title="Execute Tool" class="text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 border-none">
                        <svg xmlns="http://www.w3.org/2000/svg" class="size-6" viewBox="0 0 20 20" fill="currentColor">
                            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clip-rule="evenodd" />
                        </svg>
                    </button>
                </div>

                <div class="tool-description p-4 flex-1 flex flex-col">
                     <div v-if="tool.function.description" class="text-sm text-gray-600 dark:text-gray-300 mb-4 flex-1 flex flex-col">
                        <div v-if="tool.function.description.length < 350">
                            <div v-html="$fmt.markdown(tool.function.description)"></div>
                        </div>
                        <div v-else>
                            <div class="relative transition-all duration-300 ease-in-out" 
                                :class="{'max-h-[200px] overflow-hidden': !isExpanded(tool.function.name)}">
                                <div v-html="$fmt.markdown(tool.function.description)" :title="tool.function.description"></div>
                                
                                <!-- Fade overlay when collapsed -->
                                <div v-if="!isExpanded(tool.function.name)" 
                                    class="absolute bottom-0 left-0 right-0 h-12 bg-gradient-to-t from-white dark:from-gray-800 to-transparent pointer-events-none">
                                </div>
                            </div>

                            <button @click="toggleDescription(tool.function.name)" 
                                    type="button"
                                    class="mt-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 focus:outline-none self-start">
                                {{ isExpanded(tool.function.name) ? 'Show Less' : 'Show More' }}
                            </button>
                        </div>
                     </div>
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
        const ctx = inject('ctx')

        // Execution State
        const execForm = ref({})
        const execResult = ref(null)
        const execError = ref(null)
        const loading = ref(false)
        const refForm = ref()
        const refTop = ref()

        const executingTool = computed(() => {
            const tool = ext.prefs.selectedTool
            if (!tool) return null
            return ctx.state.tool.definitions.find(x => x.function.name === tool)
        })

        // UI State
        const expandedDescriptions = ref({})

        const groups = computed(() => Object.keys(ctx.state.tool.groups || {}))

        const filteredTools = computed(() => {
            const allTools = ctx.state.tool.definitions.filter(x => x.function)
            if (ext.prefs.selectedGroup === 'All') return allTools

            const groupTools = ctx.state.tool.groups[ext.prefs.selectedGroup] || []
            return allTools.filter(t => groupTools.includes(t.function.name))
        })

        function startExec(tool) {
            ext.setPrefs({ selectedTool: tool.function.name })
            execForm.value = {}
            execResult.value = null
            execError.value = null

            // Initialize defaults if any
            if (tool.function.parameters?.properties) {
                Object.entries(tool.function.parameters.properties).forEach(([key, prop]) => {
                    if (prop.default !== undefined) {
                        execForm.value[key] = prop.default
                    }
                    // Initialize booleans to false if likely
                    if (prop.type === 'boolean' && prop.default === undefined) {
                        // Optional: default to false? or maybe undefined is better to force user choice or let server handle it?
                        // Let's leave it undefined unless explicitly set
                        execForm.value[key] = false
                    }
                })
            }
            // Scroll to top
            // window.scrollTo({ top: 0, behavior: 'smooth' })
            refForm.value?.scrollIntoView({ behavior: 'smooth' })
        }

        function closeExec() {
            ext.setPrefs({ selectedTool: null })
            execForm.value = {}
            execResult.value = null
            execError.value = null
        }

        async function execTool() {
            if (!executingTool.value) return

            loading.value = true
            execResult.value = null
            execError.value = null

            try {
                const ext = ctx.scope('tools')
                // Filter out undefined values to avoid sending empty params that might confuse backend validation
                // Or maybe send them as null? existing backend `tool_prop_value` handles things.
                const payload = { ...execForm.value }

                // Ensure numbers are numbers
                if (executingTool.value.function.parameters?.properties) {
                    Object.entries(executingTool.value.function.parameters.properties).forEach(([key, prop]) => {
                        if ((prop.type === 'integer' || prop.type === 'number') && payload[key] !== '') {
                            payload[key] = Number(payload[key])
                        }
                    });
                }

                const res = await ext.postJson('/exec/' + executingTool.value.function.name, payload)
                if (res.error) {
                    execError.value = res.error.message
                } else {
                    execResult.value = res.response
                }
            } catch (e) {
                execError.value = e.message || 'Unknown error occurred'
            } finally {
                loading.value = false
            }
        }

        function toggleDescription(name) {
            expandedDescriptions.value[name] = !expandedDescriptions.value[name]
        }

        function isExpanded(name) {
            return !!expandedDescriptions.value[name]
        }

        onMounted(() => {
            if (!ext.prefs.selectedGroup) {
                ext.setPrefs({ selectedGroup: 'All' })
            }
        })

        return {
            ext,
            refForm,
            refTop,
            groups,
            filteredTools,
            // Exec
            executingTool,
            execForm,
            execResult,
            execError,
            loading,
            startExec,
            closeExec,
            execTool,
            // UI
            toggleDescription,
            isExpanded
        }
    }
}

const ToolSelector = {
    template: `
        <div class="px-4 py-4 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 max-h-[80vh] overflow-y-auto">
            
            <!-- Global Controls -->
            <div class="flex items-center justify-between mb-4">
                <span class="text-xs font-bold uppercase text-gray-500 tracking-wider">Include Tools</span>
                <div class="flex items-center gap-2">
                    <button @click="$ctx.setPrefs({ onlyTools: null })"
                        class="px-3 py-1 rounded-md text-xs font-medium border transition-colors select-none"
                        :class="prefs.onlyTools == null
                            ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 border-green-300 dark:border-green-800' 
                            : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                        All Tools
                    </button>
                    <button @click="$ctx.setPrefs({ onlyTools:[] })"
                        class="px-3 py-1 rounded-md text-xs font-medium border transition-colors select-none"
                        :class="prefs.onlyTools?.length === 0
                            ? 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-800 dark:text-fuchsia-300 border-fuchsia-200 dark:border-fuchsia-800' 
                            : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                        No Tools
                    </button>
                </div>
            </div>

            <!-- Groups -->
            <div class="space-y-3">
                <div v-for="group in toolGroups" :key="group.name" 
                     class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                     
                     <!-- Group Header -->
                     <div class="flex items-center justify-between px-3 py-2 bg-gray-50/50 dark:bg-gray-800/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                          @click="toggleCollapse(group.name)">
                        
                        <div class="flex items-center gap-2 min-w-0">
                             <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4 text-gray-400 transition-transform duration-200" :class="{ '-rotate-90': isCollapsed(group.name) }">
                                <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                             </svg>
                             <span class="font-semibold text-sm text-gray-700 dark:text-gray-200 truncate">
                                {{ group.name || 'Other Tools' }}
                             </span>
                             <span class="text-xs text-gray-400 font-mono">
                                {{ getActiveCount(group) }}/{{ group.tools.length }}
                             </span>
                        </div>

                        <div class="flex items-center gap-2" @click.stop>
                             <button @click="setGroupTools(group, true)" type="button"
                                title="Include All in Group"
                                class="px-2 py-0.5 rounded text-xs font-medium border transition-colors select-none"
                                :class="getActiveCount(group) === group.tools.length
                                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 border-green-300 dark:border-green-800 hover:bg-green-100 dark:hover:bg-green-900/40'
                                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                all
                             </button>
                             <button @click="setGroupTools(group, false)" type="button"
                                title="Include None in Group"
                                class="px-2 py-0.5 rounded text-xs font-medium border transition-colors select-none"
                                :class="getActiveCount(group) === 0
                                    ? 'bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-700 dark:text-fuchsia-300 border-fuchsia-200 dark:border-fuchsia-800 hover:bg-fuchsia-100 dark:hover:bg-fuchsia-900/40'
                                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                none
                             </button>
                        </div>
                     </div>
                     
                     <!-- Group Body -->
                     <div v-show="!isCollapsed(group.name)" class="p-3 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-800">
                         <div class="flex flex-wrap gap-2">
                            <button v-for="tool in group.tools" :key="tool.function.name" type="button"
                                @click="$tools.toggleTool(tool.function.name)"
                                :title="tool.function.description"
                                class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none text-left truncate max-w-[200px]"
                                :class="$tools.isToolEnabled(tool.function.name)
                                    ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-800' 
                                    : 'bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                {{ tool.function.name }}
                            </button>
                         </div>
                     </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const collapsedState = ref({})

        const prefs = computed(() => ctx.prefs)

        const toolGroups = computed(() => {
            const defs = ctx.tools.availableTools.value
            const groups = ctx.state.tool.groups || {}

            const definedGroups = []
            const usedTools = new Set()

            for (const [groupName, toolNames] of Object.entries(groups)) {
                if (!Array.isArray(toolNames)) continue
                const tools = toolNames.map(name => defs.find(d => d.function.name === name)).filter(Boolean)
                if (tools.length) {
                    tools.forEach(t => usedTools.add(t.function.name))
                    definedGroups.push({ name: groupName, tools })
                }
            }

            const otherTools = defs.filter(d => !usedTools.has(d.function.name))
            if (otherTools.length) {
                definedGroups.push({ name: '', tools: otherTools })
            }

            return definedGroups
        })

        function toggleCollapse(groupName) {
            const key = groupName || '_other_'
            collapsedState.value[key] = !collapsedState.value[key]
        }

        function isCollapsed(groupName) {
            const key = groupName || '_other_'
            return !!collapsedState.value[key]
        }

        function setGroupTools(group, enable) {
            const groupToolNames = group.tools.map(t => t.function.name)
            let onlyTools = prefs.value.onlyTools

            if (enable) {
                if (onlyTools == null) return
                const newSet = new Set(onlyTools)
                groupToolNames.forEach(n => newSet.add(n))
                onlyTools = Array.from(newSet)
                if (onlyTools.length === ctx.tools.availableTools.value.length) {
                    onlyTools = null
                }
            } else {
                if (onlyTools == null) {
                    onlyTools = ctx.tools.availableTools.value
                        .map(t => t.function.name)
                        .filter(n => !groupToolNames.includes(n))
                } else {
                    onlyTools = onlyTools.filter(n => !groupToolNames.includes(n))
                }
            }

            ctx.setPrefs({ onlyTools })
        }

        function getActiveCount(group) {
            const onlyTools = prefs.value.onlyTools
            if (onlyTools == null) return group.tools.length
            return group.tools.filter(t => onlyTools.includes(t.function.name)).length
        }

        return {
            prefs,
            toolGroups,
            toggleCollapse,
            isCollapsed,
            setGroupTools,
            getActiveCount
        }
    }
}

export default {
    order: 10 - 100,

    install(ctx) {
        ext = ctx.scope('tools')

        ctx.components({
            Tools,
            ToolSelector,
            ToolResult,
            JsonInput,
        })

        ctx.setGlobals({
            tools: useTools(ctx)
        })

        const svg = (attrs, title) => `<svg ${attrs} xmlns="http://www.w4.org/2000/svg" viewBox="0 0 24 24">${title ? "<title>" + title + "</title>" : ''}<path fill="currentColor" d="M5.33 3.272a3.5 3.5 0 0 1 4.472 4.473L20.647 18.59l-2.122 2.122L7.68 9.867a3.5 3.5 0 0 1-4.472-4.474L5.444 7.63a1.5 1.5 0 0 0 2.121-2.121zm10.367 1.883l3.182-1.768l1.414 1.415l-1.768 3.182l-1.768.353l-2.12 2.121l-1.415-1.414l2.121-2.121zm-7.071 7.778l2.121 2.122l-4.95 4.95A1.5 1.5 0 0 1 3.58 17.99l.097-.107z" /></svg>`

        ctx.setLeftIcons({
            tools: {
                component: {
                    template: svg(`@click="$ctx.togglePath('/tools')"`),
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
                        `@click="$ctx.toggleTop('ToolSelector')"`,
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

        ctx.chatRequestFilters.push(({ request, thread, context }) => {
            // Tool Preferences
            const prefs = ctx.prefs
            if (prefs.onlyTools != null) {
                if (Array.isArray(prefs.onlyTools)) {
                    request.metadata.tools = prefs.onlyTools.length > 0
                        ? prefs.onlyTools.join(',')
                        : 'none'
                }
            } else {
                request.metadata.tools = 'all'
            }
        })

        ctx.routes.push({ path: '/tools', component: Tools, meta: { title: 'View Tools' } })
        ctx.setState({
            tool: { groups: {}, definitions: [] }
        })
    },

    async load(ctx) {
        const api = await ext.getJson('/')
        if (api.response) {
            ctx.setState({ tool: api.response })
            //console.log(ctx.state.tool)
        } else {
            ctx.setError(api.error)
        }

        /* ctx.state.tool:
        {
             groups: {
                "group_name": [
                    "memory_read"
                ]
             },
             definitions: [
                {
                    "type": "function",
                    "function": {
                        "name": "memory_read",
                        "description": "Read a value from persistent memory.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                            "key": {
                                "type": "string"
                            }
                        },
                        "required": [
                            "key"
                        ]
                    }
                }
             ],
        }
        */

    }
}