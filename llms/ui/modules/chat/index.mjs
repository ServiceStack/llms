
import { ref, watch, computed, nextTick, inject } from 'vue'
import { $$, createElement, lastRightPart, ApiResult, createErrorStatus } from "@servicestack/client"
import SettingsDialog, { useSettings } from './SettingsDialog.mjs'
import { ChatBody, LightboxImage, TypeText, TypeImage, TypeAudio, TypeFile, ViewType, ViewTypes, ViewToolTypes, TextViewer, ToolArguments, ToolOutput, MessageUsage, MessageReasoning } from './ChatBody.mjs'
import { AppContext } from '../../ctx.mjs'

const imageExts = 'png,webp,jpg,jpeg,gif,bmp,svg,tiff,ico'.split(',')
const audioExts = 'mp3,wav,ogg,flac,m4a,opus,webm'.split(',')

/* Example image generation request: https://openrouter.ai/docs/guides/overview/multimodal/image-generation
{
    "model": "google/gemini-2.5-flash-image-preview",
    "messages": [
        {
            "role": "user",
            "content": "Create a picture of a nano banana dish in a fancy restaurant with a Gemini theme"
        }
    ],
    "modalities": ["image", "text"],
    "image_config": {
        "aspect_ratio": "16:9"
    }
}
Example response:
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "I've generated a beautiful sunset image for you.",
        "images": [
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
            }
          }
        ]
      }
    }
  ]
}
*/
const imageAspectRatios = {
    '1024×1024': '1:1',
    '832×1248': '2:3',
    '1248×832': '3:2',
    '864×1184': '3:4',
    '1184×864': '4:3',
    '896×1152': '4:5',
    '1152×896': '5:4',
    '768×1344': '9:16',
    '1344×768': '16:9',
    '1536×672': '21:9',
}
// Reverse lookup
const imageRatioSizes = Object.entries(imageAspectRatios).reduce((acc, [key, value]) => {
    acc[value] = key
    return acc
}, {})

const svg = {
    clipboard: `<svg class="w-6 h-6" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none"><path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-1M8 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M8 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2m0 0h2a2 2 0 0 1 2 2v3m2 4H10m0 0l3-3m-3 3l3 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></g></svg>`,
    check: `<svg class="w-6 h-6 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>`,
}

function copyBlock(btn) {
    // console.log('copyBlock',btn)
    const label = btn.previousElementSibling
    const code = btn.parentElement.nextElementSibling
    label.classList.remove('hidden')
    label.innerHTML = 'copied'
    btn.classList.add('border-gray-600', 'bg-gray-700')
    btn.classList.remove('border-gray-700')
    btn.innerHTML = svg.check
    navigator.clipboard.writeText(code.innerText)
    setTimeout(() => {
        label.classList.add('hidden')
        label.innerHTML = ''
        btn.innerHTML = svg.clipboard
        btn.classList.remove('border-gray-600', 'bg-gray-700')
        btn.classList.add('border-gray-700')
    }, 2000)
}

function addCopyButtonToCodeBlocks(sel) {
    globalThis.copyBlock ??= copyBlock
    //console.log('addCopyButtonToCodeBlocks', sel, [...$$(sel)].length)

    $$(sel).forEach(code => {
        let pre = code.parentElement;
        if (pre.classList.contains('group')) return
        pre.classList.add('relative', 'group')

        const div = createElement('div', { attrs: { className: 'opacity-0 group-hover:opacity-100 transition-opacity duration-100 flex absolute right-2 -mt-1 select-none' } })
        const label = createElement('div', { attrs: { className: 'hidden font-sans p-1 px-2 mr-1 rounded-md border border-gray-600 bg-gray-700 text-gray-400' } })
        const btn = createElement('button', {
            attrs: {
                type: 'button',
                className: 'p-1 rounded-md border block text-gray-500 hover:text-gray-400 border-gray-700 hover:border-gray-600',
                onclick: 'copyBlock(this)'
            }
        })
        btn.innerHTML = svg.clipboard
        div.appendChild(label)
        div.appendChild(btn)
        pre.insertBefore(div, code)
    })
}

