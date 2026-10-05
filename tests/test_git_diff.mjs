import assert from 'node:assert/strict'
import { parseUnifiedDiff } from '../llms/extensions/git/ui/diff.mjs'

const rows = parseUnifiedDiff(`diff --git a/a.txt b/a.txt
index abcd..ef01 100644
--- a/a.txt
+++ b/a.txt
@@ -2,3 +2,4 @@ heading
 keep
-removed
+++starts with pluses
+added
 tail
@@ -20 +21 @@
-old
+new
\\ No newline at end of file
`)
assert.deepEqual(rows.slice(0, 6), [
    {type:'hunk',text:'@@ -2,3 +2,4 @@ heading'},
    {type:'context',oldLine:2,newLine:2,prefix:' ',text:'keep'},
    {type:'removed',oldLine:3,prefix:'−',text:'removed'},
    {type:'added',newLine:3,prefix:'+',text:'++starts with pluses'},
    {type:'added',newLine:4,prefix:'+',text:'added'},
    {type:'context',oldLine:4,newLine:5,prefix:' ',text:'tail'},
])
assert.equal(rows[7].oldLine, 20)
assert.equal(rows[8].newLine, 21)
assert.deepEqual(rows[9], {type:'meta',text:'\\ No newline at end of file'})
assert.deepEqual(parseUnifiedDiff(''), [])
assert.deepEqual(parseUnifiedDiff('old mode 100644\nnew mode 100755\n'), [
    {type:'meta',text:'old mode 100644'}, {type:'meta',text:'new mode 100755'},
])
assert.equal(parseUnifiedDiff('@@ -0,0 +1 @@\n+<script>unsafe</script>\n')[1].text, '<script>unsafe</script>')
assert.equal(parseUnifiedDiff('@@ -0,0 +1 @@\r\n+Windows line\r\n')[1].text, 'Windows line')
console.log('Git diff parsing tests passed')
