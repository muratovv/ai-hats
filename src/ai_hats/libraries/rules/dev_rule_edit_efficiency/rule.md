# Rule: Edit Efficiency

1. **New Files**: Use Write for new files. Never build a file incrementally with Edit.
2. **Full Rewrites**: If more than 3 consecutive Edit operations target the same file,
   STOP. Plan all changes, then use Write to rewrite the file in one operation.
3. **Surgical Edits**: Use Edit only for targeted, isolated modifications to existing files
   (a few lines changed in specific locations).
4. **Plan Before Editing**: Before a series of changes to the same file, read the file,
   plan all modifications mentally, then execute in the fewest operations possible.