export function addCopyButtons() {
    addCopyButtonToCodeBlocks('.prose pre>code')
}

export function useChatPrompt(ctx) {
    const messageText = ref('')
    const promptHistory = ref([])
    const attachedFiles = ref([])
    const hasImage = () => attachedFiles.value.some(f => imageExts.includes(lastRightPart(f.name, '.')))
    const hasAudio = () => attachedFiles.value.some(f => audioExts.includes(lastRightPart(f.name, '.')))
    const hasFile = () => attachedFiles.value.length > 0

    const editingMessage = ref(null)

    function reset() {
        // Ensure initial state is ready to accept input
        attachedFiles.value = []
        messageText.value = ''
        editingMessage.value = null
    }

    const settings = useSettings()

    function getModel(name) {
        return ctx.state.models.find(x => x.name === name) ?? ctx.state.models.find(x => x.id === name)
    }

    function getSelectedModel() {
        const candidates = [ctx.state.selectedModel, ctx.state.config.defaults.text.model]
        const ret = candidates.map(name => name && getModel(name)).find(x => !!x)
        if (!ret) {
            // Try to find a model in the latest threads
            for (const thread in ctx.threads.threads) {
                const model = thread.model && getModel(thread.model)
                if (model) return model
            }
        }
        return ret
    }

    function setSelectedModel(model) {
        ctx.setState({
            selectedModel: model.name
        })
        ctx.setPrefs({
            model: model.name
        })
    }

    function getProviderForModel(model) {
        return getModel(model)?.provider
    }

    const canGenerateImage = model => {
        return model?.modalities?.output?.includes('image')
    }
    const canGenerateAudio = model => {
        return model?.modalities?.output?.includes('audio')
    }

    function applySettings(request) {
        settings.applySettings(request)
    }

    function createContent({ text, files }) {
        let content = []

        // Add Text Block
        if (text) {
            content.push({ type: 'text', text })
        }

        // Add Attachment Blocks
        if (Array.isArray(files)) {
            for (const f of files) {
                const ext = lastRightPart(f.name, '.')
                if (imageExts.includes(ext)) {
                    content.push({ type: 'image_url', image_url: { url: f.url } })
                } else if (audioExts.includes(ext)) {
                    content.push({ type: 'input_audio', input_audio: { data: f.url, format: ext } })
                } else {
                    content.push({ type: 'file', file: { file_data: f.url, filename: f.name } })
                }
            }
        }
        return content
    }

    function createRequest({ model, text, files, systemPrompt, aspectRatio }) {
        // Construct API Request from History
        const request = {
            model: model.name,
            messages: [],
            metadata: {}
        }

        // Apply user settings
        applySettings(request)

        if (systemPrompt) {
            request.messages = request.messages.filter(m => m.role !== 'system')
            request.messages.unshift({
                role: 'system',
                content: systemPrompt
            })
        }

        if (canGenerateImage(model)) {
            request.image_config = {
                aspect_ratio: aspectRatio || imageAspectRatios[ctx.state.selectedAspectRatio] || '1:1'
            }
            request.modalities = ["image", "text"]
        }
        else if (canGenerateAudio(model)) {
            request.modalities = ["audio", "text"]
        }

        if (text) {
            const content = createContent({ text, files })
            request.messages.push({
                role: 'user',
                content
            })
        }

        return request
    }

    async function completion({ request, thread, model, controller, redirect }) {
        try {
            let error
            if (!model) {
                if (request.model) {
                    model = getModel(request.model)
                } else {
                    model = getModel(request.model) ?? getSelectedModel()
                }
            }

            if (!model) {
                return ctx.createErrorResult({ message: `Model ${request.model || ''} not found`, errorCode: 'NotFound' })
            }

            if (!thread) {
                const title = getTextContent(request) || 'New Chat'
                thread = await ctx.threads.startNewThread({ title, model, redirect })
            }

            const ctxRequest = ctx.createChatContext({ request, thread })
            ctx.chatRequestFilters.forEach(f => f(ctxRequest))
            ctx.completeChatContext(ctxRequest)

            // Send to API
            const startTime = Date.now()
            const res = await ctx.post('/v1/chat/completions', {
                body: JSON.stringify(request),
                signal: controller?.signal
            })

            let response = null
            if (!res.ok) {
                error = ctx.createErrorStatus({ message: `HTTP ${res.status} ${res.statusText}` })
                let errorBody = null
                try {
                    errorBody = await res.text()
                    if (errorBody) {
                        // Try to parse as JSON for better formatting
                        try {
                            const errorJson = JSON.parse(errorBody)
                            const status = errorJson?.responseStatus
                            if (status) {
                                error.errorCode += ` ${status.errorCode}`
                                error.message = status.message
                                error.stackTrace = status.stackTrace
                            } else {
                                error.stackTrace = JSON.stringify(errorJson, null, 2)
                            }
                        } catch (e) {
                        }
                    }
                } catch (e) {
                    // If we can't read the response body, just use the status
                }
            } else {
                try {
                    response = await res.json()
                    const ctxResponse = {
                        response,
                        thread,
                    }
                    ctx.chatResponseFilters.forEach(f => f(ctxResponse))
                    console.debug('completion.response', JSON.stringify(response, null, 2))
                } catch (e) {
                    error = createErrorStatus(e.message)
                }
            }

            if (response?.error) {
                error ??= createErrorStatus()
                error.message = response.error
            }

            if (error) {
                ctx.chatErrorFilters.forEach(f => f(error))
                return new ApiResult({ error })
            }

            if (!error) {
                // Add tool history messages if any
                if (response.tool_history && Array.isArray(response.tool_history)) {
                    for (const msg of response.tool_history) {
                        if (msg.role === 'assistant') {
                            msg.model = model.name // tag with model
                        }
                    }
                }

                nextTick(addCopyButtons)

                return new ApiResult({ response })
            }
        } catch (e) {
            console.log('completion.error', e)
            return new ApiResult({ error: createErrorStatus(e.message, 'ChatFailed') })
        }
    }
    function getTextContent(chat) {
        const textMessage = chat.messages.find(m =>
            m.role === 'user' && Array.isArray(m.content) && m.content.some(c => c.type === 'text'))
        return textMessage?.content.find(c => c.type === 'text')?.text || ''
    }
    function getAnswer(response) {
        const textMessage = response.choices?.[0]?.message
        return textMessage?.content || ''
    }
    function selectAspectRatio(ratio) {
        const selectedAspectRatio = imageRatioSizes[ratio] || '1024×1024'
        console.log(`selectAspectRatio(${ratio})`, selectedAspectRatio)
        ctx.setState({ selectedAspectRatio })
    }

    async function sendUserMessage(text, { model, redirect = true } = {}) {
        ctx.clearError()

        if (!model) {
            model = getSelectedModel()
        }

        let content = createContent({ text, files: attachedFiles.value })

        let thread

        // Create thread if none exists
        if (!ctx.threads.currentThread.value) {
            thread = await ctx.threads.startNewThread({ model, redirect })
        } else {
            thread = ctx.threads.currentThread.value
        }

        let threadId = thread.id
        let messages = thread.messages || []
        if (!threadId) {
            console.error('No thread ID found', thread, ctx.threads.currentThread.value)
            return
        }

        // Handle Editing / Redo Logic
        const editingMsg = editingMessage.value
        if (editingMsg) {
            let messageIndex = messages.findIndex(m => m.timestamp === editingMsg)
            if (messageIndex == -1) {
                messageIndex = messages.findLastIndex(m => m.role === 'user')
            }
            console.log('Editing message', editingMsg, messageIndex, messages)

            if (messageIndex >= 0) {
                messages[messageIndex].content = content
                // Truncate messages to only include up to the edited message
                messages.length = messageIndex + 1
            } else {
                messages.push({
                    timestamp: new Date().valueOf(),
                    role: 'user',
                    content,
                })
            }
        } else {
            // Regular Send Logic
            const lastMessage = messages[messages.length - 1]

            // Check duplicate based on text content extracted from potential array
            const getLastText = (msgContent) => {
                if (typeof msgContent === 'string') return msgContent
                if (Array.isArray(msgContent)) return msgContent.find(c => c.type === 'text')?.text || ''
                return ''
            }
            const newText = text // content[0].text
            const lastText = lastMessage && lastMessage.role === 'user' ? getLastText(lastMessage.content) : null
            const isDuplicate = lastText === newText

            // Add user message only if it's not a duplicate
            // Note: We are saving the FULL STRUCTURED CONTENT array here
            if (!isDuplicate) {
                messages.push({
                    timestamp: new Date().valueOf(),
                    role: 'user',
                    content,
                })
            }
        }

        const request = createRequest({ model })

        // Add Thread History
        messages.forEach(m => {
            request.messages.push(m)
        })

        // Update Thread Title if not set or is default
        if (!thread.title || thread.title === 'New Chat' || request.title === 'New Chat') {
            request.title = text.length > 100
                ? text.slice(0, 100) + '...'
                : text
            console.debug(`changing thread title from '${thread.title}' to '${request.title}'`)
        } else {
            console.debug(`thread title is '${thread.title}'`, request.title)
        }

        const api = await ctx.threads.queueChat({ request, thread })
        if (api.response) {
            // success
            editingMessage.value = null
            attachedFiles.value = []
            thread = api.response
            ctx.threads.replaceThread(thread)
        } else {
            ctx.setError(api.error)
        }
    }

    return {
        completion,
        createContent,
        createRequest,
        applySettings,
        promptHistory,
        messageText,
        attachedFiles,
        editingMessage,
        hasImage,
        hasAudio,
        hasFile,
        reset,
        settings,
        addCopyButtons,
        getModel,
        getSelectedModel,
        setSelectedModel,
        getProviderForModel,
        canGenerateImage,
        canGenerateAudio,
        getTextContent,
        getAnswer,
        selectAspectRatio,
        sendUserMessage,
    }
}

