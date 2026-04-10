# Rule: Tool-Call Hygiene

1. **Parallel by default**: If multiple tool calls don't depend on each other's output,
   invoke them in a single parallel batch. Never chain independent Reads, Greps, or Globs
   sequentially.
2. **Combine shell checks**: Merge related shell commands into one Bash call with `&&`
   (e.g., `git status && git log --oneline -5` instead of two separate calls).
3. **5-call threshold**: If you notice 5+ similar sequential tool calls — STOP. Rethink
   the approach: batch, combine, or use a more targeted tool.
4. **Right tool for the job**: Use Glob over `find`/`ls`, Grep over `grep`/`rg`,
   Read over `cat`/`head`. Dedicated tools are cheaper than shell round-trips.
5. **Discovery budget**: Initial codebase exploration should take no more than 3-5 tool
   calls. Use Agent(Explore) for broad searches instead of manual sequential probing.
