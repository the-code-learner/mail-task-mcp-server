from pathlib import Path

p = Path('src/postmaster/file_store.py')
s = p.read_text()
old = '''        sql = "SELECT * FROM stored_files" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(int(limit), 1000)), max(0, int(offset))])
        with self._conn() as conn:
            items = [self._snapshot(row) for row in conn.execute(sql, args).fetchall()]
        if tag:
            wanted = str(tag).strip().lower()
            items = [item for item in items if wanted in item.get("tags", [])]
        return items
'''
new = '''        requested_limit = max(1, min(int(limit), 1000))
        requested_offset = max(0, int(offset))
        # Tags are stored as normalized JSON metadata. When filtering by tag, fetch the
        # complete bounded candidate set first, then paginate the matches. Filtering
        # after the SQL LIMIT could otherwise hide valid tagged files behind newer
        # non-matching records.
        sql_limit = min(self.max_files, self.HARD_MAX_FILES) if tag else requested_limit
        sql_offset = 0 if tag else requested_offset
        sql = "SELECT * FROM stored_files" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        args.extend([sql_limit, sql_offset])
        with self._conn() as conn:
            items = [self._snapshot(row) for row in conn.execute(sql, args).fetchall()]
        if tag:
            wanted = str(tag).strip().lower()
            items = [item for item in items if wanted in item.get("tags", [])]
            items = items[requested_offset:requested_offset + requested_limit]
        return items
'''
if s.count(old) != 1:
    raise SystemExit(f'expected one list_files block, found {s.count(old)}')
p.write_text(s.replace(old, new, 1))
