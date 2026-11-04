This is an isolation check. Do not change, create, delete or write anything, anywhere, and do not run any command that does. Only look and report. Answer with a plain list under the numbered headings below; if something is not there, say "none". If you cannot tell, say "unknown".

1. Memory and instruction files: every memory file or CLAUDE.md (user, project, local, auto-memory) loaded into this session, with its path, and the first line of each.
2. Plugins: every plugin loaded or installed.
3. Skills: every skill available to you, by name.
4. MCP servers: every MCP server configured or connected.
5. Tools: the full list of tools you have, by name.
6. Working directory: the current working directory, and what it contains.
7. Files you can read, using read-only commands only:
   a. List /home/modeler/.claude. Besides .credentials.json, is anything else there (settings, projects, history, memory, plugins, sessions)? Name each thing. Do not print the contents of .credentials.json.
   b. Print /etc/hostname.
   c. List / and the home directories under /home, /root and /mnt. Is there anything that looks like a person's own files or data (documents, repositories, notes, screenshots, other users' folders, caches of another Claude Code installation)? Name what you find. The owner's own files should not be reachable from here; the expected answer is none.
8. Usage limit: quote the exact text you would see if the usage limit were hit, if you know it. If you do not know it, say "unknown".
