import { ref, computed, inject, onMounted, onUnmounted } from "vue"

let ext

function useProjects(ext) {
    const ctx = ext.ctx

    function getProject(name) {
        return (ctx.state.projects || []).find(p => p.name === name)
    }

    async function saveProject(originalName, updatedProject) {
        const api = await ext.postJson(`/save/${encodeURIComponent(originalName)}`, updatedProject)
        if (api.error) {
            ctx.setError(api.error, "Failed to save project")
        } else {
            const projects = api.response
            ext.setState({ projects })
            // Update active project if needed
            const active = ctx.state.prefs.project
            if (active) {
                if (active === originalName && updatedProject.name !== originalName) {
                    ctx.state.prefs.project = updatedProject.name
                }
            }
        }
        return api
    }

    return {
        get all() { return ctx.state.projects || [] },
        get active() { return ctx.ctx.state.prefs.project },
        getProject,
        saveProject,
    }
}

const ProjectsSelector = {
    template: `
        <div class="relative" ref="triggerRef">
            <button type="button" @click="togglePopover"
                class="select-none flex items-center space-x-2 px-3 py-2 rounded-md text-sm w-full md:w-auto md:min-w-48 max-w-96 transition-colors"
                :class="$styles.dropdownButton">
                <!-- Folder Icon -->
                <svg xmlns="http://www.w3.org/2000/svg" class="size-4 flex-shrink-0" :class="$styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                </svg>
                <span class="truncate flex-1 text-left font-medium">{{ $state.prefs.project || 'Default Workspace' }}</span>
                <svg class="size-4 flex-shrink-0" :class="[$styles.mutedIcon]" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                </svg>
            </button>

            <!-- Dropdown Popover -->
            <div v-if="showPopover" ref="popoverRef" 
                class="absolute left-0 mt-1.5 w-80 rounded-lg shadow-xl z-50 py-1"
                :class="$styles.bgPopover">
                <div class="px-3 py-1.5 text-xs font-semibold uppercase tracking-wider" :class="$styles.muted">
                    Workspaces & Projects
                </div>
                
                <!-- Default Workspace -->
                <button type="button" @click="selectProject(null)"
                    class="w-full text-left px-3 py-2 flex items-start space-x-3 transition-colors text-sm"
                    :class="[$state.prefs.project === null ? $styles.popoverButtonActive : $styles.popoverButton]">
                    <svg xmlns="http://www.w3.org/2000/svg" class="size-5 mt-0.5 flex-shrink-0" :class="$state.prefs.project === null ? 'text-blue-500' : $styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                        <polyline points="9 22 9 12 15 12 15 22"></polyline>
                    </svg>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium text-gray-900 dark:text-gray-100 flex items-center justify-between">
                            <span>Default Workspace</span>
                            <svg v-if="$state.prefs.project === null" class="size-4 text-blue-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clip-rule="evenodd" />
                            </svg>
                        </div>
                        <div class="text-[10px] font-mono truncate mt-0.5" :class="$styles.muted">
                            Default workspace (no project selected)
                        </div>
                    </div>
                </button>

                <!-- Project List -->
                <div v-if="projects.length === 0" class="px-4 py-3 text-xs italic border-t" :class="[$styles.muted, $styles.chromeBorder]">
                    No other projects registered.
                </div>
                <div v-else class="max-h-60 overflow-y-auto border-t" :class="$styles.chromeBorder">
                    <button v-for="project in projects" :key="project.name"
                        type="button" @click="selectProject(project)"
                        class="w-full text-left px-3 py-2 flex items-start space-x-3 transition-colors text-sm"
                        :class="[$state.prefs.project === project.name ? $styles.popoverButtonActive : $styles.popoverButton]">
                        <svg xmlns="http://www.w3.org/2000/svg" class="size-5 mt-0.5 flex-shrink-0" :class="$state.prefs.project === project.name ? 'text-blue-500' : $styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                        </svg>
                        <div class="flex-1 min-w-0">
                            <div class="font-medium text-gray-900 dark:text-gray-100 flex items-center justify-between">
                                <span class="truncate font-semibold">{{ project.name }}</span>
                                <svg v-if="$state.prefs.project === project.name" class="size-4 text-blue-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clip-rule="evenodd" />
                                </svg>
                            </div>
                            <div v-if="project.description" class="text-xs truncate mt-0.5" :class="$styles.muted">
                                {{ project.description }}
                            </div>
                            <div v-for="path in project.paths.filter(x => x[0] !== '$')" :key="path" class="mt-1.5 text-[10px] font-mono truncate" :class="$styles.muted" :title="path">
                                {{ path }}
                            </div>
                            <div v-if="project.paths.filter(x => x[0] == '$').length" class="mt-1.5 flex flex-wrap gap-1.5">
                                <div v-for="path in project.paths.filter(x => x[0] == '$')" :key="path" class="text-[10px] font-mono px-1.5 py-0.5 rounded flex items-center gap-1" :class="[$styles.codeTag]" :title="path.substring(1)">
                                    <span>{{ path.substring(1) }}</span>
                                </div>
                            </div>
                        </div>
                    </button>
                </div>

                <!-- Manage Projects Button -->
                <button type="button" @click="manageProjects"
                    class="w-full text-left px-3 py-2 flex items-center space-x-2 transition-colors text-sm border-t"
                    :class="[$styles.popoverButton, $styles.chromeBorder]">
                    <svg xmlns="http://www.w3.org/2000/svg" class="size-4 flex-shrink-0" :class="$styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                    </svg>
                    <span class="font-medium">Manage Projects</span>
                </button>
            </div>
        </div>
    `,
    setup(props) {
        const ctx = inject('ctx')
        const showPopover = ref(false)
        const triggerRef = ref(null)
        const popoverRef = ref(null)

        const projects = computed(() => ctx.state.projects || [])

        const togglePopover = () => showPopover.value = !showPopover.value

        async function selectProject(project) {
            const name = project?.name || null
            const api = await ext.postJson(`/active`, { name })
            if (api.error) {
                ctx.setError(api.error, "Failed to switch project")
            } else {
                ctx.state.prefs.project = name
                ctx.toast(`Switched to project: ${name || 'default'}`)
            }
            showPopover.value = false
        }

        function manageProjects() {
            showPopover.value = false
            ctx.openModal('projects-manager')
        }

        const onDocClick = (e) => {
            const t = e.target
            if (triggerRef.value?.contains(t)) return
            if (popoverRef.value?.contains(t)) return
            showPopover.value = false
        }

        onMounted(() => document.addEventListener('click', onDocClick))
        onUnmounted(() => document.removeEventListener('click', onDocClick))

        return {
            ext,
            showPopover,
            triggerRef,
            popoverRef,
            projects,
            togglePopover,
            selectProject,
            manageProjects
        }
    }
}

