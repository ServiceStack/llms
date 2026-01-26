import { ref, inject, computed } from "vue"
import { leftPart } from "@servicestack/client"

let ext

const SkillSelector = {
    template: `
        <div class="px-4 py-4 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 max-h-[80vh] overflow-y-auto">
            
            <!-- Global Controls -->
            <div class="flex items-center justify-between mb-4">
                <span class="text-xs font-bold uppercase text-gray-500 tracking-wider">Include Skills</span>
                <div class="flex items-center gap-2">
                    <button @click="$ctx.setPrefs({ onlySkills: null })"
                        class="px-3 py-1 rounded-md text-xs font-medium border transition-colors select-none"
                        :class="$prefs.onlySkills == null
                            ? 'bg-green-100 dark:bg-green-900/40 text-green-800 dark:text-green-300 border-green-300 dark:border-green-800' 
                            : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                        All Skills
                    </button>
                    <button @click="$ctx.setPrefs({ onlySkills:[] })"
                        class="px-3 py-1 rounded-md text-xs font-medium border transition-colors select-none"
                        :class="$prefs.onlySkills?.length === 0
                            ? 'bg-fuchsia-100 dark:bg-fuchsia-900/40 text-fuchsia-800 dark:text-fuchsia-300 border-fuchsia-200 dark:border-fuchsia-800' 
                            : 'cursor-pointer bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                        No Skills
                    </button>
                </div>
            </div>

            <!-- Groups -->
            <div class="space-y-3">
                <div v-for="group in skillGroups" :key="group.name" 
                     class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                     
                     <!-- Group Header -->
                     <div class="flex items-center justify-between px-3 py-2 bg-gray-50/50 dark:bg-gray-800/50 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                          @click="toggleCollapse(group.name)">
                        
                        <div class="flex items-center gap-2 min-w-0">
                             <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4 text-gray-400 transition-transform duration-200" :class="{ '-rotate-90': isCollapsed(group.name) }">
                                <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                             </svg>
                             <span class="font-semibold text-sm text-gray-700 dark:text-gray-200 truncate">
                                {{ group.name || 'Other Skills' }}
                             </span>
                             <span class="text-xs text-gray-400 font-mono">
                                {{ getActiveCount(group) }}/{{ group.skills.length }}
                             </span>
                        </div>

                        <div class="flex items-center gap-2" @click.stop>
                             <button @click="setGroupSkills(group, true)" type="button"
                                title="Include All in Group"
                                class="px-2 py-0.5 rounded text-xs font-medium border transition-colors select-none"
                                :class="getActiveCount(group) === group.skills.length
                                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 border-green-300 dark:border-green-800 hover:bg-green-100 dark:hover:bg-green-900/40'
                                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                all
                             </button>
                             <button @click="setGroupSkills(group, false)" type="button"
                                title="Include None in Group"
                                class="px-2 py-0.5 rounded text-xs font-medium border transition-colors select-none"
                                :class="getActiveCount(group) === 0
                                    ? 'bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-700 dark:text-fuchsia-300 border-fuchsia-200 dark:border-fuchsia-800 hover:bg-fuchsia-100 dark:hover:bg-fuchsia-900/40'
                                    : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                none
                             </button>
                        </div>
                     </div>
                     
                     <!-- Group Body -->
                     <div v-show="!isCollapsed(group.name)" class="p-3 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-800">
                         <div class="flex flex-wrap gap-2">
                            <button v-for="skill in group.skills" :key="skill.name" type="button"
                                @click="toggleSkill(skill.name)"
                                :title="skill.description"
                                class="px-2.5 py-1 rounded-full text-xs font-medium border transition-colors select-none text-left truncate max-w-[200px]"
                                :class="isSkillActive(skill.name)
                                    ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-800' 
                                    : 'bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'">
                                {{ skill.name }}
                            </button>
                         </div>
                     </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const ctx = inject('ctx')
        const collapsedState = ref({})

        const availableSkills = computed(() => Object.values(ctx.state.skills || {}))

        const skillGroups = computed(() => {
            const skills = availableSkills.value
            const groupsMap = {}
            const otherSkills = []

            skills.forEach(skill => {
                if (skill.group) {
                    if (!groupsMap[skill.group]) groupsMap[skill.group] = []
                    groupsMap[skill.group].push(skill)
                } else {
                    otherSkills.push(skill)
                }
            })

            const definedGroups = Object.entries(groupsMap).map(([name, skills]) => ({
                name,
                skills
            }))

            // Sort groups by name if needed, but for now rely on insertion order or backend order
            definedGroups.sort((a, b) => a.name.localeCompare(b.name))

            if (otherSkills.length > 0) {
                definedGroups.push({ name: '', skills: otherSkills })
            }

            return definedGroups
        })

        function isSkillActive(name) {
            const only = ctx.prefs.onlySkills
            if (only == null) return true
            if (Array.isArray(only)) {
                return only.includes(name)
            }
            return false
        }

        function toggleSkill(name) {
            let onlySkills = ctx.prefs.onlySkills

            if (onlySkills == null) {
                // If currently 'All', clicking a skill means we enter custom mode with all OTHER skills selected (deselecting clicked)
                // Wait, logic in ToolSelector:
                // if (onlyTools == null) { onlyTools = availableTools.value.map(t => t.function.name).filter(t => t !== name) }
                // This means deselecting one tool switches to "custom" with all but that one.

                onlySkills = availableSkills.value.map(s => s.name).filter(s => s !== name)
            } else {
                if (onlySkills.includes(name)) {
                    onlySkills = onlySkills.filter(s => s !== name)
                } else {
                    onlySkills = [...onlySkills, name]
                }
            }

            ctx.setPrefs({ onlySkills })
        }

        function toggleCollapse(groupName) {
            const key = groupName || '_other_'
            collapsedState.value[key] = !collapsedState.value[key]
        }

        function isCollapsed(groupName) {
            const key = groupName || '_other_'
            return !!collapsedState.value[key]
        }

        function setGroupSkills(group, enable) {
            const groupSkillNames = group.skills.map(s => s.name)
            let onlySkills = ctx.prefs.onlySkills

            if (enable) {
                if (onlySkills == null) return
                const newSet = new Set(onlySkills)
                groupSkillNames.forEach(n => newSet.add(n))
                onlySkills = Array.from(newSet)
                if (onlySkills.length === availableSkills.value.length) {
                    onlySkills = null
                }
            } else {
                if (onlySkills == null) {
                    onlySkills = availableSkills.value
                        .map(s => s.name)
                        .filter(n => !groupSkillNames.includes(n))
                } else {
                    onlySkills = onlySkills.filter(n => !groupSkillNames.includes(n))
                }
            }

            ctx.setPrefs({ onlySkills })
        }

        function getActiveCount(group) {
            const onlySkills = ctx.prefs.onlySkills
            if (onlySkills == null) return group.skills.length
            return group.skills.filter(s => onlySkills.includes(s.name)).length
        }

        return {
            availableSkills,
            skillGroups,
            isSkillActive,
            toggleSkill,
            toggleCollapse,
            isCollapsed,
            setGroupSkills,
            getActiveCount
        }
    }
}

function codeFragment(s) {
    return "`" + s + "`"
}
function codeBlock(s) {
    return "```\n" + s + "\n```\n"
}

const SkillInstructions = `
You have access to specialized skills that extend your capabilities with domain-specific knowledge, workflows, and tools. 
Skills are modular packages containing instructions, scripts, references, and assets for particular tasks.

## Using Skills

Use the skill tool to read a skill's main instructions and guidance, e.g:
${codeBlock("skill({ name: \"skill-name\" })")}

To read a specific file within a skill (scripts, references, assets):
${codeBlock("skill({ name: \"skill-name\", file: \"relative/path/to/file\" })")}

Examples:
- ${codeFragment("skill({ name: \"create-plan\" })")} - Read the create-plan skill's SKILL.md instructions
- ${codeFragment("skill({ name: \"web-artifacts-builder\", file: \"scripts/init-artifact.sh\" })")} - Read a specific script

## When to Use Skills

You should read the appropriate skill BEFORE starting work on relevant tasks. Skills contain best practices, scripts, and reference materials that significantly improve output quality.

**Skill Selection Guidelines:**
- Match the task to available skill descriptions
- Multiple skills may be relevant - read all that apply
- Read the skill first, then follow its instructions

## Available Skills
$$AVAILABLE_SKILLS$$

## Important Notes

- Always read the skill BEFORE starting implementation
- Skills may contain scripts that can be executed directly without loading into context
- Multiple skills can and should be combined when tasks span multiple domains
- If a skill references additional files (references/, scripts/, assets/), read those as needed during execution
`

export default {
    order: 15 - 100,

    install(ctx) {
        ext = ctx.scope("skills")

        ctx.components({ SkillSelector })

        const svg = (attrs, title) => `<svg ${attrs} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">${title ? "<title>" + title + "</title>" : ''}<path fill="currentColor" d="M20 17a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H9.46c.35.61.54 1.3.54 2h10v11h-9v2m4-10v2H9v13H7v-6H5v6H3v-8H1.5V9a2 2 0 0 1 2-2zM8 4a2 2 0 0 1-2 2a2 2 0 0 1-2-2a2 2 0 0 1 2-2a2 2 0 0 1 2 2"/></svg>`

        ctx.setTopIcons({
            skills: {
                component: {
                    template: svg([
                        `@click="$ctx.toggleTop('SkillSelector')"`,
                        `:class="$prefs.onlySkills == null ? 'text-green-600 dark:text-green-300' : $prefs.onlySkills.length ? 'text-blue-600! dark:text-blue-300!' : ''"`
                    ].join(' ')),
                },
                isActive({ top }) {
                    return top === 'SkillSelector'
                },
                get title() {
                    return ctx.prefs.onlySkills == null
                        ? `All Skills Included`
                        : ctx.prefs.onlySkills.length
                            ? `${ctx.prefs.onlySkills.length} ${ctx.utils.pluralize('Skill', ctx.prefs.onlySkills.length)} Included`
                            : 'No Skills Included'
                }
            }
        })

        ctx.chatRequestFilters.push(({ request, thread, context }) => {

            const prefs = ctx.prefs
            if (prefs.onlySkills != null) {
                if (Array.isArray(prefs.onlySkills)) {
                    request.metadata.skills = prefs.onlySkills.length > 0
                        ? prefs.onlySkills.join(',')
                        : 'none'
                }
            } else {
                request.metadata.skills = 'all'
            }

            console.log('skills.chatRequestFilters', prefs.onlySkills, Object.keys(ctx.state.skills || {}))
            const skills = ctx.state.skills
            if (!skills) return

            const includeSkills = []
            for (const skill of Object.values(skills)) {
                if (prefs.onlySkills == null || prefs.onlySkills.includes(skill.name)) {
                    includeSkills.push(skill)
                }
            }
            if (!includeSkills.length) return

            const sb = []
            sb.push("<available_skills>")
            for (const skill of includeSkills) {
                sb.push(" <skill>")
                sb.push("  <name>" + ctx.utils.encodeHtml(skill.name) + "</name>")
                sb.push("  <description>" + ctx.utils.encodeHtml(skill.description) + "</description>")
                sb.push("  <location>" + ctx.utils.encodeHtml(skill.location) + "</location>")
                sb.push(" </skill>")
            }
            sb.push("</available_skills>")

            const skillsPrompt = SkillInstructions.replace('$$AVAILABLE_SKILLS$$', sb.join('\n')).trim()
            context.requiredSystemPrompts.push(skillsPrompt)
        })

        ctx.setThreadFooters({
            skills: {
                component: {
                    template: `
                        <div class="mt-2 w-full flex justify-center">
                            <button type="button" @click="$ctx.chat.sendUserMessage('proceed')"
                                class="px-3 py-1 rounded-md text-xs font-medium border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors select-none">
                                proceed
                            </button>
                        </div>
                    `
                },
                show({ thread }) {
                    if (thread.messages.length < 2) return false
                    const msgRoles = thread.messages.map(m => m.role)
                    if (msgRoles[msgRoles.length - 1] != "assistant") return false
                    const hasSkillToolCall = thread.messages.some(m =>
                        m.tool_calls?.some(tc => tc.type == "function" && tc.function.name == "skill"))
                    const systemPrompt = thread.messages.find(m => m.role == "system")?.content.toLowerCase() || ''
                    const line1 = leftPart(systemPrompt.trim(), "\n")
                    const hasPlanSystemPrompt = line1.includes("plan") || systemPrompt.includes("# plan")
                    return hasSkillToolCall || hasPlanSystemPrompt
                }
            }
        })

        ctx.setState({
            skills: {}
        })
    },

    async load(ctx) {
        const api = await ext.getJson('/')
        if (api.response) {
            ctx.setState({ skills: api.response })
        } else {
            ctx.setError(api.error)
        }
    }
}