# Shared model picker and checkboxes

`ModelPicker` and `CheckBox` are global Vue components registered in `llms/ui/index.mjs`.
Their styles load from `llms/index.html`; extensions need no additional stylesheet or component import.
They use native browser ESM and require no build step.

## ModelPicker

The shared model-selection dialog is used by profile overrides, Gemini File Stores and Jev recipe
creation. Main chat retains its original selector in `llms/ui/modules/model-selector.mjs`; the user
explicitly requested that it remain unchanged. Shared picker and input styling must not affect it.
The shared picker starts with 20 cards, newest releases first. Scrolling near the bottom appends 20 cards;
a keyboard-accessible **Load 20 more** button provides the same action. Filtering and sorting operate
on the entire enabled catalog, then reset the visible count and scroll position. Unknown release dates
and prices sort after known values, including when sorting prices ascending.

```html
<!-- By default the catalog comes from the injected app context. -->
<ModelPicker v-model="model" />

<!-- Restrict the catalog regardless of what the user searches for. -->
<ModelPicker v-model="model" provider="google" provider-label="Gemini" initial-search="Gemini" value-key="id"
    help-text="Select a Gemini model for this request." />

<!-- Image input and output are mandatory; only their locked icons are shown. -->
<ModelPicker v-model="model" :input-modalities="['image']" :output-modalities="['image']"
    :minimum-context="32000" require-tools />

<!-- Open from an existing chip or button; model selection remains scoped to this form. -->
<ModelPicker hide-trigger v-model:open="pickerOpen" v-model="form.model" :models="allowedModels" />
```

- `models`: optional array, defaults to `ctx.state.models`.
- `modelValue`: selected string. Both catalog `name` and `id` are recognized; selections emit the unique
  `name` by default. `valueKey="id"` emits the provider model ID instead.
- `provider`: optional fixed provider restriction, applied before all search and filtering. Its icon and
  name remain visible in a disabled provider button. `providerLabel` overrides its display name; Gemini
  uses `provider="google" provider-label="Gemini"` and excludes unsupported model types in its wrapper.
- `allowedProviders`: optional provider ID list restricting the catalog and provider popup.
- `inputModalities`, `outputModalities`: optional lists of permitted capabilities. A model must support
  at least one permitted modality in each specified direction. Only permitted icons are shown; a single
  permitted modality is selected and locked. An empty list hides that direction's controls without
  restricting capabilities. Missing modality metadata falls back to text, matching existing catalog behavior.
- `requireTools`, `requireReasoning`, `minimumContext`: mandatory model capabilities and minimum context size.
- `modelFilter`: optional predicate for additional model requirements. Caller requirements are applied
  before user search, provider selection or sorting, and remain enforced after **Clear filters**.
- `initialSearch`: search field text populated each time the dialog opens.
- `open` / `update:open`: optional controlled open state. `hideTrigger` renders only the dialog.
- `disabled`, `triggerLabel`, `placeholder`, `title`, `helpText`: trigger and dialog presentation.
- `favorites`: optional array of `provider:id` keys; enables favorite actions, a favorites filter and
  unavailable entries. `toggle-favorite` emits the model; the caller owns persistence.
- `showModalities`: shows input/output icon filters by default. Setting it to false hides the controls
  while retaining caller requirements. Search, provider selection, modality icons and sort share one
  desktop toolbar row; search and provider selection stay together when the layout wraps on narrow
  screens. Existing chat SVGs are reused for search, modalities and sorting.
  Cards show costs, context, dates, reasoning and tool support.
- `select`: emits the whole catalog model. `update:modelValue` emits its selected string. `close` fires
  on selection, Close, Escape or backdrop dismissal.
- `header-actions`: slot for caller-specific controls such as chat's provider manager.

The exposed `show()` and `close()` methods support parent-dialog lifecycle coordination. Native
`showModal()` provides top-layer rendering and focus containment. Closing restores the trigger or prior
focus. Escape is contained within the picker so an outer modal remains open. Every instance has a unique
accessible title ID. The picker never changes chat state directly; each caller owns its selected value.

The provider button opens a popup with provider icons, names, model counts and a search field. Search
matches both IDs and display names. Arrow keys navigate options; Escape, outside clicks and leaving the
popup with Tab dismiss it. Closing restores the provider button's focus unless the user moved elsewhere.

Gemini's wrapper preserves its existing API: controlled values use provider model IDs; the File Stores
preference uses unique model names. Clearing an override restores the default.

## CheckBox

```html
<label><CheckBox v-model="enabled" /> Enable setting</label>
<CheckBox :model-value="allSelected" :indeterminate="someSelected && !allSelected"
    @update:model-value="selectAll" aria-label="Select all rows" />
```

`CheckBox` emits booleans and accepts `modelValue` and `indeterminate`. Native input attributes including
`disabled`, `id` and `aria-label` pass through. Provide a label or accessible name.

`CheckBox.css` also applies the same appearance to ordinary native checkbox inputs throughout the app,
including schema-generated inputs. Existing array bindings and change handlers continue to work. Checked
boxes use a blue fill and SVG tick; indeterminate boxes use a dash. Dark mode, keyboard focus, disabled
state, reduced motion and forced colors are supported. Inputs used inside track/thumb switches
(`switch` or `sr-only`) retain their switch presentation.

## Text fields

Text fields and textareas also share `TextInput.css`, loaded globally from `llms/index.html`.
It matches Project Manager and Create with AI: 1px gray borders (gray-300 in light mode, gray-600
in dark mode), 8px corners, and a single blue focus border without additional rings or shadows. Field sizes, padding,
backgrounds and validation error colors remain controlled by each form. Borderless composer and
embedded editor inputs retain their surrounding control's presentation. A custom embedded field
can use `llms-input-unframed` to let its surrounding component own the border. For a search control
with an embedded icon, give the outer container `llms-input-frame` and its input
`llms-input-unframed`; the shared focus border then surrounds the whole control, and the inner input stays borderless.
Focus must not change a control’s padding, border width or dimensions. Never combine a wrapper’s
focus indicator with an outline or ring on its inner input.

## Verification

```sh
desktop/.venv/bin/python tests/verify_jev_browser.py --fixture model-picker
desktop/.venv/bin/python tests/verify_jev_browser.py --fixture model-picker --width 390 --dark
```

The fixture mounts the shared component, Gemini and profile wrappers, and the original chat selector. It verifies
pagination, search/filter resets, provider popup keyboard behavior, provider and capability confinement,
value semantics, original chat favorite behavior, nested
Escape behavior, selection isolation, checkbox bindings, centering and responsive overflow.