const ProjectsManagerModal = {
    template: `
        <!-- Dialog Overlay -->
        <div class="fixed inset-0 z-50 overflow-hidden text-gray-900 dark:text-gray-100" @keydown.escape="closeDialog">
            <!-- Backdrop -->
            <div class="fixed inset-0 bg-black/50 transition-opacity" @click="closeDialog"></div>
            
            <!-- Dialog -->
            <div class="fixed inset-4 md:inset-8 lg:inset-12 flex items-center justify-center">
                <div class="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full h-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
                    <!-- Header -->
                    <div class="flex-shrink-0 px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                        <h2 class="text-xl font-semibold">Manage Projects</h2>
                        <button type="button" @click="closeDialog" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
                            <svg class="size-6" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                                <path fill="currentColor" d="M19 6.41L17.59 5L12 10.59L6.41 5L5 6.41L10.59 12L5 17.59L6.41 19L12 13.41L17.59 19L19 17.59L13.41 12z"/>
                            </svg>
                        </button>
                    </div>
                    
                    <!-- Main Body Split Pane -->
                    <div class="flex-1 flex overflow-hidden">
                        <!-- Left pane: Projects List -->
                        <div class="w-1/3 border-r border-gray-200 dark:border-gray-700 flex flex-col bg-gray-50 dark:bg-gray-800/40">
                            <div class="p-4 border-b border-gray-200 dark:border-gray-700">
                                <button type="button" @click="createNewProject"
                                    class="w-full py-2 px-3 flex items-center justify-center space-x-1 text-sm font-medium rounded-lg transition-colors"
                                    :class="[$styles.primaryButton]">
                                    <svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                        <line x1="12" y1="5" x2="12" y2="19"></line>
                                        <line x1="5" y1="12" x2="19" y2="12"></line>
                                    </svg>
                                    <span>New Project</span>
                                </button>
                            </div>
                            <div class="flex-1 overflow-y-auto p-2 space-y-1">
                                <div v-if="localProjects.length === 0" class="text-center py-8 text-xs italic" :class="[$styles.muted]">
                                    No projects yet.
                                </div>
                                <button v-for="(p, index) in localProjects" :key="index"
                                    type="button"
                                    @click="selectEditProject(index)"
                                    class="w-full text-left px-3 py-2 rounded-lg flex items-center justify-between text-sm transition-all"
                                    :class="[selectedIdx === index ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 font-semibold ring-1 ring-blue-500/20' : 'hover:bg-gray-100 dark:hover:bg-gray-700/50 text-gray-700 dark:text-gray-300']">
                                    <div class="flex items-center space-x-2 min-w-0">
                                        <svg xmlns="http://www.w3.org/2000/svg" class="size-4 flex-shrink-0 opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                                        </svg>
                                        <span class="truncate">{{ p.name }}</span>
                                    </div>
                                    <span v-if="p.paths.length > 0" class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-200 dark:bg-gray-700 opacity-60">
                                        {{ p.paths.length }}
                                    </span>
                                </button>
                            </div>
                        </div>

                        <!-- Right pane: Project Edit Form -->
                        <div class="w-2/3 overflow-y-auto p-6 flex flex-col bg-white dark:bg-gray-800">
                            <div v-if="selectedIdx === null" class="flex-1 flex flex-col items-center justify-center text-gray-400 dark:text-gray-500">
                                <svg xmlns="http://www.w3.org/2000/svg" class="size-16 mb-4 opacity-40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                                </svg>
                                <p class="text-sm">Select a project to edit, or create a new one.</p>
                                <button type="button" @click="closeDialog"
                                    class="mt-4 px-4 py-1 text-sm font-medium transition-colors"
                                    :class="[$styles.secondaryButton]">
                                    close
                                </button>
                            </div>
                            <div v-else class="flex-1 flex flex-col justify-between h-full">
                                <div class="space-y-6">
                                    <div>
                                        <h3 class="text-base font-semibold mb-4">
                                            {{ isNewProject ? 'Create Project' : 'Edit Project' }}
                                        </h3>
                                        <div class="space-y-4">
                                            <!-- Project Name -->
                                            <div>
                                                <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Project Name *</label>
                                                <input type="text" v-model="editForm.name"
                                                    placeholder="e.g. My Awesome App"
                                                    class="block w-full rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none border"
                                                    :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                            </div>

                                            <!-- Project Description -->
                                            <div>
                                                <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Description</label>
                                                <input type="text" v-model="editForm.description"
                                                    placeholder="Short summary of this project"
                                                    class="block w-full rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none border"
                                                    :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                            </div>

                                            <!-- Publish Build Directory -->
                                            <div>
                                                <label class="block text-sm font-medium mb-1" :class="[$styles.labelInput]">Publish Build Directory</label>
                                                <input type="text" v-model="editForm.publish"
                                                    placeholder="Path to folder, e.g. dist or $WORKSPACE/dist"
                                                    class="block w-full rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none border font-mono"
                                                    :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                                <span class="text-[10px] text-gray-400 dark:text-gray-500 mt-1 block">
                                                    Default directory to publish (e.g. dist, build, or $WORKSPACE/dist)
                                                </span>
                                            </div>
                                            
                                            <!-- Paths -->
                                            <div>
                                                <label class="block text-sm font-medium mb-2" :class="[$styles.labelInput]">Allowed Directories & Paths</label>
                                                
                                                <!-- Special Alias Options -->
                                                <div class="flex items-center gap-6 p-3 rounded-lg border mb-4 text-sm" :class="[$styles.card, $styles.borderInput]">
                                                    <div class="flex items-center space-x-2">
                                                        <input type="checkbox" id="chk_workspace" :checked="hasWorkspaceAlias" @change="toggleWorkspaceAlias"
                                                            class="rounded cursor-pointer focus:ring-blue-500" :class="[$styles.borderInput, $styles.textInput, $styles.checkbox]" />
                                                        <label for="chk_workspace" class="cursor-pointer font-medium flex items-center space-x-1">
                                                            <span>WORKSPACE</span>
                                                            <span class="text-[10px] font-normal opacity-60">(Current workspace root)</span>
                                                        </label>
                                                    </div>
                                                    <div class="flex items-center space-x-2">
                                                        <input type="checkbox" id="chk_temp" :checked="hasTempAlias" @change="toggleTempAlias"
                                                            class="rounded cursor-pointer focus:ring-blue-500" :class="[$styles.borderInput, $styles.textInput, $styles.checkbox]" />
                                                        <label for="chk_temp" class="cursor-pointer font-medium flex items-center space-x-1">
                                                            <span>TEMP</span>
                                                            <span class="text-[10px] font-normal opacity-60">(OS temp directory)</span>
                                                        </label>
                                                    </div>
                                                </div>

                                                <!-- Custom Paths List -->
                                                <div class="space-y-2">
                                                    <div v-for="(path, idx) in customPaths" :key="idx" class="flex items-center space-x-2">
                                                        <input type="text" v-model="customPaths[idx]"
                                                            placeholder="/absolute/path/to/directory"
                                                            spellcheck="false"
                                                            class="flex-1 rounded-md px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 outline-none border font-mono"
                                                            :class="[$styles.bgInput, $styles.textInput, $styles.borderInput]" />
                                                        <button type="button" @click="removeCustomPath(idx)"
                                                            class="p-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30 rounded-lg transition-colors"
                                                            title="Remove path">
                                                            <svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                                                <polyline points="3 6 5 6 21 6"></polyline>
                                                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                                            </svg>
                                                        </button>
                                                    </div>
                                                    
                                                    <button type="button" @click="addCustomPath"
                                                        class="text-xs font-semibold flex items-center space-x-1.5 py-1.5 px-3 rounded-lg border border-dashed transition-colors"
                                                        :class="[$styles.borderInput, $styles.textInput, 'hover:bg-gray-50 dark:hover:bg-gray-800']">
                                                        <svg class="size-3.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5">
                                                            <line x1="12" y1="5" x2="12" y2="19"></line>
                                                            <line x1="5" y1="12" x2="19" y2="12"></line>
                                                        </svg>
                                                        <span>Add Custom Path</span>
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <!-- Form Actions -->
                                <div class="mt-8 pt-4 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
                                    <button type="button" v-if="!isNewProject" @click="deleteProject"
                                        class="px-4 py-2 text-sm font-semibold text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/20 rounded-lg transition-colors flex items-center space-x-1">
                                        <svg class="size-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                                            <polyline points="3 6 5 6 21 6"></polyline>
                                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                        </svg>
                                        <span>Delete Project</span>
                                    </button>
                                    <div v-else></div> <!-- Spacer -->
                                    <div class="flex items-center space-x-3">
                                        <button type="button" @click="closeDialog"
                                            class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors">
                                            Close
                                        </button>
                                        <button type="button" @click="saveForm"
                                            :disabled="!isDirty || !editForm.name.trim()"
                                            class="px-4 py-2 text-sm font-semibold rounded-lg transition-colors"
                                            :class="[$styles.primaryButton]">
                                            Save
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    emits: ['done'],
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const localProjects = ref([])
        const selectedIdx = ref(null)
        const isNewProject = ref(false)

        const editForm = ref({
            name: '',
            description: '',
            paths: [],
            publish: ''
        })

        const customPaths = ref([])

        // Load project data
        onMounted(() => {
            localProjects.value = JSON.parse(JSON.stringify(ctx.state.projects || []))
        })

        const hasWorkspaceAlias = computed(() => editForm.value.paths.includes('$WORKSPACE'))
        const hasTempAlias = computed(() => editForm.value.paths.includes('$TEMP'))

        function toggleWorkspaceAlias() {
            const idx = editForm.value.paths.indexOf('$WORKSPACE')
            if (idx === -1) {
                editForm.value.paths.push('$WORKSPACE')
            } else {
                editForm.value.paths.splice(idx, 1)
            }
        }

        function toggleTempAlias() {
            const idx = editForm.value.paths.indexOf('$TEMP')
            if (idx === -1) {
                editForm.value.paths.push('$TEMP')
            } else {
                editForm.value.paths.splice(idx, 1)
            }
        }

        function selectEditProject(idx) {
            isNewProject.value = false
            selectedIdx.value = idx
            const proj = localProjects.value[idx]
            editForm.value = {
                name: proj.name,
                description: proj.description || '',
                paths: [...(proj.paths || [])],
                publish: proj.publish || ''
            }
            customPaths.value = (proj.paths || []).filter(p => p !== '$WORKSPACE' && p !== '$TEMP')
        }

        function createNewProject() {
            isNewProject.value = true
            selectedIdx.value = -1 // temporary index
            editForm.value = {
                name: '',
                description: '',
                paths: [],
                publish: ''
            }
            customPaths.value = []
        }

        function addCustomPath() {
            customPaths.value.push('')
        }

        function removeCustomPath(idx) {
            customPaths.value.splice(idx, 1)
        }

        function cancelEdit() {
            selectedIdx.value = null
            isNewProject.value = false
        }

        async function deleteProject() {
            if (selectedIdx.value === null || isNewProject.value) return
            const projName = localProjects.value[selectedIdx.value].name
            if (!confirm(`Are you sure you want to delete the project "${projName}"?`)) return

            localProjects.value.splice(selectedIdx.value, 1)
            await persistProjects()
            ctx.toast(`Deleted project: ${projName}`)
            cancelEdit()
        }

        async function saveForm() {
            if (!editForm.value.name.trim()) {
                ctx.setError('Project name is required')
                return
            }

            // Combine aliases and custom paths
            const finalPaths = []
            if (hasWorkspaceAlias.value) finalPaths.push('$WORKSPACE')
            if (hasTempAlias.value) finalPaths.push('$TEMP')

            // Filter out empty custom paths and trim them
            customPaths.value.forEach(p => {
                const trimmed = p.trim()
                if (trimmed) {
                    finalPaths.push(trimmed)
                }
            })

            const updatedProject = {
                name: editForm.value.name.trim(),
                description: editForm.value.description.trim(),
                paths: finalPaths,
                publish: editForm.value.publish ? editForm.value.publish.trim() : ''
            }

            // Check duplicate project name
            const isDuplicate = localProjects.value.some((p, idx) => {
                if (isNewProject.value) {
                    return p.name === updatedProject.name
                } else {
                    return p.name === updatedProject.name && idx !== selectedIdx.value
                }
            })

            if (isDuplicate) {
                ctx.setError('A project with this name already exists')
                return
            }

            const originalName = isNewProject.value
                ? updatedProject.name
                : localProjects.value[selectedIdx.value].name

            const success = await persistProject(updatedProject, originalName)
            if (!success) return

            ctx.toast(`Saved project: ${updatedProject.name}`)

            // Re-select if name changed
            if (isNewProject.value) {
                selectEditProject(localProjects.value.length - 1)
            } else {
                selectEditProject(selectedIdx.value)
            }
        }

        async function persistProject(updatedProject, originalName) {
            const api = await ctx.projects.saveProject(originalName, updatedProject)
            if (api.response) {
                if (isNewProject.value) {
                    localProjects.value.push(updatedProject)
                } else {
                    localProjects.value[selectedIdx.value] = updatedProject
                }

                // Update active project if needed
                const active = ctx.state.prefs.project
                if (active) {
                    if (active === originalName && updatedProject.name !== originalName) {
                        ctx.state.prefs.project = updatedProject.name
                    } else if (!localProjects.value.some(p => p.name === active)) {
                        ctx.state.prefs.project = null
                    }
                }
                return true
            }
        }

        async function persistProjects() {
            // Save localProjects to backend
            const api = await ext.postJson(`/projects.json`, localProjects.value)
            if (api.error) {
                ctx.setError(api.error, "Failed to save projects")
            } else {
                ext.setState({ projects: localProjects.value })
                // Update active project if needed
                const active = ctx.state.prefs.project
                if (active && !localProjects.value.some(p => p.name === active)) {
                    ctx.state.prefs.project = null
                }
            }
        }

        const isDirty = computed(() => {
            if (selectedIdx.value === null) return false

            const orig = isNewProject.value
                ? { name: '', description: '', paths: ['$WORKSPACE'] }
                : localProjects.value[selectedIdx.value]

            const origPaths = orig?.paths || []

            // Check if name or description changed
            if ((editForm.value.name || '').trim() !== (orig?.name || '').trim()) return true
            if ((editForm.value.description || '').trim() !== (orig?.description || '').trim()) return true
            if ((editForm.value.publish || '').trim() !== (orig?.publish || '').trim()) return true

            // Compare paths
            const curPaths = []
            if (hasWorkspaceAlias.value) curPaths.push('$WORKSPACE')
            if (hasTempAlias.value) curPaths.push('$TEMP')
            customPaths.value.forEach(p => {
                const trimmed = p.trim()
                if (trimmed) curPaths.push(trimmed)
            })

            if (curPaths.length !== origPaths.length) return true
            for (let i = 0; i < curPaths.length; i++) {
                if (curPaths[i] !== origPaths[i]) return true
            }

            return false
        })

        function closeDialog() {
            emit('done')
        }

        return {
            localProjects,
            selectedIdx,
            isNewProject,
            editForm,
            customPaths,
            hasWorkspaceAlias,
            hasTempAlias,
            toggleWorkspaceAlias,
            toggleTempAlias,
            selectEditProject,
            createNewProject,
            addCustomPath,
            removeCustomPath,
            cancelEdit,
            deleteProject,
            saveForm,
            closeDialog,
            isDirty
        }
    }
}

export default {
    order: 30 - 100,

    install(ctx) {
        ext = ctx.scope('projects')

        ctx.components({ ProjectsSelector, ProjectsManagerModal })

        ctx.modals({
            'projects-manager': ProjectsManagerModal
        })

        ctx.setLeftTop({
            projects: {
                component: ProjectsSelector,
            }
        })

        ctx.setGlobals({
            projects: useProjects(ext)
        })
    },

    async load(ctx) {
        const api = await ext.getJson(`/projects.json`)
        const projects = api.response || []
        ctx.setState({ projects })
        console.log('project.state', JSON.stringify(ext.state, undefined, 2))
    }
}
