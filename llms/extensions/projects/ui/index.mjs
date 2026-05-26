import { ref, computed, inject, onMounted, onUnmounted } from "vue"

let ext

function useProjects(ext) {
    return {
        get all() { return ext.state.projects || [] },
        get active() { return ext.state.activeProject },
        get defaultPath() { return ext.state.defaultPath },
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
                <span class="truncate flex-1 text-left font-medium">{{ activeProject?.name || 'Default Workspace' }}</span>
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
                    :class="[activeProject === null ? $styles.popoverButtonActive : $styles.popoverButton]">
                    <svg xmlns="http://www.w3.org/2000/svg" class="size-5 mt-0.5 flex-shrink-0" :class="activeProject === null ? 'text-blue-500' : $styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                        <polyline points="9 22 9 12 15 12 15 22"></polyline>
                    </svg>
                    <div class="flex-1 min-w-0">
                        <div class="font-medium text-gray-900 dark:text-gray-100 flex items-center justify-between">
                            <span>Default Workspace</span>
                            <svg v-if="activeProject === null" class="size-4 text-blue-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clip-rule="evenodd" />
                            </svg>
                        </div>
                        <div class="text-[10px] font-mono truncate mt-0.5 opacity-60" :class="$styles.muted" :title="defaultPath">
                            {{ defaultPath }}
                        </div>
                    </div>
                </button>

                <hr class="my-1 border-t" :class="$styles.chromeBorder" />

                <!-- Project List -->
                <div v-if="projects.length === 0" class="px-4 py-3 text-xs italic" :class="$styles.muted">
                    No other projects registered. Add projects to projects.json.
                </div>
                <div v-else class="max-h-60 overflow-y-auto">
                    <button v-for="project in projects" :key="project.path"
                        type="button" @click="selectProject(project)"
                        class="w-full text-left px-3 py-2 flex items-start space-x-3 transition-colors text-sm"
                        :class="[activeProject?.path === project.path ? $styles.popoverButtonActive : $styles.popoverButton]">
                        <svg xmlns="http://www.w3.org/2000/svg" class="size-5 mt-0.5 flex-shrink-0" :class="activeProject?.path === project.path ? 'text-blue-500' : $styles.mutedIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                        </svg>
                        <div class="flex-1 min-w-0">
                            <div class="font-medium text-gray-900 dark:text-gray-100 flex items-center justify-between">
                                <span class="truncate font-semibold">{{ project.name }}</span>
                                <svg v-if="activeProject?.path === project.path" class="size-4 text-blue-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clip-rule="evenodd" />
                                </svg>
                            </div>
                            <div v-if="project.description" class="text-xs truncate mt-0.5 opacity-80" :class="$styles.muted">
                                {{ project.description }}
                            </div>
                            <div class="text-[10px] font-mono truncate mt-0.5 opacity-60" :class="$styles.muted" :title="project.path">
                                {{ project.path }}
                            </div>
                        </div>
                    </button>
                </div>
            </div>
        </div>
    `,
    setup(props) {
        const ctx = inject('ctx')
        const showPopover = ref(false)
        const triggerRef = ref(null)
        const popoverRef = ref(null)

        const projects = computed(() => ext.state.projects || [])
        const activeProject = computed(() => ext.state.activeProject)
        const defaultPath = computed(() => ext.state.defaultPath || '')

        const togglePopover = () => showPopover.value = !showPopover.value

        async function selectProject(project) {
            try {
                if (project === null) {
                    const res = await ext.postJson(`/active`, { path: null })
                    ext.setState({ activeProject: null })
                    ext.setPrefs({ activeProjectPath: ext.state.defaultPath })
                    ctx.toast("Switched to Default Workspace")
                } else {
                    const res = await ext.postJson(`/active`, { path: project.path, name: project.name })
                    if (res.responseStatus?.errorCode) {
                        alert(`Error: ${res.responseStatus.message}`)
                    } else {
                        ext.setState({ activeProject: project })
                        ext.setPrefs({ activeProjectPath: project.path })
                        ctx.toast(`Switched to project: ${project.name}`)
                    }
                }
            } catch (e) {
                console.error("Failed to switch project", e)
                alert(`Failed to switch project: ${e.message ?? e}`)
            } finally {
                showPopover.value = false
            }
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
            activeProject,
            defaultPath,
            togglePopover,
            selectProject
        }
    }
}

export default {
    order: 30 - 100,

    install(ctx) {
        ext = ctx.scope('projects')

        ctx.components({ ProjectsSelector })

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
        ext.setState({ projects })

        const activeRes = await ext.getJson(`/active`)
        const activeData = activeRes.response || {}
        ext.setState({ defaultPath: activeData.defaultPath })

        const prefs = ext.getPrefs()
        const savedPath = prefs.activeProjectPath

        if (savedPath) {
            if (savedPath === activeData.defaultPath) {
                await ext.postJson(`/active`, { path: null })
                ext.setState({ activeProject: null })
            } else {
                const project = projects.find(p => p.path === savedPath)
                if (project) {
                    await ext.postJson(`/active`, { path: project.path, name: project.name })
                    ext.setState({ activeProject: project })
                } else {
                    await ext.postJson(`/active`, { path: null })
                    ext.setState({ activeProject: null })
                    ext.setPrefs({ activeProjectPath: activeData.defaultPath })
                }
            }
        } else {
            if (activeData.active) {
                const project = projects.find(p => p.path === activeData.active.path) || activeData.active
                ext.setState({ activeProject: project })
                ext.setPrefs({ activeProjectPath: project.path })
            } else {
                ext.setState({ activeProject: null })
                ext.setPrefs({ activeProjectPath: activeData.defaultPath })
            }
        }
    }
}
