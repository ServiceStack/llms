
import { ref, computed, watch, nextTick, inject } from 'vue'
import { useRouter } from 'vue-router'
import { $$, createElement, lastRightPart, ApiResult, createErrorStatus } from "@servicestack/client"
import SettingsDialog, { useSettings } from './SettingsDialog.mjs'
import ChatBody from './ChatBody.mjs'
import HomeTools from './HomeTools.mjs'
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
    const attachedFiles = ref([])
    const isGenerating = ref(false)
    const errorStatus = ref(null)
    const abortController = ref(null)
    const hasImage = () => attachedFiles.value.some(f => imageExts.includes(lastRightPart(f.name, '.')))
    const hasAudio = () => attachedFiles.value.some(f => audioExts.includes(lastRightPart(f.name, '.')))
    const hasFile = () => attachedFiles.value.length > 0
    // const hasText = () => !hasImage() && !hasAudio() && !hasFile()

    const editingMessageId = ref(null)

    function reset() {
        // Ensure initial state is ready to accept input
        isGenerating.value = false
        attachedFiles.value = []
        messageText.value = ''
        abortController.value = null
        editingMessageId.value = null
    }

    function cancel() {
        // Cancel the pending request
        if (abortController.value) {
            abortController.value.abort()
        }
        // Reset UI state
        isGenerating.value = false
        abortController.value = null
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

    async function completion({ request, model, thread, controller, store }) {
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
                return new ApiResult({
                    error: createErrorStatus(`Model ${request.model || ''} not found`, 'NotFound')
                })
            }

            if (!request.messages) request.messages = []
            if (!request.metadata) request.metadata = {}

            if (store && !thread) {
                const title = getTextContent(request) || 'New Chat'
                thread = await ctx.threads.startNewThread({ title, model })
            }

            const threadId = thread?.id || ctx.threads.generateThreadId()

            const ctxRequest = {
                request,
                thread,
            }
            ctx.chatRequestFilters.forEach(f => f(ctxRequest))

            console.debug('completion.request', request)

            // Send to API
            const startTime = Date.now()
            const res = await ctx.post('/v1/chat/completions', {
                body: JSON.stringify(request),
                signal: controller?.signal
            })

            let response = null
            if (!res.ok) {
                error = createErrorStatus('', `HTTP ${res.status} ${res.statusText}`)
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
                        if (store) {
                            await ctx.threads.addMessageToThread(threadId, msg)
                        }
                    }
                }

                // Add assistant response (save entire message including reasoning)
                const assistantMessage = response.choices?.[0]?.message

                const usage = response.usage
                if (usage) {
                    if (response.metadata?.pricing) {
                        const [input, output] = response.metadata.pricing.split('/')
                        usage.duration = response.metadata.duration ?? (Date.now() - startTime)
                        usage.input = input
                        usage.output = output
                        usage.tokens = usage.completion_tokens
                        usage.price = usage.output
                        usage.cost = ctx.fmt.tokenCost(usage.prompt_tokens / 1_000_000 * parseFloat(input) + usage.completion_tokens / 1_000_000 * parseFloat(output))
                    }
                    await ctx.threads.logRequest(threadId, model, request, response)
                }
                if (store) {
                    assistantMessage.model = model.name
                    await ctx.threads.addMessageToThread(threadId, assistantMessage, usage)
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

    return {
        completion,
        createContent,
        createRequest,
        applySettings,
        messageText,
        attachedFiles,
        errorStatus,
        isGenerating,
        abortController,
        editingMessageId,
        get generating() {
            return isGenerating.value
        },
        hasImage,
        hasAudio,
        hasFile,
        // hasText,
        reset,
        cancel,
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
                            :disabled="isGenerating || !model"
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
                        :disabled="isGenerating || !model"
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
                        :disabled="isGenerating || !model"
                    ></textarea>
                    <button v-if="!isGenerating" title="Send (Enter)" type="button"
                        @click="sendMessage"
                        :disabled="!messageText.trim() || isGenerating || !model"
                        class="absolute bottom-2 right-2 size-8 flex items-center justify-center rounded-md border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed disabled:border-gray-200 dark:disabled:border-gray-700 transition-colors">
                        <svg class="size-5" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path stroke-dasharray="20" stroke-dashoffset="20" d="M12 21l0 -17.5"><animate fill="freeze" attributeName="stroke-dashoffset" dur="0.2s" values="20;0"/></path><path stroke-dasharray="12" stroke-dashoffset="12" d="M12 3l7 7M12 3l-7 7"><animate fill="freeze" attributeName="stroke-dashoffset" begin="0.2s" dur="0.2s" values="12;0"/></path></g></svg>
                    </button>
                    <button v-else title="Cancel request" type="button"
                        @click="cancelRequest"
                        class="absolute bottom-2 right-2 size-8 flex items-center justify-center rounded-md border border-red-300 dark:border-red-600 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors">
                        <svg class="size-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                        </svg>
                    </button>
                </div>

                <!-- Attachments & Image Options -->
                <div class="mt-2 flex justify-between items-start gap-2">
                    <div class="flex flex-wrap gap-2">
                        <div v-for="(f,i) in attachedFiles" :key="i" class="flex items-center gap-2 px-2 py-1 rounded-md border border-gray-300 dark:border-gray-600 text-xs text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-800">
                            <span class="truncate max-w-48" :title="f.name">{{ f.name }}</span>
                            <button type="button" class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200" @click="removeAttachment(i)" title="Remove Attachment">
                                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                            </button>
                        </div>
                    </div>

                    <!-- Image Aspect Ratio Selector -->
                    <div v-if="$chat.canGenerateImage(model)" class="min-w-[120px]">
                        <select name="aspect_ratio" v-model="$state.selectedAspectRatio" 
                                class="block w-full rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-xs text-gray-700 dark:text-gray-300 pl-2 pr-6 py-1 focus:ring-blue-500 focus:border-blue-500">
                            <option v-for="(ratio, size) in imageAspectRatios" :key="size" :value="size">
                                {{ size }} ({{ ratio }})
                            </option>
                        </select>
                    </div>
                </div>

                <div v-if="!model" class="mt-2 text-sm text-red-600 dark:text-red-400">
                    Please select a model
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
        const router = useRouter()
        const chatPrompt = ctx.chat
        const {
            messageText,
            attachedFiles,
            isGenerating,
            errorStatus,
            hasImage,
            hasAudio,
            hasFile,
            editingMessageId,
            getTextContent,
        } = chatPrompt
        const threads = ctx.threads
        const {
            currentThread,
        } = ctx.threads

        const fileInput = ref(null)
        const refMessage = ref(null)
        const showSettings = ref(false)

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
                            threadId: currentThread.value?.id,
                            created: Date.now()
                        }

                        return {
                            ...metadata,
                            file: f // Keep original file for preview/fallback if needed
                        }
                    } catch (error) {
                        console.error('File upload failed:', error)
                        errorStatus.value = {
                            errorCode: 'Upload Failed',
                            message: `Failed to upload ${f.name}: ${error.message}`
                        }
                        return null
                    }
                }))

                attachedFiles.value.push(...uploadedFiles.filter(f => f))
            }

            // allow re-selecting the same file
            if (fileInput.value) fileInput.value.value = ''

            if (!messageText.value.trim()) {
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
            attachedFiles.value.splice(i, 1)
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
            if (!messageText.value.trim() && !hasImage() && !hasAudio() && !hasFile()) return
            if (isGenerating.value || !props.model) return

            // Clear any existing error message
            errorStatus.value = null

            // 1. Construct Structured Content (Text + Attachments)
            let text = messageText.value.trim()


            messageText.value = ''
            let content = ctx.chat.createContent({ text, files: attachedFiles.value })

            // Create AbortController for this request
            const controller = new AbortController()
            chatPrompt.abortController.value = controller
            const model = props.model.name

            let thread

            try {
                // Create thread if none exists
                if (!currentThread.value) {
                    thread = await ctx.threads.startNewThread({ model: props.model })
                } else {
                    let threadId = currentThread.value.id
                    // Update the existing thread's model to match current selection
                    await threads.updateThread(threadId, {
                        model,
                        info: ctx.utils.toModelInfo(props.model),
                    })

                    // Get the thread to check for duplicates
                    thread = await threads.getThread(threadId)
                }

                let threadId = thread.id

                // Handle Editing / Redo Logic
                if (editingMessageId.value) {
                    // Check if message still exists
                    const messageExists = thread.messages.find(m => m.id === editingMessageId.value)
                    if (messageExists) {
                        // Update the message content
                        await threads.updateMessageInThread(threadId, editingMessageId.value, { content })
                        // Redo from this message (clears subsequent)
                        await threads.redoMessageFromThread(threadId, editingMessageId.value)

                        // Clear editing state
                        editingMessageId.value = null
                    } else {
                        // Fallback if message was deleted
                        editingMessageId.value = null
                    }
                    // Refresh thread state
                    thread = await threads.getThread(threadId)
                } else {
                    // Regular Send Logic
                    const lastMessage = thread.messages[thread.messages.length - 1]

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
                        await threads.addMessageToThread(threadId, {
                            role: 'user',
                            content: content,
                            model: props.model.name,
                        })
                        // Reload thread after adding message
                        thread = await threads.getThread(threadId)
                    }
                }

                isGenerating.value = true

                const request = ctx.chat.createRequest({ model: props.model })

                // Add History
                thread?.messages.forEach(m => {
                    request.messages.push({
                        role: m.role,
                        content: m.content
                    })
                })
                request.metadata.threadId = thread.id

                const api = await ctx.chat.completion({ request, thread, controller, store: true })
                if (api.response) {
                    // success
                    attachedFiles.value = []
                } else {
                    errorStatus.value = api.error
                }

            } catch (error) {
                // Check if the error is due to abort
                if (error.name === 'AbortError') {
                    console.log('Request was cancelled by user')
                    // Don't show error for cancelled requests
                } else {
                    // Re-throw other errors to be handled by outer catch
                    throw error
                }
            } finally {
                isGenerating.value = false
                chatPrompt.abortController.value = null
                // Restore focus to the textarea
                nextTick(() => {
                    refMessage.value?.focus()
                })
            }
        }

        const cancelRequest = () => {
            chatPrompt.cancel()
        }

        const addNewLine = () => {
            // Enter key already adds new line
            //messageText.value += '\n'
        }

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
            isGenerating,
            attachedFiles,
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
            cancelRequest,
            addNewLine,
            imageAspectRatios,
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
            HomeTools,
            Home,
        })
        ctx.setGlobals({
            chat: useChatPrompt(ctx)
        })

        ctx.setLeftIcons({
            chat: {
                component: {
                    template: `<svg @click="$ctx.togglePath('/')" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="currentColor" d="M8 2.19c3.13 0 5.68 2.25 5.68 5s-2.55 5-5.68 5a5.7 5.7 0 0 1-1.89-.29l-.75-.26l-.56.56a14 14 0 0 1-2 1.55a.13.13 0 0 1-.07 0v-.06a6.58 6.58 0 0 0 .15-4.29a5.25 5.25 0 0 1-.55-2.16c0-2.77 2.55-5 5.68-5M8 .94c-3.83 0-6.93 2.81-6.93 6.27a6.4 6.4 0 0 0 .64 2.64a5.53 5.53 0 0 1-.18 3.48a1.32 1.32 0 0 0 2 1.5a15 15 0 0 0 2.16-1.71a6.8 6.8 0 0 0 2.31.36c3.83 0 6.93-2.81 6.93-6.27S11.83.94 8 .94"/><ellipse cx="5.2" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="10.8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/></svg>`,
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

        const prefs = ctx.getPrefs()
        if (prefs.model) {
            ctx.state.selectedModel = prefs.model
        }
        ctx.state.selectedAspectRatio = prefs.aspectRatio || '1:1'
    }
}
