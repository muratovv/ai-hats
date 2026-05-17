# Tool-Call Hygiene — Worked Examples

❌ **Bad** — Bash substitutes Grep:
```
Bash("rg -n 'class TaskCard' src/")
```
✅ **Good**:
```
Grep(pattern="class TaskCard", path="src/", output_mode="content", -n=true)
```

❌ **Bad** — Bash substitutes Glob:
```
Bash("find . -name 'task.yaml' | head -20")
```
✅ **Good**:
```
Glob(pattern="**/task.yaml")
```

❌ **Bad** — Bash substitutes Read on a known path:
```
Bash("cat src/ai_hats/retro/builder.py | head -100")
```
✅ **Good**:
```
Read(file_path="/abs/path/to/src/ai_hats/retro/builder.py", limit=100)
```

❌ **Bad** — Bash substitutes Edit:
```
Bash("sed -i 's/old_name/new_name/g' src/foo.py")
```
✅ **Good**:
```
Edit(file_path="/abs/path/to/src/foo.py", old_string="old_name", new_string="new_name", replace_all=true)
```
