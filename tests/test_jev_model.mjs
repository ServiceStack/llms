import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import {
    clone,
    canSaveRunExample,
    exampleFromRun,
    clearChangedExampleResults,
    defaults,
    compile,
    inputErrors,
    renameQuestion,
    removeExpectations,
    expectation,
    cost,
    validKey,
} from '../llms/extensions/jev/ui/recipeModel.mjs'
import {
    createApi,
    createRunTracker,
} from '../llms/extensions/jev/ui/studioState.mjs'

const folder = new URL('../llms/extensions/jev/recipes/', import.meta.url)
const recipes = readdirSync(folder).map((name) =>
    JSON.parse(readFileSync(new URL(name, folder))),
)

test('every starter example validates and compiles without mutating source values', () => {
    assert.equal(recipes.length, 7)
    for (const recipe of recipes)
        for (const example of recipe.examples) {
            assert.deepEqual(inputErrors(recipe.inputSchema, example.input), [])
            const request = compile(recipe, example.input)
            assert.deepEqual(Object.keys(request), [
                'model',
                'state',
                'questions',
            ])
            assert.deepEqual(request.state, example.input)
            request.state.changed = true
            assert.equal(example.input.changed, undefined)
            assert.notEqual(request.questions, recipe.questions)
        }
})

test('input constraints preserve false and zero and identify nested fields', () => {
    const schema = {
        type: 'object',
        properties: {
            flag: { type: 'boolean' },
            amount: { type: 'integer', minimum: 0 },
            nested: {
                type: 'object',
                properties: { text: { type: 'string', minLength: 3 } },
                required: ['text'],
            },
            list: { type: 'array', items: { type: 'number' }, maxItems: 1 },
        },
        required: ['flag', 'amount', 'nested'],
    }
    const value = {
        flag: false,
        amount: 0,
        nested: { text: 'valid' },
        list: [1],
    }
    assert.deepEqual(inputErrors(schema, value), [])
    assert.equal(
        inputErrors(schema, { ...value, nested: { text: '' } })[0].fieldName,
        'nested.text',
    )
    assert.equal(
        inputErrors(schema, { ...value, amount: 1.5 })[0].fieldName,
        'amount',
    )
    assert.equal(
        inputErrors(schema, { ...value, list: [1, 2] })[0].fieldName,
        'list',
    )
    assert.equal(
        inputErrors(schema, { ...value, amount: Infinity })[0].fieldName,
        'amount',
    )
    const first = defaults(schema),
        second = defaults(schema)
    first.nested.text = 'edited'
    assert.equal(second.nested.text, '')
})

test('question renaming preserves presentation and expectations, semantic changes clear labels', () => {
    const recipe = clone(recipes[0]),
        oldKey = Object.keys(recipe.questions)[0]
    recipe.examples[0].expected = { [oldKey]: 'negative' }
    recipe.examples[0].provenance = 'user-reviewed'
    const renamed = renameQuestion(recipe, oldKey, 'tone')
    assert.deepEqual(renamed.examples[0].expected, { tone: 'negative' })
    assert.ok(renamed.presentation.questions.tone)
    assert.ok(recipe.questions[oldKey])
    assert.throws(() => renameQuestion(recipe, oldKey, 'constructor'))
    assert.equal(validKey('__proto__'), false)
    removeExpectations(renamed, 'tone')
    assert.deepEqual(renamed.examples[0].expected, {})
    assert.equal(renamed.examples[0].provenance, 'authored')
})

test('probabilities, fractional scores and small costs retain useful meaning', () => {
    assert.equal(cost(0.000019992), '$0.00001999')
    assert.equal(cost(undefined), 'Cost unavailable')
    assert.equal(
        expectation(
            { expected: { a: true }, provenance: 'user-reviewed' },
            'a',
            {
                type: 'noul',
                noul: 0.51,
            },
        ),
        'Matches',
    )
    assert.equal(
        expectation({ expected: { a: 2 }, provenance: 'ai-suggested' }, 'a', {
            type: 'score',
            score: 1.99,
        }),
        'Suggested: Matches',
    )
    assert.equal(
        expectation(
            { expected: { a: 'x' }, provenance: 'user-reviewed' },
            'a',
            {
                type: 'choice',
                choice: 'y',
            },
        ),
        'Differs',
    )
    const recipe = clone(recipes[0])
    recipe.state = { mode: 'text', field: 'text' }
    assert.equal(
        compile(recipe, { text: 'Quotes " Unicode ☃\nNewline' }).state,
        'Quotes " Unicode ☃\nNewline',
    )
})

test('API reports structured conflict fields and rejects unreadable responses', async () => {
    const api = createApi({
        get: async () =>
            new Response(
                JSON.stringify({
                    responseStatus: {
                        message: 'Changed in another tab',
                        errorCode: 'RecipeExistsError',
                        existingRecipe: { id: 'support', revision: 2 },
                        errors: [{ fieldName: 'revision', message: 'Stale' }],
                    },
                }),
                { status: 409 },
            ),
    })
    await assert.rejects(
        api('/recipes'),
        (e) => e.status === 409 && e.fields[0].fieldName === 'revision',
    )
    await assert.rejects(
        api('/recipes'),
        (e) =>
            e.code === 'RecipeExistsError' &&
            e.existingRecipe.id === 'support' &&
            e.existingRecipe.revision === 2,
    )
    await assert.rejects(
        createApi({
            get: async () =>
                new Response('<html>proxy error</html>', { status: 502 }),
        })('/runs'),
        /unreadable/,
    )
})

