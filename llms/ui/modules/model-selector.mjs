import { ref, computed, watch, inject, onMounted, onUnmounted } from "vue"

const SORT_OPTIONS = [
    { id: 'name', label: 'Name' },
    { id: 'knowledge', label: 'Knowledge Cutoff' },
    { id: 'release_date', label: 'Release Date' },
    { id: 'last_updated', label: 'Last Updated' },
    { id: 'cost_input', label: 'Cost (Input)' },
    { id: 'cost_output', label: 'Cost (Output)' },
    { id: 'context', label: 'Context Limit' },
]

const I = x => `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${x}</svg>`
const modalityIcons = {
    text: I(`<polyline points="4,7 4,4 20,4 20,7"></polyline><line x1="9" y1="20" x2="15" y2="20"></line><line x1="12" y1="4" x2="12" y2="20"></line>`),
    image: I(`<rect width="18" height="18" x="3" y="3" rx="2" ry="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path>`),
    audio: I(`<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="m19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path>`),
    video: I(`<path d="m22 8-6 4 6 4V8Z"></path><rect width="14" height="12" x="2" y="6" rx="2" ry="2"></rect>`),
    pdf: I(`<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14,2 14,8 20,8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10,9 9,9 8,9"></polyline>`),
}

// Formatting helpers
const numFmt = new Intl.NumberFormat()
const currFmt = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

function formatCost(cost) {
    if (cost == null) return '-'
    const val = parseFloat(cost)
    if (val === 0) return 'Free'
    return currFmt.format(val)
}

function formatNumber(num) {
    if (num == null) return '-'
    return numFmt.format(num)
}

function formatShortNumber(num) {
    if (num == null) return '-'
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M'
    if (num >= 1000) return (num / 1000).toFixed(0) + 'K'
    return numFmt.format(num)
}

function getInputModalities(model) {
    const mods = new Set()
    const input = model.modalities?.input || []

    // Collect input modalities
    input.forEach(m => mods.add(m))

    // Filter out text and ensure we only show known icons for inputs
    const allowed = ['image', 'audio', 'video', 'pdf']
    return Array.from(mods).filter(m => m !== 'text' && allowed.includes(m)).sort()
}

function getOutputModalities(model) {
    const mods = new Set()
    const output = model.modalities?.output || []

    // Collect output modalities
    output.forEach(m => mods.add(m))

    // Filter out text (we show tags for other output types like audio/image generation)
    return Array.from(mods).filter(m => m !== 'text').sort()
}

