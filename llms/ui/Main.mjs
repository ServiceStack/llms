import { ref, computed, nextTick, watch, onMounted, provide, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useFormatters } from '@servicestack/vue'
import { useThreadStore } from './threadStore.mjs'
import { addCopyButtons, formatCost, statsTitle, fetchCacheInfos } from './utils.mjs'
import { renderMarkdown } from './markdown.mjs'
import ChatPrompt, { useChatPrompt } from './ChatPrompt.mjs'
import SignIn from './SignIn.mjs'
import OAuthSignIn from './OAuthSignIn.mjs'
import Avatar from './Avatar.mjs'
import { useSettings } from "./SettingsDialog.mjs"
import Welcome from './Welcome.mjs'

const { humanifyMs, humanifyNumber } = useFormatters()

const TopBar = {
    template: `
        <div class="flex space-x-2">
            <div v-for="(ext, index) in extensions" :key="ext.id" class="relative flex items-center justify-center">
                <component :is="ext.topBarIcon" 
                    class="size-7 p-1 cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 block"
                    :class="{ 'bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded' : ext.isActive($layout.top) }" 
                    @mouseenter="tooltip = ext.name"
                    @mouseleave="tooltip = ''"
                    />
                <div v-if="tooltip === ext.name" 
                    class="absolute top-full mt-2 px-2 py-1 text-xs text-white bg-gray-900 dark:bg-gray-800 rounded shadow-md z-50 whitespace-nowrap pointer-events-none"
                    :class="index <= extensions.length - 1 ? 'right-0' : 'left-1/2 -translate-x-1/2'">
                    {{ext.name}}
                </div>    
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const tooltip = ref('')
        const extensions = computed(() => ctx.extensions.filter(x => x.topBarIcon))
        return {
            extensions,
            tooltip,
        }
    }
}

const TopPanel = {
    template: `
        <component v-if="component" :is="component" />
    `,
    setup() {
        const ctx = inject('ctx')
        const component = computed(() => ctx.component(ctx.layout.top))
        return {
            component,
        }
    }
}

export default {
    components: {
        TopBar,
        TopPanel,
        ChatPrompt,
        SignIn,
        OAuthSignIn,
        Avatar,
        Welcome,
    },
    template: `
        <div class="flex flex-col h-full w-full">
            <!-- Header with model selectors -->
            <div v-if="$ai.hasAccess" 
                :class="!$ai.isSidebarOpen ? 'pl-6' : ''"
                class="flex items-center border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 w-full min-h-16">
                <div class="flex flex-wrap items-center justify-between w-full">
                    <ModelSelector :models="models" v-model="selectedModel" @updated="configUpdated" />

                    <div class="flex items-center space-x-2 pl-4">
                        <TopBar />
                        <Avatar />
                    </div>
                </div>
            </div>

            <TopPanel />

            <!-- Messages Area -->
            <div class="flex-1 overflow-y-auto" ref="messagesContainer">
                <div class="mx-auto max-w-6xl px-4 py-6">
                    <div v-if="!$ai.hasAccess">
                        <OAuthSignIn v-if="$ai.authType === 'oauth'" @done="$ai.signIn($event)" />
                        <SignIn v-else @done="$ai.signIn($event)" />
                    </div>
                    <!-- Welcome message when no thread is selected -->
                    <div v-else-if="!currentThread" class="text-center py-12">
                        <Welcome />

                        <!-- Chat input for new conversation -->
                        <!-- Moved to bottom input area -->
                        <div class="h-2"></div>

                        <!-- Export/Import buttons -->
                        <div class="mt-2 flex space-x-3 justify-center items-center">
                            <button type="button"
                                @click="(e) => e.altKey ? exportRequests() : exportThreads()"
                                :disabled="isExporting"
                                :title="'Export ' + threads?.threads?.value?.length + ' conversations'"
                                class="inline-flex items-center px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <svg v-if="!isExporting" class="size-5 mr-1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                                    <path fill="currentColor" d="m12 16l-5-5l1.4-1.45l2.6 2.6V4h2v8.15l2.6-2.6L17 11zm-6 4q-.825 0-1.412-.587T4 18v-3h2v3h12v-3h2v3q0 .825-.587 1.413T18 20z"></path>
                                </svg>
                                <svg v-else class="size-5 mr-1 animate-spin" fill="none" viewBox="0 0 24 24">
                                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                {{ isExporting ? 'Exporting...' : 'Export' }}
                            </button>

                            <button type="button"
                                @click="triggerImport"
                                :disabled="isImporting"
                                title="Import conversations from JSON file"
                                class="inline-flex items-center px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <svg v-if="!isImporting" class="size-5 mr-1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                                    <path fill="currentColor" d="m14 12l-4-4v3H2v2h8v3m10 2V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v3h2V6h12v12H6v-3H4v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2"/>
                                </svg>
                                <svg v-else class="size-5 mr-1 animate-spin" fill="none" viewBox="0 0 24 24">
                                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                </svg>
                                {{ isImporting ? 'Importing...' : 'Import' }}
                            </button>

                            <!-- Hidden file input for import -->
                            <input
                                ref="fileInput"
                                type="file"
                                accept=".json"
                                @change="handleFileImport"
                                class="hidden"
                            />

                            <DarkModeToggle />

                        </div>

                    </div>

                    <!-- Messages -->
                    <div v-else class="space-y-6">
                        <div
                            v-for="message in currentThread.messages"
                            :key="message.id"
                            class="flex items-start space-x-3 group"
                            :class="message.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''"
                        >
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0 flex flex-col justify-center">
                                <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium"
                                     :class="message.role === 'user'
                                        ? 'bg-blue-100 dark:bg-blue-900 text-gray-900 dark:text-gray-100 border border-blue-200 dark:border-blue-700'
                                        : 'bg-gray-600 dark:bg-gray-500 text-white'"
                                >
                                    {{ message.role === 'user' ? 'U' : 'AI' }}
                                </div>

                                <!-- Delete button (shown on hover) -->
                                <button type="button" @click.stop="threads.deleteMessageFromThread(currentThread.id, message.id)"
                                    class="mx-auto opacity-0 group-hover:opacity-100 mt-2 rounded text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 transition-all"
                                    title="Delete message">
                                    <svg class="size-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                                    </svg>
                                </button>
                            </div>

                            <!-- Message bubble -->
                            <div
                                class="message rounded-lg px-4 py-3 relative group"
                                :class="message.role === 'user'
                                    ? 'bg-blue-100 dark:bg-blue-900 text-gray-900 dark:text-gray-100 border border-blue-200 dark:border-blue-700'
                                    : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700'"
                            >
                                <!-- Copy button in top right corner -->
                                <button
                                    type="button"
                                    @click="copyMessageContent(message)"
                                    class="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200 p-1 rounded hover:bg-black/10 dark:hover:bg-white/10 focus:outline-none focus:ring-0"
                                    :class="message.role === 'user' ? 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'"
                                    title="Copy message content"
                                >
                                    <svg v-if="copying === message" class="size-4 text-green-500 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                                    <svg v-else class="size-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
                                        <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
                                    </svg>
                                </button>

                                <div
                                    v-if="message.role === 'assistant'"
                                    v-html="renderMarkdown(message.content)"
                                    class="prose prose-sm max-w-none dark:prose-invert"
                                ></div>

                                <!-- Collapsible reasoning section -->
                                <div v-if="message.role === 'assistant' && message.reasoning" class="mt-2">
                                    <button type="button" @click="toggleReasoning(message.id)" class="text-xs text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 flex items-center space-x-1">
                                        <svg class="w-3 h-3" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" :class="isReasoningExpanded(message.id) ? 'transform rotate-90' : ''"><path fill="currentColor" d="M7 5l6 5l-6 5z"/></svg>
                                        <span>{{ isReasoningExpanded(message.id) ? 'Hide reasoning' : 'Show reasoning' }}</span>
                                    </button>
                                    <div v-if="isReasoningExpanded(message.id)" class="mt-2 rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-2">
                                        <div v-if="typeof message.reasoning === 'string'" v-html="renderMarkdown(message.reasoning)" class="prose prose-xs max-w-none dark:prose-invert"></div>
                                        <pre v-else class="text-xs whitespace-pre-wrap overflow-x-auto text-gray-900 dark:text-gray-100">{{ formatReasoning(message.reasoning) }}</pre>
                                    </div>
                                </div>

                                <!-- User Message with separate attachments -->
                                <div v-if="message.role !== 'assistant'">
                                    <div v-html="renderMarkdown(message.content)" class="prose prose-sm max-w-none dark:prose-invert break-words"></div>
                                    
                                    <!-- Attachments Grid -->
                                    <div v-if="hasAttachments(message)" class="mt-2 flex flex-wrap gap-2">
                                        <template v-for="(part, i) in getAttachments(message)" :key="i">
                                            <!-- Image -->
                                            <div v-if="part.type === 'image_url'" class="group relative cursor-pointer" @click="openLightbox(part.image_url.url)">
                                                <img :src="part.image_url.url" class="max-w-[400px] max-h-96 rounded-lg border border-gray-200 dark:border-gray-700 object-contain bg-gray-50 dark:bg-gray-900 shadow-sm transition-transform hover:scale-[1.02]" />
                                            </div>
                                            <!-- Audio -->
                                            <div v-else-if="part.type === 'input_audio'" class="flex items-center gap-2 p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
                                                <svg class="w-5 h-5 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
                                                <audio controls :src="part.input_audio.data" class="h-8 w-48"></audio>
                                            </div>
                                            <!-- File -->
                                            <a v-else-if="part.type === 'file'" :href="part.file.file_data" target="_blank" 
                                            class="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-sm text-blue-600 dark:text-blue-400 hover:underline">
                                                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>
                                                <span class="max-w-xs truncate">{{ part.file.filename || 'Attachment' }}</span>
                                            </a>
                                        </template>
                                    </div>
                                </div>

                                <div class="mt-2 text-xs opacity-70">
                                    <span>{{ formatTime(message.timestamp) }}</span>
                                    <span v-if="message.usage" :title="tokensTitle(message.usage)">
                                        &#8226;
                                        {{ humanifyNumber(message.usage.tokens) }} tokens
                                        <span v-if="message.usage.cost">&#183; {{ message.usage.cost }}</span>
                                        <span v-if="message.usage.duration"> in {{ humanifyMs(message.usage.duration) }}</span>
                                    </span>
                                </div>
                            </div>

                            <!-- Edit and Redo buttons (shown on hover for user messages, outside bubble) -->
                            <div v-if="message.role === 'user'" class="flex flex-col gap-2 opacity-0 group-hover:opacity-100 transition-opacity mt-1">
                                <button type="button" @click.stop="editMessage(message)"
                                    class="whitespace-nowrap text-xs px-2 py-1 rounded text-gray-400 dark:text-gray-500 hover:text-green-600 dark:hover:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/30 transition-all"
                                    title="Edit message">
                                    <svg class="size-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path>
                                    </svg>
                                    Edit
                                </button>
                                <button type="button" @click.stop="redoMessage(message)"
                                    class="whitespace-nowrap text-xs px-2 py-1 rounded text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-all"
                                    title="Redo message (clears all responses after this message and re-runs it)">
                                    <svg class="size-4 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                                    </svg>
                                    Redo
                                </button>
                            </div>
                        </div>

                        <div v-if="currentThread.stats && currentThread.stats.outputTokens" class="text-center text-gray-500 dark:text-gray-400 text-sm">
                            <span :title="statsTitle(currentThread.stats)">
                                {{ currentThread.stats.cost ? formatCost(currentThread.stats.cost) + '  for ' : '' }} {{ humanifyNumber(currentThread.stats.inputTokens) }} → {{ humanifyNumber(currentThread.stats.outputTokens) }} tokens over {{ currentThread.stats.requests }} request{{currentThread.stats.requests===1?'':'s'}} in {{ humanifyMs(currentThread.stats.duration) }}
                            </span>
                        </div>

                        <!-- Loading indicator -->
                        <div v-if="isGenerating" class="flex items-start space-x-3 group">
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0">
                                <div class="w-8 h-8 rounded-full bg-gray-600 dark:bg-gray-500 text-white flex items-center justify-center text-sm font-medium">
                                    AI
                                </div>
                            </div>

                            <!-- Loading bubble -->
                            <div class="rounded-lg px-4 py-3 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
                                <div class="flex space-x-1">
                                    <div class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce"></div>
                                    <div class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style="animation-delay: 0.1s"></div>
                                    <div class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style="animation-delay: 0.2s"></div>
                                </div>
                            </div>

                            <!-- Cancel button -->
                            <button type="button" @click="cancelRequest"
                                class="px-3 py-1 rounded text-sm text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 border border-transparent hover:border-red-300 dark:hover:border-red-600 transition-all"
                                title="Cancel request">
                                cancel
                            </button>
                        </div>

                        <!-- Error message bubble -->
                        <div v-if="errorStatus" class="flex items-start space-x-3">
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0">
                                <div class="w-8 h-8 rounded-full bg-red-600 dark:bg-red-500 text-white flex items-center justify-center text-sm font-medium">
                                    !
                                </div>
                            </div>

                            <!-- Error bubble -->
                            <div class="max-w-[85%] rounded-lg px-4 py-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-200 shadow-sm">
                                <div class="flex items-start space-x-2">
                                    <div class="flex-1 min-w-0">
                                        <div class="text-base font-medium mb-1">{{ errorStatus?.errorCode || 'Error' }}</div>
                                        <div v-if="errorStatus?.message" class="text-base mb-1">{{ errorStatus.message }}</div>
                                        <div v-if="errorStatus?.stackTrace" class="text-sm whitespace-pre-wrap break-words max-h-80 overflow-y-auto font-mono p-2 rounded bg-red-100 dark:bg-red-950/50">
                                            {{ errorStatus.stackTrace }}
                                        </div>
                                    </div>
                                    <button type="button"
                                        @click="errorStatus = null"
                                        class="text-red-400 dark:text-red-300 hover:text-red-600 dark:hover:text-red-100 flex-shrink-0"
                                    >
                                        <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                                        </svg>
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Input Area -->
            <div v-if="$ai.hasAccess" class="flex-shrink-0 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-6 py-4">
                <ChatPrompt :model="selectedModelObj" />
            </div>
            
            <!-- Lightbox -->
            <div v-if="lightboxUrl" class="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center p-4 cursor-pointer" @click="closeLightbox">
                <div class="relative max-w-full max-h-full">
                    <img :src="lightboxUrl" class="max-w-full max-h-[90vh] object-contain rounded-sm shadow-2xl" @click.stop />
                    <button type="button" @click="closeLightbox"
                        class="absolute -top-12 right-0 text-white/70 hover:text-white p-2 rounded-full bg-white/10 hover:bg-white/20 transition-colors"
                        title="Close">
                        <svg class="size-8" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const models = ctx.state.models
        const config = ctx.state.config
        const router = useRouter()
        const route = useRoute()
        const threads = useThreadStore()
        const { currentThread } = threads
        const chatPrompt = useChatPrompt()
        const chatSettings = useSettings()
        const {
            errorStatus,
            isGenerating,
        } = chatPrompt
        provide('threads', threads)
        provide('chatPrompt', chatPrompt)
        provide('chatSettings', chatSettings)

        const prefs = ctx.getPrefs()

        const selectedModel = ref(prefs.model || config.defaults.text.model || '')
        const selectedModelObj = computed(() => {
            if (!selectedModel.value || !models) return null
            return models.find(m => m.name === selectedModel.value) || models.find(m => m.id === selectedModel.value)
        })
        const messagesContainer = ref(null)
        const isExporting = ref(false)
        const isImporting = ref(false)
        const fileInput = ref(null)
        const copying = ref(null)
        const lightboxUrl = ref(null)

        const openLightbox = (url) => {
            lightboxUrl.value = url
        }
        const closeLightbox = () => {
            lightboxUrl.value = null
        }

        // Auto-scroll to bottom when new messages arrive
        const scrollToBottom = async () => {
            await nextTick()
            if (messagesContainer.value) {
                messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
            }
        }

        // Watch for new messages and scroll
        watch(() => currentThread.value?.messages?.length, scrollToBottom)

        // Watch for route changes and load the appropriate thread
        watch(() => route.params.id, async (newId) => {
            const thread = await threads.setCurrentThreadFromRoute(newId, router)

            // If the selected thread specifies a model and it's available, switch to it
            if (thread?.model && Array.isArray(models) && models.includes(thread.model)) {
                selectedModel.value = thread.model
            }

            if (!newId) {
                chatPrompt.reset()
            }
            nextTick(addCopyButtons)
        }, { immediate: true })

        watch(() => [selectedModel.value], () => {
            ctx.setPrefs({
                model: selectedModel.value,
            })
        })

        async function exportThreads() {
            if (isExporting.value) return

            isExporting.value = true
            try {
                // Load all threads from IndexedDB
                await threads.loadThreads()
                const allThreads = threads.threads.value

                // Create export data with metadata
                const exportData = {
                    exportedAt: new Date().toISOString(),
                    version: '1.0',
                    source: 'llmspy',
                    threadCount: allThreads.length,
                    threads: allThreads
                }

                // Create and download JSON file
                const jsonString = JSON.stringify(exportData, null, 2)
                const blob = new Blob([jsonString], { type: 'application/json' })
                const url = URL.createObjectURL(blob)

                const link = document.createElement('a')
                link.href = url
                link.download = `llmsthreads-export-${new Date().toISOString().split('T')[0]}.json`
                document.body.appendChild(link)
                link.click()
                document.body.removeChild(link)
                URL.revokeObjectURL(url)

            } catch (error) {
                console.error('Failed to export threads:', error)
                alert('Failed to export threads: ' + error.message)
            } finally {
                isExporting.value = false
            }
        }

        async function exportRequests() {
            if (isExporting.value) return

            isExporting.value = true
            try {
                // Load all threads from IndexedDB
                const allRequests = await threads.getAllRequests()

                // Create export data with metadata
                const exportData = {
                    exportedAt: new Date().toISOString(),
                    version: '1.0',
                    source: 'llmspy',
                    requestsCount: allRequests.length,
                    requests: allRequests
                }

                // Create and download JSON file
                const jsonString = JSON.stringify(exportData, null, 2)
                const blob = new Blob([jsonString], { type: 'application/json' })
                const url = URL.createObjectURL(blob)

                const link = document.createElement('a')
                link.href = url
                link.download = `llmsrequests-export-${new Date().toISOString().split('T')[0]}.json`
                document.body.appendChild(link)
                link.click()
                document.body.removeChild(link)
                URL.revokeObjectURL(url)

            } catch (error) {
                console.error('Failed to export requests:', error)
                alert('Failed to export requests: ' + error.message)
            } finally {
                isExporting.value = false
            }
        }

        function triggerImport() {
            if (isImporting.value) return
            fileInput.value?.click()
        }

        async function handleFileImport(event) {
            const file = event.target.files?.[0]
            if (!file) return

            isImporting.value = true
            var importType = 'threads'
            try {
                const text = await file.text()
                const importData = JSON.parse(text)
                importType = importData.threads
                    ? 'threads'
                    : importData.requests
                        ? 'requests'
                        : 'unknown'

                // Import threads one by one
                let importedCount = 0
                let existingCount = 0

                const db = await threads.initDB()

                if (importData.threads) {
                    if (!Array.isArray(importData.threads)) {
                        throw new Error('Invalid import file: missing or invalid threads array')
                    }

                    const threadIds = new Set(await threads.getAllThreadIds())

                    for (const threadData of importData.threads) {
                        if (!threadData.id) {
                            console.warn('Skipping thread without ID:', threadData)
                            continue
                        }

                        try {
                            // Check if thread already exists
                            const existingThread = threadIds.has(threadData.id)
                            if (existingThread) {
                                existingCount++
                            } else {
                                // Add new thread directly to IndexedDB
                                const tx = db.transaction(['threads'], 'readwrite')
                                await tx.objectStore('threads').add(threadData)
                                await tx.complete
                                importedCount++
                            }
                        } catch (error) {
                            console.error('Failed to import thread:', threadData.id, error)
                        }
                    }

                    // Reload threads to reflect changes
                    await threads.loadThreads()

                    alert(`Import completed!\nNew threads: ${importedCount}\nExisting threads: ${existingCount}`)
                }
                if (importData.requests) {
                    if (!Array.isArray(importData.requests)) {
                        throw new Error('Invalid import file: missing or invalid requests array')
                    }

                    const requestIds = new Set(await threads.getAllRequestIds())

                    for (const requestData of importData.requests) {
                        if (!requestData.id) {
                            console.warn('Skipping request without ID:', requestData)
                            continue
                        }

                        try {
                            // Check if request already exists
                            const existingRequest = requestIds.has(requestData.id)
                            if (existingRequest) {
                                existingCount++
                            } else {
                                // Add new request directly to IndexedDB
                                const db = await threads.initDB()
                                const tx = db.transaction(['requests'], 'readwrite')
                                await tx.objectStore('requests').add(requestData)
                                await tx.complete
                                importedCount++
                            }
                        } catch (error) {
                            console.error('Failed to import request:', requestData.id, error)
                        }
                    }

                    alert(`Import completed!\nNew requests: ${importedCount}\nExisting requests: ${existingCount}`)
                }

            } catch (error) {
                console.error('Failed to import ' + importType + ':', error)
                alert('Failed to import ' + importType + ': ' + error.message)
            } finally {
                isImporting.value = false
                // Clear the file input
                if (fileInput.value) {
                    fileInput.value.value = ''
                }
            }
        }

        function configUpdated() {
            console.log('configUpdated', selectedModel.value, models.length, models.includes(selectedModel.value))
            if (selectedModel.value && !models.includes(selectedModel.value)) {
                selectedModel.value = config.defaults.text.model || ''
            }
        }

        // Format timestamp
        const formatTime = (timestamp) => {
            return new Date(timestamp).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit'
            })
        }

        // Reasoning collapse state and helpers
        const expandedReasoning = ref(new Set())
        const isReasoningExpanded = (id) => expandedReasoning.value.has(id)
        const toggleReasoning = (id) => {
            const s = new Set(expandedReasoning.value)
            if (s.has(id)) {
                s.delete(id)
            } else {
                s.add(id)
            }
            expandedReasoning.value = s
        }
        const formatReasoning = (r) => typeof r === 'string' ? r : JSON.stringify(r, null, 2)

        const copyMessageContent = async (message) => {
            let content = ''
            if (Array.isArray(message.content)) {
                content = message.content.map(part => {
                    if (part.type === 'text') return part.text
                    if (part.type === 'image_url') {
                        const name = part.image_url.url.split('/').pop() || 'image'
                        return `\n![${name}](${part.image_url.url})\n`
                    }
                    if (part.type === 'input_audio') {
                        const name = part.input_audio.data.split('/').pop() || 'audio'
                        return `\n[${name}](${part.input_audio.data})\n`
                    }
                    if (part.type === 'file') {
                        const name = part.file.filename || part.file.file_data.split('/').pop() || 'file'
                        return `\n[${name}](${part.file.file_data})`
                    }
                    return ''
                }).join('\n')
            } else {
                content = message.content
            }

            try {
                copying.value = message
                await navigator.clipboard.writeText(content)
                // Could add a toast notification here if desired
            } catch (err) {
                console.error('Failed to copy message content:', err)
                // Fallback for older browsers
                const textArea = document.createElement('textarea')
                textArea.value = content
                document.body.appendChild(textArea)
                textArea.select()
                document.execCommand('copy')
                document.body.removeChild(textArea)
            }
            setTimeout(() => { copying.value = null }, 2000)
        }

        const getAttachments = (message) => {
            if (!Array.isArray(message.content)) return []
            return message.content.filter(c => c.type === 'image_url' || c.type === 'input_audio' || c.type === 'file')
        }
        const hasAttachments = (message) => getAttachments(message).length > 0

        // Helper to extract content and files from message
        const extractMessageState = async (message) => {
            let text = ''
            let files = []
            const getCacheInfos = []

            if (Array.isArray(message.content)) {
                for (const part of message.content) {
                    if (part.type === 'text') {
                        text += part.text
                    } else if (part.type === 'image_url') {
                        const url = part.image_url.url
                        const name = url.split('/').pop() || 'image'
                        files.push({ name, url, type: 'image/png' }) // Assume image
                        getCacheInfos.push(url)
                    } else if (part.type === 'input_audio') {
                        const url = part.input_audio.data
                        const name = url.split('/').pop() || 'audio'
                        files.push({ name, url, type: 'audio/wav' }) // Assume audio
                        getCacheInfos.push(url)
                    } else if (part.type === 'file') {
                        const url = part.file.file_data
                        const name = part.file.filename || url.split('/').pop() || 'file'
                        files.push({ name, url })
                        getCacheInfos.push(url)
                    }
                }
            } else {
                text = message.content
            }

            const infos = await fetchCacheInfos(getCacheInfos)
            // replace name with info.name
            for (let i = 0; i < files.length; i++) {
                const url = files[i]?.url
                const info = infos[url]
                if (info) {
                    files[i].name = info.name
                }
            }

            return { text, files }
        }

        // Redo a user message (clear all messages after this one and re-run)
        const redoMessage = async (message) => {
            if (!currentThread.value || message.role !== 'user') return

            try {
                const threadId = currentThread.value.id

                // Clear all messages after this one
                await threads.redoMessageFromThread(threadId, message.id)

                const state = await extractMessageState(message)

                // Set the message text in the chat prompt
                chatPrompt.messageText.value = state.text

                // Restore attached files
                chatPrompt.attachedFiles.value = state.files

                // Trigger send by simulating the send action
                // We'll use a small delay to ensure the UI updates
                await nextTick()

                // Find the send button and click it
                const sendButton = document.querySelector('button[title*="Send"]')
                if (sendButton && !sendButton.disabled) {
                    sendButton.click()
                }
            } catch (error) {
                console.error('Failed to redo message:', error)
                errorStatus.value = {
                    errorCode: 'Error',
                    message: 'Failed to redo message: ' + error.message,
                    stackTrace: null
                }
            }
        }

        // Edit a user message
        const editMessage = async (message) => {
            if (!currentThread.value || message.role !== 'user') return

            // set the message in the input box
            const state = await extractMessageState(message)
            chatPrompt.messageText.value = state.text
            chatPrompt.attachedFiles.value = state.files
            chatPrompt.editingMessageId.value = message.id

            // Focus the textarea
            nextTick(() => {
                const textarea = document.querySelector('textarea')
                if (textarea) {
                    textarea.focus()
                    // Set cursor to end
                    textarea.selectionStart = textarea.selectionEnd = textarea.value.length
                }
            })
        }

        // Cancel pending request
        const cancelRequest = () => {
            chatPrompt.cancel()
        }

        function tokensTitle(usage) {
            let title = []
            if (usage.tokens && usage.price) {
                const msg = parseFloat(usage.price) > 0
                    ? `${usage.tokens} tokens @ ${usage.price} = ${tokenCost(usage.price, usage.tokens)}`
                    : `${usage.tokens} tokens`
                const duration = usage.duration ? ` in ${usage.duration}ms` : ''
                title.push(msg + duration)
            }
            return title.join('\n')
        }
        const numFmt = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 6 })
        function tokenCost(price, tokens) {
            if (!price || !tokens) return ''
            return numFmt.format(parseFloat(price) * tokens)
        }

        onMounted(() => {
            setTimeout(addCopyButtons, 1)
        })

        return {
            config,
            models,
            threads,
            isGenerating,
            currentThread,
            selectedModel,
            selectedModelObj,
            messagesContainer,
            errorStatus,
            copying,
            formatTime,
            renderMarkdown,
            isReasoningExpanded,
            toggleReasoning,
            formatReasoning,
            copyMessageContent,
            redoMessage,
            editMessage,
            cancelRequest,
            configUpdated,
            exportThreads,
            exportRequests,
            isExporting,
            triggerImport,
            handleFileImport,
            isImporting,
            fileInput,
            tokensTitle,
            humanifyMs,
            humanifyNumber,
            formatCost,
            formatCost,
            statsTitle,
            getAttachments,
            hasAttachments,
            lightboxUrl,
            openLightbox,
            closeLightbox,
        }
    }
}
