import { computed, inject, ref, onMounted, onUnmounted } from "vue"
import { toJsonObject } from "../utils.mjs"

const Brand = {
    template: `
    <div class="flex-shrink-0 p-2 border-b border-gray-200 dark:border-gray-700 select-none">
        <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
                <button type="button"
                    @click="$ctx.to('/')"
                    class="text-lg font-semibold text-gray-900 dark:text-gray-200 hover:text-blue-600 dark:hover:text-blue-400 focus:outline-none transition-colors"
                    title="Go back home">
                    {{ $state.title }}
                </button>
            </div>
        </div>
    </div>
    `,
}

const Welcome = {
    template: `
        <div class="mb-2 flex justify-center">
            <svg class="size-20 text-gray-700 dark:text-gray-300" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="currentColor" d="M8 2.19c3.13 0 5.68 2.25 5.68 5s-2.55 5-5.68 5a5.7 5.7 0 0 1-1.89-.29l-.75-.26l-.56.56a14 14 0 0 1-2 1.55a.13.13 0 0 1-.07 0v-.06a6.58 6.58 0 0 0 .15-4.29a5.25 5.25 0 0 1-.55-2.16c0-2.77 2.55-5 5.68-5M8 .94c-3.83 0-6.93 2.81-6.93 6.27a6.4 6.4 0 0 0 .64 2.64a5.53 5.53 0 0 1-.18 3.48a1.32 1.32 0 0 0 2 1.5a15 15 0 0 0 2.16-1.71a6.8 6.8 0 0 0 2.31.36c3.83 0 6.93-2.81 6.93-6.27S11.83.94 8 .94"/><ellipse cx="5.2" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/><ellipse cx="10.8" cy="7.7" fill="currentColor" rx=".8" ry=".75"/></svg>
        </div>
        <h2 class="text-2xl font-semibold text-gray-900 dark:text-gray-100 mb-2">{{ $ai.welcome }}</h2>
    `
}

const Avatar = {
    template: `
        <div v-if="$ai.auth?.profileUrl" class="relative" ref="avatarContainer">
            <img
                @click.stop="toggleMenu"
                :src="$ai.auth.profileUrl"
                :title="authTitle"
                class="mr-1 size-6 rounded-full cursor-pointer hover:ring-2 hover:ring-gray-300"
            />
            <div
                v-if="showMenu"
                @click.stop
                class="absolute right-0 mt-2 w-48 bg-white dark:bg-gray-800 rounded-md shadow-lg py-1 z-50 border border-gray-200 dark:border-gray-700"
            >
                <div class="px-4 py-2 text-sm text-gray-700 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700">
                    <div class="font-medium whitespace-nowrap overflow-hidden text-ellipsis">{{ $ai.auth.displayName || $ai.auth.userName }}</div>
                    <div class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap overflow-hidden text-ellipsis">{{ $ai.auth.email }}</div>
                </div>
                <button type="button"
                    @click="handleLogout"
                    class="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center whitespace-nowrap"
                >
                    <svg class="w-4 h-4 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path>
                    </svg>
                    Sign Out
                </button>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const ai = ctx.ai
        const showMenu = ref(false)
        const avatarContainer = ref(null)

        const authTitle = computed(() => {
            if (!ai.auth) return ''
            const { userId, userName, displayName, bearerToken, roles } = ai.auth
            const name = userName || displayName
            const prefix = roles && roles.includes('Admin') ? 'Admin' : 'Name'
            const sb = [
                name ? `${prefix}: ${name}` : '',
                `API Key: ${bearerToken}`,
                `${userId}`,
            ]
            return sb.filter(x => x).join('\n')
        })

        function toggleMenu() {
            showMenu.value = !showMenu.value
        }

        async function handleLogout() {
            showMenu.value = false
            await ai.signOut()
            // Reload the page to show sign-in screen
            window.location.reload()
        }

        // Close menu when clicking outside
        const handleClickOutside = (event) => {
            if (showMenu.value && avatarContainer.value && !avatarContainer.value.contains(event.target)) {
                showMenu.value = false
            }
        }

        onMounted(() => {
            document.addEventListener('click', handleClickOutside)
        })

        onUnmounted(() => {
            document.removeEventListener('click', handleClickOutside)
        })

        return {
            authTitle,
            handleLogout,
            showMenu,
            toggleMenu,
            avatarContainer,
        }
    }
}

const SignIn = {
    template: `
    <div class="min-h-full -mt-12 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
        <div class="sm:mx-auto sm:w-full sm:max-w-md">
            <h2 class="mt-6 text-center text-3xl font-extrabold text-gray-900 dark:text-gray-50">
                Sign In
            </h2>
        </div>
        <div class="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
            <ErrorSummary v-if="errorSummary" class="mb-3" :status="errorSummary" />
            <div class="bg-white dark:bg-black py-8 px-4 shadow sm:rounded-lg sm:px-10">
                <form @submit.prevent="submit">
                    <div class="flex flex-1 flex-col justify-between">
                        <div class="space-y-6">
                            <fieldset class="grid grid-cols-12 gap-6">
                                <div class="w-full col-span-12">
                                    <TextInput id="apiKey" name="apiKey" label="API Key" v-model="apiKey" />
                                </div>
                            </fieldset>
                        </div>
                    </div>
                    <div class="mt-8">
                        <PrimaryButton class="w-full">Sign In</PrimaryButton>
                    </div>
                </form>
            </div>
        </div>
    </div>     
    `,
    emits: ['done'],
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const ai = ctx.ai
        const apiKey = ref('')
        const errorSummary = ref()
        async function submit() {
            const r = await ai.get('/auth', {
                headers: {
                    'Authorization': `Bearer ${apiKey.value}`
                },
            })
            const txt = await r.text()
            const json = toJsonObject(txt)
            // console.log('json', json)
            if (r.ok) {
                json.apiKey = apiKey.value
                emit('done', json)
            } else {
                errorSummary.value = json.responseStatus || {
                    errorCode: "Unauthorized",
                    message: 'Invalid API Key'
                }
            }
        }

        return {
            apiKey,
            submit,
            errorSummary,
        }
    }
}

const ErrorViewer = {
    template: `
        <div v-if="$state.error" class="rounded-lg px-4 py-3 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-800 dark:text-red-200 shadow-sm">
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
    `,
    setup() {

    }
}

export default {
    install(ctx) {
        ctx.components({
            Brand,
            Welcome,
            Avatar,
            SignIn,
            ErrorViewer,
        })
    }
}
