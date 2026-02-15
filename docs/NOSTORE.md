### Persistence Options

By default, all chat completions are saved to the database, including both the chat thread (conversation history) and the individual API request log. 
Use these options to control what gets saved to the database when making chat completions.

#### `--nohistory`

Skip saving the **chat thread** (conversation history) to the database. The individual API **request log** is still recorded.

```sh
llms "What is the capital of France?" --nohistory
```

#### `--nostore`

Do not save **anything** to the database — no request log and no chat thread history. Implies `--nohistory`.

```sh
llms "What is the capital of France?" --nostore
```
