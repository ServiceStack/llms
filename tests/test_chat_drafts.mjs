import assert from 'node:assert/strict'
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'
const source = fs.readFileSync('llms/ui/modules/chat/draftStore.mjs', 'utf8')
    .replace("from 'vue'", `from '${pathToFileURL(process.cwd() + '/llms/ui/lib/vue.min.mjs')}'`)
const { createDraftStore } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
let account = 'alice'
const store = createDraftStore(() => account)
const text = store.adapter('text'), editing = store.adapter('editingMessage')
store.bind('1'); text.value = 'Draft A'; editing.value = 12
store.bind('2'); text.value = 'Draft B'
store.bind('1'); assert.equal(text.value, 'Draft A'); assert.equal(editing.value, 12)
const sent = store.snapshot()
text.value = 'Next draft'
store.bind('2'); store.accepted(sent)
assert.equal(text.value, 'Draft B')
store.bind('1'); assert.equal(text.value, 'Next draft')
const accepted = store.snapshot(); store.accepted(accepted); assert.equal(text.value, '')
const local = store.fresh('project-a'); text.value = 'First prompt'
const first = store.snapshot()
store.bind('2'); store.transfer(local, 3); store.accepted(first)
assert.equal(text.value, 'Draft B'); store.bind('3'); assert.equal(text.value, '')
assert.equal(store.get().projectId, 'project-a')
let complete
const uploaded = new Promise(resolve => {complete = resolve})
store.bind('1')
const upload = store.attach('1', [{name:'test.pdf', type:'application/pdf'}], () => uploaded)
store.bind('2'); complete({url:'/cache/pdf'}); await upload
assert.equal(store.get().attachments.length, 0)
store.bind('1'); assert.equal(store.get().attachments[0].state, 'ready')
let finish
const late = store.attach('1', [{name:'removed.pdf',type:'application/pdf'}], () => new Promise(resolve => {finish = resolve}))
const entry = store.get().attachments.at(-1)
store.remove('1', entry.id); finish({url:'/cache/removed'}); await late
assert.equal(store.get().attachments.length, 1)
account = 'bob'; assert.equal(text.value, ''); assert.equal(store.get().attachments.length, 0)
account = 'alice'; assert.equal(store.get().attachments.length, 1)
await store.discard('1'); assert.equal(store.get().attachments.length, 0)
console.log('Draft navigation, account isolation, accepted snapshots and upload races passed')

// Empty project-scoped drafts must be visible before their first message.
const scopedDraft = store.fresh('new-project')
assert.ok(store.list().some(d => d.key === scopedDraft && d.projectId === 'new-project'))
const unscopedDraft = store.fresh()
assert.ok(!store.list().some(d => d.key === unscopedDraft))
await store.discard(scopedDraft)
assert.ok(!store.list().some(d => d.key === scopedDraft))

// Deleting the selected project draft must not replace it with another scoped draft.
const sidebarSource = fs.readFileSync(new URL('../llms/extensions/app/ui/ProjectThreads.mjs', import.meta.url), 'utf8')
const removeBody = sidebarSource.match(/async function removeDraft\(draft\) \{([\s\S]*?)\n        \}/)[1]
const removeDraft = new Function('ctx', 'return async function(draft) {' + removeBody + '}')({
    chat: {drafts: store},
    threads: {clearCurrentThread() {}},
    to() {},
})
const deletedKey = store.fresh('delete-project')
await removeDraft(store.get())
assert.ok(!store.list().some(d => d.projectId === 'delete-project'))
assert.notEqual(store.state.key, deletedKey)
assert.equal(store.get().projectId, null)
