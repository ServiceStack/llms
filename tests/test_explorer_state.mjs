import assert from 'node:assert/strict'
import { directoryBreadcrumbs, workspaceQuery, installWorkspaceNavigation, resolveWorkspaceView } from '../llms/ui/modules/explorerState.mjs'
assert.deepEqual(directoryBreadcrumbs('/work/src/deep', '/work'), [
    { name: 'work', path: '/work' }, { name: 'src', path: '/work/src' }, { name: 'deep', path: '/work/src/deep' }
])
assert.deepEqual(directoryBreadcrumbs('C:\\work\\src', 'C:\\work'), [
    { name: 'work', path: 'C:\\work' }, { name: 'src', path: 'C:\\work\\src' }
])
assert.deepEqual(directoryBreadcrumbs('/src', '/'), [{ name: '/', path: '/' }, { name: 'src', path: '/src' }])
assert.deepEqual(workspaceQuery({ chat: '123', workspaceFile: 'old' }, { workspaceFile: null, workspacePath: '/a b' }), {chat:'123', workspacePath:'/a b'})
console.log('Explorer state tests passed')

let guard
const removeGuard = () => {}
assert.equal(installWorkspaceNavigation({beforeEach(fn) { guard = fn; return removeGuard }}), removeGuard)
const from = {path:'/c/one',query:{workspace:'1',workspacePath:'/work',workspaceFile:'/work/a.txt',workspacePreview:'git',workspaceCommit:'abc1234',workspaceProject:'p'}}
const destination = {path:'/c/two',hash:'',query:{},meta:{}}
assert.deepEqual(guard(destination, from), {path:'/c/two',hash:'',query:{workspace:'1',workspacePath:'/work',workspaceProject:'p'}})
assert.equal(guard(destination, {...from, query:{...from.query, workspaceView:'git'}}).query.workspaceView, 'git', 'Thread navigation preserves the selected sidebar view')
assert.equal(guard({...destination,query:{workspace:'0'}},from), undefined)
assert.deepEqual(guard({...destination,path:from.path},from), {path:from.path,hash:'',query:{workspace:'1',workspacePath:'/work',workspaceProject:'p'}}, 'Reselecting the current thread keeps the sidebar open')
assert.equal(guard(destination,{...from,query:{workspace:'0'}}), undefined)
assert.deepEqual(guard({...destination,query:{workspace:'1',workspaceFile:'/work/a.txt',workspacePreview:'git',workspaceCommit:'abc1234',q:'keep'},meta:{workspaceSidebar:false}},from),
 {path:'/c/two',hash:'',query:{q:'keep'},replace:true})
assert.equal(guard({...destination,meta:{workspaceSidebar:false}},from), undefined)
console.log('Workspace navigation tests passed')

const icons = { files: {}, git: { aliases: ['history'] }, custom: {} }
assert.equal(resolveWorkspaceView(icons, 'git'), 'git')
assert.equal(resolveWorkspaceView(icons, 'history'), 'git', 'Legacy Git links resolve through extension aliases')
assert.equal(resolveWorkspaceView(icons, 'custom'), 'custom', 'Third-party views use their own IDs')
assert.equal(resolveWorkspaceView({files:{}}, 'history'), 'files', 'Disabled extensions fall back to Files')
assert.equal(resolveWorkspaceView(icons, 'toString'), 'files', 'Inherited names are not registered views')
console.log('Pluggable workspace view tests passed')
