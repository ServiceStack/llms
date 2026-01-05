import { ref, computed, nextTick, watch, onMounted, onUnmounted, inject } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const MessageUsage = {
    template: `
    <div class="mt-2 text-xs opacity-70">                                        
        <span v-if="message.model" @click="$chat.setSelectedModel({ name: message.model })" title="Select model"><span class="cursor-pointer hover:underline">{{ message.model }}</span> &#8226; </span>
        <span>{{ $fmt.time(message.timestamp) }}</span>
        <span v-if="usage" :title="$fmt.tokensTitle(usage)">
            &#8226;
            {{ $fmt.humanifyNumber(usage.tokens) }} tokens
            <span v-if="usage.cost">&#183; {{ $fmt.tokenCostLong(usage.cost) }}</span>
            <span v-if="usage.duration"> in {{ $fmt.humanifyMs(usage.duration) }}</span>
        </span>
    </div>    
    `,
    props: {
        usage: Object,
        message: Object,
    }
}

export default {
    components: {
        MessageUsage,
    },
    template: `
        <div class="flex flex-col h-full">
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
                        <HomeTools />
                    </div>

                    <!-- Messages -->
                    <div v-else-if="currentThread?.messages?.length" class="space-y-2">
                        <div v-if="currentThread?.messages.length && currentThread?.model" class="flex items-center justify-center select-none">
                            <span @click="$chat.setSelectedModel({ name: currentThread.model})" 
                                class="flex items-center cursor-pointer px-1.5 py-0.5 text-xs rounded text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-100 transition-colors border hover:border-gray-300 dark:hover:border-gray-700">
                                <ProviderIcon class="size-4 mr-1" :provider="$chat.getProviderForModel(currentThread.model)" />
                                {{currentThread.model}}
                            </span>
                        </div>
                        <div
                            v-for="message in currentThread.messages.filter(x => x.role !== 'system')"
                            :key="message.id"
                            v-show="!(message.role === 'tool' && isToolLinked(message))"
                            class="flex items-start space-x-3 group"
                            :class="message.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''"
                        >
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0 flex flex-col justify-center">
                                <div class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium"
                                     :class="message.role === 'user'
                                        ? 'bg-blue-100 dark:bg-blue-900 text-gray-900 dark:text-gray-100 border border-blue-200 dark:border-blue-700'
                                        : message.role === 'tool'
                                            ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800'
                                            : 'bg-gray-600 dark:bg-gray-500 text-white'"
                                >
                                    <span v-if="message.role === 'user'">U</span>
                                    <svg v-else-if="message.role === 'tool'" class="size-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path>
                                    </svg>
                                    <span v-else>AI</span>
                                </div>

                                <!-- Delete button (shown on hover) -->
                                <button type="button" @click.stop="$threads.deleteMessageFromThread(currentThread.id, message.id)"
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
                                    v-html="$fmt.markdown(message.content)"
                                    class="prose prose-sm max-w-none dark:prose-invert"
                                ></div>

                                <!-- Collapsible reasoning section -->
                                <div v-if="message.role === 'assistant' && message.reasoning" class="mt-2 mb-2">
                                    <button type="button" @click="toggleReasoning(message.id)" class="text-xs text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 flex items-center space-x-1">
                                        <svg class="w-3 h-3" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" :class="isReasoningExpanded(message.id) ? 'transform rotate-90' : ''"><path fill="currentColor" d="M7 5l6 5l-6 5z"/></svg>
                                        <span>{{ isReasoningExpanded(message.id) ? 'Hide reasoning' : 'Show reasoning' }}</span>
                                    </button>
                                    <div v-if="isReasoningExpanded(message.id)" class="reasoning mt-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-2">
                                        <div v-if="typeof message.reasoning === 'string'" v-html="$fmt.markdown(message.reasoning)" class="prose prose-xs max-w-none dark:prose-invert"></div>
                                        <pre v-else class="text-xs whitespace-pre-wrap overflow-x-auto">{{ formatReasoning(message.reasoning) }}</pre>
                                    </div>
                                </div>

                                <!-- Tool Calls & Outputs -->
                                <div v-if="message.tool_calls && message.tool_calls.length > 0" class="mb-3 space-y-4">
                                    <div v-for="(tool, i) in message.tool_calls" :key="i" class="rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden">
                                        <!-- Tool Call Header -->
                                        <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between bg-gray-50/30 dark:bg-gray-800 space-x-4">
                                            <div class="flex items-center gap-2">
                                                <svg class="size-3.5 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>
                                                <span class="font-mono text-xs font-bold text-gray-700 dark:text-gray-300">{{ tool.function.name }}</span>
                                            </div>
                                            <span class="text-[10px] uppercase tracking-wider text-gray-400 font-medium">Tool Call</span>
                                        </div>
                                        
                                        <!-- Arguments -->
                                        <div v-if="tool.function.arguments && tool.function.arguments != '{}'" class="not-prose px-3 py-2">
                                            <HtmlFormat v-if="hasJsonStructure(tool.function.arguments)" :value="tryParseJson(tool.function.arguments)" :classes="customHtmlClasses" />
                                            <pre v-else class="tool-arguments">{{ tool.function.arguments }}</pre>
                                        </div>

                                        <!-- Tool Output (Nested) -->
                                        <div v-if="getToolOutput(tool.id)" class="border-t border-gray-200 dark:border-gray-700">
                                            <div class="px-3 py-1.5 flex justify-between items-center border-b border-gray-200 dark:border-gray-800 bg-gray-50/30 dark:bg-gray-800">
                                                <div class="flex items-center gap-2 ">
                                                    <svg class="size-3.5 text-gray-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                                                    <span class="text-[10px] uppercase tracking-wider text-gray-400 font-medium">Output</span>
                                                </div>    
                                                <div v-if="hasJsonStructure(getToolOutput(tool.id).content)" class="flex items-center gap-2 text-[10px] uppercase tracking-wider font-medium select-none">
                                                    <span @click="setPrefs({ toolFormat: 'text' })" 
                                                          class="cursor-pointer transition-colors"
                                                          :class="prefs.toolFormat !== 'preview' ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'">
                                                        text
                                                    </span>
                                                    <span class="text-gray-300 dark:text-gray-700">|</span>
                                                    <span @click="setPrefs({ toolFormat: 'preview' })" 
                                                          class="cursor-pointer transition-colors"
                                                          :class="prefs.toolFormat == 'preview' ? 'text-gray-600 dark:text-gray-300' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'">
                                                        preview
                                                    </span>
                                                </div>
                                            </div>
                                            <div class="not-prose px-3 py-2">
                                                <pre v-if="prefs.toolFormat !== 'preview' || !hasJsonStructure(getToolOutput(tool.id).content)" class="tool-output">{{ getToolOutput(tool.id).content }}</pre>
                                                <div v-else class="text-xs">
                                                    <HtmlFormat v-if="tryParseJson(getToolOutput(tool.id).content)" :value="tryParseJson(getToolOutput(tool.id).content)" :classes="customHtmlClasses" />
                                                    <div v-else class="text-gray-500 italic p-2">Invalid JSON content</div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Tool Output (Orphaned) -->
                                <div v-if="message.role === 'tool' && !isToolLinked(message)" class="text-sm">
                                    <div class="flex items-center gap-2 mb-1 opacity-70">
                                        <div class="flex items-center text-xs font-mono font-medium text-gray-500 uppercase tracking-wider">
                                            <svg class="size-3 mr-1" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                                            Tool Output
                                        </div>
                                        <div v-if="message.name" class="text-xs font-mono bg-gray-200 dark:bg-gray-700 px-1.5 rounded text-gray-700 dark:text-gray-300">
                                            {{ message.name }}
                                        </div>
                                        <div v-if="message.tool_call_id" class="text-[10px] font-mono text-gray-400">
                                            {{ message.tool_call_id.slice(0,8) }}
                                        </div>
                                    </div>
                                    <div class="not-prose bg-white dark:bg-gray-900 rounded border border-gray-200 dark:border-gray-800 p-2 overflow-x-auto">
                                        <pre class="tool-output">{{ message.content }}</pre>
                                    </div>
                                </div>

                                <!-- Assistant Images -->
                                <div v-if="message.images && message.images.length > 0" class="mt-2 flex flex-wrap gap-2">
                                    <template v-for="(img, i) in message.images" :key="i">
                                        <div v-if="img.type === 'image_url'" class="group relative cursor-pointer" @click="openLightbox(resolveUrl(img.image_url.url))">
                                            <img :src="resolveUrl(img.image_url.url)" class="max-w-[400px] max-h-96 rounded-lg border border-gray-200 dark:border-gray-700 object-contain bg-gray-50 dark:bg-gray-900 shadow-sm transition-transform hover:scale-[1.02]" />
                                        </div>
                                    </template>
                                </div>

                                <!-- Assistant Audios -->
                                <div v-if="message.audios && message.audios.length > 0" class="mt-2 flex flex-wrap gap-2">
                                    <template v-for="(audio, i) in message.audios" :key="i">
                                        <div v-if="audio.type === 'audio_url'" class="flex items-center gap-2 p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
                                            <audio controls :src="resolveUrl(audio.audio_url.url)" class="h-8 w-64"></audio>
                                        </div>
                                    </template>
                                </div>

                                <!-- User Message with separate attachments -->
                                <div v-else-if="message.role !== 'assistant' && message.role !== 'tool'">
                                    <div v-html="$fmt.markdown(message.content)" class="prose prose-sm max-w-none dark:prose-invert break-words"></div>
                                    
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

                                <MessageUsage :message="message" :usage="getMessageUsage(message)" />
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
                            <span :title="$fmt.statsTitle(currentThread.stats)">
                                {{ currentThread.stats.cost ? $fmt.costLong(currentThread.stats.cost) + '  for ' : '' }} {{ $fmt.humanifyNumber(currentThread.stats.inputTokens) }} → {{ $fmt.humanifyNumber(currentThread.stats.outputTokens) }} tokens over {{ currentThread.stats.requests }} request{{currentThread.stats.requests===1?'':'s'}} in {{ $fmt.humanifyMs(currentThread.stats.duration) }}
                            </span>
                        </div>

                        <!-- Loading indicator -->
                        <div v-if="$threads.watchingThread" class="flex items-start space-x-3 group">
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
                            <button type="button" @click="$threads.cancelThread()"
                                class="px-3 py-1 rounded text-sm text-gray-400 dark:text-gray-500 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 border border-transparent hover:border-red-300 dark:hover:border-red-600 transition-all"
                                title="Cancel request">
                                cancel
                            </button>
                        </div>

                        <!-- Thread error message bubble -->
                        <div v-if="currentThread?.error" class="mt-8 flex items-center space-x-3">
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0">
                                <div class="size-8 rounded-full bg-red-600 dark:bg-red-500 text-white flex items-center justify-center text-lg font-bold">
                                    !
                                </div>
                            </div>
                            <!-- Error bubble -->
                            <div class="max-w-[85%] rounded-lg px-3 py-1 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-200 shadow-sm">
                                <div class="flex items-start space-x-2">
                                    <div class="flex-1 min-w-0">
                                        <div v-if="currentThread.error" class="text-base mb-1">{{ currentThread.error }}</div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Error message bubble -->
                        <div v-if="$state.error" class="mt-8 flex items-start space-x-3">
                            <!-- Avatar outside the bubble -->
                            <div class="flex-shrink-0">
                                <div class="size-8 rounded-full bg-red-600 dark:bg-red-500 text-white flex items-center justify-center text-lg font-bold">
                                    !
                                </div>
                            </div>

                            <!-- Error bubble -->
                            <div class="max-w-[85%] rounded-lg px-4 py-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-200 shadow-sm">
                                <div class="flex items-start space-x-2">
                                    <div class="flex-1 min-w-0">
                                        <div class="flex justify-between items-start">
                                            <div class="text-base font-medium mb-1">{{ $state.error?.errorCode || 'Error' }}</div>
                                            <button type="button" @click="$ctx.clearError()" title="Clear Error"
                                                class="text-red-400 dark:text-red-300 hover:text-red-600 dark:hover:text-red-100 flex-shrink-0">
                                                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                                                </svg>
                                            </button>
                                        </div>
                                        <div v-if="$state.error?.message" class="text-base mb-1">{{ $state.error.message }}</div>
                                        <div v-if="$state.error?.stackTrace" class="mt-2 text-sm whitespace-pre-wrap break-words max-h-80 overflow-y-auto font-mono p-2 border border-red-200/70 dark:border-red-800/70">
                                            {{ $state.error.stackTrace }}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

            </div>

            <!-- Input Area -->
            <div v-if="$ai.hasAccess" :class="$ctx.cls('chat-input', 'flex-shrink-0 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-6 py-4')">
                <ChatPrompt :model="$chat.getSelectedModel()" />
            </div>
            
            <!-- Lightbox -->
            <div v-if="lightboxUrl" class="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center p-4 cursor-pointer" 
                @click="closeLightbox">
                <button type="button" @click="closeLightbox"
                    class="absolute top-4 right-4 text-white/70 hover:text-white p-2 rounded-full hover:bg-white/10 transition-colors z-[101]"
                    title="Close">
                    <svg class="size-8" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
                <div class="relative max-w-full max-h-full">
                    <img :src="lightboxUrl" class="max-w-full max-h-[90vh] object-contain rounded-sm shadow-2xl" @click.stop />
                </div>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const models = ctx.state.models
        const config = ctx.state.config
        const threads = ctx.threads
        const chatPrompt = ctx.chat
        const { currentThread } = threads

        const router = useRouter()
        const route = useRoute()

        const prefs = ref(ctx.getPrefs())

        const selectedModel = ref(prefs.value.model || config.defaults.text.model || '')
        const selectedModelObj = computed(() => {
            if (!selectedModel.value || !models) return null
            return models.find(m => m.name === selectedModel.value) || models.find(m => m.id === selectedModel.value)
        })
        const messagesContainer = ref(null)
        const copying = ref(null)
        const lightboxUrl = ref(null)

        const openLightbox = (url) => {
            lightboxUrl.value = url
        }
        const closeLightbox = () => {
            lightboxUrl.value = null
        }

        const resolveUrl = (url) => {
            if (url && url.startsWith('~')) {
                return '/' + url
            }
            return ctx.ai.resolveUrl(url)
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
            // console.debug('watch route.params.id', newId)
            ctx.clearError()
            threads.setCurrentThreadFromRoute(newId, router)

            if (!newId) {
                chatPrompt.reset()
            }
            nextTick(ctx.chat.addCopyButtons)
        }, { immediate: true })

        watch(() => [selectedModel.value], () => {
            ctx.setPrefs({
                model: selectedModel.value,
            })
        })
        function configUpdated() {
            console.log('configUpdated', selectedModel.value, models.length, models.includes(selectedModel.value))
            if (selectedModel.value && !models.includes(selectedModel.value)) {
                selectedModel.value = config.defaults.text.model || ''
            }
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

            const infos = await ctx.ai.fetchCacheInfos(getCacheInfos)
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

            const threadId = currentThread.value.id

            // Clear all messages after this one
            await threads.redoMessageFromThread(threadId, message.timestamp)

            const state = await extractMessageState(message)

            // Set the message text in the chat prompt
            chatPrompt.messageText.value = state.text

            // Restore attached files
            chatPrompt.attachedFiles.value = state.files
        }

        // Edit a user message
        const editMessage = async (message) => {
            if (!currentThread.value || message.role !== 'user') return

            // set the message in the input box
            const state = await extractMessageState(message)
            chatPrompt.messageText.value = state.text
            chatPrompt.attachedFiles.value = state.files
            chatPrompt.editingMessage.value = message.timestamp

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

        let sub
        onMounted(() => {
            sub = ctx.events.subscribe(`keydown:Escape`, closeLightbox)
            setTimeout(ctx.chat.addCopyButtons, 1)
        })
        onUnmounted(() => sub?.unsubscribe())

        const getToolOutput = (toolCallId) => {
            return currentThread.value?.messages?.find(m => m.role === 'tool' && m.tool_call_id === toolCallId)
        }

        const getMessageUsage = (message) => {
            if (message.usage) return message.usage
            if (message.tool_calls?.length) {
                const toolUsages = message.tool_calls.map(tc => getToolOutput(tc.id)?.usage)
                const agg = {
                    tokens: toolUsages.reduce((a, b) => a + (b?.tokens || 0), 0),
                    cost: toolUsages.reduce((a, b) => a + (b?.cost || 0), 0),
                    duration: toolUsages.reduce((a, b) => a + (b?.duration || 0), 0)
                }
                return agg
            }
            return null
        }

        const isToolLinked = (message) => {
            if (message.role !== 'tool') return false
            return currentThread.value?.messages?.some(m => m.role === 'assistant' && m.tool_calls?.some(tc => tc.id === message.tool_call_id))
        }

        const tryParseJson = (str) => {
            try {
                return JSON.parse(str)
            } catch (e) {
                return null
            }
        }
        const hasJsonStructure = (str) => {
            return tryParseJson(str) != null
        }
        /**
         * @param {object|array} type 
         * @param {'div'|'table'|'thead'|'th'|'tr'|'td'} tag 
         * @param {number} depth 
         * @param {string} cls 
         * @param {number} index 
        */
        const customHtmlClasses = (type, tag, depth, cls, index) => {
            cls = cls.replace('shadow ring-1 ring-black/5 md:rounded-lg', '')
            if (tag == 'th') {
                cls += ' lowercase'
            }
            if (tag == 'td') {
                cls += ' whitespace-pre-wrap'
            }
            return cls
        }

        function setPrefs(o) {
            Object.assign(prefs.value, o)
            ctx.setPrefs(prefs.value)
        }

        return {
            prefs,
            setPrefs,
            config,
            models,
            currentThread,
            selectedModel,
            selectedModelObj,
            messagesContainer,
            copying,
            isReasoningExpanded,
            toggleReasoning,
            formatReasoning,
            copyMessageContent,
            redoMessage,
            editMessage,
            configUpdated,
            getAttachments,
            hasAttachments,
            lightboxUrl,
            openLightbox,
            closeLightbox,
            resolveUrl,
            getMessageUsage,
            getToolOutput,
            isToolLinked,
            tryParseJson,
            hasJsonStructure,
            customHtmlClasses,
        }
    }
}