const ProviderIcon = {
    template: `
        <svg v-if="matches(['openrouter'])" xmlns="http://www.w3.org/2000/svg" fill="#71717A" fill-rule="evenodd" viewBox="0 0 24 24"><path d="M16.804 1.957l7.22 4.105v.087L16.73 10.21l.017-2.117-.821-.03c-1.059-.028-1.611.002-2.268.11-1.064.175-2.038.577-3.147 1.352L8.345 11.03c-.284.195-.495.336-.68.455l-.515.322-.397.234.385.23.53.338c.476.314 1.17.796 2.701 1.866 1.11.775 2.083 1.177 3.147 1.352l.3.045c.694.091 1.375.094 2.825.033l.022-2.159 7.22 4.105v.087L16.589 22l.014-1.862-.635.022c-1.386.042-2.137.002-3.138-.162-1.694-.28-3.26-.926-4.881-2.059l-2.158-1.5a21.997 21.997 0 00-.755-.498l-.467-.28a55.927 55.927 0 00-.76-.43C2.908 14.73.563 14.116 0 14.116V9.888l.14.004c.564-.007 2.91-.622 3.809-1.124l1.016-.58.438-.274c.428-.28 1.072-.726 2.686-1.853 1.621-1.133 3.186-1.78 4.881-2.059 1.152-.19 1.974-.213 3.814-.138l.02-1.907z"></path></svg>
        <svg v-else-if="matches(['alibaba'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"><path d="M9 2h3.5L14 4.5h5.13L20.5 7m1.5 7.5l-1.645 2.663h-2.477L15 22h-3.217M5 20l-1.5-2.5l1-3l-2.5-5L4 7"/><path d="m19.19 9.662l1.31-2.661H10l1-2l-2-3l-2.251 5H4l5 10H6l-1 3h5.5l1.252 2l5.65-9.935L18.94 14.5H22z"/><path d="M12 15.5L9 10h6z"/></g></svg>
        <svg v-else-if="matches(['anthropic'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M16.765 5h-3.308l5.923 15h3.23zM7.226 5L1.38 20h3.308l1.307-3.154h6.154l1.23 3.077h3.309L10.688 5zm-.308 9.077l2-5.308l2.077 5.308z"/></svg>
        <svg v-else-if="matches(['chutes'])" viewBox="0 0 62 41" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M38.01 39.6943C37.1263 41.1364 35.2525 41.4057 34.0442 40.2642L28.6738 35.1904C27.4656 34.049 27.4843 32.0273 28.7133 30.9115L34.1258 25.9979C40.1431 20.5352 48.069 18.406 55.6129 20.2255L59.6853 21.2078C59.8306 21.2428 59.9654 21.3165 60.0771 21.422C60.6663 21.9787 60.3364 23.0194 59.552 23.078L59.465 23.0845C52.0153 23.6409 45.1812 27.9913 40.9759 34.8542L38.01 39.6943Z" fill="currentColor"></path><path d="M15.296 36.5912C14.1726 37.8368 12.2763 37.7221 11.2913 36.349L0.547139 21.3709C-0.432786 20.0048 -0.0547272 18.0273 1.34794 17.1822L22.7709 4.27482C29.6029 0.158495 37.7319 -0.277291 44.8086 3.0934L60.3492 10.4956C60.5897 10.6101 60.7997 10.7872 60.9599 11.0106C61.8149 12.2025 60.8991 13.9056 59.5058 13.7148L50.2478 12.4467C42.8554 11.4342 35.4143 14.2848 30.1165 20.1587L15.296 36.5912Z" fill="url(#paint0_linear_10244_130)"></path><defs><linearGradient id="paint0_linear_10244_130" x1="33.8526" y1="0.173618" x2="25.5505" y2="41.4493" gradientUnits="userSpaceOnUse"><stop stop-color="currentColor"></stop><stop offset="1" stop-color="currentColor"></stop></linearGradient></defs></svg>
        <svg v-else-if="matches(['github-models'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M12.001 2c-5.525 0-10 4.475-10 10a9.99 9.99 0 0 0 6.837 9.488c.5.087.688-.213.688-.476c0-.237-.013-1.024-.013-1.862c-2.512.463-3.162-.612-3.362-1.175c-.113-.288-.6-1.175-1.025-1.413c-.35-.187-.85-.65-.013-.662c.788-.013 1.35.725 1.538 1.025c.9 1.512 2.337 1.087 2.912.825c.088-.65.35-1.087.638-1.337c-2.225-.25-4.55-1.113-4.55-4.938c0-1.088.387-1.987 1.025-2.687c-.1-.25-.45-1.275.1-2.65c0 0 .837-.263 2.75 1.024a9.3 9.3 0 0 1 2.5-.337c.85 0 1.7.112 2.5.337c1.913-1.3 2.75-1.024 2.75-1.024c.55 1.375.2 2.4.1 2.65c.637.7 1.025 1.587 1.025 2.687c0 3.838-2.337 4.688-4.562 4.938c.362.312.675.912.675 1.85c0 1.337-.013 2.412-.013 2.75c0 .262.188.574.688.474A10.02 10.02 0 0 0 22 12c0-5.525-4.475-10-10-10"/></svg>
        <svg v-else-if="matches(['github-copilot'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M23.922 16.997C23.061 18.492 18.063 22.02 12 22.02S.939 18.492.078 16.997A.6.6 0 0 1 0 16.741v-2.869a1 1 0 0 1 .053-.22c.372-.935 1.347-2.292 2.605-2.656c.167-.429.414-1.055.644-1.517a10 10 0 0 1-.052-1.086c0-1.331.282-2.499 1.132-3.368c.397-.406.89-.717 1.474-.952C7.255 2.937 9.248 1.98 11.978 1.98s4.767.957 6.166 2.093c.584.235 1.077.546 1.474.952c.85.869 1.132 2.037 1.132 3.368c0 .368-.014.733-.052 1.086c.23.462.477 1.088.644 1.517c1.258.364 2.233 1.721 2.605 2.656a.8.8 0 0 1 .053.22v2.869a.6.6 0 0 1-.078.256m-11.75-5.992h-.344a4 4 0 0 1-.355.508c-.77.947-1.918 1.492-3.508 1.492c-1.725 0-2.989-.359-3.782-1.259a2 2 0 0 1-.085-.104L4 11.746v6.585c1.435.779 4.514 2.179 8 2.179s6.565-1.4 8-2.179v-6.585l-.098-.104s-.033.045-.085.104c-.793.9-2.057 1.259-3.782 1.259c-1.59 0-2.738-.545-3.508-1.492a4 4 0 0 1-.355-.508m2.328 3.25c.549 0 1 .451 1 1v2c0 .549-.451 1-1 1s-1-.451-1-1v-2c0-.549.451-1 1-1m-5 0c.549 0 1 .451 1 1v2c0 .549-.451 1-1 1s-1-.451-1-1v-2c0-.549.451-1 1-1m3.313-6.185c.136 1.057.403 1.913.878 2.497c.442.544 1.134.938 2.344.938c1.573 0 2.292-.337 2.657-.751c.384-.435.558-1.15.558-2.361c0-1.14-.243-1.847-.705-2.319c-.477-.488-1.319-.862-2.824-1.025c-1.487-.161-2.192.138-2.533.529c-.269.307-.437.808-.438 1.578v.021q0 .397.063.893m-1.626 0q.063-.496.063-.894v-.02c-.001-.77-.169-1.271-.438-1.578c-.341-.391-1.046-.69-2.533-.529c-1.505.163-2.347.537-2.824 1.025c-.462.472-.705 1.179-.705 2.319c0 1.211.175 1.926.558 2.361c.365.414 1.084.751 2.657.751c1.21 0 1.902-.394 2.344-.938c.475-.584.742-1.44.878-2.497"/></svg>
        <svg v-else-if="matches(['google'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linejoin="round" stroke-width="1.5" d="M3 12a9 9 0 0 0 9-9a9 9 0 0 0 9 9a9 9 0 0 0-9 9a9 9 0 0 0-9-9Z"/></svg>
        <svg v-else-if="matches(['groq'])" xmlns="http://www.w3.org/2000/svg" fill="currentColor" fill-rule="evenodd" viewBox="0 0 24 24"><path d="M12.036 2c-3.853-.035-7 3-7.036 6.781-.035 3.782 3.055 6.872 6.908 6.907h2.42v-2.566h-2.292c-2.407.028-4.38-1.866-4.408-4.23-.029-2.362 1.901-4.298 4.308-4.326h.1c2.407 0 4.358 1.915 4.365 4.278v6.305c0 2.342-1.944 4.25-4.323 4.279a4.375 4.375 0 01-3.033-1.252l-1.851 1.818A7 7 0 0012.029 22h.092c3.803-.056 6.858-3.083 6.879-6.816v-6.5C18.907 4.963 15.817 2 12.036 2z"></path></svg>
        <svg v-else-if="matches(['llama'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" d="M12.125 42.5c-.68-2.589-.258-5.178.971-7.767c-2.259-2.467-2.127-6.929 0-9.376c-3.78-4.258-2.17-11.787 4.754-11.766c1.511-1.973 3.704-3.622 6.15-3.725" stroke-width="1"/><path fill="none" stroke="currentColor" d="M19.303 12.006c0-4.65-1.428-6.506-3.184-6.506s-2.315 5.607-1.699 8.823m14.277-2.317c0-4.65 1.428-6.506 3.184-6.506s2.315 5.607 1.699 8.823" stroke-width="1"/><ellipse cx="24" cy="26.987" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" rx="4.892" ry="3.805" stroke-width="1"/><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" d="M24 26.987v1.087m0-1.087l.917-.917m-.917.917l-.917-.917" stroke-width="1"/><circle cx="17.205" cy="21.008" r=".75" fill="currentColor"/><circle cx="30.795" cy="21.008" r=".75" fill="currentColor"/><path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" d="M35.875 42.5c.68-2.589.258-5.178-.971-7.767c2.259-2.467 2.127-6.929 0-9.376c3.78-4.258 2.17-11.787-4.754-11.766c-1.511-1.973-3.704-3.622-6.15-3.725" stroke-width="1"/></svg>
        <svg v-else-if="matches(['minimax'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 35 35" fill="none"><g transform="translate(0, 3.5)"><path d="M15.5119 2.71901C15.5119 2.07384 14.9885 1.55031 14.3461 1.55031C13.7038 1.55031 13.1803 2.07504 13.1803 2.71901V22.0895C13.1803 23.5886 11.9634 24.8085 10.4682 24.8085C8.97288 24.8085 7.75595 23.5886 7.75595 22.0895V9.66201C7.75595 9.01685 7.23254 8.49331 6.59017 8.49331C5.94781 8.49331 5.42439 9.01804 5.42439 9.66201V14.9295C5.42439 16.4285 4.20746 17.6485 2.71218 17.6485C1.2169 17.6485 0 16.4285 0 14.9295V13.0202C0 12.5921 0.346163 12.2451 0.773217 12.2451C1.20027 12.2451 1.54643 12.5921 1.54643 13.0202V14.9295C1.54643 15.5747 2.06982 16.0982 2.71218 16.0982C3.35455 16.0982 3.87796 15.5735 3.87796 14.9295V9.66201C3.87796 8.16298 5.09489 6.943 6.59017 6.943C8.08545 6.943 9.30238 8.16298 9.30238 9.66201V22.0895C9.30238 22.7347 9.8258 23.2582 10.4682 23.2582C11.1105 23.2582 11.6339 22.7335 11.6339 22.0895V14.4906V2.71901C11.6339 1.21998 12.8508 0 14.3461 0C15.8414 0 17.0583 1.21998 17.0583 2.71901V18.7588C17.0583 19.1869 16.7122 19.5339 16.2851 19.5339C15.8581 19.5339 15.5119 19.1869 15.5119 18.7588V2.71901ZM29.8592 6.943C28.364 6.943 27.147 8.16298 27.147 9.66201V20.0431C27.147 20.6883 26.6236 21.2118 25.9813 21.2118C25.3389 21.2118 24.8155 20.6871 24.8155 20.0431V2.71901C24.8155 1.21998 23.5985 0 22.1033 0C20.608 0 19.3911 1.21998 19.3911 2.71901V24.7096C19.3911 25.3547 18.8677 25.8783 18.2253 25.8783C17.5829 25.8783 17.0595 25.3535 17.0595 24.7096V21.987C17.0595 21.5589 16.7134 21.2118 16.2863 21.2118C15.8593 21.2118 15.5131 21.5589 15.5131 21.987V24.7096C15.5131 26.2086 16.73 27.4286 18.2253 27.4286C19.7206 27.4286 20.9375 26.2086 20.9375 24.7096V2.71901C20.9375 2.07384 21.4609 1.55031 22.1033 1.55031C22.7456 1.55031 23.269 2.07504 23.269 2.71901V20.0431C23.269 21.5422 24.486 22.7621 25.9813 22.7621C27.4765 22.7621 28.6935 21.5422 28.6935 20.0431V9.66201C28.6935 9.01685 29.2169 8.49331 29.8592 8.49331C30.5016 8.49331 31.025 9.01804 31.025 9.66201V18.7588C31.025 19.1869 31.3712 19.5339 31.7982 19.5339C32.2253 19.5339 32.5714 19.1869 32.5714 18.7588V9.66201C32.5714 8.16298 31.3545 6.943 29.8592 6.943Z" fill="#9CA3AF"/></g></svg>
        <svg v-else-if="matches(['mistral','codestral'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 233"><path d="M186.182 0h46.545v46.545h-46.545z"/><path fill="#f7d046" d="M209.455 0H256v46.545h-46.545z"/><path d="M0 0h46.545v46.545H0zm0 46.545h46.545V93.09H0zm0 46.546h46.545v46.545H0zm0 46.545h46.545v46.545H0zm0 46.546h46.545v46.545H0z"/><path fill="#f7d046" d="M23.273 0h46.545v46.545H23.273z"/><path fill="#f2a73b" d="M209.455 46.545H256V93.09h-46.545zm-186.182 0h46.545V93.09H23.273z"/><path d="M139.636 46.545h46.545V93.09h-46.545z"/><path fill="#f2a73b" d="M162.909 46.545h46.545V93.09h-46.545zm-93.091 0h46.545V93.09H69.818z"/><path fill="#ee792f" d="M116.364 93.091h46.545v46.545h-46.545zm46.545 0h46.545v46.545h-46.545zm-93.091 0h46.545v46.545H69.818z"/><path d="M93.091 139.636h46.545v46.545H93.091z"/><path fill="#eb5829" d="M116.364 139.636h46.545v46.545h-46.545z"/><path fill="#ee792f" d="M209.455 93.091H256v46.545h-46.545zm-186.182 0h46.545v46.545H23.273z"/><path d="M186.182 139.636h46.545v46.545h-46.545z"/><path fill="#eb5829" d="M209.455 139.636H256v46.545h-46.545z"/><path d="M186.182 186.182h46.545v46.545h-46.545z"/><path fill="#eb5829" d="M23.273 139.636h46.545v46.545H23.273z"/><path fill="#ea3326" d="M209.455 186.182H256v46.545h-46.545zm-186.182 0h46.545v46.545H23.273z"/></svg>
        <svg v-else-if="matches(['openai'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" d="M20.562 10.188c.25-.688.313-1.376.25-2.063c-.062-.687-.312-1.375-.625-2c-.562-.937-1.375-1.687-2.312-2.125c-1-.437-2.063-.562-3.125-.312c-.5-.5-1.063-.938-1.688-1.25S11.687 2 11 2a5.17 5.17 0 0 0-3 .938c-.875.624-1.5 1.5-1.813 2.5c-.75.187-1.375.5-2 .875c-.562.437-1 1-1.375 1.562c-.562.938-.75 2-.625 3.063a5.44 5.44 0 0 0 1.25 2.874a4.7 4.7 0 0 0-.25 2.063c.063.688.313 1.375.625 2c.563.938 1.375 1.688 2.313 2.125c1 .438 2.062.563 3.125.313c.5.5 1.062.937 1.687 1.25S12.312 22 13 22a5.17 5.17 0 0 0 3-.937c.875-.625 1.5-1.5 1.812-2.5a4.54 4.54 0 0 0 1.938-.875c.562-.438 1.062-.938 1.375-1.563c.562-.937.75-2 .625-3.062c-.125-1.063-.5-2.063-1.188-2.876m-7.5 10.5c-1 0-1.75-.313-2.437-.875c0 0 .062-.063.125-.063l4-2.312a.5.5 0 0 0 .25-.25a.57.57 0 0 0 .062-.313V11.25l1.688 1v4.625a3.685 3.685 0 0 1-3.688 3.813M5 17.25c-.438-.75-.625-1.625-.438-2.5c0 0 .063.063.125.063l4 2.312a.56.56 0 0 0 .313.063c.125 0 .25 0 .312-.063l4.875-2.812v1.937l-4.062 2.375A3.7 3.7 0 0 1 7.312 19c-1-.25-1.812-.875-2.312-1.75M3.937 8.563a3.8 3.8 0 0 1 1.938-1.626v4.751c0 .124 0 .25.062.312a.5.5 0 0 0 .25.25l4.875 2.813l-1.687 1l-4-2.313a3.7 3.7 0 0 1-1.75-2.25c-.25-.937-.188-2.062.312-2.937M17.75 11.75l-4.875-2.812l1.687-1l4 2.312c.625.375 1.125.875 1.438 1.5s.5 1.313.437 2.063a3.7 3.7 0 0 1-.75 1.937c-.437.563-1 1-1.687 1.25v-4.75c0-.125 0-.25-.063-.312c0 0-.062-.126-.187-.188m1.687-2.5s-.062-.062-.125-.062l-4-2.313c-.125-.062-.187-.062-.312-.062s-.25 0-.313.062L9.812 9.688V7.75l4.063-2.375c.625-.375 1.312-.5 2.062-.5c.688 0 1.375.25 2 .688c.563.437 1.063 1 1.313 1.625s.312 1.375.187 2.062m-10.5 3.5l-1.687-1V7.063c0-.688.187-1.438.562-2C8.187 4.438 8.75 4 9.375 3.688a3.37 3.37 0 0 1 2.062-.313c.688.063 1.375.375 1.938.813c0 0-.063.062-.125.062l-4 2.313a.5.5 0 0 0-.25.25c-.063.125-.063.187-.063.312zm.875-2L12 9.5l2.187 1.25v2.5L12 14.5l-2.188-1.25z"/></svg>
        <svg v-else-if="matches(['xai'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd"><path d="M6.469 8.776L16.512 23h-4.464L2.005 8.776H6.47zm-.004 7.9l2.233 3.164L6.467 23H2l4.465-6.324zM22 2.582V23h-3.659V7.764L22 2.582zM22 1l-9.952 14.095-2.233-3.163L17.533 1H22z"></path></svg>
        <svg v-else-if="matches(['zai'])" xmlns="http://www.w3.org/2000/svg" fill="currentColor" fill-rule="evenodd" viewBox="0 0 24 24"><title>Z.ai</title><path d="M12.105 2L9.927 4.953H.653L2.83 2h9.276zM23.254 19.048L21.078 22h-9.242l2.174-2.952h9.244zM24 2L9.264 22H0L14.736 2H24z"></path></svg>
        <svg v-else-if="matches(['deepseek'])" fill="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 01-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 00-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 01-.465.137 9.597 9.597 0 00-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 001.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 011.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614zm1-6.44a.306.306 0 01.415-.287.302.302 0 01.2.288.306.306 0 01-.31.307.303.303 0 01-.304-.308zm3.11 1.596c-.2.081-.399.151-.59.16a1.245 1.245 0 01-.798-.254c-.274-.23-.47-.358-.552-.758a1.73 1.73 0 01.016-.588c.07-.327-.008-.537-.239-.727-.187-.156-.426-.199-.688-.199a.559.559 0 01-.254-.078c-.11-.054-.2-.19-.114-.358.028-.054.16-.186.192-.21.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.391.451.462.576.685.914.176.265.336.537.445.848.067.195-.019.354-.25.452z"></path></svg>
        <svg v-else-if="matches(['nvidia'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="currentColor" transform="translate(0,4)" d="M9.01,4.79L9.01,3.35C9.14,3.34 9.28,3.33 9.43,3.33C13.38,3.2 15.97,6.72 15.97,6.72C15.97,6.72 13.17,10.6 10.17,10.6C9.77,10.6 9.38,10.54 9.01,10.41L9.01,6.03C10.54,6.23 10.85,6.9 11.77,8.44L13.83,6.71C13.83,6.71 12.32,4.75 9.8,4.75C9.52,4.75 9.26,4.76 9.01,4.79ZM9.01,0.025L9.01,2.18C9.14,2.17 9.28,2.16 9.43,2.15C14.91,1.97 18.5,6.65 18.5,6.65C18.5,6.65 14.38,11.64 10.11,11.64C9.71,11.64 9.35,11.61 9.01,11.54L9.01,12.88C9.3,12.91 9.6,12.93 9.93,12.93C13.9,12.93 16.78,10.91 19.57,8.5C20.03,8.87 21.91,9.77 22.3,10.17C19.66,12.38 13.48,14.17 9.99,14.17C9.66,14.17 9.33,14.15 9.01,14.12L9.01,16L24.12,16L24.12,0.025L9.01,0.025ZM9.01,10.41L9.01,11.54C5.32,10.89 4.3,7.06 4.3,7.06C4.3,7.06 6.07,5.1 9.01,4.79L9.01,6.03L9,6.03C7.46,5.85 6.25,7.29 6.25,7.29C6.25,7.29 6.93,9.71 9.01,10.41ZM2.47,6.9C2.47,6.9 4.65,3.68 9.01,3.35L9.01,2.18C4.18,2.57 0,6.65 0,6.65C0,6.65 2.37,13.5 9.01,14.11L9.01,12.88C4.13,12.26 2.47,6.9 2.47,6.9Z"/></svg>
        <svg v-else-if="matches(['lmstudio'])" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path shape-rendering="geometricPrecision" d="M9.8132 15.9038L9 18.75L8.1868 15.9038C7.75968 14.4089 6.59112 13.2403 5.09619 12.8132L2.25 12L5.09619 11.1868C6.59113 10.7597 7.75968 9.59112 8.1868 8.09619L9 5.25L9.8132 8.09619C10.2403 9.59113 11.4089 10.7597 12.9038 11.1868L15.75 12L12.9038 12.8132C11.4089 13.2403 10.2403 14.4089 9.8132 15.9038Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /><path d="M18.2589 8.71454L18 9.75L17.7411 8.71454C17.4388 7.50533 16.4947 6.56117 15.2855 6.25887L14.25 6L15.2855 5.74113C16.4947 5.43883 17.4388 4.49467 17.7411 3.28546L18 2.25L18.2589 3.28546C18.5612 4.49467 19.5053 5.43883 20.7145 5.74113L21.75 6L20.7145 6.25887C19.5053 6.56117 18.5612 7.50533 18.2589 8.71454Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /><path d="M16.8942 20.5673L16.5 21.75L16.1058 20.5673C15.8818 19.8954 15.3546 19.3682 14.6827 19.1442L13.5 18.75L14.6827 18.3558C15.3546 18.1318 15.8818 17.6046 16.1058 16.9327L16.5 15.75L16.8942 16.9327C17.1182 17.6046 17.6454 18.1318 18.3173 18.3558L19.5 18.75L18.3173 19.1442C17.6454 19.3682 17.1182 19.8954 16.8942 20.5673Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
        <svg v-else-if="matches(['huggingface'])" fill="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M16.781 3.277c2.997 1.704 4.844 4.851 4.844 8.258 0 .995-.155 1.955-.443 2.857a1.332 1.332 0 011.125.4 1.41 1.41 0 01.2 1.723c.204.165.352.385.428.632l.017.062c.06.222.12.69-.2 1.166.244.37.279.836.093 1.236-.255.57-.893 1.018-2.128 1.5l-.202.078-.131.048c-.478.173-.89.295-1.061.345l-.086.024c-.89.243-1.808.375-2.732.394-1.32 0-2.3-.36-2.923-1.067a9.852 9.852 0 01-3.18.018C9.778 21.647 8.802 22 7.494 22a11.249 11.249 0 01-2.541-.343l-.221-.06-.273-.08a16.574 16.574 0 01-1.175-.405c-1.237-.483-1.875-.93-2.13-1.501-.186-.4-.151-.867.093-1.236a1.42 1.42 0 01-.2-1.166c.069-.273.226-.516.447-.694a1.41 1.41 0 01.2-1.722c.233-.248.557-.391.917-.407l.078-.001a9.385 9.385 0 01-.44-2.85c0-3.407 1.847-6.554 4.844-8.258a9.822 9.822 0 019.687 0zM4.188 14.758c.125.687 2.357 2.35 2.14 2.707-.19.315-.796-.239-.948-.386l-.041-.04-.168-.147c-.561-.479-2.304-1.9-2.74-1.432-.43.46.119.859 1.055 1.42l.784.467.136.083c1.045.643 1.12.84.95 1.113-.188.295-3.07-2.1-3.34-1.083-.27 1.011 2.942 1.304 2.744 2.006-.2.7-2.265-1.324-2.685-.537-.425.79 2.913 1.718 2.94 1.725l.16.04.175.042c1.227.284 3.565.65 4.435-.604.673-.973.64-1.709-.248-2.61l-.057-.057c-.945-.928-1.495-2.288-1.495-2.288l-.017-.058-.025-.072c-.082-.22-.284-.639-.63-.584-.46.073-.798 1.21.12 1.933l.05.038c.977.721-.195 1.21-.573.534l-.058-.104-.143-.25c-.463-.799-1.282-2.111-1.739-2.397-.532-.332-.907-.148-.782.541zm14.842-.541c-.533.335-1.563 2.074-1.94 2.751a.613.613 0 01-.687.302.436.436 0 01-.176-.098.303.303 0 01-.049-.06l-.014-.028-.008-.02-.007-.019-.003-.013-.003-.017a.289.289 0 01-.004-.048c0-.12.071-.266.25-.427.026-.024.054-.047.084-.07l.047-.036c.022-.016.043-.032.063-.049.883-.71.573-1.81.131-1.917l-.031-.006-.056-.004a.368.368 0 00-.062.006l-.028.005-.042.014-.039.017-.028.015-.028.019-.036.027-.023.02c-.173.158-.273.428-.31.542l-.016.054s-.53 1.309-1.439 2.234l-.054.054c-.365.358-.596.69-.702 1.018-.143.437-.066.868.21 1.353.055.097.117.195.187.296.882 1.275 3.282.876 4.494.59l.286-.07.25-.074c.276-.084.736-.233 1.2-.42l.188-.077.065-.028.064-.028.124-.056.081-.038c.529-.252.964-.543.994-.827l.001-.036a.299.299 0 00-.037-.139c-.094-.176-.271-.212-.491-.168l-.045.01c-.044.01-.09.024-.136.04l-.097.035-.054.022c-.559.23-1.238.705-1.607.745h.006a.452.452 0 01-.05.003h-.024l-.024-.003-.023-.005c-.068-.016-.116-.06-.14-.142a.22.22 0 01-.005-.1c.062-.345.958-.595 1.713-.91l.066-.028c.528-.224.97-.483.985-.832v-.04a.47.47 0 00-.016-.098c-.048-.18-.175-.251-.36-.251-.785 0-2.55 1.36-2.92 1.36-.025 0-.048-.007-.058-.024a.6.6 0 01-.046-.088c-.1-.238.068-.462 1.06-1.066l.209-.126c.538-.32 1.01-.588 1.341-.831.29-.212.475-.406.503-.6l.003-.028c.008-.113-.038-.227-.147-.344a.266.266 0 00-.07-.054l-.034-.015-.013-.005a.403.403 0 00-.13-.02c-.162 0-.369.07-.595.18-.637.313-1.431.952-1.826 1.285l-.249.215-.033.033c-.08.078-.288.27-.493.386l-.071.037-.041.019a.535.535 0 01-.122.036h.005a.346.346 0 01-.031.003l.01-.001-.013.001c-.079.005-.145-.021-.19-.095a.113.113 0 01-.014-.065c.027-.465 2.034-1.991 2.152-2.642l.009-.048c.1-.65-.271-.817-.791-.493zM11.938 2.984c-4.798 0-8.688 3.829-8.688 8.55 0 .692.083 1.364.24 2.008l.008-.009c.252-.298.612-.46 1.017-.46.355.008.699.117.993.312.22.14.465.384.715.694.261-.372.69-.598 1.15-.605.852 0 1.367.728 1.562 1.383l.047.105.06.127c.192.396.595 1.139 1.143 1.68 1.06 1.04 1.324 2.115.8 3.266a8.865 8.865 0 002.024-.014c-.505-1.12-.26-2.17.74-3.186l.066-.066c.695-.684 1.157-1.69 1.252-1.912.195-.655.708-1.383 1.56-1.383.46.007.889.233 1.15.605.25-.31.495-.553.718-.694a1.87 1.87 0 01.99-.312c.357 0 .682.126.925.36.14-.61.215-1.245.215-1.898 0-4.722-3.89-8.55-8.687-8.55zm1.857 8.926l.439-.212c.553-.264.89-.383.89.152 0 1.093-.771 3.208-3.155 3.262h-.184c-2.325-.052-3.116-2.06-3.156-3.175l-.001-.087c0-1.107 1.452.586 3.25.586.716 0 1.379-.272 1.917-.526zm4.017-3.143c.45 0 .813.358.813.8 0 .441-.364.8-.813.8a.806.806 0 01-.812-.8c0-.442.364-.8.812-.8zm-11.624 0c.448 0 .812.358.812.8 0 .441-.364.8-.812.8a.806.806 0 01-.813-.8c0-.442.364-.8.813-.8zm7.79-.841c.32-.384.846-.54 1.33-.394.483.146.83.564.878 1.06.048.495-.212.97-.659 1.203-.322.168-.447-.477-.767-.585l.002-.003c-.287-.098-.772.362-.925.079a1.215 1.215 0 01.14-1.36zm-4.323 0c.322.384.377.92.14 1.36-.152.283-.64-.177-.925-.079l.003.003c-.108.036-.194.134-.273.24l-.118.165c-.11.15-.22.262-.377.18a1.226 1.226 0 01-.658-1.204c.048-.495.395-.913.878-1.059a1.262 1.262 0 011.33.394z"></path></svg>
        <svg v-else-if="matches(['servicestack'])" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><path fill="currentColor" d="M96 216c81.7 10.2 273.7 102.3 304 232H8c99.5-8.1 184.5-137 88-232m32-152c32.3 35.6 47.7 83.9 46.4 133.6C257.3 231.3 381.7 321.3 408 448h96C463.3 231.9 230.8 79.5 128 64"/></svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-linejoin="round" d="M12.019 16.225L8.35 14.13m3.669 2.096l3.65-2.129m-3.65 2.13L9.183 17.88l-5.196-3a5 5 0 0 1-.714-.498m5.077-.252L5.5 12.5v-6q0-.444.075-.867m2.775 8.496l-.018-4.225m5.97-6.652a5.001 5.001 0 0 0-8.727 2.38m8.727-2.38a5 5 0 0 0-.789.369l-5.196 3l.015 3.283m5.97-6.652a5.001 5.001 0 0 1 6.425 6.367M5.575 5.633a5.001 5.001 0 0 0-2.302 8.748m8.708-6.606l3.669 2.096m-3.67-2.096L8.33 9.904m3.65-2.129l2.836-1.654l5.196 3q.384.223.714.498m-5.077.252L18.5 11.5v6q0 .444-.075.867M15.65 9.871l.018 4.225m-5.97 6.652a5.001 5.001 0 0 0 8.727-2.38m-8.727 2.38a5 5 0 0 0 .789-.369l5.196-3l-.015-3.283m-5.97 6.652a5.001 5.001 0 0 1-6.425-6.367m15.152 3.986a5.001 5.001 0 0 0 2.302-8.748" stroke-width="1"/></svg>
    `,
    props: {
        provider: String,
    },
    setup(props) {
        function matches(providers) {
            if (!props.provider) return false
            const providerLower = props.provider.toLowerCase().replace(/[\s-.]+/g, '')
            return providers.some(provider => providerLower.includes(provider))
        }
        return {
            matches
        }
    }
}


