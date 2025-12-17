import { ref, nextTick, inject, unref } from 'vue'
import { useRouter } from 'vue-router'
import { lastRightPart } from '@servicestack/client'
import { deepClone, fileToDataUri, fileToBase64, addCopyButtons, toModelInfo, tokenCost, uploadFile } from './utils.mjs'
import { toRaw } from 'vue'

const imageExts = 'png,webp,jpg,jpeg,gif,bmp,svg,tiff,ico'.split(',')
const audioExts = 'mp3,wav,ogg,flac,m4a,opus,webm'.split(',')

export function useChatPrompt() {
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

    return {
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
    }
}

export default {
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

                <!-- Attached files preview -->
                <div v-if="attachedFiles.length" class="mt-2 flex flex-wrap gap-2">
                    <div v-for="(f,i) in attachedFiles" :key="i" class="flex items-center gap-2 px-2 py-1 rounded-md border border-gray-300 dark:border-gray-600 text-xs text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-800">
                        <span class="truncate max-w-48" :title="f.name">{{ f.name }}</span>
                        <button type="button" class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200" @click="removeAttachment(i)" title="Remove Attachment">
                            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                        </button>
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
        const ai = ctx.ai
        const chatSettings = inject('chatSettings')
        const router = useRouter()
        const chatPrompt = inject('chatPrompt')
        const {
            messageText,
            attachedFiles,
            isGenerating,
            errorStatus,
            hasImage,
            hasAudio,
            hasFile,
            editingMessageId
        } = chatPrompt
        const threads = inject('threads')
        const {
            currentThread,
        } = threads

        const fileInput = ref(null)
        const refMessage = ref(null)
        const showSettings = ref(false)
        const { applySettings } = chatSettings

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
                        const response = await uploadFile(f)
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

        // Helper function to add files and set default message
        const addFilesAndSetMessage = (files) => {
            if (files.length === 0) return

            attachedFiles.value.push(...files)

            // Set default message text if empty
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

        function getTextContent(chat) {
            const textMessage = chat.messages.find(m =>
                m.role === 'user' && Array.isArray(m.content) && m.content.some(c => c.type === 'text'))
            return textMessage?.content.find(c => c.type === 'text')?.text || ''
        }

        // Send message
        const sendMessage = async () => {
            if (!messageText.value.trim() || isGenerating.value || !props.model) return

            // Clear any existing error message
            errorStatus.value = null

            // 1. Construct Structured Content (Text + Attachments)
            let text = messageText.value.trim()
            let content = []


            messageText.value = ''

            // Add Text Block
            content.push({ type: 'text', text: text })

            // Add Attachment Blocks
            for (const f of attachedFiles.value) {
                const ext = lastRightPart(f.name, '.')
                if (imageExts.includes(ext)) {
                    content.push({ type: 'image_url', image_url: { url: f.url } })
                } else if (audioExts.includes(ext)) {
                    content.push({ type: 'input_audio', input_audio: { data: f.url, format: ext } })
                } else {
                    content.push({ type: 'file', file: { file_data: f.url, filename: f.name } })
                }
            }

            // Create AbortController for this request
            const controller = new AbortController()
            chatPrompt.abortController.value = controller

            try {
                let threadId

                // Create thread if none exists
                if (!currentThread.value) {
                    const newThread = await threads.createThread({
                        title: 'New Chat',
                        model: props.model.id,
                        info: toModelInfo(props.model),
                    })
                    threadId = newThread.id
                    // Navigate to the new thread URL
                    router.push(`${ai.base}/c/${newThread.id}`)
                } else {
                    threadId = currentThread.value.id
                    // Update the existing thread's model to match current selection
                    await threads.updateThread(threadId, {
                        model: props.model.name,
                        info: toModelInfo(props.model),
                    })
                }

                // Get the thread to check for duplicates
                let thread = await threads.getThread(threadId)

                // Handle Editing / Redo Logic
                if (editingMessageId.value) {
                    // Check if message still exists
                    const messageExists = thread.messages.find(m => m.id === editingMessageId.value)
                    if (messageExists) {
                        // Update the message content
                        await threads.updateMessageInThread(threadId, editingMessageId.value, { content: content })
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
                            content: content
                        })
                        // Reload thread after adding message
                        thread = await threads.getThread(threadId)
                    }
                }

                isGenerating.value = true

                // Construct API Request from History
                const request = {
                    model: props.model.name,
                    messages: [],
                    metadata: {}
                }

                // Add History
                thread.messages.forEach(m => {
                    request.messages.push({
                        role: m.role,
                        content: m.content
                    })
                })

                // Apply user settings
                applySettings(request)
                request.metadata.threadId = threadId

                const ctxRequest = {
                    request,
                    thread,
                }
                ctx.chatRequestFilters.forEach(f => f(ctxRequest))

                console.debug('chatRequest', request)

                // Send to API
                const startTime = Date.now()
                const res = await ai.post('/v1/chat/completions', {
                    body: JSON.stringify(request),
                    signal: controller.signal
                })

                let response = null
                if (!res.ok) {
                    errorStatus.value = {
                        errorCode: `HTTP ${res.status} ${res.statusText}`,
                        message: null,
                        stackTrace: null
                    }
                    let errorBody = null
                    try {
                        errorBody = await res.text()
                        if (errorBody) {
                            // Try to parse as JSON for better formatting
                            try {
                                const errorJson = JSON.parse(errorBody)
                                const status = errorJson?.responseStatus
                                if (status) {
                                    errorStatus.value.errorCode += ` ${status.errorCode}`
                                    errorStatus.value.message = status.message
                                    errorStatus.value.stackTrace = status.stackTrace
                                } else {
                                    errorStatus.value.stackTrace = JSON.stringify(errorJson, null, 2)
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
                        console.debug('chatResponse', JSON.stringify(response, null, 2))
                    } catch (e) {
                        errorStatus.value = {
                            errorCode: 'Error',
                            message: e.message,
                            stackTrace: null
                        }
                    }
                }

                if (response?.error) {
                    errorStatus.value ??= {
                        errorCode: 'Error',
                    }
                    errorStatus.value.message = response.error
                }

                if (!errorStatus.value) {
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
                            usage.cost = tokenCost(usage.prompt_tokens / 1_000_000 * parseFloat(input) + usage.completion_tokens / 1_000_000 * parseFloat(output))
                        }
                        await threads.logRequest(threadId, props.model, request, response)
                    }
                    await threads.addMessageToThread(threadId, assistantMessage, usage)

                    nextTick(addCopyButtons)

                    attachedFiles.value = []
                    // Error will be cleared when user sends next message (no auto-timeout)
                } else {
                    ctx.chatErrorFilters.forEach(f => f(errorStatus.value))
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
        }
    }
}