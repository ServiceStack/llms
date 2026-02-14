import { ref, onMounted, onUnmounted, computed, watch, nextTick, inject } from 'vue'
import { Terminal } from 'xterm'

let ext

const BrowserPage = {
    template: `
    <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100">
        <!-- Header Bar -->
        <div class="flex items-center gap-3 px-4 py-2 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
            <div class="flex gap-1">
                <button type="button" @click="goBack" :disabled="!isRunning" 
                    class="p-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Back">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="currentColor" d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
                </button>
                <button type="button" @click="goForward" :disabled="!isRunning"
                    class="p-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Forward">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="currentColor" d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z"/></svg>
                </button>
                <button type="button" @click="reload" :disabled="!isRunning"
                    class="p-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Reload">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="currentColor" d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
                </button>
            </div>
            <div class="flex-1 flex items-center gap-2 px-1 bg-gray-100 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <div class="ml-2 w-2 h-2 rounded-full flex-shrink-0" :class="isRunning ? 'bg-green-500' : 'bg-gray-400'" :title="isRunning ? 'Browser running' : 'Browser stopped'"></div>
                <input type="text" v-model="urlInput" @keyup.enter="navigate" @focus="urlFocused = true" @blur="urlFocused = false" placeholder="Enter URL..."
                    class="flex-1 bg-transparent border-none text-sm outline-none text-gray-900 dark:text-gray-100 placeholder-gray-400" spellcheck="false" />
                <button type="button" @click="navigate" class="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-md transition-colors">Go</button>
            </div>
            <div class="flex gap-1">
                <button type="button" @click="saveState" :disabled="!isRunning"
                    class="p-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Save Session">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="currentColor" d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/></svg>
                </button>
                <button type="button" @click="closeBrowser" :disabled="!isRunning"
                    class="p-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-red-100 dark:hover:bg-red-900/30 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Close Browser">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                </button>
            </div>
        </div>

        <!-- Main Content -->
        <div class="flex flex-1 overflow-hidden">
            <!-- Left Panel: Script Editor + Screenshot -->
            <div class="flex-1 flex flex-col overflow-hidden">
                <!-- Inline Script Editor -->
                <div v-if="showScriptEditor" class="flex-1 flex flex-col border-b border-gray-200 dark:border-gray-700 overflow-hidden" style="min-height: 200px; background: #1e1e2e">
                    <div class="flex justify-between items-center px-2 py-1 flex-shrink-0 text-gray-510 hover:bg-gray-100 dark:hover:bg-gray-700 border-l border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-900">
                        <div class="flex items-center gap-3">
                            <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ editingScript ? 'Edit Script' : 'New Script' }}</h3>
                            <input type="text" v-model="scriptName" placeholder="script-name.sh" class="bg-gray-50 dark:bg-gray-900 px-1 py-0 border border-gray-200 dark:border-gray-600 hover:border-blue-500 dark:hover:border-blue-500 rounded text-sm outline-none focus:border-blue-500 w-60" />
                        </div>
                        <div class="flex items-center gap-2">
                            <button type="button" @click="runScript(scriptName)" :disabled="!scriptName" class="px-3 py-1 text-xs bg-green-600 hover:bg-green-700 text-white rounded disabled:opacity-50 transition-colors" :title="hasSelection ? 'Run selected text (CTRL+Enter)' : 'Run (CTRL+Enter)'">▶ {{ hasSelection ? 'Run selected text' : 'Run' }}</button>
                            <button type="button" @click="saveScript" :disabled="!hasUnsavedChanges" class="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded disabled:opacity-50 transition-colors">Save</button>
                            <button type="button" @click="closeScriptEditor" class="px-2 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-lg">&times;</button>
                        </div>
                    </div>
                    <div v-if="hasCodeMirror" ref="scriptEditorRef" id="scriptEditorRef" class="relative flex-1 w-full overflow-hidden" style="min-height: 0; max-width: calc(100vw - 323px)"></div>
                    <textarea v-else v-model="scriptContent" spellcheck="false" class="flex-1 w-full px-4 py-2 bg-gray-900 text-gray-100 font-mono text-sm border-none resize-none outline-none overflow-y-auto" style="min-height: 0"></textarea>
                    <!-- AI Prompt Bar -->
                    <div class="flex items-center gap-2 px-2 py-1.5 bg-gray-100 dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
                        <svg class="w-4 h-4 text-purple-500 flex-shrink-0" viewBox="0 0 24 24"><path fill="currentColor" d="M21.928 11.607c-.202-.488-.635-.605-.928-.633V8c0-1.103-.897-2-2-2h-6V4.61c.305-.274.5-.668.5-1.11a1.5 1.5 0 0 0-3 0c0 .442.195.836.5 1.11V6H5c-1.103 0-2 .897-2 2v2.997l-.082.006A1 1 0 0 0 1.99 12v2a1 1 0 0 0 1 1H3v5c0 1.103.897 2 2 2h14c1.103 0 2-.897 2-2v-5a1 1 0 0 0 1-1v-1.938a1.006 1.006 0 0 0-.072-.455M5 20V8h14l.001 3.996L19 12v2l.001.005.001 5.995z"/><ellipse cx="8.5" cy="13.5" rx="1.5" ry="2" fill="currentColor"/><ellipse cx="15.5" cy="13.5" rx="1.5" ry="2" fill="currentColor"/><path fill="currentColor" d="M11.998 17c-1.105 0-2.752-.624-2.752-.624a.246.246 0 0 0-.253.377C9.862 18.155 10.85 18.5 12 18.5s2.138-.345 3.007-1.747a.246.246 0 0 0-.253-.377s-1.647.624-2.756.624"/></svg>
                        <input type="text" v-model="aiPrompt" @keyup.enter="generateInline" :disabled="generating" :placeholder="hasExistingScript ? 'Describe changes to make...' : 'Describe what to automate...'"
                            class="flex-1 px-2 py-1 text-xs bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded outline-none focus:border-purple-500 dark:focus:border-purple-500 placeholder-gray-400 text-gray-900 dark:text-gray-100" />
                        <button type="button" @click="generateInline" :disabled="!aiPrompt || generating" class="px-2.5 py-1 text-xs bg-purple-600 hover:bg-purple-700 text-white rounded disabled:opacity-50 transition-colors whitespace-nowrap">
                            {{ generating ? 'Generating...' : 'AI' }}
                        </button>
                    </div>
                </div>

                <!-- Status Bar -->
                <div class="flex justify-between px-2 py-1.5 text-xs text-gray-500 bg-gray-100 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700">
                    <span class="truncate">{{ pageTitle || 'No page loaded' }}</span>
                    <span v-if="lastUpdate">Last update: {{ timeSinceUpdate }}</span>
                </div>

                <!-- Screenshot Area -->
                <div class="relative flex items-center justify-center bg-black overflow-hidden cursor-crosshair" :class="showScriptEditor ? 'flex-shrink-0' : 'flex-1'" :style="showScriptEditor ? { maxHeight: '45%' } : {}" @click="handleScreenshotClick" ref="screenshotContainer">
                    <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
                        <div class="w-10 h-10 border-3 border-gray-600 border-t-blue-500 rounded-full animate-spin"></div>
                    </div>
                    <img v-if="screenshotUrl" :src="screenshotUrl" class="max-w-full max-h-full object-contain" @load="onScreenshotLoad" @error="onScreenshotError" ref="screenshotImg" />
                    <div v-else class="flex flex-col items-center gap-4 text-gray-500">
                        <svg class="w-16 h-16" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
                        <p class="text-sm">Enter a URL to start browsing</p>
                    </div>
                </div>

                <!-- Debug Log Panel -->
                <div class="flex flex-col border-t border-gray-200 dark:border-gray-700 bg-gray-950" :style="debugLogExpanded ? { height: debugLogHeight + 'px' } : {}">
                    <div v-if="debugLogExpanded" @mousedown="startDebugLogResize" class="h-1 cursor-ns-resize hover:bg-blue-500/50 bg-transparent transition-colors flex-shrink-0"></div>
                    <div @click="debugLogExpanded = !debugLogExpanded" class="flex justify-between items-center px-3 py-1 cursor-pointer hover:bg-gray-900 select-none flex-shrink-0 border-b border-gray-800">
                        <div class="flex items-center gap-2">
                            <span class="text-xs font-semibold text-gray-400">Debug Log</span>
                            <span v-if="debugLogCount" class="text-[10px] px-1.5 py-0.5 bg-gray-800 text-gray-500 rounded-full">{{ debugLogCount }}</span>
                        </div>
                        <div class="flex items-center gap-2">
                            <button v-if="debugLogExpanded && debugLogCount" type="button" @click.stop="clearDebugLog" class="text-[10px] px-1.5 py-0.5 text-gray-500 hover:text-gray-300 hover:bg-gray-800 rounded transition-colors">Clear</button>
                            <span class="text-xs text-gray-500">{{ debugLogExpanded ? '▼' : '▲' }}</span>
                        </div>
                    </div>
                    <div v-if="debugLogExpanded" ref="debugLogContainer" class="flex-1" style="min-height: 0"></div>
                </div>
            </div>

            <!-- Sidebar -->
            <div class="flex flex-col bg-gray-50 dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 transition-all overflow-hidden" :class="sidebarCollapsed ? 'w-8' : 'w-72'">
                <button type="button" @click="sidebarCollapsed = !sidebarCollapsed" class="w-full p-2 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 border-b border-gray-200 dark:border-gray-700 flex">
                    <span>{{ sidebarCollapsed ? '◀' : '▶' }}</span>
                </button>
                
                <div v-if="!sidebarCollapsed" class="flex-1 overflow-y-auto min-h-0">
                    <!-- Scripts Panel -->
                    <div class="border-b border-gray-200 dark:border-gray-700">
                        <div @click="scriptsExpanded = !scriptsExpanded" class="flex justify-between px-3 py-2 text-xs font-semibold text-gray-500 dark:text-gray-400 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 select-none">
                            <span>Scripts</span>
                            <span>{{ scriptsExpanded ? '▼' : '▶' }}</span>
                        </div>
                        <div v-if="scriptsExpanded" class="px-3 pb-3">
                            <div class="flex gap-2 mb-2">
                                <button type="button" @click="newScript" class="px-2 py-1 text-xs bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 rounded transition-colors">+ New</button>
                            </div>
                            <div class="flex flex-col gap-1 overflow-y-auto">
                                <div v-for="script in scripts" :key="script.name" class="flex gap-1 items-center text-sm">
                                    <button type="button" @click.stop="runScript(script.name)" class="opacity-60 hover:opacity-100 text-green-700 dark:text-green-600" :title="'Run ' + script.name">▶</button>
                                    <div @click.stop="editScript(script)" class="flex justify-between items-center w-full text-xs w-full">
                                        <div class="flex items-center gap-1 text-gray-600 dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300">
                                            {{ script.name }}
                                        </div>
                                        <div class="flex gap-1">
                                            <button type="button" @click.stop="deleteScript(script.name)" class="opacity-60 hover:opacity-100" title="Delete">
                                                <svg class="size-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"></path></svg>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                <div v-if="scripts.length === 0" class="py-2 text-center text-xs text-gray-400">No scripts yet</div>
                            </div>
                        </div>
                    </div>

                    <!-- Elements Panel -->
                    <div class="border-b border-gray-200 dark:border-gray-700">
                        <div @click="elementsExpanded = !elementsExpanded" class="flex justify-between px-3 py-2 text-xs font-semibold text-gray-500 dark:text-gray-400 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 select-none">
                            <span>Elements</span>
                            <span>{{ elementsExpanded ? '▼' : '▶' }}</span>
                        </div>
                        <div v-if="elementsExpanded" class="px-3 pb-3">
                            <div class="flex justify-between gap-2 mb-2">
                                <button type="button" @click="refreshSnapshot" :disabled="!isRunning" class="px-2 py-1 text-xs bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 rounded disabled:opacity-40 transition-colors">Refresh</button>
                                <button v-if="snapshot" type="button" class="p-1 text-gray-400 hover:text-gray-600" :title="snapshot.substring(0,500) + (snapshot.length > 500 ? '...' : '')" @click="copySnapshot">
                                    <svg v-if="copyingSnapshot" class="size-3.5 text-green-600 dark:text-green-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="m9.55 18l-5.7-5.7l1.425-1.425L9.55 15.15l9.175-9.175L20.15 7.4z"/></svg>
                                    <svg v-else xmlns="http://www.w3.org/2000/svg" class="size-3.5" viewBox="0 0 24 24"><path fill="currentColor" d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2m0 16H8V7h11z"></path></svg>
                                </button>
                            </div>
                            <div class="flex flex-col gap-1 overflow-y-auto">
                                <div v-for="el in visibleElements" :key="el.ref || el" @click="clickElement(el.ref || el)"
                                    class="flex gap-2 px-2 py-1.5 text-xs bg-gray-100 dark:bg-gray-700 rounded cursor-pointer hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors">
                                    <span class="text-blue-600 dark:text-blue-400 font-mono flex-shrink-0">{{ el.ref || el }}</span>
                                    <span class="text-gray-500 dark:text-gray-400 truncate">{{ el.desc || '' }}</span>
                                </div>
                                <button v-if="elements.length > elementsLimit" type="button" @click="elementsLimit += 50"
                                    class="mt-1 px-2 py-1 text-xs text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors">
                                    Load more ({{ elements.length - elementsLimit }} remaining)
                                </button>
                                <div v-if="elements.length === 0" class="py-2 text-center text-xs text-gray-400">No elements. Click Refresh.</div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>
        </div>

        <!-- Quick Actions Bar -->
        <div class="flex flex-wrap items-center gap-4 pl-1 pr-4 py-2 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700">
            <div class="flex-1 flex gap-2">
                <input type="text" v-model="typeText" @keyup.enter="sendType" placeholder="Type text..." :disabled="!isRunning"
                    class="flex-1 px-3 py-1 text-sm bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg outline-none focus:border-blue-500 disabled:opacity-40" />
                <button @click="sendType" :disabled="!isRunning || !typeText" class="px-3 py-1 text-sm bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-900/50 disabled:opacity-40 transition-colors">Send</button>
            </div>
            <div class="flex gap-1">
                <button v-for="key in ['Enter', 'Tab', 'Escape', '↑', '↓']" :key="key" @click="pressKey(key === '↑' ? 'ArrowUp' : key === '↓' ? 'ArrowDown' : key)" :disabled="!isRunning"
                    class="px-2 py-1 text-xs bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 rounded disabled:opacity-40 transition-colors">{{ key === 'Escape' ? 'Esc' : key }}</button>
            </div>
            <div class="flex gap-1">
                <button @click="scroll('up', 300)" :disabled="!isRunning" class="px-2 py-1 text-xs bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 rounded disabled:opacity-40 transition-colors">▲ Scroll</button>
                <button @click="scroll('down', 300)" :disabled="!isRunning" class="px-2 py-1 text-xs bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 rounded disabled:opacity-40 transition-colors">▼ Scroll</button>
            </div>
            <div class="flex items-center gap-2 text-xs text-gray-500">
                <label class="flex items-center gap-1 cursor-pointer">
                    <input type="checkbox" v-model="autoRefresh" class="rounded" />
                    <span>Auto</span>
                </label>
                <select v-model="refreshInterval" :disabled="!autoRefresh" class="pl-2 pr-4 py-1 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded text-xs">
                    <option :value="1000">1s</option>
                    <option :value="3000">3s</option>
                    <option :value="5000">5s</option>
                    <option :value="10000">10s</option>
                </select>
            </div>
        </div>

    </div>
    `,
    setup() {
        const appCtx = inject('ctx')  // Parent AppContext for raw API access

        // Helper to call browser endpoints at root /browser/ path with proper JSON body
        async function postBrowser(url, body = {}) {
            const res = await fetch(`/browser${url}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            })
            return res.json()
        }

        async function getBrowser(url) {
            const res = await fetch(`/browser${url}`)
            return res.json()
        }

        async function deleteBrowser(url) {
            const res = await fetch(`/browser${url}`, { method: 'DELETE' })
            return res.json()
        }

        const urlInput = ref('')
        const urlFocused = ref(false)  // Track focus to prevent overwriting user input
        const screenshotUrl = ref(null)
        const screenshotContainer = ref(null)
        const screenshotImg = ref(null)
        const loading = ref(false)
        const isRunning = ref(false)
        const pageTitle = ref('')
        const lastUpdate = ref(null)
        const autoRefresh = ref(true)
        const refreshInterval = ref(3000)
        const typeText = ref('')

        // Sidebar state
        const sidebarCollapsed = ref(false)
        const elementsExpanded = ref(true)
        const scriptsExpanded = ref(true)
        const elements = ref([])
        const snapshot = ref('')
        const copyingSnapshot = ref(false)
        const elementsLimit = ref(25)
        const visibleElements = computed(() => elements.value.slice(0, elementsLimit.value))
        const scripts = ref([])

        // Script editor
        const showScriptEditor = ref(false)
        const editingScript = ref(null)
        const scriptName = ref('')
        const scriptContent = ref('#!/bin/bash\nset -euo pipefail\n\n')
        const savedScriptContent = ref('')
        const scriptEditorRef = ref(null)
        const hasCodeMirror = typeof CodeMirror !== 'undefined'
        const hasSelection = ref(false)
        let cmEditor = null

        // AI inline prompt
        const aiPrompt = ref('')
        const generating = ref(false)
        const hasUnsavedChanges = computed(() => scriptName.value && scriptContent.value !== savedScriptContent.value)
        const defaultScript = '#!/bin/bash\nset -euo pipefail\n\n'
        const hasExistingScript = computed(() => {
            const s = scriptContent.value.trim()
            return s && s !== defaultScript.trim()
        })

        // Debug log
        const debugLogCount = ref(0)
        const debugLogExpanded = ref(true)
        const debugLogContainer = ref(null)
        const debugLogHeight = ref(200)
        let debugLogSinceId = 0
        let debugLogResizing = false
        let term = null

        function initTerminal() {
            if (term || !debugLogContainer.value) return
            term = new Terminal({
                convertEol: true,
                disableStdin: true,
                cursorBlink: false,
                cursorStyle: 'bar',
                cursorInactiveStyle: 'none',
                fontSize: 12,
                fontFamily: 'monospace',
                theme: { background: '#030712' },
                scrollback: 1000,
            })
            term.open(debugLogContainer.value)
            const fit = () => { try { term.resize(Math.floor(debugLogContainer.value.clientWidth / 7.2), Math.floor(debugLogContainer.value.clientHeight / 17)) } catch (e) { } }
            fit()
            new ResizeObserver(fit).observe(debugLogContainer.value)
        }

        function writeEntryToTerm(entry) {
            if (!term) return
            const ok = entry.ok ? '\x1b[32m' : '\x1b[31m'
            const reset = '\x1b[0m'
            const dim = '\x1b[90m'
            term.writeln(`${ok}$ ${reset}${entry.cmd} ${dim}${entry.ms}ms rc=${entry.rc}${reset}`)
            if (entry.stdout && entry.stdout.trim()) {
                for (const line of entry.stdout.trim().split('\n')) {
                    term.writeln(`  ${dim}${line}${reset}`)
                }
            }
            if (entry.stderr && entry.stderr.trim()) {
                for (const line of entry.stderr.trim().split('\n')) {
                    term.writeln(`  \x1b[31m${line}${reset}`)
                }
            }
        }

        function resizeEditor() {
            if (scriptEditorRef.value?.clientHeight) {
                console.log('setting size', scriptEditorRef.value.clientHeight)
                cmEditor.setSize('100%', scriptEditorRef.value.clientHeight)
            }
        }

        // CodeMirror editor initialization
        function initCodeMirror() {
            if (!hasCodeMirror || !scriptEditorRef.value || cmEditor) return
            cmEditor = CodeMirror(scriptEditorRef.value, {
                lineNumbers: true,
                styleActiveLine: true,
                matchBrackets: true,
                mode: 'shell',
                theme: 'ctp-mocha',
                value: scriptContent.value,
                tabSize: 4,
                indentUnit: 4,
                lineWrapping: false,
            })
            cmEditor.on('change', () => {
                scriptContent.value = cmEditor.getValue()
            })
            cmEditor.on('cursorActivity', () => {
                hasSelection.value = cmEditor.somethingSelected()
            })
            nextTick(resizeEditor)
        }

        function destroyCodeMirror() {
            if (cmEditor) {
                cmEditor.toTextArea?.()
                cmEditor = null
            }
            if (scriptEditorRef.value) {
                scriptEditorRef.value.innerHTML = ''
            }
        }

        watch(showScriptEditor, async (show) => {
            if (show && hasCodeMirror) {
                await nextTick()
                initCodeMirror()
            } else {
                destroyCodeMirror()
            }
        })

        // Sync scriptContent changes (e.g. from editScript) into CodeMirror
        watch(scriptContent, (val) => {
            if (cmEditor && cmEditor.getValue() !== val) {
                cmEditor.setValue(val)
            }
        })

        watch(debugLogExpanded, async (expanded) => {
            if (expanded) {
                debugLogSinceId = 0
                await nextTick()
                initTerminal()
                fetchDebugLog()
            } else {
                if (term) { term.dispose(); term = null }
            }
        })

        watch(debugLogHeight, () => {
            if (term && debugLogContainer.value) {
                try { term.resize(Math.floor(debugLogContainer.value.clientWidth / 7.2), Math.floor(debugLogContainer.value.clientHeight / 17)) } catch (e) { }
            }
        })

        function startDebugLogResize(e) {
            e.preventDefault()
            debugLogResizing = true
            const startY = e.clientY
            const startH = debugLogHeight.value
            function onMove(ev) {
                debugLogHeight.value = Math.max(100, Math.min(600, startH + (startY - ev.clientY)))
            }
            function onUp() {
                debugLogResizing = false
                document.removeEventListener('mousemove', onMove)
                document.removeEventListener('mouseup', onUp)
                nextTick(resizeEditor)
            }
            document.addEventListener('mousemove', onMove)
            document.addEventListener('mouseup', onUp)
        }

        let refreshTimer = null
        let statusTimer = null
        let tickTimer = null
        let debugTimer = null
        const tick = ref(0)  // Force re-evaluation of computed properties

        const timeSinceUpdate = computed(() => {
            tick.value  // Trigger re-evaluation when tick changes
            if (!lastUpdate.value) return ''
            const seconds = Math.floor((Date.now() - lastUpdate.value) / 1000)
            if (seconds < 60) return `${seconds}s ago`
            return `${Math.floor(seconds / 60)}m ago`
        })

        async function fetchStatus() {
            try {
                const res = await getBrowser('/snapshot')
                isRunning.value = !!res.running
                // Only update URL if input is not focused to prevent overwriting user input
                if (res.url && !urlFocused.value) urlInput.value = res.url
                if (res.title) pageTitle.value = res.title
                if (res.data?.snapshot) snapshot.value = res.data.snapshot
                // Update elements from snapshot refs
                const refs = res.data?.refs
                if (refs) {
                    elements.value = Object.entries(refs)
                        .sort(([a], [b]) => {
                            const na = parseInt(a.slice(1)) || 0
                            const nb = parseInt(b.slice(1)) || 0
                            return na - nb
                        })
                        .map(([key, val]) => ({
                            ref: `@${key}`,
                            desc: `${val.role || ''} "${val.name || ''}"`.trim()
                        }))
                }
            } catch (e) {
                isRunning.value = false
            }
        }

        function copySnapshot() {
            navigator.clipboard.writeText(snapshot.value)
            copyingSnapshot.value = true
            setTimeout(() => copyingSnapshot.value = false, 3000)
        }

        async function fetchScreenshot() {
            if (!isRunning.value) return
            loading.value = true
            try {
                const timestamp = Date.now()
                screenshotUrl.value = `/browser/screenshot?t=${timestamp}`
                lastUpdate.value = timestamp
            } catch (e) {
                console.error('Screenshot failed:', e)
            }
            loading.value = false
        }

        async function refreshSnapshot() {
            try {
                const res = await getBrowser('/snapshot?force=true')
                const refs = res.data?.refs
                if (refs) {
                    elements.value = Object.entries(refs)
                        .sort(([a], [b]) => {
                            const na = parseInt(a.slice(1)) || 0
                            const nb = parseInt(b.slice(1)) || 0
                            return na - nb
                        })
                        .map(([key, val]) => ({
                            ref: `@${key}`,
                            desc: `${val.role || ''} "${val.name || ''}"`.trim()
                        }))
                } else {
                    elements.value = []
                }
                elementsLimit.value = 50
                // Also update status fields from snapshot
                if (res.url && !urlFocused.value) urlInput.value = res.url
                if (res.title) pageTitle.value = res.title
                if (res.running !== undefined) isRunning.value = !!res.running
            } catch (e) {
                console.error('Snapshot failed:', e)
            }
        }

        async function fetchScripts() {
            try {
                const res = await getBrowser('/scripts')
                scripts.value = res.scripts || []
            } catch (e) {
                console.error('Failed to fetch scripts:', e)
            }
        }

        async function fetchDebugLog() {
            try {
                const res = await getBrowser(`/debug-log?since=${debugLogSinceId}`)
                if (res.entries && res.entries.length > 0) {
                    debugLogSinceId = res.entries[res.entries.length - 1].id
                    debugLogCount.value += res.entries.length
                    for (const entry of res.entries) {
                        writeEntryToTerm(entry)
                    }
                }
            } catch (e) {
                // ignore
            }
        }

        async function clearDebugLog() {
            try {
                await deleteBrowser('/debug-log')
                debugLogSinceId = 0
                debugLogCount.value = 0
                if (term) term.clear()
            } catch (e) {
                console.error('Failed to clear debug log:', e)
            }
        }

        async function navigate() {
            console.log('navigate() called, urlInput:', urlInput.value)
            if (!urlInput.value) return
            loading.value = true
            try {
                console.log('Calling POST /browser/open with url:', urlInput.value)
                const result = await postBrowser('/open', { url: urlInput.value })
                console.log('POST /browser/open result:', result)
                isRunning.value = true
                await fetchScreenshot()
                await refreshSnapshot()
            } catch (e) {
                console.error('Navigation failed:', e)
            }
            loading.value = false
        }

        async function goBack() {
            await postBrowser('/back', {})
            await fetchScreenshot()
        }

        async function goForward() {
            await postBrowser('/forward', {})
            await fetchScreenshot()
        }

        async function reload() {
            loading.value = true
            await postBrowser('/reload', {})
            await fetchScreenshot()
            loading.value = false
        }

        async function closeBrowser() {
            await postBrowser('/close', {})
            isRunning.value = false
            screenshotUrl.value = null
            elements.value = []
        }

        async function saveState() {
            await postBrowser('/state/save', {})
        }

        function handleScreenshotClick(e) {
            if (!isRunning.value || !screenshotImg.value) return

            const rect = screenshotImg.value.getBoundingClientRect()
            const scaleX = screenshotImg.value.naturalWidth / rect.width
            const scaleY = screenshotImg.value.naturalHeight / rect.height

            const x = Math.round((e.clientX - rect.left) * scaleX)
            const y = Math.round((e.clientY - rect.top) * scaleY)

            postBrowser('/click', { x, y }).then(() => {
                setTimeout(fetchScreenshot, 500)
            })
        }

        async function clickElement(ref) {
            await postBrowser('/click', { ref })
            setTimeout(fetchScreenshot, 500)
        }

        async function pressKey(key) {
            await postBrowser('/press', { key })
            setTimeout(fetchScreenshot, 300)
        }

        async function scroll(direction, amount) {
            await postBrowser('/scroll', { direction, amount })
            setTimeout(fetchScreenshot, 300)
        }

        async function sendType() {
            if (!typeText.value) return
            await postBrowser('/type', { text: typeText.value })
            typeText.value = ''
            setTimeout(fetchScreenshot, 300)
        }

        async function newScript() {
            const name = prompt('Enter script filename:', '')
            if (!name) return
            const filename = name.endsWith('.sh') ? name : name + '.sh'
            const content = '#!/bin/bash\nset -euo pipefail\n\n'
            try {
                await postBrowser('/scripts', { name: filename, content })
                await fetchScripts()
                editingScript.value = null
                scriptName.value = filename
                scriptContent.value = content
                savedScriptContent.value = content
                showScriptEditor.value = true
            } catch (e) {
                console.error('Failed to create script:', e)
            }
        }

        function closeScriptEditor() {
            if (scriptContent.value !== savedScriptContent.value) {
                const action = confirm('You have unsaved changes. Save before closing?')
                if (action) {
                    saveScript()
                }
            }
            showScriptEditor.value = false
        }

        async function editScript(script) {
            try {
                const res = await getBrowser(`/scripts/${script.name}`)
                editingScript.value = script
                scriptName.value = script.name
                scriptContent.value = res.content
                savedScriptContent.value = res.content
                showScriptEditor.value = true
            } catch (e) {
                console.error('Failed to load script:', e)
            }
        }

        async function saveScript() {
            try {
                await postBrowser('/scripts', {
                    name: scriptName.value,
                    content: scriptContent.value
                })
                savedScriptContent.value = scriptContent.value
                await fetchScripts()
            } catch (e) {
                console.error('Failed to save script:', e)
            }
        }

        async function deleteScript(name) {
            if (!confirm(`Delete script "${name}"?`)) return
            try {
                await deleteBrowser(`/scripts/${name}`)
                if (showScriptEditor.value && scriptName.value === name) {
                    showScriptEditor.value = false
                }
                await fetchScripts()
            } catch (e) {
                console.error('Failed to delete script:', e)
            }
        }

        async function runScript(name) {
            loading.value = true
            try {
                const selectedText = cmEditor ? cmEditor.getSelection() : ''
                // Auto-save when running the same script that's open in the editor
                if (showScriptEditor.value && scriptName.value === name && scriptContent.value) {
                    await postBrowser('/scripts', {
                        name: scriptName.value,
                        content: scriptContent.value
                    })
                    savedScriptContent.value = scriptContent.value
                    await fetchScripts()
                }
                const body = selectedText ? { content: selectedText } : {}
                const res = await postBrowser(`/scripts/${name}/run`, body)
                console.log('Script output:', res)
                await fetchScreenshot()
            } catch (e) {
                console.error('Failed to run script:', e)
            }
            loading.value = false
        }

        async function generateInline() {
            if (!aiPrompt.value || generating.value) return
            generating.value = true
            try {
                const existing = scriptContent.value
                const res = await postBrowser('/scripts/generate', {
                    prompt: aiPrompt.value,
                    name: scriptName.value || 'generated-script.sh',
                    existing_script: existing,
                })
                if (res.content) {
                    scriptContent.value = res.content
                }
                aiPrompt.value = ''
            } catch (e) {
                console.error('Failed to generate script:', e)
            }
            generating.value = false
        }

        function onScreenshotLoad() {
            loading.value = false
        }

        function onScreenshotError(e) {
            loading.value = false
            e.src = '/ext/browser/connecting.svg'
        }

        function startAutoRefresh() {
            if (refreshTimer) clearTimeout(refreshTimer)
            if (autoRefresh.value) {
                async function refreshLoop() {
                    if (isRunning.value) await fetchScreenshot()
                    if (autoRefresh.value) refreshTimer = setTimeout(refreshLoop, refreshInterval.value)
                }
                refreshTimer = setTimeout(refreshLoop, refreshInterval.value)
            }
        }

        watch([autoRefresh, refreshInterval], startAutoRefresh)

        function handleKeydown(e) {
            if (!showScriptEditor.value) return
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault()
                if (hasUnsavedChanges.value) {
                    saveScript()
                }
            } else if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault()
                if (scriptName.value) {
                    runScript(scriptName.value)
                }
            }
        }

        onMounted(() => {
            fetchStatus()
            fetchScripts()
            async function statusLoop() {
                await fetchStatus()
                statusTimer = setTimeout(statusLoop, 10000)
            }
            statusTimer = setTimeout(statusLoop, 1)
            function tickLoop() {
                tick.value++
                tickTimer = setTimeout(tickLoop, 1000)
            }
            tickTimer = setTimeout(tickLoop, 1)
            startAutoRefresh()
            // Initialize terminal and poll debug log alongside status
            nextTick(() => {
                initTerminal()
                async function debugLoop() {
                    await fetchDebugLog()
                    debugTimer = setTimeout(debugLoop, 2000)
                }
                fetchDebugLog()
                debugTimer = setTimeout(debugLoop, 1)
            })
            window.addEventListener('keydown', handleKeydown)
        })

        onUnmounted(() => {
            if (refreshTimer) clearTimeout(refreshTimer)
            if (statusTimer) clearTimeout(statusTimer)
            if (tickTimer) clearTimeout(tickTimer)
            if (debugTimer) clearTimeout(debugTimer)
            destroyCodeMirror()
            if (term) { term.dispose(); term = null }
            window.removeEventListener('keydown', handleKeydown)
        })

        return {
            urlInput, urlFocused, screenshotUrl, screenshotContainer, screenshotImg, loading,
            isRunning, pageTitle, lastUpdate, autoRefresh, refreshInterval,
            typeText, sidebarCollapsed, elementsExpanded, scriptsExpanded, snapshot, elements, elementsLimit, visibleElements,
            scripts, showScriptEditor, editingScript, scriptName, scriptContent, scriptEditorRef, hasCodeMirror, hasUnsavedChanges, hasSelection,
            aiPrompt, generating, hasExistingScript,
            debugLogCount, debugLogExpanded, debugLogContainer, debugLogHeight, startDebugLogResize,
            timeSinceUpdate,
            fetchStatus, fetchScreenshot, refreshSnapshot, navigate, goBack, goForward, copyingSnapshot, copySnapshot,
            reload, closeBrowser, saveState, handleScreenshotClick, clickElement,
            pressKey, scroll, sendType, newScript, closeScriptEditor, editScript, saveScript, deleteScript, runScript,
            generateInline, onScreenshotLoad, onScreenshotError,
            clearDebugLog,
        }
    }
}

export default {
    install(ctx) {
        ext = ctx.scope('browser')

        ctx.components({ BrowserPage })

        ctx.setLeftIcons({
            browser: {
                component: {
                    template: `<svg @click="$ctx.togglePath('/browser', { left:false })" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="4" d="M24 15a9 9 0 1 1 0 18a9 9 0 0 1 0-18m0 0h17.865M17 42.74L29.644 31M6 15.272l10.875 14.28M24 44c11.046 0 20-8.954 20-20S35.046 4 24 4S4 12.954 4 24s8.954 20 20 20"/></svg>`,
                },
                isActive({ path }) {
                    return path === '/browser'
                }
            }
        })

        ctx.routes.push({ path: '/browser', component: BrowserPage, meta: { title: 'Agent Browser' } })
    }
}