const ChatPrompt = {
    template: `
    <div class="mx-auto max-w-3xl">
        <SettingsDialog :isOpen="showSettings" @close="showSettings = false" />
        <div class="flex space-x-2">
            <!-- Attach (+) button and Settings button -->
            <div class="mt-1.5 flex flex-col space-y-1 items-center">
                <div>
                    <button type="button"
                            @click="triggerFilePicker"
                            :disabled="$threads.isWatchingThread.value || !model"
                            class="size-8 flex items-center justify-center rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed"
                            title="Attach image or audio">
                        <svg class="size-5" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 256 256">
                            <path d="M224,128a8,8,0,0,1-8,8H136v80a8,8,0,0,1-16,0V136H40a8,8,0,0,1,0-16h80V40a8,8,0,0,1,16,0v80h80A8,8,0,0,1,224,128Z"></path>
                        </svg>
                    </button>
                    <!-- Hidden file input -->
                    <input ref="fileInput" type="file" multiple @change="onFilesSelected"
                        class="hidden" accept="image/*,audio/*,.pdf,.doc,.docx,.xml,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        />
                </div>
                <div>
                    <button type="button" title="Settings" @click="showSettings = true"
                        :disabled="$threads.watchingThread || !model"
                        class="size-8 flex items-center justify-center rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed">
                        <svg class="size-4 text-gray-600 dark:text-gray-400 disabled:text-gray-400" xmlns="http://www.w3.org/2000/svg" fill="currentColor" viewBox="0 0 256 256"><path d="M40,88H73a32,32,0,0,0,62,0h81a8,8,0,0,0,0-16H135a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16Zm64-24A16,16,0,1,1,88,80,16,16,0,0,1,104,64ZM216,168H199a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16h97a32,32,0,0,0,62,0h17a8,8,0,0,0,0-16Zm-48,24a16,16,0,1,1,16-16A16,16,0,0,1,168,192Z"></path></svg>
                    </button>
                </div>
            </div>

            <div class="flex-1">
                <div class="relative">
                    <textarea
                        ref="refMessage"
                        v-model="messageText"
                        @keydown="onKeyDown"
                        @keydown.enter.exact.prevent="sendMessage"
                        @keydown.enter.shift.exact="addNewLine"
                        @paste="onPaste"
                        @dragover="onDragOver"
                        @dragleave="onDragLeave"
                        @drop="onDrop"
                        placeholder="Type message... (Enter to send, Shift+Enter for new line, drag & drop or paste files)"
                        rows="3"
                        :class="[
                            'block w-full rounded-md border px-3 py-2 pr-12 text-sm text-gray-900 dark:text-gray-100 bg-white dark:bg-gray-900 placeholder-gray-500 dark:placeholder-gray-400 focus:outline-none focus:ring-1',
                            isDragging
                                ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 ring-1 ring-blue-500'
                                : 'border-gray-300 dark:border-gray-600 focus:border-blue-500 focus:ring-blue-500'
                        ]"
                        :disabled="$threads.watchingThread || !model"
                    ></textarea>
                    <button v-if="!$threads.watchingThread" title="Send (Enter)" type="button"
                        @click="sendMessage"
                        :disabled="!messageText.trim() || $threads.watchingThread || !model"
                        class="absolute bottom-2 right-2 size-8 flex items-center justify-center rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed disabled:border-gray-200 dark:disabled:border-gray-700 transition-colors">
                        <svg class="size-5" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path stroke-dasharray="20" stroke-dashoffset="20" d="M12 21l0 -17.5"><animate fill="freeze" attributeName="stroke-dashoffset" dur="0.2s" values="20;0"/></path><path stroke-dasharray="12" stroke-dashoffset="12" d="M12 3l7 7M12 3l-7 7"><animate fill="freeze" attributeName="stroke-dashoffset" begin="0.2s" dur="0.2s" values="12;0"/></path></g></svg>
                    </button>
                    <button v-else title="Cancel request" type="button"
                        @click="$threads.cancelThread()"
                        class="absolute bottom-2 right-2 size-8 flex items-center justify-center rounded-md border border-red-300 dark:border-red-600 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                        <svg class="size-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                        </svg>
                    </button>
                </div>

                <!-- Attachments & Image Options -->
                <div class="mt-2 flex justify-between items-start gap-2">
                    <div class="flex flex-wrap gap-2">
                        <div v-for="(f,i) in $chat.attachedFiles.value" :key="i" class="flex items-center gap-2 px-2 py-1 rounded-md border border-gray-300 dark:border-gray-600 text-xs text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-800">
                            <span class="truncate max-w-48" :title="f.name">{{ f.name }}</span>
                            <button type="button" class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200" @click="removeAttachment(i)" title="Remove Attachment">
                                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                            </button>
                        </div>
                    </div>

                    <!-- Image Aspect Ratio Selector -->
                    <div v-if="$chat.canGenerateImage(model)">
                        <select name="aspect_ratio" v-model="$state.selectedAspectRatio" 
                                class="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-xs text-gray-700 dark:text-gray-300 pl-2 pr-6 py-1 focus:ring-blue-500 focus:border-blue-500">
                            <option v-for="(ratio, size) in imageAspectRatios" :key="size" :value="size">
                                {{ ratio }}
                            </option>
                        </select>
                    </div>
                </div>

                <div v-if="!model" class="mt-2 text-sm text-red-600 dark:text-red-400">
                    Please select a model
                </div>
            </div>
        </div>
    </div>    
    `,
    props: {
        model: {
            type: Object,
            default: null
        }
    },
    setup(props) {
        const ctx = inject('ctx')
        const config = ctx.state.config
        const {
            messageText,
            promptHistory,
            hasImage,
            hasAudio,
            hasFile,
            getTextContent,
            sendUserMessage,
        } = ctx.chat

        const fileInput = ref(null)
        const refMessage = ref(null)
        const showSettings = ref(false)
        const historyIndex = ref(-1)
        const isNavigatingHistory = ref(false)

        // File attachments (+) handlers
        const triggerFilePicker = () => {
            if (fileInput.value) fileInput.value.click()
        }
        const onFilesSelected = async (e) => {
            const files = Array.from(e.target?.files || [])
            if (files.length) {
                // Upload files immediately
                const uploadedFiles = await Promise.all(files.map(async f => {
                    try {
                        const response = await ctx.ai.uploadFile(f)
                        const metadata = {
                            url: response.url,
                            name: f.name,
                            size: response.size,
                            type: f.type,
                            width: response.width,
                            height: response.height,
                            threadId: ctx.threads.currentThread.value?.id,
                            created: Date.now()
                        }

                        return {
                            ...metadata,
                            file: f // Keep original file for preview/fallback if needed
                        }
                    } catch (error) {
                        ctx.setError({
                            errorCode: 'Upload Failed',
                            message: `Failed to upload ${f.name}: ${error.message}`
                        })
                        return null
                    }
                }))

                ctx.chat.attachedFiles.value.push(...uploadedFiles.filter(f => f))
            }

            // allow re-selecting the same file
            if (fileInput.value) fileInput.value.value = ''

            if (!messageText.value?.trim()) {
                if (hasImage()) {
                    messageText.value = getTextContent(config.defaults.image)
                } else if (hasAudio()) {
                    messageText.value = getTextContent(config.defaults.audio)
                } else {
                    messageText.value = getTextContent(config.defaults.file)
                }
            }
        }
        const removeAttachment = (i) => {
            ctx.chat.attachedFiles.value.splice(i, 1)
        }

        // Handle paste events for clipboard images, audio, and files
        const onPaste = async (e) => {
            // Use the paste event's clipboardData directly (works best for paste events)
            const items = e.clipboardData?.items
            if (!items) return

            const files = []

            // Check all clipboard items
            for (let i = 0; i < items.length; i++) {
                const item = items[i]

                // Handle files (images, audio, etc.)
                if (item.kind === 'file') {
                    const file = item.getAsFile()
                    if (file) {
                        // Generate a better filename based on type
                        let filename = file.name
                        if (!filename || filename === 'image.png' || filename === 'blob') {
                            const ext = file.type.split('/')[1] || 'png'
                            const timestamp = new Date().getTime()
                            if (file.type.startsWith('image/')) {
                                filename = `pasted-image-${timestamp}.${ext}`
                            } else if (file.type.startsWith('audio/')) {
                                filename = `pasted-audio-${timestamp}.${ext}`
                            } else {
                                filename = `pasted-file-${timestamp}.${ext}`
                            }
                            // Create a new File object with the better name
                            files.push(new File([file], filename, { type: file.type }))
                        } else {
                            files.push(file)
                        }
                    }
                }
            }

            if (files.length > 0) {
                e.preventDefault()
                // Reuse the same logic as onFilesSelected for consistency
                const event = { target: { files: files } }
                await onFilesSelected(event)
            }
        }

        // Handle drag and drop events
        const isDragging = ref(false)

        const onDragOver = (e) => {
            e.preventDefault()
            e.stopPropagation()
            isDragging.value = true
        }

        const onDragLeave = (e) => {
            e.preventDefault()
            e.stopPropagation()
            isDragging.value = false
        }

        const onDrop = async (e) => {
            e.preventDefault()
            e.stopPropagation()
            isDragging.value = false

            const files = Array.from(e.dataTransfer?.files || [])
            if (files.length > 0) {
                // Reuse the same logic as onFilesSelected for consistency
                const event = { target: { files: files } }
                await onFilesSelected(event)
            }
        }

        // Send message
        const sendMessage = async () => {
            if (!messageText.value?.trim() && !hasImage() && !hasAudio() && !hasFile()) return
            if (ctx.threads.isWatchingThread.value || !props.model) return

            // 1. Construct Structured Content (Text + Attachments)
            let text = messageText.value.trim()

            if (text) {
                const idx = promptHistory.value.indexOf(text)
                if (idx !== -1) {
                    promptHistory.value.splice(idx, 1)
                }
                promptHistory.value.push(text)
            }

            messageText.value = ''

            await sendUserMessage(text, { model: props.model })

            // Restore focus to the textarea
            nextTick(() => {
                refMessage.value?.focus()
            })
        }

        const addNewLine = () => {
            // Enter key already adds new line
            //messageText.value += '\n'
        }

        const onKeyDown = (e) => {
            if (e.key === 'ArrowUp') {
                if (refMessage.value.selectionStart === 0 && refMessage.value.selectionEnd === 0) {
                    if (promptHistory.value.length > 0) {
                        e.preventDefault()
                        if (historyIndex.value === -1) {
                            historyIndex.value = promptHistory.value.length - 1
                        } else {
                            historyIndex.value = Math.max(0, historyIndex.value - 1)
                        }
                        isNavigatingHistory.value = true
                        messageText.value = promptHistory.value[historyIndex.value]
                        nextTick(() => {
                            refMessage.value.setSelectionRange(0, 0)
                        })
                    }
                }
            } else if (e.key === 'ArrowDown') {
                if (historyIndex.value !== -1) {
                    e.preventDefault()
                    if (historyIndex.value < promptHistory.value.length - 1) {
                        historyIndex.value++
                        isNavigatingHistory.value = true
                        messageText.value = promptHistory.value[historyIndex.value]
                    } else {
                        historyIndex.value = -1
                        isNavigatingHistory.value = true
                        messageText.value = ''
                    }
                    nextTick(() => {
                        refMessage.value.setSelectionRange(0, 0)
                    })
                }
            }
        }

        watch(messageText, (newValue) => {
            if (!isNavigatingHistory.value) {
                historyIndex.value = -1
            }
            isNavigatingHistory.value = false
        })

        watch(() => ctx.state.selectedAspectRatio, newValue => {
            ctx.setPrefs({ aspectRatio: newValue })
        })

        watch(() => ctx.layout.path, newValue => {
            if (newValue === '/' || newValue.startsWith('/c/')) {
                nextTick(() => {
                    refMessage.value?.focus()
                })
            }
        })

        return {
            messageText,
            fileInput,
            refMessage,
            showSettings,
            isDragging,
            triggerFilePicker,
            onFilesSelected,
            onPaste,
            onDragOver,
            onDragLeave,
            onDrop,
            removeAttachment,
            sendMessage,
            addNewLine,
            onKeyDown,
            imageAspectRatios,
            sendUserMessage,
        }
    }
}

