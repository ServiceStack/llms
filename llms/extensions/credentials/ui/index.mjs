import { ref, computed, inject } from "vue"

let ext = null

const SignIn = {
    template: `
    <div class="min-h-full -mt-12 flex flex-col justify-center py-12 sm:px-6 lg:px-8">
        <div class="sm:mx-auto sm:w-full sm:max-w-md">
            <h2 class="mt-6 text-center text-3xl font-extrabold" :class="[$styles.heading]">
                Sign In
            </h2>
        </div>
        <div class="mt-8 sm:mx-auto sm:w-full sm:max-w-md">
            <ErrorSummary v-if="errorSummary" class="mb-3" :status="errorSummary" />
            <div class="py-8 px-4 shadow sm:rounded-lg sm:px-10" :class="[$styles.infoCard]">
                <form @submit.prevent="submit">
                    <div class="flex flex-1 flex-col justify-between">
                        <div class="space-y-6">
                            <fieldset class="grid grid-cols-12 gap-6">
                                <div class="w-full col-span-12">
                                    <label for="username" class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Username</label>
                                    <input type="text" v-model="username"
                                        placeholder="Username"
                                        class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                </div>
                                <div class="w-full col-span-12">
                                    <label for="password" class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Password</label>
                                    <input type="password" v-model="password"
                                        placeholder="Password"
                                        class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                </div>
                            </fieldset>
                        </div>
                    </div>
                    <div class="mt-8">
                        <button type="submit" class="w-full px-3 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">Sign In</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    `,
    emits: ['done'],
    setup(props, { emit }) {
        const ctx = inject("ctx")
        const username = ref('')
        const password = ref('')
        const errorSummary = ref()

        async function submit() {
            errorSummary.value = null
            if (!username.value || !password.value) {
                errorSummary.value = {
                    errorCode: "Validation",
                    message: "Username and password are required"
                }
                return
            }

            const api = await ctx.postJson('/auth/login', {
                body: JSON.stringify({
                    username: username.value,
                    password: password.value,
                })
            })
            if (api.response) {
                emit('done', api.response)
            } else {
                errorSummary.value = api.error
            }
        }

        return {
            username,
            password,
            submit,
            errorSummary,
        }
    }
}

