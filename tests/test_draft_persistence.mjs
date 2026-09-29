import assert from 'node:assert/strict'
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

// Async IndexedDB surface used by the draft store.
const saved = new Map()
const request = result => {
    const req = {}
    queueMicrotask(() => { req.result = result(); req.onsuccess?.() })
    return req
}
const objectStore = {
    get: key => request(() => structuredClone(saved.get(key))),
    getAll: () => request(() => structuredClone([...saved.values()])),
    put: row => saved.set(row.key, structuredClone(row)),
    delete: key => saved.delete(key),
}
globalThis.indexedDB = { open: () => request(() => ({
    transaction: () => ({objectStore: () => objectStore}),
})) }
const source = fs.readFileSync('llms/ui/modules/chat/draftStore.mjs', 'utf8')
    .replace("from 'vue'", `from '${pathToFileURL(process.cwd() + '/llms/ui/lib/vue.min.mjs')}'`)
const {createDraftStore} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const key = 'local:restored'
const storageKey = 'alice::' + key
saved.set(storageKey, {key:storageKey, value:{
    key, scope:'alice', text:'Keep this prompt', projectId:'project-a',
    attachments:[], editingMessage:null, revision:3, updatedAt:1, tabId:'previous-page',
}})
const store = createDraftStore(() => 'alice')
await new Promise(resolve => setTimeout(resolve, 0))
store.bind(key)
const image = new Blob(['image'], {type:'image/png'})
Object.defineProperty(image, 'name', {value:'test.png'})
await store.attach(key, [image], async () => ({url:'/cache/test.png'}))
await store.flush()
await new Promise(resolve => setTimeout(resolve, 0))
assert.equal(store.state.key, key)
assert.equal(store.get().text, 'Keep this prompt')
assert.equal(store.get().projectId, 'project-a')
assert.equal(store.get().attachments.length, 1)
assert.equal(store.list().length, 1)
assert.equal(saved.size, 1)

// A genuinely unseen edit from another tab still gets a recovery copy.
const concurrent = structuredClone(saved.get(storageKey))
Object.assign(concurrent.value, {tabId:'another-tab', updatedAt:2, revision:4, text:'Concurrent edit'})
saved.set(storageKey, concurrent)
store.adapter('text').value = 'Local edit'
await store.flush()
await new Promise(resolve => setTimeout(resolve, 0))
assert.ok(store.list().some(d => d.recovered && d.text === 'Concurrent edit'))
console.log('Restored draft attachment updates preserve identity; concurrent edits retain recovery.')
