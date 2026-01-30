import { ref, computed, watch, inject, onMounted, onUnmounted } from "vue"
import { useRouter, useRoute } from "vue-router"
import { AppContext } from "./ctx.mjs"

// Vertical Sidebar Icons
const LeftBar = {
    template: `
        <div class="select-none flex flex-col space-y-2 pt-2.5 px-1">
            <div v-for="(icon, id) in $ctx.left" :key="id" class="relative flex items-center justify-center">
                <component :is="icon.component" 
                    class="size-7 p-1 cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 rounded block"
                    :class="{ 'bg-gray-200 dark:bg-gray-700' : icon.isActive({ ...$layout }) }" 
                    @mouseenter="tooltip = icon.id"
                    @mouseleave="tooltip = ''"
                    />
                <div v-if="tooltip === icon.id && !icon.isActive({ ...$layout })" 
                    class="absolute left-full top-1/2 -translate-y-1/2 ml-2 px-2 py-1 text-xs text-white bg-gray-900 dark:bg-gray-800 rounded shadow-md z-50 whitespace-nowrap pointer-events-none" style="z-index: 60">
                    {{icon.title ?? icon.name}}
                </div>    
            </div>
        </div>
    `,
    setup() {
        const tooltip = ref('')
        return {
            tooltip,
        }
    }
}

const LeftPanel = {
    template: `
        <div v-if="component" class="flex flex-col h-full border-r border-gray-200 dark:border-gray-700">
            <button type="button" @click="$ctx.toggleLayout('left',false)" class="absolute top-2 right-2 p-1 rounded-md text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 lg:hidden z-20">
                <svg class="size-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
            </button>
            <component :is="component" />
        </div>
    `,
    setup() {
        /**@type {AppContext} */
        const ctx = inject('ctx')
        const component = computed(() => ctx.component(ctx.layout.left))
        return {
            component,
        }
    }
}

const TopBar = {
    template: `
        <div class="select-none flex space-x-1">
            <div v-for="(icon, id) in $ctx.top" :key="id" class="relative flex items-center justify-center">
                <component :is="icon.component" 
                    class="size-7 p-1 cursor-pointer text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 block border border-transparent"
                    :class="{ 'bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-600 rounded' : icon.isActive({ ...$layout }) }" 
                    @mouseenter="tooltip = icon.id"
                    @mouseleave="tooltip = ''"
                    />
                <div v-if="tooltip === icon.id && !icon.isActive({ ...$layout })" 
                    class="absolute top-full mt-2 px-2 py-1 text-xs text-white bg-gray-900 dark:bg-gray-800 rounded shadow-md z-50 whitespace-nowrap pointer-events-none"
                    :class="last2.includes(id) ? 'right-0' : 'left-1/2 -translate-x-1/2'">
                    {{icon.title ?? icon.name}}
                </div>    
            </div>
        </div>
    `,
    setup() {
        const tooltip = ref('')
        const last2 = ref(Object.keys($ctx.top).slice(-2))
        return {
            tooltip,
            last2,
        }
    }
}

const TopPanel = {
    template: `
        <component v-if="component" :is="component" class="mb-2" />
    `,
    setup() {
        /**@type {AppContext} */
        const ctx = inject('ctx')
        const component = computed(() => ctx.component(ctx.layout.top))
        return {
            component,
        }
    }
}

export default {
    components: {
        LeftBar,
        LeftPanel,
        TopBar,
        TopPanel,
    },
    setup() {
        const router = useRouter()
        const route = useRoute()

        /**@type {AppContext} */
        const ctx = inject('ctx')
        const ai = ctx.ai
        const isMobile = ref(false)
        const modal = ref()

        const checkMobile = () => {
            //const wasMobile = isMobile.value
            isMobile.value = window.innerWidth < 640 // sm breakpoint

            //console.log('checkMobile', wasMobile, isMobile.value)
            // Only auto-adjust sidebar state when transitioning between mobile/desktop
            if (isMobile.value) {
                ctx.toggleLayout('left', false)
            }
        }

        onMounted(() => {
            checkMobile()
            window.addEventListener('resize', checkMobile)
            if (route.query.open) {
                modal.value = ctx.openModal(route.query.open)
            }
        })

        onUnmounted(() => {
            window.removeEventListener('resize', checkMobile)
        })

        function closeModal() {
            ctx.closeModal(route.query.open)
        }

        watch(() => route.query.open, (newVal) => {
            modal.value = ctx.modalComponents[newVal]
            console.log('open', newVal, modal.value)
        })

        watch(() => ctx.state.selectedModel, (newVal) => {
            ctx.chat.setSelectedModel(ctx.chat.getModel(newVal))
        })

        return { ai, modal, isMobile, closeModal }
    },
    template: `
        <div class="flex h-screen">
            <!-- Mobile Overlay -->
            <div v-if="isMobile && $ctx.layoutVisible('left') && $ai.hasAccess"
                @click="$ctx.toggleLayout('left')"
                :class="$ctx.cls('mobile-overlay', 'fixed inset-0 bg-black/50 z-40 lg:hidden')"
            ></div>

            <div v-if="$ai.hasAccess" id="sidebar" :class="$ctx.cls('sidebar', 'z-100 relative flex bg-gray-50 dark:bg-gray-800')">
                <LeftBar id="left-bar" />
                <LeftPanel id="left-panel"
                    v-if="$ai.hasAccess && $ctx.layoutVisible('left')"
                    :class="[
                        'transition-transform duration-300 ease-in-out z-50',
                        'w-72 xl:w-80 flex-shrink-0',
                        'lg:relative',
                        'fixed inset-y-0 left-[2.25rem] lg:left-0',
                        'bg-gray-50 dark:bg-gray-800'
                    ]"
                />
            </div>

            <!-- Main Area -->
            <div id="main" :class="$ctx.cls('main', 'flex-1 flex flex-col')">
                <div id="main-inner" :class="$ctx.cls('main-inner', 'flex flex-col h-full w-full overflow-hidden')">
                    <div v-if="$ai.hasAccess" id="header" :class="$ctx.cls('header', 'py-1 pr-1 flex items-center justify-between shrink-0')">
                        <div>
                            <ModelSelector :models="$state.models" v-model="$state.selectedModel" />
                        </div>
                        <div class="flex items-center gap-2">
                            <TopBar id="top-bar" />
                            <Avatar />
                        </div>
                    </div>
                    <TopPanel v-if="$ai.hasAccess" id="top-panel" :class="$ctx.cls('top-panel', 'shrink-0')" />
                    <div id="page" :class="$ctx.cls('page', 'flex-1 overflow-y-auto min-h-0 flex flex-col')">
                        <RouterView class="h-full" />
                    </div>
                </div>
            </div>

            <component v-if="modal" :is="modal" :class="$ctx.cls('modal', '!z-[200]')" @done="closeModal" />
        </div>
    `,
}
