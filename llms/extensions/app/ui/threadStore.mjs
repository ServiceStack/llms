import { ref, computed } from 'vue'
import { appendQueryString, createErrorStatus } from '@servicestack/client'

/**
 * Returns an ever-increasing unique integer id.
 */
export const nextId = (() => {
    let last = 0               // cache of the last id that was handed out
    return () => {
        const now = Date.now() // current millisecond timestamp
        last = (now > last) ? now : last + 1
        return last
    }
})();


const threads = ref([])
const currentThread = ref(null)
const isLoading = ref(false)

let ctx = null
let ext = null

// Generate unique thread ID
function generateThreadId() {
    return Date.now().toString()
}

function setError(error, msg = null) {
    ctx?.setError(error, msg)
}

async function query(query) {
    return (await ext.getJson(appendQueryString(`/threads`, query))).response || []
}

let watchThreadTimeout = ref(null)
async function watchThreadUpdates() {
    const thread = currentThread.value
    // console.debug('watchThreadUpdates', thread?.id, thread?.messages?.length, thread?.completedAt)
    if (thread && thread?.messages?.length && !thread.completedAt) {
        const api = await ext.getJson(appendQueryString(`/threads/${thread.id}/updates`, { after: thread.updatedAt }))
        // console.log('watchThreadUpdates', api)
        if (api.response) {
            replaceThread(api.response)
            return
        } else {
            setError(api.error, `watching thread ${thread.id}`)
        }
    }
    stopWatchingThread()
}

function startWatchingThread() {
    stopWatchingThread()
    const thread = currentThread.value
    if (thread && thread?.messages?.length && !thread.completedAt) {
        watchThreadTimeout.value = setTimeout(watchThreadUpdates, 100)
    }
}

function stopWatchingThread() {
    console.debug('stopWatchingThread')
    if (watchThreadTimeout.value) {
        clearTimeout(watchThreadTimeout.value)
    }
    watchThreadTimeout.value = null
}

const isWatchingThread = computed(() => watchThreadTimeout.value != null)

async function cancelThread() {
    console.log('cancelThread')
    stopWatchingThread()
    const thread = currentThread.value
    if (!thread) return
    const api = await ext.postJson(`/threads/${thread.id}/cancel`)
    if (api.response) {
        replaceThread(api.response)
    } else {
        setError(api.error, `Canceling thread ${thread.id}`)
    }
}

// Create a new thread
async function createThread(args = {}) {
    const thread = {
        messages: [],
        ...args
    }
    if (!thread.title) {
        thread.title = 'New Chat'
    }
    if (thread.title.length > 200) {
        thread.title = thread.title.slice(0, 200) + '...'
    }

    ctx.createThreadFilters.forEach(f => f(thread))

    const api = await ext.postJson("/threads", thread)
    if (api.response) {
        threads.value.unshift(api.response)
        return api.response
    } else {
        setError(api.error, `Creating thread ${thread.title}`)
    }

    return thread
}

function replaceThread(thread) {
    if (!thread) {
        console.error('replaceThread(null)')
        return
    }
    const index = threads.value.findIndex(t => t.id === thread.id)
    if (index !== -1) {
        threads.value[index] = thread
    }
    if (currentThread.value?.id === thread.id) {
        currentThread.value = thread
    }
    startWatchingThread()
    return thread
}

// Update thread
async function updateThread(threadId, updates) {

    if (!threadId)
        throw new Error('threadId is required')

    ctx.updateThreadFilters.forEach(f => f(updates))

    const api = await ext.patchJson(`/threads/${threadId}`, updates)
    if (api.response) {
        return replaceThread(api.response)
    } else {
        setError(api.error, `Updating thread ${threadId}`)
    }
}

async function deleteMessageFromThread(threadId, messageId) {
    const thread = await getThread(threadId)
    if (!thread) throw new Error('Thread not found')
    const updatedMessages = thread.messages.filter(m => m.id !== messageId)
    await updateThread(threadId, { messages: updatedMessages })
}

async function updateMessageInThread(threadId, messageId, updates) {
    const thread = await getThread(threadId)
    if (!thread) throw new Error('Thread not found')

    const messageIndex = thread.messages.findIndex(m => m.id === messageId)
    if (messageIndex === -1) throw new Error('Message not found')

    const updatedMessages = [...thread.messages]
    updatedMessages[messageIndex] = {
        ...updatedMessages[messageIndex],
        ...updates
    }

    await updateThread(threadId, { messages: updatedMessages })
}

async function redoMessageFromThread(threadId, timestamp) {
    const thread = await getThread(threadId)
    if (!thread) throw new Error('Thread not found')

    // Find the index of the message to redo
    const messageIndex = thread.messages.findIndex(m => m.timestamp === timestamp)
    if (messageIndex === -1) {
        setError(createErrorStatus(`Message not found for timestamp ${timestamp}`))
        return
    }

    // setError({
    //     errorCode: 'TestError',
    //     message: `Error redoing message ${timestamp} in thread ${threadId}`,
    //     stackTrace: `Error in page.mjs
    //         at Line 1
    //         at Line 2
    //         at Line 3`,
    // })
    // return

    // Keep only messages up to and including the target message
    const updatedMessages = thread.messages.slice(0, messageIndex + 1)

    // Update the thread with the new messages
    const api = await queueChat(threadId, { messages: updatedMessages })
    if (api.response) {
        replaceThread(api.response)
    } else {
        setError(api.error, `Redoing message ${timestamp} in thread ${threadId}`)
    }
}

