import { inject } from "vue"
import Sidebar from "./Sidebar.mjs"

export default {
    components: {
        Sidebar,
    },
    setup() {
        const ai = inject('ai')
        return { ai }
    },
    template: `
        <div class="flex h-screen bg-white dark:bg-gray-900">
            <!-- Sidebar (hidden when auth required and not authenticated) -->
            <div v-if="!(ai.requiresAuth && !ai.auth)" class="w-72 xl:w-80 flex-shrink-0">
                <Sidebar />
            </div>

            <!-- Main Area -->
            <div class="flex-1 flex flex-col">
                <RouterView />
            </div>
        </div>
    `,
}