const ProviderStatus = {
    template: `
        <div v-if="$ai.isAdmin" ref="triggerRef" class="relative" :key="renderKey">
            <button type="button" @click="togglePopover"
                class="flex space-x-2 items-center text-sm font-semibold select-none rounded-md py-2 px-3 border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300 transition-colors">
                <span class="text-gray-600 dark:text-gray-400" :title="$state.models.length + ' models from ' + ($state.config.status.enabled||[]).length + ' enabled providers'">{{$state.models.length}}</span>
                <div class="cursor-pointer flex items-center" :title="'Enabled:\\n' + ($state.config.status.enabled||[]).map(x => '  ' + x).join('\\n')">
                    <svg class="size-4 text-green-400 dark:text-green-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="currentColor"/></svg>
                    <span class="text-green-700 dark:text-green-400">{{($state.config.status.enabled||[]).length}}</span>
                </div>
                <div class="cursor-pointer flex items-center" :title="'Disabled:\\n' + ($state.config.status.disabled||[]).map(x => '  ' + x).join('\\n')">
                    <svg class="size-4 text-red-400 dark:text-red-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="currentColor"/></svg>
                    <span class="text-red-700 dark:text-red-400">{{($state.config.status.disabled||[]).length}}</span>
                </div>
            </button>
            <div v-if="showPopover" ref="popoverRef" class="absolute right-0 mt-2 w-72 overflow-y-auto bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md shadow-lg z-10">
                <div class="divide-y divide-gray-100 dark:divide-gray-700">
                    <div v-for="p in allProviders" :key="p" class="flex items-center justify-between px-3 py-2">
                        <label :for="'chk_' + p" class="cursor-pointer text-sm text-gray-900 dark:text-gray-100 truncate mr-2" :title="p">{{ p }}</label>
                        <div @click="onToggle(p, !isEnabled(p))" class="cursor-pointer group relative inline-flex h-5 w-10 shrink-0 items-center justify-center rounded-full outline-offset-2 outline-green-600 has-focus-visible:outline-2">
                            <span class="absolute mx-auto h-4 w-9 rounded-full bg-gray-200 dark:bg-gray-700 inset-ring inset-ring-gray-900/5 dark:inset-ring-gray-100/5 transition-colors duration-200 ease-in-out group-has-checked:bg-green-600 dark:group-has-checked:bg-green-500" />
                            <span class="absolute left-0 size-5 rounded-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-200 shadow-xs transition-transform duration-200 ease-in-out group-has-checked:translate-x-5" />
                            <input :id="'chk_' + p" type="checkbox" :checked="isEnabled(p)" class="switch cursor-pointer absolute inset-0 appearance-none focus:outline-hidden" aria-label="Use setting" name="setting" />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    emits: ['updated'],
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const showPopover = ref(false)
        const triggerRef = ref(null)
        const popoverRef = ref(null)
        const pending = ref({})
        const renderKey = ref(0)
        const allProviders = computed(() => ctx.state.config.status?.all)
        const isEnabled = (p) => ctx.state.config.status.enabled.includes(p)
        const togglePopover = () => showPopover.value = !showPopover.value

        const onToggle = async (provider, enable) => {
            pending.value = { ...pending.value, [provider]: true }
            try {
                const res = await ctx.post(`/providers/${encodeURIComponent(provider)}`, {
                    body: JSON.stringify(enable ? { enable: true } : { disable: true })
                })
                if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`)
                const json = await res.json()
                ctx.state.config.status.enabled = json.enabled || []
                ctx.state.config.status.disabled = json.disabled || []
                if (json.feedback) {
                    alert(json.feedback)
                }

                try {
                    const [configRes, modelsRes] = await Promise.all([
                        ctx.ai.getConfig(),
                        ctx.ai.getModels(),
                    ])
                    const [config, models] = await Promise.all([
                        configRes.json(),
                        modelsRes.json(),
                    ])
                    Object.assign(ctx.state, { config, models })
                    renderKey.value++
                    emit('updated')
                } catch (e) {
                    alert(`Failed to reload config: ${e.message}`)
                }

            } catch (e) {
                alert(`Failed to ${enable ? 'enable' : 'disable'} ${provider}: ${e.message}`)
            } finally {
                pending.value = { ...pending.value, [provider]: false }
            }
        }

        const onDocClick = (e) => {
            const t = e.target
            if (triggerRef.value?.contains(t)) return
            if (popoverRef.value?.contains(t)) return
            showPopover.value = false
        }
        onMounted(() => document.addEventListener('click', onDocClick))
        onUnmounted(() => document.removeEventListener('click', onDocClick))
        return {
            renderKey,
            showPopover,
            triggerRef,
            popoverRef,
            allProviders,
            isEnabled,
            togglePopover,
            onToggle,
            pending,
        }
    }
}

