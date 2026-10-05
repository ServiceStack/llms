import assert from 'node:assert/strict'
import { createWorkspaceTree } from '../llms/ui/modules/workspaceTree.mjs'
const state = {nodes:{}}, calls = []
const tree = createWorkspaceTree(state, async path => { calls.push(path); return {path:path || '/root',roots:['/root'],entries:[]} })
await tree.ensure()
await tree.ensure('/root')
assert.deepEqual(calls, [''])
tree.node('/root/child')
assert.deepEqual(calls, [''], 'Creating a collapsed child does not fetch it')
await Promise.all([tree.ensure('/root/child'),tree.ensure('/root/child')])
await tree.ensure('/root/child')
assert.deepEqual(calls, ['', '/root/child'], 'Concurrent requests and reopening reuse the listing')
let finish
const delayedState = {nodes:{}}, delayed = createWorkspaceTree(delayedState, () => new Promise(resolve => finish=resolve))
const old = delayed.ensure('/old')
delayed.reset();finish({path:'/old',entries:[]});assert.equal(await old,null)
assert.deepEqual(delayedState.nodes, {}, 'Old project responses do not repopulate a new tree')
const failingState = {nodes:{}}, failing = createWorkspaceTree(failingState, async () => {throw Error('Denied')})
await assert.rejects(failing.ensure('/denied'), /Denied/)
assert.equal(failing.node('/denied').error, 'Denied')
assert.equal(failing.node('/denied').loading, false)
console.log('Workspace lazy tree tests passed')
