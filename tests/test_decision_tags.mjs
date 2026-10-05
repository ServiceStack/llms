import assert from 'node:assert/strict'
import {
    loadDecisionTags,
    validCatalog,
    normalizeTags,
    recipeMetadata,
    tagGroup,
} from '../llms/extensions/jev/ui/decisionTags.mjs'
const catalog = {
    version: 3,
    tags: [{ label: 'Email', group: 'content' }],
}
const values = new Map()
const storage = {
    getItem: (key) => values.get(key),
    setItem: (key, value) => values.set(key, value),
}
let calls = 0
const api = async (path) => {
    assert.equal(path, '/tags')
    calls++
    return catalog
}
assert(validCatalog(catalog))
assert(!validCatalog({ ...catalog, tags: [...catalog.tags, ...catalog.tags] }))
assert(
    !validCatalog({
        version: 3,
        tags: [{ group: 'tag', label: ' Bad ' }],
    }),
)
assert.deepEqual(normalizeTags(' Email, Risk Analysis, email, ,custom-tag '), [
    'Email',
    'Risk Analysis',
    'custom-tag',
])
assert.deepEqual(
    await Promise.all([
        loadDecisionTags(api, 'a', { storage, now: 100 }),
        loadDecisionTags(api, 'a', { storage, now: 100 }),
    ]),
    [catalog, catalog],
)
assert.equal(calls, 1, 'Concurrent editor/import loads share a request')
await loadDecisionTags(api, 'a', { storage, now: 200 })
assert.equal(calls, 1, 'Fresh cache avoids network')
await loadDecisionTags(api, 'b', { storage, now: 200 })
assert.equal(calls, 2, 'Different publisher/account scope has its own cache')
const offline = async () => {
    throw Error('offline')
}
assert.deepEqual(
    await loadDecisionTags(offline, 'a', { storage, now: 100 + 86400001 }),
    catalog,
    'Expired cache works offline',
)
assert.deepEqual(
    (await loadDecisionTags(offline, 'uncached', { storage, now: 200 })).tags,
    [],
    'Offline without cache allows custom tags',
)
values.set('jev:decision-tags:v3:corrupt', '{broken')
assert.deepEqual(
    await loadDecisionTags(api, 'corrupt', { storage, now: 200 }),
    catalog,
)
values.set(
    'jev:decision-tags:v3:persisted',
    JSON.stringify({ savedAt: 100, catalog }),
)
assert.deepEqual(
    await loadDecisionTags(offline, 'persisted', { storage, now: 200 }),
    catalog,
    'Persistent cache survives new app session',
)
assert.deepEqual(
    (await loadDecisionTags(async () => ({}), 'invalid', { storage, now: 200 }))
        .tags,
    [],
)
assert.deepEqual(
    await loadDecisionTags(api, 'no-storage', {
        storage: {
            getItem() {
                throw Error()
            },
            setItem() {
                throw Error()
            },
        },
        now: 200,
    }),
    catalog,
)
console.log(
    'PASS: tag catalogue validation, normalization, persistent cache, deduplication, scope and offline fallback',
)

const grouped = {
    version: 3,
    tags: [
        { label: 'Email', group: 'content' },
        { label: 'News', group: 'content' },
        { label: 'Routing', group: 'tag' },
    ],
}
assert(validCatalog(grouped))
assert.equal(tagGroup({ group: 'context' }), 'content')
assert.equal(tagGroup({ group: 'task' }), 'tag')
const legacy = { tags: ['email', 'news', 'routing', 'custom'] }
assert.deepEqual(recipeMetadata(legacy, grouped.tags), {
    content: 'Email',
    tags: ['Routing', 'custom'],
})
assert.deepEqual(
    legacy.tags,
    ['email', 'news', 'routing', 'custom'],
    'Viewing legacy metadata does not change the source',
)
assert.deepEqual(
    recipeMetadata({ content: 'custom-type', tags: ['email'] }, grouped.tags),
    { content: 'custom-type', tags: ['Email'] },
)

values.set(
    'jev:decision-tags:v1:legacy-offline',
    JSON.stringify({ savedAt: 100, catalog: {version:1,tags:[{name:'email',label:'Email',group:'context'}]} }),
)
assert.deepEqual(
    await loadDecisionTags(offline, 'legacy-offline', { storage, now: 200 }),
    { version: 1, tags: [{ label: 'Email', group: 'content' }] },
    'An older cached catalogue remains available offline while the new cache is refreshed',
)

assert(!validCatalog({version:3,tags:[{name:'email',label:'Email',group:'content'}]}), 'New catalogues contain no separate name')
assert(validCatalog({version:3,tags:[{label:'Risk analysis',group:'tag'}]}), 'Multiword labels are valid values')
assert.deepEqual(normalizeTags('Risk Analysis, risk-analysis, Other Topic'), ['Risk Analysis', 'Other Topic'], 'Labels retain spaces and spelling while duplicates are ignored')
values.set('jev:decision-tags:v2:upgrade', JSON.stringify({savedAt:100,catalog:{version:2,tags:[{name:'old',label:'Old',group:'task'}]}}))
assert.deepEqual(await loadDecisionTags(api,'upgrade',{storage,now:200}),catalog,'Older cache refreshes immediately')
assert.deepEqual(JSON.parse(values.get('jev:decision-tags:v3:upgrade')).catalog,catalog,'Only labels are persisted in the new cache')
