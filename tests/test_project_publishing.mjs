import assert from 'node:assert/strict'
import { publicationUrl, publishProjectOutput } from '../llms/ui/modules/shareProject.mjs'

const publication = { urlPath: '/p/alice/my%20project/', publishedUrl: null }
assert.equal(publicationUrl({ baseUrl: 'http://127.0.0.1:8080/p' }, publication), 'http://127.0.0.1:8080/p/alice/my%20project/')
assert.equal(publicationUrl({ baseUrl: 'https://example.com/sites/' }, publication), 'https://example.com/sites/alice/my%20project/')
assert.equal(publicationUrl({ baseUrl: null }, publication), null)
assert.equal(publicationUrl({ baseUrl: '' }, publication), null)
const currentDomain = path => new URL(path, 'https://chat.example:8443').href
assert.equal(publicationUrl({ baseUrl: '' }, publication, currentDomain), 'https://chat.example:8443/p/alice/my%20project/')
assert.equal(publicationUrl({ baseUrl: null }, publication, currentDomain), 'https://chat.example:8443/p/alice/my%20project/')
assert.equal(publicationUrl({ baseUrl: 'https://static.example/sites' }, publication, currentDomain), 'https://static.example/sites/alice/my%20project/')
assert.equal(publicationUrl({ baseUrl: 'https://example.com' }, null), null)
let resume, posted = [], saved = []
const project = { id: 'one', name: 'My Project', publish: 'dist' }
const ctx = {
    ai: { auth: {} },
    projects: {
        async saveProject(name, data, options) {
            saved.push({ name, data, options })
            await new Promise(resolve => { resume = resolve })
            return { response: [data] }
        },
    },
}
const ext = { postJson: async path => { posted.push(path); return { response: { publishedPath: '/p/default/one' } } } }
const publishing = publishProjectOutput(ctx, ext, project, 'build', 'folder')
project.id = 'two'
project.name = 'Other Project'
resume()
assert.ok((await publishing).response.publishedPath)
assert.deepEqual(posted, ['/project/one/folder'])
assert.equal(saved[0].name, 'My Project')
assert.equal(saved[0].data.publish, 'build')
assert.equal(saved[0].options.reportError, false)

await publishProjectOutput(ctx, ext, { id: 'one', name: 'My Project', publish: 'dist' }, 'dist', 'remote')
assert.equal(posted.at(-1), '/project/My%20Project')
assert.equal(saved.length, 1)

const accountChange = publishProjectOutput(ctx, ext, project, 'build', 'folder')
ctx.ai.auth.userName = 'new-user'
resume()
assert.ok((await accountChange).error)
assert.equal(posted.length, 2)

ctx.projects.saveProject = async () => ({ error: { message: 'Save failed' } })
assert.ok((await publishProjectOutput(ctx, ext, project, 'build', 'folder')).error)
assert.equal(posted.length, 2)
console.log('Publishing defaults, target capture, account changes and save errors passed.')
