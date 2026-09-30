const colorScheme = window.llmsDesktopColorScheme
if (colorScheme === 'light' || colorScheme === 'dark') {
    document.documentElement.dataset.colorScheme = colorScheme
    document.documentElement.style.colorScheme = colorScheme
}

window.llmsDesktopShowError = message => {
    document.querySelector('#status').hidden = true
    document.querySelector('#error').hidden = false
    document.querySelector('#error-message').textContent = message
}
