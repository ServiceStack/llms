import { ref, inject } from 'vue'

export default {
    template: `
        <!-- Export/Import buttons -->
        <div class="mt-4 flex space-x-3 justify-center items-center">
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

    `,
    setup() {
        const ctx = inject('ctx')
        const threads = ctx.threads

        const isExporting = ref(false)
        const isImporting = ref(false)
        const fileInput = ref(null)

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

        return {
            exportThreads,
            exportRequests,
            isExporting,
            triggerImport,
            handleFileImport,
            isImporting,
            fileInput,
        }
    }
}