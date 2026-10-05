import assert from 'node:assert/strict'
import { highlightSource } from '../llms/ui/modules/sourceHighlight.mjs'

function text(html) {
    return html.replace(/<\/?span\b[^>]*>/g, '')
        .replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
}
for (const [file, source] of [
    ['src/app.mjs', 'const title = "Hello"; // note\n'],
    ['C:\\src\\main.py', 'def greet(name):\n    """A multiline\n    comment."""\n    return "Hello " + name\n'],
    ['Models.cs', 'public class Example { public int Count = 42; }'],
    ['package.json', '{"name": "demo", "enabled": true}'],
    ['Cargo.toml', '[package]\nname = "demo"\n'],
    ['app.yaml', 'enabled: true\nname: demo\n'],
    ['page.HTML', '<div title="Hello">Example &amp; text</div>'],
    ['style.css', '.example { color: red; }'],
    ['Makefile', 'all:\n\techo "hello"\n'],
]) {
    const html = highlightSource(source, file)
    assert.match(html, /class="hljs-/)
    assert.equal(text(html), source, file + ' must preserve whitespace and source text')
}
const hostile = 'const html = "<script>alert(1)</script><img src=x onerror=alert(2)> &";\n'
const highlighted = highlightSource(hostile, 'file.js')
assert.ok(!/<script|<img/i.test(highlighted))
assert.equal(text(highlighted), hostile)
for (const file of ['notes.txt', 'unknown.custom', 'constructor', 'source.__proto__', 'toString']) {
    const html = highlightSource(hostile, file)
    assert.ok(!/<span|<script|<img/.test(html))
    assert.equal(text(html), hostile)
}
for (const source of ['x'.repeat(200_001) + '<script>', 'const long = "' + 'x'.repeat(8_001) + '";']) {
    const html = highlightSource(source, 'file.js')
    assert.ok(!html.includes('<span'))
    assert.equal(text(html), source)
}
assert.equal(highlightSource(null, 'file.js'), '')
console.log('File highlighting: languages, escaped source, whitespace, and large-file fallbacks passed')