const ManageUsersPage = {
    template: `
    <div class="max-w-3xl mx-auto p-6">
        <div class="flex items-center justify-between mb-8">
            <h1 class="text-2xl font-bold" :class="[$styles.heading]">Manage Users</h1>
            <button type="button" @click="showCreate = true" v-if="!showCreate"
                class="px-4 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">
                New User
            </button>
        </div>

        <ErrorSummary v-if="errorSummary" class="mb-4" :status="errorSummary" />

        <!-- Create User Form -->
        <div v-if="showCreate" class="mb-6 p-6 rounded-xl shadow-sm" :class="[$styles.card]">
            <h2 class="text-lg font-semibold mb-4" :class="[$styles.heading]">Create User</h2>
            <form @submit.prevent="createUser" class="space-y-4">
                <div>
                    <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Username</label>
                    <input type="text" v-model="newUser.userName" placeholder="Username" autocomplete="off"
                        class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                </div>
                <div>
                    <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Password</label>
                    <input type="password" v-model="newUser.password" placeholder="Password" autocomplete="new-password"
                        class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                </div>
                <div class="flex items-center gap-2">
                    <input type="checkbox" id="newUserAdmin" v-model="newUser.isAdmin"
                        :class="[$styles.borderInput, $styles.textInput, $styles.checkbox]" />
                    <label for="newUserAdmin" class="text-sm" :class="[$styles.labelInput]">Admin</label>
                </div>
                <div class="flex gap-3">
                    <button type="submit" class="px-4 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">
                        Create User
                    </button>
                    <button type="button" @click="cancelCreate" class="px-4 py-2 text-sm font-medium rounded-md transition-colors border"
                        :class="[$styles.card, $styles.borderInput]">
                        Cancel
                    </button>
                </div>
            </form>
        </div>

        <!-- Users Table -->
        <div class="rounded-xl shadow-sm overflow-hidden">
            <div v-if="loading" class="p-8 text-center text-sm" :class="[$styles.muted]">Loading users...</div>
            <table v-else-if="users.length" class="w-full text-sm bg-table">
                <thead>
                    <tr class="border-b border-th bg-th">
                        <th class="text-left px-4 py-3 font-medium" :class="[$styles.labelInput]">Username</th>
                        <th class="text-left px-4 py-3 font-medium" :class="[$styles.labelInput]">Roles</th>
                        <th class="text-left px-4 py-3 font-medium" :class="[$styles.labelInput]">Status</th>
                        <th class="text-left px-4 py-3 font-medium" :class="[$styles.labelInput]">Created</th>
                        <th class="text-left px-4 py-3 font-medium" :class="[$styles.labelInput]">Last Login</th>
                        <th class="text-right px-4 py-3 font-medium" :class="[$styles.labelInput]">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="user in users" :key="user.userName" class="border-b last:border-b-0 border-td">
                        <td class="px-4 py-3 font-medium" :class="[$styles.heading]">{{ user.userName }}</td>
                        <td class="px-4 py-3">
                            <span v-for="role in user.roles" :key="role"
                                class="inline-block px-2 py-0.5 text-xs font-medium rounded-full mr-1"
                                :class="role === 'Admin' ? $styles.codeTagStrong : $styles.codeTag">
                                {{ role }}
                            </span>
                        </td>
                        <td class="px-4 py-3">
                            <span v-if="user.locked" class="inline-flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400"
                                :title="user.locked">
                                <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z" clip-rule="evenodd"/></svg>
                                Locked
                            </span>
                            <span v-else class="text-xs font-medium text-green-600 dark:text-green-400">Active</span>
                        </td>
                        <td class="px-4 py-3" :class="[$styles.muted]">{{ formatDate(user.created) }}</td>
                        <td class="px-4 py-3" :class="[$styles.muted]">
                            <template v-if="user.lastLogin">
                                <div class="flex flex-wrap">
                                    <span v-if="user.lastIp" :class="$styles.muted">{{ user.lastIp }}</span>                                    
                                    <span class="mx-2">·</span>
                                    <span>
                                        {{ timeAgo(user.lastLogin) }}
                                    </span>
                                </div>
                            </template>
                        </td>
                        <td class="px-4 py-3 text-right">
                            <div class="flex items-center justify-end gap-1">
                                <button type="button" @click="openChangePassword(user)" title="Change password"
                                    class="p-1.5 rounded-md transition-colors" :class="[$styles.mutedHover]">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>
                                </button>
                                <button type="button" v-if="!user.locked && canLock(user)" @click="confirmLock(user)" title="Lock user"
                                    class="p-1.5 rounded-md transition-colors" :class="[$styles.mutedHover]">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>
                                </button>
                                <button type="button" v-else-if="user.locked" @click="unlockUser(user)" title="Unlock user"
                                    class="p-1.5 rounded-md transition-colors text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z"/></svg>
                                </button>
                                <button type="button" @click="confirmDelete(user)" title="Delete user"
                                    class="p-1.5 rounded-md transition-colors text-red-500 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                                </button>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
            <div v-else class="p-8 text-center text-sm" :class="[$styles.muted]">No users found.</div>
        </div>

        <!-- Change Password Modal -->
        <div v-if="changePwUser" class="fixed inset-0 z-100 flex items-center justify-center" @click.self="changePwUser = null">
            <div class="w-full max-w-md p-6 rounded-xl shadow-lg" :class="[$styles.card]">
                <h2 class="text-lg font-semibold mb-4" :class="[$styles.heading]">Change Password for {{ changePwUser.userName }}</h2>
                <form @submit.prevent="changePassword" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">New Password</label>
                        <input type="password" v-model="newPassword" placeholder="New password" autocomplete="new-password"
                            class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Confirm Password</label>
                        <input type="password" v-model="confirmPassword" placeholder="Confirm password" autocomplete="new-password"
                            class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                    </div>
                    <ErrorSummary v-if="modalError" class="mb-2" :status="modalError" />
                    <div class="flex gap-3 justify-end">
                        <button type="button" @click="changePwUser = null" class="px-4 py-2 text-sm font-medium rounded-md transition-colors border"
                            :class="[$styles.secondaryButton]">
                            Cancel
                        </button>
                        <button type="submit" class="px-4 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">
                            Update Password
                        </button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Lock Confirmation Modal -->
        <div v-if="lockUserTarget" class="fixed inset-0 z-100 flex items-center justify-center bg-black/50" @click.self="lockUserTarget = null">
            <div class="w-full max-w-md p-6 rounded-xl shadow-lg" :class="[$styles.card]">
                <h2 class="text-lg font-semibold mb-2" :class="[$styles.heading]">Lock User</h2>
                <p class="text-sm mb-6" :class="[$styles.muted]">Are you sure you want to lock <strong>{{ lockUserTarget.userName }}</strong>? They will be signed out and unable to log in.</p>
                <ErrorSummary v-if="modalError" class="mb-4" :status="modalError" />
                <div class="flex gap-3 justify-end">
                    <button type="button" @click="lockUserTarget = null" class="px-4 py-2 text-sm font-medium rounded-md transition-colors border"
                        :class="[$styles.secondaryButton]">
                        Cancel
                    </button>
                    <button type="button" @click="doLock" class="px-4 py-2 text-sm font-medium rounded-md transition-colors bg-red-600 hover:bg-red-700 text-white">
                        Lock User
                    </button>
                </div>
            </div>
        </div>

        <!-- Delete Confirmation Modal -->
        <div v-if="deleteUser" class="fixed inset-0 z-100 flex items-center justify-center bg-black/50" @click.self="deleteUser = null">
            <div class="w-full max-w-md p-6 rounded-xl shadow-lg" :class="[$styles.card]">
                <h2 class="text-lg font-semibold mb-2" :class="[$styles.heading]">Delete User</h2>
                <p class="text-sm mb-6" :class="[$styles.muted]">Are you sure you want to delete <strong>{{ deleteUser.userName }}</strong>? This action cannot be undone.</p>
                <ErrorSummary v-if="modalError" class="mb-4" :status="modalError" />
                <div class="flex gap-3 justify-end">
                    <button type="button" @click="deleteUser = null" class="px-4 py-2 text-sm font-medium rounded-md transition-colors border"
                        :class="[$styles.secondaryButton]">
                        Cancel
                    </button>
                    <button type="button" @click="doDelete" class="px-4 py-2 text-sm font-medium rounded-md transition-colors bg-red-600 hover:bg-red-700 text-white">
                        Delete User
                    </button>
                </div>
            </div>
        </div>
    </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const users = ref([])
        const loading = ref(true)
        const errorSummary = ref(null)
        const showCreate = ref(false)
        const newUser = ref({ userName: '', password: '', isAdmin: false })
        const changePwUser = ref(null)
        const newPassword = ref('')
        const confirmPassword = ref('')
        const deleteUser = ref(null)
        const lockUserTarget = ref(null)
        const modalError = ref(null)
        const currentUserName = computed(() => ctx.ai.auth?.userName)

        function formatDate(ts) {
            if (!ts) return ''
            return new Date(ts * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
        }

        function timeAgo(ts) {
            if (!ts) return ''
            const seconds = Math.floor(Date.now() / 1000 - ts)
            if (seconds < 60) return 'just now'
            const minutes = Math.floor(seconds / 60)
            if (minutes < 60) return `${minutes}m ago`
            const hours = Math.floor(minutes / 60)
            if (hours < 24) return `${hours}h ago`
            const days = Math.floor(hours / 24)
            if (days < 30) return `${days}d ago`
            return formatDate(ts)
        }

        async function loadUsers() {
            loading.value = true
            errorSummary.value = null
            const api = await ctx.getJson('/admin/users')
            if (api.response) {
                users.value = api.response.users || []
            } else {
                errorSummary.value = api.error
            }
            loading.value = false
        }

        function cancelCreate() {
            showCreate.value = false
            newUser.value = { userName: '', password: '', isAdmin: false }
        }

        async function createUser() {
            errorSummary.value = null
            if (!newUser.value.userName || !newUser.value.password) {
                errorSummary.value = { errorCode: 'Validation', message: 'Username and password are required' }
                return
            }
            const roles = newUser.value.isAdmin ? ['Admin'] : []
            const api = await ctx.postJson('/admin/users', {
                body: JSON.stringify({ userName: newUser.value.userName, password: newUser.value.password, roles })
            })
            if (api.response) {
                cancelCreate()
                await loadUsers()
            } else {
                errorSummary.value = api.error
            }
        }

        function openChangePassword(user) {
            changePwUser.value = user
            newPassword.value = ''
            confirmPassword.value = ''
            modalError.value = null
        }

        async function changePassword() {
            modalError.value = null
            if (!newPassword.value) {
                modalError.value = { errorCode: 'Validation', message: 'Password is required' }
                return
            }
            if (newPassword.value !== confirmPassword.value) {
                modalError.value = { errorCode: 'Validation', message: 'Passwords do not match' }
                return
            }
            const api = await ctx.postJson('/admin/users', {
                method: 'PUT',
                body: JSON.stringify({ userName: changePwUser.value.userName, password: newPassword.value })
            })
            if (api.response) {
                changePwUser.value = null
            } else {
                modalError.value = api.error
            }
        }

        function canLock(user) {
            return user.userName !== currentUserName.value && !user.roles?.includes('Admin')
        }

        function confirmLock(user) {
            lockUserTarget.value = user
            modalError.value = null
        }

        async function doLock() {
            modalError.value = null
            const api = await ctx.postJson('/admin/users', {
                method: 'PUT',
                body: JSON.stringify({ userName: lockUserTarget.value.userName, locked: 'Account suspended' })
            })
            if (api.response) {
                lockUserTarget.value = null
                await loadUsers()
            } else {
                modalError.value = api.error
            }
        }

        async function unlockUser(user) {
            errorSummary.value = null
            const api = await ctx.postJson('/admin/users', {
                method: 'PUT',
                body: JSON.stringify({ userName: user.userName, locked: false })
            })
            if (api.response) {
                await loadUsers()
            } else {
                errorSummary.value = api.error
            }
        }

        function confirmDelete(user) {
            deleteUser.value = user
            modalError.value = null
        }

        async function doDelete() {
            modalError.value = null
            const api = await ctx.getJson('/admin/users/' + encodeURIComponent(deleteUser.value.userName), { method: 'DELETE' })
            if (api.response) {
                deleteUser.value = null
                await loadUsers()
            } else {
                modalError.value = api.error
            }
        }

        loadUsers()

        return {
            users, loading, errorSummary, showCreate, newUser,
            changePwUser, newPassword, confirmPassword, deleteUser, lockUserTarget, modalError,
            formatDate, timeAgo, cancelCreate, createUser, openChangePassword, changePassword,
            canLock, confirmLock, doLock, unlockUser, confirmDelete, doDelete, loadUsers,
        }
    }
}

const ManageAccountPage = {
    template: `
    <div class="max-w-xl mx-auto p-6">
        <h1 class="text-2xl font-bold mb-8" :class="[$styles.heading]">My Account</h1>

        <!-- Profile Info -->
        <div class="p-6 rounded-xl shadow-sm" :class="[$styles.card]">
            <div class="flex items-center gap-4">
                <div @click="$router.push('/settings')" title="Change Avatar">
                    <img :src="avatarUrl" alt="Avatar" class="w-16 h-16 rounded-full cursor-pointer" />
                </div>    
                <div class="flex-1">
                    <div class="text-lg font-semibold" :class="[$styles.heading]">{{ auth.userName }}</div>
                    <div v-if="auth.roles?.length" class="mt-1 flex flex-wrap gap-1">
                        <span v-for="role in auth.roles" :key="role"
                            class="inline-block px-2 py-0.5 text-xs font-medium rounded-full"
                            :class="role === 'Admin' ? $styles.codeTagStrong : $styles.codeTag">
                            {{ role }}
                        </span>
                    </div>
                </div>
                <button type="button" @click="showChangePw = true"
                    class="px-4 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">
                    Change Password
                </button>
            </div>
        </div>

        <!-- Change Password Modal -->
        <div v-if="showChangePw" class="fixed inset-0 z-100 flex items-center justify-center bg-black/50" @click.self="closeChangePw">
            <div class="w-full max-w-md p-6 rounded-xl shadow-lg" :class="[$styles.card]">
                <h2 class="text-lg font-semibold mb-4" :class="[$styles.heading]">Change Password</h2>
                <form @submit.prevent="changePassword" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Current Password</label>
                        <input type="password" v-model="currentPassword" placeholder="Current password" autocomplete="current-password"
                            class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">New Password</label>
                        <input type="password" v-model="newPassword" placeholder="New password" autocomplete="new-password"
                            class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Confirm New Password</label>
                        <input type="password" v-model="confirmPassword" placeholder="Confirm new password" autocomplete="new-password"
                            class="block w-full rounded-md" :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                    </div>
                    <ErrorSummary v-if="errorSummary" class="mb-2" :status="errorSummary" />
                    <div v-if="success" class="text-sm text-green-600 dark:text-green-400">Password changed successfully.</div>
                    <div class="flex gap-3 justify-end">
                        <button type="button" @click="closeChangePw" class="px-4 py-2 text-sm font-medium rounded-md transition-colors border"
                            :class="[$styles.secondaryButton]">
                            Cancel
                        </button>
                        <button type="submit" class="px-4 py-2 text-sm font-medium rounded-md transition-colors" :class="[$styles.primaryButton]">
                            Update Password
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const auth = computed(() => ctx.ai.auth || {})
        const avatarUrl = computed(() => ctx.getUserAvatar())
        const showChangePw = ref(false)
        const currentPassword = ref('')
        const newPassword = ref('')
        const confirmPassword = ref('')
        const errorSummary = ref(null)
        const success = ref(false)

        function closeChangePw() {
            showChangePw.value = false
            currentPassword.value = ''
            newPassword.value = ''
            confirmPassword.value = ''
            errorSummary.value = null
            success.value = false
        }

        async function changePassword() {
            errorSummary.value = null
            success.value = false
            if (!currentPassword.value) {
                errorSummary.value = { errorCode: 'Validation', message: 'Current password is required' }
                return
            }
            if (!newPassword.value) {
                errorSummary.value = { errorCode: 'Validation', message: 'New password is required' }
                return
            }
            if (newPassword.value !== confirmPassword.value) {
                errorSummary.value = { errorCode: 'Validation', message: 'Passwords do not match' }
                return
            }
            const api = await ctx.postJson('/account/change-password', {
                body: JSON.stringify({
                    currentPassword: currentPassword.value,
                    newPassword: newPassword.value,
                })
            })
            if (api.response) {
                success.value = true
                currentPassword.value = ''
                newPassword.value = ''
                confirmPassword.value = ''
            } else {
                errorSummary.value = api.error
            }
        }

        return {
            auth,
            avatarUrl,
            showChangePw,
            currentPassword,
            newPassword,
            confirmPassword,
            errorSummary,
            success,
            closeChangePw,
            changePassword,
        }
    }
}