const ModelSelectorModal = {
    template: `
        <!-- Dialog Overlay -->
        <div class="fixed inset-0 z-50 overflow-hidden" @keydown.escape="closeDialog">
            <!-- Backdrop -->
            <div class="fixed inset-0 bg-black/50 transition-opacity" @click="closeDialog"></div>
            
            <!-- Dialog -->
            <div class="fixed inset-4 md:inset-8 lg:inset-12 flex items-center justify-center">
                <div class="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full h-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden">
                    <!-- Header -->
                    <div class="flex-shrink-0 px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                        <div class="flex items-center justify-between mb-4">
                                <h2 class="mr-4 text-xl font-semibold text-gray-900 dark:text-gray-100">Select Model</h2>
                            <div class="flex items-center gap-4">
                                <ProviderStatus @updated="renderKey++" />
                                <button type="button" @click="closeDialog" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
                                    <svg class="size-6" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                                        <path fill="currentColor" d="M19 6.41L17.59 5L12 10.59L6.41 5L5 6.41L10.59 12L5 17.59L6.41 19L12 13.41L17.59 19L19 17.59L13.41 12z"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Search and Controls -->
                        <div class="flex flex-col md:flex-row gap-3">
                            <!-- Search -->
                            <div class="flex-1 relative">
                                <svg class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                                    <path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
                                </svg>
                                <input type="text" v-model="prefs.query" ref="searchInput"
                                    placeholder="Search models..."
                                    class="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm" />
                            </div>

                            <!-- Modality Filters -->
                            <div class="flex items-center gap-1.5">
                                <!-- Input Modalities (Exclusive) -->
                                <div class="flex items-center space-x-1">
                                    <button v-for="type in inputModalityTypes" :key="type" type="button"
                                        @click="toggleInputModality(type)"
                                        :title="'Input: ' + type"
                                        :class="[
                                            'p-2 rounded-lg transition-colors border',
                                            prefs.inputModality === type
                                                ? 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800'
                                                : 'bg-white dark:bg-gray-800 text-gray-400 border-transparent hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-200'
                                        ]"
                                        v-html="modalityIcons[type]">
                                    </button>
                                </div>

                                <!-- Divider -->
                                <div class="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1"></div>

                                <!-- Output Modalities (Exclusive) -->
                                <div class="flex items-center space-x-1">
                                    <button v-for="type in outputModalityTypes" :key="type" type="button"
                                        @click="toggleOutputModality(type)"
                                        :title="'Output: ' + type"
                                        :class="[
                                            'p-2 rounded-lg transition-colors border',
                                            prefs.outputModality === type
                                                ? 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800'
                                                : 'bg-white dark:bg-gray-800 text-gray-400 border-transparent hover:bg-gray-50 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-200'
                                        ]"
                                        v-html="modalityIcons[type]">
                                    </button>
                                </div>
                            </div>
                            
                            <!-- Sort -->
                            <div class="flex items-center space-x-2">
                                <label class="text-sm text-gray-600 dark:text-gray-400 whitespace-nowrap">Sort by:</label>
                                <select v-model="prefs.sortBy" 
                                    class="px-3 py-2 pr-8 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 min-w-[200px]">
                                    <option v-for="opt in sortOptions" :key="opt.id" :value="opt.id">{{ opt.label }}</option>
                                </select>
                                <button type="button" @click="toggleSortDirection" 
                                    class="p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                    :title="prefs.sortAsc ? 'Ascending' : 'Descending'">
                                    <svg v-if="prefs.sortAsc" class="size-5 text-gray-600 dark:text-gray-400" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
                                        <path fill="currentColor" d="M19 7h3l-4-4l-4 4h3v14h2M2 17h10v2H2M6 5v2H2V5m0 6h7v2H2z"/>
                                    </svg>
                                    <svg v-else class="size-5 text-gray-600 dark:text-gray-400" style="transform: scaleY(-1)" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
                                        <path fill="currentColor" d="M19 7h3l-4-4l-4 4h3v14h2M2 17h10v2H2M6 5v2H2V5m0 6h7v2H2z"/>
                                    </svg>
                                </button>
                            </div>
                        </div>
                        
                        <!-- Provider Filter -->
                        <div class="mt-3 flex flex-wrap gap-2">
                             <button type="button" 
                                @click="setActiveTab('favorites')"
                                :class="[
                                    'flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'favorites'
                                        ? 'bg-fuchsia-600 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-3.5">
                                    <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                </svg>
                                <span>Favorites</span>
                                <span v-if="favorites.length > 0" class="ml-1 opacity-75">({{ favorites.length }})</span>
                            </button>
                            <div class="w-px h-6 bg-gray-300 dark:bg-gray-600 mx-1 self-center"></div>
                            <button type="button" 
                                @click="setActiveTab('browse', null)"
                                :class="[
                                    'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'browse' && !prefs.provider
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                All
                            </button>
                            <button v-for="provider in uniqueProviders" :key="provider"
                                type="button"
                                @click="setActiveTab('browse', provider)"
                                :class="[
                                    'flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                                    activeTab === 'browse' && prefs.provider == provider
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                                ]">
                                <ProviderIcon :provider="provider" class="size-4" />
                                <span>{{ provider }}</span>
                                <span class="opacity-60">({{ providerCounts[provider] }})</span>
                            </button>
                        </div>
                    </div>
                    
                    <!-- Model List -->
                    <div class="flex-1 overflow-y-auto p-4">
                        <div v-if="filteredModels.length === 0 && !hasUnavailableFavorites" class="text-center py-12 text-gray-500 dark:text-gray-400">
                            No models found matching your criteria.
                        </div>
                        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                            <button v-for="model in filteredModels" :key="model.id + '-' + model.provider"
                                type="button"
                                @click="selectModel(model)"
                                :class="[
                                    'relative text-left p-4 rounded-lg border transition-all group',
                                    $state.selectedModel === model.name
                                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 ring-2 ring-blue-500/50'
                                        : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                                ]">
                                <!-- Favorite Star -->
                                <div @click.stop="toggleFavorite(model)" 
                                    class="absolute top-2 right-2 p-1.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors z-10 cursor-pointer"
                                    :title="isFavorite(model) ? 'Remove from favorites' : 'Add to favorites'">
                                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" 
                                        :class="['size-4 transition-colors', isFavorite(model) ? 'text-yellow-400' : 'text-gray-300 dark:text-gray-600 group-hover:text-gray-400 dark:group-hover:text-gray-500']">
                                        <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                    </svg>
                                </div>

                                <div class="flex items-start justify-between mb-2 pr-6">
                                    <div class="flex items-center space-x-2 min-w-0">
                                        <ProviderIcon :provider="model.provider" class="size-5 flex-shrink-0" />
                                        <span class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ model.name }}</span>
                                    </div>
                                    <div v-if="isFreeModel(model)" class="flex-shrink-0 ml-2">
                                        <span class="px-1.5 py-0.5 text-xs font-semibold rounded bg-green-100 dark:bg-green-900/50 text-green-700 dark:text-green-300">FREE</span>
                                    </div>
                                </div>
                                
                                <div class="text-xs text-gray-500 dark:text-gray-400 mb-2 truncate" :title="model.id">{{ model.id }}</div>
                                
                                <div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600 dark:text-gray-400">
                                    <span v-if="model.cost && !isFreeModel(model)" :title="'Input: ' + model.cost.input + ' / Output: ' + model.cost.output + ' per 1M tokens'">
                                        💰 {{ formatCost(model.cost.input) }} / {{ formatCost(model.cost.output) }}
                                    </span>
                                    <span v-if="model.limit?.context" :title="'Context window: ' + formatNumber(model.limit.context) + ' tokens'">
                                        📏 {{ formatShortNumber(model.limit.context) }}
                                    </span>
                                    <span v-if="model.knowledge" :title="'Knowledge cutoff: ' + model.knowledge">
                                        📅 {{ model.knowledge }}
                                    </span>
                                </div>
                                
                                <div class="flex flex-wrap gap-1 mt-2">
                                    <span v-if="model.reasoning" class="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300">reasoning</span>
                                    <span v-if="model.tool_call" class="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300">tools</span>
                                    
                                    <!-- Modality Icons (Input) -->
                                    <span v-for="mod in getInputModalities(model)" :key="mod" 
                                        class="inline-flex items-center justify-center p-0.5 text-gray-400 dark:text-gray-500"
                                        :title="'Input: ' + mod"
                                        v-html="modalityIcons[mod]">
                                    </span>

                                    <!-- Modality Tags (Output) -->
                                    <span v-for="mod in getOutputModalities(model)" :key="mod" 
                                        class="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300"
                                        :title="'Output: ' + mod">
                                        {{ mod }}
                                    </span>
                                </div>
                            </button>
                        </div>


                        <!-- Unavailable Favorites -->
                        <div v-if="activeTab === 'favorites' && unavailableFavorites.length > 0" class="mt-6 pt-6 border-t border-gray-200 dark:border-gray-700">
                             <div class="text-sm font-medium text-gray-500 dark:text-gray-400 mb-3 ml-1">Unavailable</div>
                             <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 opacity-60 grayscale">
                                <div v-for="model in unavailableFavorites" :key="model.id"
                                    class="relative text-left p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 cursor-not-allowed">
                                    
                                    <!-- Remove from favorites button -->
                                    <div @click.stop="toggleFavorite(model)" 
                                        class="absolute top-2 right-2 p-1.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors z-10 cursor-pointer"
                                        title="Remove from favorites">
                                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-4 text-yellow-400">
                                            <path fill-rule="evenodd" d="M10.868 2.884c-.321-.772-1.415-.772-1.736 0l-1.83 4.401-4.753.381c-.833.067-1.171 1.107-.536 1.651l3.62 3.102-1.106 4.637c-.194.813.691 1.456 1.405 1.02L10 15.591l4.069 2.485c.713.436 1.598-.207 1.404-1.02l-1.106-4.637 3.62-3.102c.635-.544.297-1.584-.536-1.65l-4.752-.382-1.831-4.401z" clip-rule="evenodd" />
                                        </svg>
                                    </div>

                                    <div class="flex items-start justify-between mb-2 pr-6">
                                        <div class="flex items-center space-x-2 min-w-0">
                                            <ProviderIcon v-if="model.provider" :provider="model.provider" class="size-5 flex-shrink-0" />
                                            <span class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ model.name || model.id }}</span>
                                        </div>
                                    </div>
                                    <div class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ model.id }}</div>
                                    <div class="mt-2 text-xs italic text-gray-400">Provider unavailable</div>
                                </div>
                             </div>
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div class="flex-shrink-0 px-6 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                        <div class="flex items-center justify-between">
                            <span class="text-sm text-gray-600 dark:text-gray-400">
                                {{ filteredModels.length }} of {{ models.length }} models
                            </span>
                            <button type="button" @click="closeDialog"
                                class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-gray-100 transition-colors">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>    
    `,
    emits: ['done'],
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const searchInput = ref(null)

        // Load preferences
        const renderKey = ref(0)
        const ext = ctx.scope('model-selector')
        const prefs = ref(ext.getPrefs())

        const inputModalityTypes = ['image', 'audio', 'video', 'pdf']
        const outputModalityTypes = ['image', 'audio']

        const models = computed(() => ctx.state.models || [])

        // Favorites State
        const favorites = computed(() => prefs.value.favorites || [])

        const activeTab = computed(() => prefs.value.activeTab || (favorites.value.length > 0 ? 'favorites' : 'browse'))

        const sortOptions = SORT_OPTIONS

        // Get unique providers
        const uniqueProviders = computed(() => {
            if (!models.value) return []
            const providers = [...new Set(models.value.map(m => m.provider))].filter(Boolean)
            return providers.sort()
        })

        // Provider counts
        const providerCounts = computed(() => {
            if (!models.value) return {}
            const counts = {}
            models.value.forEach(m => {
                if (m.provider) {
                    counts[m.provider] = (counts[m.provider] || 0) + 1
                }
            })
            return counts
        })

        // Filter and sort helpers
        function getModelKey(model) {
            return `${model.provider}:${model.id}`
        }

        function isFavorite(model) {
            const key = getModelKey(model)
            return favorites.value.includes(key)
        }

        // Unavailable favorites (provider disabled or model removed)
        const unavailableFavorites = computed(() => {
            if (!models.value) return []
            const availableKeys = new Set(models.value.map(getModelKey))
            const missingKeys = favorites.value.filter(key => !availableKeys.has(key))

            return missingKeys.map(key => {
                const [provider, ...idParts] = key.split(':')
                const id = idParts.join(':')
                return {
                    id,
                    provider,
                    name: id // Fallback
                }
            })
        })

        const hasUnavailableFavorites = computed(() => unavailableFavorites.value.length > 0)

        // Filter and sort models
        const filteredModels = computed(() => {
            if (!models.value) return []

            let result = [...models.value]

            // Filter by Tab
            if (activeTab.value === 'favorites') {
                result = result.filter(isFavorite)
            } else {
                // Browse Tab - Filter by provider
                if (prefs.value.provider) {
                    result = result.filter(m => m.provider == prefs.value.provider)
                }
            }

            // Filter by Modalities (Input)
            if (prefs.value.inputModality) {
                result = result.filter(m => {
                    const mods = m.modalities || {}
                    const inputMods = mods.input || []
                    return inputMods.includes(prefs.value.inputModality)
                })
            }

            // Filter by Modalities (Output)
            if (prefs.value.outputModality) {
                result = result.filter(m => {
                    const mods = m.modalities || {}
                    const outputMods = mods.output || []
                    return outputMods.includes(prefs.value.outputModality)
                })
            }

            // Filter by search query
            if (prefs.value.query?.trim()) {
                const query = prefs.value.query.toLowerCase()
                result = result.filter(m =>
                    m.name?.toLowerCase().includes(query) ||
                    m.id?.toLowerCase().includes(query) ||
                    m.provider?.toLowerCase().includes(query)
                )
            }

            // Sort
            result.sort((a, b) => {
                let cmp = 0
                switch (prefs.value.sortBy) {
                    case 'name':
                        cmp = (a.name || '').localeCompare(b.name || '')
                        break
                    case 'knowledge':
                        cmp = (a.knowledge || '').localeCompare(b.knowledge || '')
                        break
                    case 'release_date':
                        cmp = (a.release_date || '').localeCompare(b.release_date || '')
                        break
                    case 'last_updated':
                        cmp = (a.last_updated || '').localeCompare(b.last_updated || '')
                        break
                    case 'cost_input':
                        cmp = (parseFloat(a.cost?.input) || 0) - (parseFloat(b.cost?.input) || 0)
                        break
                    case 'cost_output':
                        cmp = (parseFloat(a.cost?.output) || 0) - (parseFloat(b.cost?.output) || 0)
                        break
                    case 'context':
                        cmp = (a.limit?.context || 0) - (b.limit?.context || 0)
                        break
                    default:
                        cmp = 0
                }
                return prefs.value.sortAsc ? cmp : -cmp
            })

            return result
        })

        function isFreeModel(model) {
            return model.cost && parseFloat(model.cost.input) === 0 && parseFloat(model.cost.output) === 0
        }

        function selectModel(model) {
            ctx.setState({ selectedModel: model.name })
            closeDialog()
        }

        function closeDialog() {
            emit('done')
        }

        function setActiveTab(tab, provider) {
            prefs.value.activeTab = tab
            ext.setPrefs(prefs.value)
            if (tab === 'browse') {
                toggleProvider(provider)
            }
        }

        function toggleProvider(provider) {
            prefs.value.provider = provider == prefs.value.provider ? '' : provider
            ext.setPrefs(prefs.value)
        }

        function toggleInputModality(modality) {
            setPrefs({
                inputModality: prefs.value.inputModality === modality ? null : modality
            })
        }

        function toggleOutputModality(modality) {
            setPrefs({
                outputModality: prefs.value.outputModality === modality ? null : modality
            })
        }

        function toggleFavorite(model) {
            const key = getModelKey(model)
            const favorites = prefs.value.favorites || (prefs.value.favorites = [])
            const idx = favorites.indexOf(key)
            if (idx === -1) {
                favorites.push(key)
            } else {
                favorites.splice(idx, 1)
            }
            setPrefs({ favorites })
        }

        function toggleSortDirection() {
            setPrefs({
                sortAsc: !prefs.value.sortAsc
            })
        }

        // Save preferences when sort changes
        watch(() => [prefs.value.query], () => {
            console.log('setPrefs', prefs.value.query)
            setPrefs({
                query: prefs.value.query,
            })
        })

        function setPrefs(o) {
            Object.assign(prefs.value, o)
            ext.setPrefs(prefs.value)
        }

        // Deep link logic with Vue Router
        onMounted(() => {
            if (!prefs.value.query) {
                prefs.value.query = ''
            }
            if (!prefs.value.sortBy) {
                prefs.value.sortBy = 'name'
            }
            setTimeout(() => {
                searchInput.value?.focus()
            }, 100)
        })

        return {
            renderKey,
            prefs,
            models,
            searchInput,
            sortOptions,
            uniqueProviders,
            providerCounts,
            filteredModels,
            formatCost,
            formatNumber,
            formatShortNumber,
            isFreeModel,

            closeDialog,
            selectModel,
            toggleProvider,
            toggleSortDirection,
            favorites,
            activeTab,
            setActiveTab,
            toggleFavorite,
            isFavorite,
            unavailableFavorites,
            hasUnavailableFavorites,
            modalityIcons,
            inputModalityTypes,
            outputModalityTypes,
            toggleInputModality,
            toggleOutputModality,
            getInputModalities,
            getOutputModalities,
        }
    }
}

