// This bridge runs only in the authenticated desktop UI origin.
// localStorage remains authoritative; the file is a cache for the next launch.
if (window.top === window && location.origin === 'http://127.0.0.1:18000') {
    window.addEventListener('DOMContentLoaded', () => {
        let saved = null
        let writes = Promise.resolve()
        const persist = () => {
            const colorScheme = localStorage.getItem('color-scheme')
            if (!['light', 'dark'].includes(colorScheme) || colorScheme === saved) return
            saved = colorScheme
            writes = writes.then(async () => {
                const response = await fetch('/~desktop/preferences', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ colorScheme }),
                })
                if (!response.ok) throw new Error(`Desktop preferences: HTTP ${response.status}`)
            }).catch(error => {
                if (saved === colorScheme) saved = null
                console.warn('Could not save the desktop color scheme', error)
            })
        }
        persist()
        new MutationObserver(persist).observe(document.documentElement, {
            attributes: true, attributeFilter: ['class'],
        })
        window.addEventListener('storage', event => {
            if (event.key === 'color-scheme') persist()
        })
    }, { once: true })
}