const HomeTools = {
    template: `
        <div class="mt-4 flex space-x-3 justify-center items-center">
            <DarkModeToggle />
        </div>
    `,
}

const ThreadHeader = {
    template: `
    <div v-if="showComponents.length" class="flex items-center justify-center gap-2">
        <div v-for="component in showComponents">
            <component :is="component" :thread="thread" />
        </div>
    </div>
    `,
    props: { thread: Object },
    setup(props) {
        const ctx = inject('ctx')
        const showComponents = computed(() => {
            const args = { thread: props.thread }
            return Object.values(ctx.threadHeaderComponents).filter(def => def.show(args)).map(def => def.component)
        })
        return {
            showComponents,
        }
    }
}

const ThreadFooter = {
    template: `
    <div v-if="showComponents.length">
        <div v-for="component in showComponents">
            <component :is="component" :thread="thread" />
        </div>
    </div>
    `,
    props: { thread: Object },
    setup(props) {
        const ctx = inject('ctx')
        const showComponents = computed(() => {
            const args = { thread: props.thread }
            return Object.values(ctx.threadFooterComponents).filter(def => def.show(args)).map(def => def.component)
        })
        return {
            showComponents,
        }
    }
}

const ThreadModel = {
    template: `
    <span @click="$chat.setSelectedModel({ name: thread.model})" 
        class="flex items-center cursor-pointer px-1.5 py-0.5 text-xs rounded text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-100 transition-colors border hover:border-gray-300 dark:hover:border-gray-700">
        <ProviderIcon class="size-4 mr-1" :provider="$chat.getProviderForModel(thread.model)" />
        {{thread.model}}
    </span>
    `,
    props: { thread: Object },
}