const ModelTooltip = {
    template: `
        <div v-if="model" 
            class="absolute z-50 mt-10 ml-0 p-3 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 text-sm w-72">
            <div class="font-semibold text-gray-900 dark:text-gray-100 mb-2">{{ model.name }}</div>
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-2">{{ model.provider }}</div>
            
            <div v-if="model.cost" class="mb-2">
                <div class="text-xs font-medium text-gray-700 dark:text-gray-300">Cost per 1M tokens:</div>
                <div class="text-xs text-gray-600 dark:text-gray-400 ml-2">
                    Input: {{ formatCost(model.cost.input) }} · Output: {{ formatCost(model.cost.output) }}
                </div>
            </div>
            
            <div v-if="model.limit" class="mb-2">
                <div class="text-xs font-medium text-gray-700 dark:text-gray-300">Limits:</div>
                <div class="text-xs text-gray-600 dark:text-gray-400 ml-2">
                    Context: {{ formatNumber(model.limit.context) }} · Output: {{ formatNumber(model.limit.output) }}
                </div>
            </div>
            
            <div v-if="model.knowledge" class="text-xs text-gray-600 dark:text-gray-400">
                Knowledge: {{ model.knowledge }}
            </div>
            
            <div class="flex flex-wrap gap-1 mt-2">
                <span v-if="model.reasoning" class="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300">reasoning</span>
                <span v-if="model.tool_call" class="px-1.5 py-0.5 text-xs rounded bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300">tools</span>
                
                <!-- Modality Icons (Input) -->
                <span v-for="mod in getInputModalities(model)" :key="mod" 
                    class="inline-flex items-center justify-center p-0.5 text-gray-400 dark:text-gray-500"
                    :title="'Input: ' + mod"
                    v-html="modalityIcons[mod]">
                </span>

                <!-- Modality Tags (Output) -->
                <span v-for="mod in getOutputModalities(model)" :key="mod" 
                    class="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 capitalize"
                    :title="'Output: ' + mod">
                    {{ mod }}
                </span>
            </div>
        </div>
    `,
    props: {
        model: Object,
    },
    setup(props) {
        return {
            formatCost,
            formatNumber,
            getInputModalities,
            getOutputModalities,
            modalityIcons,
        }
    }
}

