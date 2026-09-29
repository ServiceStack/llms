import assert from 'node:assert/strict'
import fs from 'node:fs'
const source = fs.readFileSync('llms/extensions/agents/ui/index.mjs', 'utf8')
const body = source.match(/async function runAction\(name, action\) \{([\s\S]*?)\n                        \}/)[1]
const draft = {projectId: 'old', text: ''}
let selected, sent, cleared = false
const ctx = {
    agents: {getAgent: () => ({}), selectAgent: id => {selected = id}},
    threads: {currentThread: {value: {id: 7, projectId: 'plan-project'}},
        clearCurrentThread: () => {cleared = true},
        startNewThread: () => {throw Error('Must create a draft, not submit a thread')}},
    chat: {
        drafts: {get: () => draft, fresh: id => {draft.projectId = id; draft.text = ''}, touch: () => {}},
        sendUserMessage: async message => {sent = {message, projectId: draft.projectId, profile: selected}},
    },
    to: () => {throw Error('Sending should handle navigation')},
}
const run = new Function('ctx', 'return async function(name, action) {' + body + '}')(ctx)
await run('Execute PLAN.md', {profile:'coder', message:'Execute PLAN.md'})
assert.equal(draft.projectId, 'plan-project')
assert.equal(draft.text, 'Execute PLAN.md')
assert.equal(selected, 'coder')
assert.deepEqual(sent, {message:'Execute PLAN.md', projectId:'plan-project', profile:'coder'})
assert.ok(cleared)
ctx.threads.currentThread.value.projectId = null
await run('Execute PLAN.md', {profile:'coder', message:'Execute PLAN.md'})
assert.equal(draft.projectId, null)
assert.equal(sent.projectId, null)
console.log('Execute PLAN.md automatically sends with the source project and Coder profile.')