test('run tracking follows captured origins, can migrate saved drafts and ignores disposed responses', async () => {
    const previousTimeout = globalThis.setTimeout,
        previousClear = globalThis.clearTimeout
    const timers = new Map()
    let timer = 0
    globalThis.setTimeout = (fn) => {
        timers.set(++timer, fn)
        return timer
    }
    globalThis.clearTimeout = (id) => timers.delete(id)
    try {
        const updates = []
        let resolve
        const api = async () => await new Promise((done) => (resolve = done))
        const tracker = createRunTracker(api, (run, origin) =>
            updates.push([run.status, origin]),
        )
        tracker.follow({ id: 'one', status: 'pending' }, 'local:one')
        tracker.follow({ id: 'one', status: 'pending' }, 'local:one')
        assert.equal(timers.size, 1, 'same run has one polling timer')
        tracker.retarget('local:one', 'saved-one')
        const poll = [...timers.values()][0]()
        resolve({ id: 'one', status: 'succeeded' })
        await poll
        assert.deepEqual(updates.at(-1), ['succeeded', 'saved-one'])
        tracker.follow({ id: 'two', status: 'pending' }, 'recipe-two')
        const pending = [...timers.values()].at(-1)()
        tracker.dispose()
        const count = updates.length
        resolve({ id: 'two', status: 'succeeded' })
        await pending
        assert.equal(
            updates.length,
            count,
            'unmounted view receives no late response',
        )
    } finally {
        globalThis.setTimeout = previousTimeout
        globalThis.clearTimeout = previousClear
    }
})

test('run examples preserve actual probabilities and input, reject incomplete/stale/duplicate results', () => {
    const recipe = clone(recipes[0])
    const run = {
        status: 'succeeded',
        recipe: clone(recipe),
        input: clone(recipe.examples[0].input),
        completedAt: 1770000000.125,
        model: 'typesafe/jev-1.13',
        durationMs: 123,
        answers: { result: { type: 'noul', noul: 0.83 } },
        response: {
            model: 'typesafe/jev-1.13',
            usage: { private: true },
            apiKey: 'secret',
        },
    }
    const before = clone(run)
    const example = exampleFromRun(recipe, run, '  Delayed delivery  ')
    assert.equal(example.label, 'Delayed delivery')
    for (const name of [undefined, '', '   ', 'x'.repeat(121)])
        assert.throws(() => exampleFromRun(recipe, run, name), /example name/)
    assert.deepEqual(example.input, run.input)
    assert.deepEqual(example.execution.answers, run.answers)
    assert.equal(example.execution.completedAt, '2026-02-02T02:40:00.125Z')
    assert.equal(example.expected, undefined)
    assert.equal(example.execution.response, undefined)
    assert.deepEqual(run, before)
    example.execution.answers.result.noul = 0.1
    assert.equal(run.answers.result.noul, 0.83)
    for (const status of [
        'pending',
        'running',
        'failed',
        'cancelled',
        'interrupted',
    ])
        assert.equal(canSaveRunExample(recipe, { ...run, status }), false)
    assert.equal(
        canSaveRunExample(recipe, { ...run, completedAt: null }),
        false,
    )
    const saved = clone(recipe)
    saved.examples.push(exampleFromRun(recipe, run, '  Delayed delivery  '))
    assert.equal(canSaveRunExample(saved, run), false)
    const metadata = { ...recipe, name: 'Renamed', tags: ['new'] }
    assert.equal(canSaveRunExample(metadata, run), true)
    for (const key of ['questions', 'inputSchema', 'state', 'decisionModel']) {
        const edited = clone(saved)
        if (key === 'decisionModel') edited[key] = 'typesafe/jev-1.13'
        else if (key === 'state') edited[key] = { mode: 'text', field: 'other' }
        else edited[key].changed = true
        assert.equal(canSaveRunExample(edited, run), false)
        const cleaned = clearChangedExampleResults(saved, edited)
        assert.equal(cleaned.examples.at(-1).execution, undefined)
        assert.deepEqual(cleaned.examples.at(-1).input, run.input)
        assert.ok(saved.examples.at(-1).execution)
    }
    assert.ok(
        clearChangedExampleResults(saved, {
            ...saved,
            description: 'Updated docs',
        }).examples.at(-1).execution,
    )
})


test('example checks compare against actual saved output before legacy expectations', () => {
    for (const [type, valueKey, saved, matching, differing] of [
        ['choice', 'choice', 'positive', 'positive', 'negative'],
        ['score', 'score', 2, 2.4, 2.6],
        ['noul', 'noul', 0.9, 0.51, 0.49],
    ]) {
        const example = {
            execution: { answers: { q: { type, [valueKey]: saved } } },
            expected: { q: differing },
        }
        assert.equal(expectation(example, 'q', { type, [valueKey]: matching }), 'Matches saved run')
        assert.equal(expectation(example, 'q', { type, [valueKey]: differing }), 'Differs from saved run')
    }
})