const ModelSelector = {
    template: `
        <!-- Model Selector Button -->
        <div class="pl-1.5 flex space-x-2">
            <button type="button" @click="openDialog"
                class="select-none flex items-center space-x-2 px-3 py-2 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 transition-colors w-full md:w-auto md:min-w-48 max-w-96"
                @mouseenter="showTooltip = true"
                @mouseleave="showTooltip = false">
                <ProviderIcon v-if="selectedModel?.provider" :provider="selectedModel.provider" class="size-5 flex-shrink-0" />
                <span class="truncate flex-1 text-left">{{ selectedModel?.name || 'Select Model...' }}</span>
                <svg class="size-4 flex-shrink-0 text-gray-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
                </svg>
            </button>

            <!-- Info Tooltip (on hover) -->
            <ModelTooltip v-if="showTooltip" :model="selectedModel" />

        </div>
    `,
    emits: ['updated', 'update:modelValue'],
    props: {
        models: Array,
        modelValue: String,
    },
    setup(props, { emit }) {
        const ctx = inject('ctx')
        const showTooltip = ref(false)

        // Get selected model object
        const selectedModel = computed(() => {
            if (!props.modelValue || !props.models) return null
            return props.models.find(m => m.name === props.modelValue) || props.models.find(m => m.id === props.modelValue)
        })

        function openDialog() {
            ctx.state.models = props.models
            ctx.openModal('models')
        }

        watch(() => ctx.state.selectedModel, (newVal) => {
            emit('update:modelValue', newVal)
        })

        onMounted(() => {
            ctx.state.models = props.models
        })

        return {
            showTooltip,
            openDialog,
            selectedModel,
        }
    }
}

export default {
    install(ctx) {
        ctx.components({
            ProviderStatus,
            ProviderIcon,
            ModelSelector,
            ModelTooltip,
        })
        ctx.modals({
            'models': ModelSelectorModal,
        })
    }
}