async function loadThreads() {
    isLoading.value = true

    try {
        const api = await ext.getJson('/threads?take=50')
        threads.value = api.response || []
        return threads.value
    } finally {
        isLoading.value = false
    }
}

async function getThread(threadId) {
    const cachedThread = threads.value.find(t => t.id == threadId)
    if (cachedThread) return cachedThread
    const api = await ext.getJson(`/threads?id=${threadId}`)
    return api.response && api.response[0] || null
}

// Delete thread
async function deleteThread(threadId) {
    await ext.delete(`/threads/${threadId}`)

    threads.value = threads.value.filter(t => t.id !== threadId)

    if (currentThread.value?.id === threadId) {
        currentThread.value = null
    }
}

// Set current thread
async function setCurrentThread(threadId) {
    const thread = await getThread(threadId)
    if (thread) {
        currentThread.value = thread
        startWatchingThread()
    }
    return thread
}

// Set current thread from router params (router-aware version)
async function setCurrentThreadFromRoute(threadId, router) {
    if (!threadId) {
        currentThread.value = null
        return null
    }

    const thread = setCurrentThread(threadId)
    if (thread) {
        return thread
    } else {
        // Thread not found, redirect to home
        if (router) {
            router.push((globalThis.ai?.base || '') + '/')
        }
        currentThread.value = null
        return null
    }
}

// Clear current thread (go back to initial state)
function clearCurrentThread() {
    currentThread.value = null
}

function getGroupedThreads(total) {
    const now = new Date()
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000)
    const lastWeek = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)
    const lastMonth = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000)

    const groups = {
        today: [],
        yesterday: [],
        lastWeek: [],
        lastMonth: [],
        older: {}
    }

    const takeThreads = threads.value.slice(0, total)

    takeThreads.forEach(thread => {
        const threadDate = new Date(thread.updatedAt)

        if (threadDate >= today) {
            groups.today.push(thread)
        } else if (threadDate >= yesterday) {
            groups.yesterday.push(thread)
        } else if (threadDate >= lastWeek) {
            groups.lastWeek.push(thread)
        } else if (threadDate >= lastMonth) {
            groups.lastMonth.push(thread)
        } else {
            const year = threadDate.getFullYear()
            const month = threadDate.toLocaleString('default', { month: 'long' })
            const key = `${month} ${year}`

            if (!groups.older[key]) {
                groups.older[key] = []
            }
            groups.older[key].push(thread)
        }
    })

    return groups
}

// Group threads by time periods
const groupedThreads = computed(() => getGroupedThreads(threads.value.length))

function getLatestCachedThread() {
    return threads.value[0]
}

async function startNewThread({ title, model }) {
    if (!model) {
        console.error('No model selected')
        return
    }
    if (!title) {
        title = 'New Chat'
    }
    const latestThread = getLatestCachedThread()

    console.log('startNewThread', title, model.name, ctx.router.currentRoute.value?.path, latestThread?.messages?.length)
    ctx.setLayout({ left: 'ThreadsSidebar' })

    if (latestThread && latestThread.title == title && !latestThread.messages?.length) {
        if (ctx.router.currentRoute.value?.path != `/c/${latestThread.id}`) {
            ctx.to(`/c/${latestThread.id}`)
        }
        return latestThread
    }
    const newThread = await createThread({
        title,
        model: model.name,
        info: ctx.utils.toModelInfo(model),
    })

    console.log('newThread', newThread, model)
    // Navigate to the new thread URL
    ctx.to(`/c/${newThread.id}`)

    // Get the thread to check for duplicates
    let thread = await getThread(newThread.id)
    console.log('thread', thread)
    return thread
}

async function queueChat(threadId, body, options = {}) {
    const api = await ctx.postJson(`/ext/app/threads/${threadId}/chat`, {
        ...options,
        body: typeof body == 'string'
            ? body
            : JSON.stringify(body),
    })
    return api
}

// Export the store
export function useThreadStore() {
    return {
        // State
        threads,
        currentThread,
        isLoading,
        groupedThreads,

        // Actions
        query,
        createThread,
        updateThread,
        deleteMessageFromThread,
        updateMessageInThread,
        redoMessageFromThread,
        loadThreads,
        getThread,
        deleteThread,
        setCurrentThread,
        setCurrentThreadFromRoute,
        clearCurrentThread,
        getGroupedThreads,
        getLatestCachedThread,
        generateThreadId,
        startNewThread,
        replaceThread,
        queueChat,
        isWatchingThread,
        startWatchingThread,
        stopWatchingThread,
        cancelThread,
        get watchingThread() {
            return isWatchingThread.value
        },
    }
}

export default {
    install(context) {
        ctx = context
        ext = ctx.scope('app')
        ctx.setGlobals({ threads: useThreadStore() })
    },

    async load() {
        await ctx.threads.loadThreads()
    }
}