const AuthMenuItems = {
    template: `
        <div class="cursor-pointer px-4 py-2 text-sm border-b border-[var(--user-border)] hover:bg-[var(--background)]/50"
            @click="$router.push('/account'), $emit('done')">
            <div class="font-medium whitespace-nowrap overflow-hidden text-ellipsis">{{ auth.displayName || auth.userName }}</div>
            <div class="text-xs whitespace-nowrap overflow-hidden text-ellipsis">{{ auth.email }}</div>
        </div>

        <div v-if="isAdmin" class="cursor-pointer px-4 py-2 text-sm border-b border-[var(--user-border)] hover:bg-[var(--background)]/50"
            @click="$router.push('/admin'), $emit('done')">
            <div class="font-medium whitespace-nowrap overflow-hidden text-ellipsis">Manage Users</div>
        </div>

        <button type="button"
            @click="handleLogout"
            class="w-full text-left px-4 py-2 text-sm flex items-center whitespace-nowrap hover:bg-[var(--background)]/50">
            <svg class="w-4 h-4 mr-2 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path>
            </svg>
            Sign Out
        </button>
    `,
    emits: ['done'],
    props: { auth: Object },
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const showComponents = computed(() => {
            const args = { auth: props.auth }
            return Object.values(ctx.userMenuItemComponents).filter(def => def.show(args)).map(def => def.component)
        })

        const isAdmin = computed(() => props.auth.roles?.includes('Admin'))

        async function handleLogout() {
            emit('done')
            await ctx.ai.signOut()
            // Reload the page to show sign-in screen
            window.location.reload()
        }

        return {
            showComponents,
            handleLogout,
            isAdmin,
        }
    }
}

export default {
    install(ctx) {
        ext = ctx.scope("github_auth")

        ctx.setUserMenuItems({
            auth: {
                component: AuthMenuItems,
                show: ({ auth }) => {
                    console.log('auth', auth)
                    return true
                }
            }
        })

        ctx.routes.push({ path: '/admin', component: ManageUsersPage, meta: { title: 'Manage Users' } })
        ctx.routes.push({ path: '/account', component: ManageAccountPage, meta: { title: 'Manage Account' } })

        ctx.components({
            SignIn,
            ManageUsersPage,
        })
    }
}