const ThreadTools = {
    template: `
    <div class="text-sm flex items-center gap-1 flex items-center px-1.5 py-0.5 text-xs rounded text-gray-600 dark:text-gray-300 border cursor-help" :title="title">
        <svg class="size-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 10h3V7L6.5 3.5a6 6 0 0 1 8 8l6 6a2 2 0 0 1-3 3l-6-6a6 6 0 0 1-8-8z"/></svg>
        <span v-if="toolFns.length==1">{{toolFns[0].function.name}}</span>
        <span v-else-if="toolFns.length>1">{{toolFns.length}} Tools</span>
    </div>
    `,
    props: { thread: Object },
    setup(props) {
        const toolFns = computed(() => props.thread.tools.filter(x => x.type === 'function'))
        const title = computed(() => toolFns.value.length == 1
            ? toolFns.value[0].function.name
            : toolFns.value.length > 1
                ? toolFns.value.map(x => x.function.name).join('\n')
                : '')
        return {
            toolFns,
            title,
        }
    }
}

export default {
    /**@param {AppContext} ctx */
    install(ctx) {
        const Home = ChatBody
        ctx.components({
            SettingsDialog,
            ChatPrompt,

            ChatBody,
            MessageUsage,
            MessageReasoning,
            LightboxImage,
            TypeText,
            TypeImage,
            TypeAudio,
            TypeFile,
            ViewType,
            ViewTypes,
            ViewToolTypes,
            TextViewer,
            ToolArguments,
            ToolOutput,

            HomeTools,
            Home,
            ThreadHeader,
            ThreadFooter,
        })
        ctx.setGlobals({
            chat: useChatPrompt(ctx)
        })

        ctx.setLeftIcons({
            chat: {
                component: {
                    template: `<svg @click="$ctx.togglePath($ctx.layout.path?.startsWith('/c/') ? $ctx.layout.path : '/')" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="currentColor" d="M8 2.19c3.13 0 5.68 2.25 5.68 5s-2.55 5-5.68 5a5.7 5.7 0 0 1-1.89-.29l-.75-.26l-.56.56a14 14 0 0 1-2 1.55a.13.13 0 0 1-.07 0v-.06a6.58 6.58 0 0 0 .15-4.29a5.25 5.25 0 0 1-.55-2.16c0-2.77 2.55-5 5.68-5M8 .94c-3.83 0-6.93 2.81-6.93 6.27a6.4 6.4 0 0 0 .64 2.64a5.53 5.53 0 0 1-.18 3.48a1.32 1.32 0 0 0 2 1.5a15 15 0 0 0 2.16-1.71a6.8 6.8 0 0 0 2.31.36c3.83 0 6.93-2.81 6.93-6.27S11.83.94 8 .94"/><ellipse cx="5.2" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="10.8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/></svg>`,
                },
                isActive({ path }) {
                    return path === '/' || path.startsWith('/c/')
                }
            }
        })

        const title = 'Chat'
        ctx.setState({
            title
        })

        const meta = { title }
        ctx.routes.push(...[
            { path: '/', component: Home, meta },
            { path: '/c/:id', component: ChatBody, meta },
        ])

        ctx.setThreadHeaders({
            model: {
                component: ThreadModel,
                show({ thread }) { return thread.model }
            },
            tools: {
                component: ThreadTools,
                show({ thread }) { return (thread.tools || []).filter(x => x.type === 'function').length }
            }
        })

        const prefs = ctx.getPrefs()
        if (prefs.model) {
            ctx.state.selectedModel = prefs.model
        }
        ctx.setState({
            selectedAspectRatio: prefs.aspectRatio || '1:1',
        })
    }
}
